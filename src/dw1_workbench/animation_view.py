from __future__ import annotations

import math
from dataclasses import dataclass

from PIL import Image
from PIL.ImageQt import ImageQt
from PySide6.QtCore import QTimer, Qt, Signal, Slot
from PySide6.QtGui import QVector3D
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QHeaderView, QLabel, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .clip_names import ClipName
from .animation import (OP_KEYFRAME, OP_SOUND, OP_TEXTURE, TICKS_PER_SECOND, AnimationClip, AnimationPlayer,
                        SoundEvent, TextureEvent)
from .mmd import StaticModel, StaticPose, Vector3
from .model_view import StaticModelView


@dataclass(frozen=True)
class ModelLoad:
    """Everything a background model load produces; geometry may be unsupported while clips still parse."""
    label: str
    model: StaticModel | None
    geometry_error: str | None
    palettes: tuple[Image.Image, ...]
    clut_y: int
    used_palettes: tuple[int, ...]
    bits_per_pixel: int
    bone_count: int
    clips: tuple[AnimationClip | None, ...]
    clip_error: str | None
    clip_names: dict[int, ClipName] | None = None


def _static_pose(pose, translation=None) -> StaticPose:
    angle = 2 * math.pi / 4096
    return StaticPose(Vector3(*(value / 4096 for value in pose.scale)),
                      Vector3(*(value * angle for value in pose.rotation)),
                      Vector3(*(float(value) for value in (translation or pose.translation))))


class ModelAnimationPanel(QWidget):
    """3D preview with render modes and game-accurate animation playback for one loaded model."""

    load_requested = Signal()
    CLIP_COLUMNS = ["Clip", "Name", "Confidence", "Frames", "Loops", "Keyframes", "Sound cues", "Texture copies"]

    def __init__(self, load_text: str | None = None, clip_table: bool = False, parent=None):
        super().__init__(parent)
        self.view = StaticModelView()
        top = QHBoxLayout()
        self.mode = QComboBox(); self.mode.addItems(["Textured", "Untextured"])
        self.wireframe = QCheckBox("Wireframe overlay")
        reset = QPushButton("Reset view"); reset.clicked.connect(self.view.reset_view)
        self.mode.currentIndexChanged.connect(self._update_modes); self.wireframe.toggled.connect(self._update_modes)
        top.addWidget(self.mode); top.addWidget(self.wireframe); top.addWidget(reset); top.addStretch()
        if load_text:
            load = QPushButton(load_text); load.clicked.connect(self.load_requested); top.addWidget(load)
        controls = QHBoxLayout()
        self.clip = QComboBox(); self.clip.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.clip.currentIndexChanged.connect(self._select_clip)
        self.play = QPushButton("Play"); self.play.clicked.connect(self.toggle)
        step = QPushButton("Step"); step.clicked.connect(self.step)
        restart = QPushButton("Restart"); restart.clicked.connect(self.restart)
        self.root = QCheckBox("Apply root motion"); self.root.toggled.connect(self.restart)
        self._controls = (self.clip, self.play, step, restart, self.root)
        controls.addWidget(QLabel("Animation")); controls.addWidget(self.clip); controls.addWidget(self.play)
        controls.addWidget(step); controls.addWidget(restart); controls.addWidget(self.root); controls.addStretch()
        self.animation_status = QLabel("Load a model to play its animation clips."); self.animation_status.setWordWrap(True)
        self.model_status = QLabel("Model geometry is loaded on request in a cancellable background worker.")
        self.model_status.setWordWrap(True)
        self.timer = QTimer(self); self.timer.setInterval(1000 // TICKS_PER_SECOND); self.timer.timeout.connect(self.step)
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(top)
        self.clip_table = None
        if clip_table:
            self.clip_table = QTableWidget(0, len(self.CLIP_COLUMNS))
            self.clip_table.setHorizontalHeaderLabels(self.CLIP_COLUMNS)
            self.clip_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
            self.clip_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
            self.clip_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            self.clip_table.verticalHeader().setVisible(False)
            header = self.clip_table.horizontalHeader()
            header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
            self.clip_table.setMaximumHeight(190)
            self.clip_table.currentCellChanged.connect(lambda row, *_: self.clip.setCurrentIndex(row))
            layout.addWidget(self.clip_table)
        layout.addWidget(self.view, 1); layout.addLayout(controls)
        layout.addWidget(self.animation_status); layout.addWidget(self.model_status)
        self._clips: list[AnimationClip] = []
        self._player: AnimationPlayer | None = None
        self._load: ModelLoad | None = None
        self._set_enabled(False)

    def _set_enabled(self, enabled: bool):
        for widget in self._controls:
            widget.setEnabled(enabled)

    def _update_modes(self):
        self.view.set_modes(self.mode.currentIndex() == 0, self.wireframe.isChecked())

    def stop(self):
        self.timer.stop(); self.play.setText("Play")

    def show_message(self, text: str):
        self.stop(); self.model_status.setText(text)

    @Slot(object)
    def show_load(self, load: ModelLoad):
        self.stop()
        self._load, self._player = load, None
        self._clips = [clip for clip in load.clips if clip is not None]
        if load.model is not None:
            self._images = tuple(load.palettes)
            self._texels_per_unit = 16 // load.bits_per_pixel
            self.view.set_model(load.model, tuple(ImageQt(image).copy() for image in load.palettes), load.clut_y)
            self.model_status.setText(
                f"{load.label}: experimental static preview — {len(load.model.objects)} objects, "
                f"{load.model.vertex_count} vertices, {load.model.primitive_count} primitives / "
                f"{load.model.triangle_count} triangles; verified palettes "
                f"{', '.join(map(str, load.used_palettes))}. {load.model.warnings[0]}")
        else:
            self.view.clear()
            self.model_status.setText(f"{load.label}: geometry unsupported ({load.geometry_error}). "
                                      "Its clips are listed but cannot be previewed.")
        self.clip.blockSignals(True); self.clip.clear()
        for clip in self._clips:
            name = self._name(clip.index)
            self.clip.addItem(f"Clip {clip.index}{f' — {name.name}' if name else ''} — {clip.frame_count} frames"
                              f"{', loops' if clip.loops_forever else ''}")
        self.clip.blockSignals(False)
        if self.clip_table is not None:
            self._fill_clip_table()
        playable = bool(self._clips) and load.model is not None
        self._set_enabled(playable)
        self.clip.setEnabled(bool(self._clips))
        if load.clip_error:
            self.animation_status.setText(f"Animation clips unsupported: {load.clip_error}")
        elif not self._clips:
            self.animation_status.setText("This model has no animation clips.")
        elif playable:
            self._select_clip(0)
        else:
            self.animation_status.setText(f"{len(self._clips)} clip(s) decoded; playback needs supported geometry.")

    def _fill_clip_table(self):
        table = self.clip_table
        table.blockSignals(True); table.setRowCount(len(self._clips))
        for row, clip in enumerate(self._clips):
            counts = {opcode: sum(i.opcode == opcode for i in clip.instructions)
                      for opcode in (OP_KEYFRAME, OP_SOUND, OP_TEXTURE)}
            name = self._name(clip.index)
            values = (clip.index, name.name if name else "not established", name.confidence if name else "—",
                      clip.frame_count, "forever" if clip.loops_forever else "no",
                      counts[OP_KEYFRAME], counts[OP_SOUND], counts[OP_TEXTURE])
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column != 1:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item.setToolTip(f"Evidence: {name.evidence}" if name else
                                "No game code starts this clip ID directly; it may be used by map or event scripts.")
                table.setItem(row, column, item)
        table.blockSignals(False)
        if self._clips:
            table.selectRow(0)

    def _name(self, clip_index: int) -> ClipName | None:
        return (self._load.clip_names or {}).get(clip_index) if self._load else None

    def _select_clip(self, index: int):
        if self.clip_table is not None and self.clip_table.currentRow() != index and index >= 0:
            self.clip_table.blockSignals(True); self.clip_table.selectRow(index); self.clip_table.blockSignals(False)
        if not 0 <= index < len(self._clips) or self._load is None or self._load.model is None:
            return
        self._player = AnimationPlayer(self._clips[index], self._load.bone_count)
        self.restart()

    def restart(self, *_):
        if self._player is None:
            return
        self._player.reset()
        self._events_seen, self._offset = 0, [0.0, 0.0, 0.0]
        self._working = [image.copy() for image in self._images]
        self.view.set_palettes(tuple(ImageQt(image).copy() for image in self._working))
        self._show_pose()

    def toggle(self):
        if self.timer.isActive():
            self.stop()
        elif self._player is not None:
            if not self._player.running:
                self.restart()
            self.timer.start(); self.play.setText("Pause")

    def step(self):
        player = self._player
        if player is None:
            return
        if not player.running:
            self.stop()
            return
        player.tick()
        if self.root.isChecked():
            # applyRootMomentum rotates the root velocity by the root bone's matrix
            matrix = StaticModelView._local_matrix(_static_pose(player.pose(0), translation=(0, 0, 0)))
            moved = matrix.map(QVector3D(*player.root_velocity))
            self._offset = [self._offset[0] + moved.x(), self._offset[1] + moved.y(), self._offset[2] + moved.z()]
        new_events = player.events[self._events_seen:]
        self._events_seen = len(player.events)
        copies = [event for event in new_events if isinstance(event, TextureEvent)]
        for event in copies:
            self._apply_texture_copy(event)
        if copies:
            self.view.set_palettes(tuple(ImageQt(image).copy() for image in self._working))
        self._show_pose()

    def _apply_texture_copy(self, event: TextureEvent):
        # The game moves a VRAM rectangle (16-bit units) inside the model's texture; at 4 bpp a unit is 4 texels.
        unit = self._texels_per_unit
        left, top = event.source[0] * unit, event.source[1]
        box = (left, top, left + event.size[0] * unit, top + event.size[1])
        target = (event.destination[0] * unit, event.destination[1])
        for image in self._working:
            width, height = image.size
            if box[2] <= width and box[3] <= height and target[0] + box[2] - box[0] <= width \
                    and target[1] + box[3] - box[1] <= height:
                image.paste(image.crop(box), target)

    def _show_pose(self):
        player = self._player
        poses = [_static_pose(player.pose(bone)) for bone in range(player.bone_count)]
        poses[0] = _static_pose(player.pose(0), translation=(0, 0, 0))
        self.view.set_pose(poses, tuple(self._offset))
        sounds = sum(isinstance(event, SoundEvent) for event in player.events)
        copies = sum(isinstance(event, TextureEvent) for event in player.events)
        clip = player.clip
        state = "finished" if not player.running else ("playing" if self.timer.isActive() else "paused")
        name = self._name(clip.index)
        self.animation_status.setText(
            f"Clip {clip.index}{f' ({name.name}, {name.confidence})' if name else ''}: frame {player.frame}/{clip.frame_count}, tick {player.ticks} "
            f"({player.ticks / TICKS_PER_SECOND:.2f} s at an assumed {TICKS_PER_SECOND} ticks/s), {state}"
            f"{'; loops forever' if clip.loops_forever else ''}. Events so far: {sounds} sound cue(s) (not played), "
            f"{copies} texture copy(ies) applied. Stepping follows the game's integer momentum code.")
