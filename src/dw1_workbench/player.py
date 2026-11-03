"""Play window: runs the user's game through a libretro core on a worker thread.

The core runs on its own QThread at the core's frame rate. Frames, audio and messages reach the GUI
through queued signals only; the GUI talks back through thread-safe attributes and a command queue.
"""
from __future__ import annotations

import hashlib
import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from PIL.ImageQt import ImageQt
from PySide6.QtCore import QBuffer, QCoreApplication, QPointF, QByteArray, QIODevice, QObject, QRectF, Qt, QThread, Signal, Slot
from PySide6.QtGui import QAction, QColor, QImage, QKeySequence, QPainter, QPen
from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDockWidget, QFormLayout, QLabel,
                               QLineEdit, QMainWindow, QMessageBox, QToolBar, QVBoxLayout, QWidget)

from .inspection import CATEGORIES, InspectionError, InspectionStore
from .libretro import (BUTTON, PIXEL_0RGB1555, PIXEL_RGB565, PIXEL_XRGB8888, Frame, LibretroCore,
                       LibretroError)
from .live import LiveNpc, LiveState, grid_differences, read_live_state
from .maps import follow_kind, has_tracking_box, trigger_description, trigger_grid_image, tracking_box
from .quicksave import RESUME_SLOT, SLOT_COUNT, QuickSaveError, QuickSaveStore

FAST_FORWARD = 4  # speed while fast-forward is held
MEMORY_CARD_FLUSH_FRAMES = 600  # write a changed memory card about every 10 s
LIVE_FRAMES = 10  # read the live map, positions and grid about six times a second
IMAGE_FORMATS = {PIXEL_XRGB8888: QImage.Format.Format_RGB32, PIXEL_RGB565: QImage.Format.Format_RGB16,
                 PIXEL_0RGB1555: QImage.Format.Format_RGB555}

# Keyboard defaults. On the PlayStation core B is Cross, A Circle, Y Square and X Triangle.
DEFAULT_KEYS = {
    Qt.Key.Key_Up: "Up", Qt.Key.Key_Down: "Down", Qt.Key.Key_Left: "Left", Qt.Key.Key_Right: "Right",
    Qt.Key.Key_Z: "B", Qt.Key.Key_X: "A", Qt.Key.Key_A: "Y", Qt.Key.Key_S: "X",
    Qt.Key.Key_Q: "L", Qt.Key.Key_W: "R", Qt.Key.Key_E: "L2", Qt.Key.Key_R: "R2",
    Qt.Key.Key_Return: "Start", Qt.Key.Key_Enter: "Start", Qt.Key.Key_Backspace: "Select",
}
KEY_HELP = ("Arrows: D-pad  •  Z Cross  X Circle  A Square  S Triangle  •  Q/W L1/R1  E/R L2/R2  •  "
            "Enter Start  Backspace Select  •  F5 quick save  F8 quick load  F6/F7 slot  •  "
            "F9 inspect here  F10 live map  •  hold Tab fast-forward  •  P pause  •  F11 full screen")


@dataclass(frozen=True)
class PlayConfig:
    core_path: Path
    bios_dir: Path
    disc_path: Path
    store_dir: Path
    launch_slot: int | None = None
    launch_inspection: str | None = None  # start from an inspection's save state instead of a slot
    resume_on_close: bool = True
    live_ram: bool = False  # read map/position/grid from RAM; the addresses are only valid for USA SLUS_010.32
    options: dict[str, str] = field(default_factory=dict)


def frame_image(frame: Frame) -> QImage:
    return QImage(frame.data, frame.width, frame.height, frame.pitch, IMAGE_FORMATS[frame.pixel_format]).copy()


def png_bytes(image: QImage | None) -> bytes | None:
    if image is None or image.isNull():
        return None
    data = QByteArray(); buffer = QBuffer(data); buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(data.data())


class EmulatorWorker(QObject):
    frame_ready = Signal()
    audio_ready = Signal(bytes)
    message = Signal(str)
    started_ok = Signal(object)  # dict with core identity and timing
    failed = Signal(str)
    slot_saved = Signal(object)  # SlotInfo
    slot_loaded = Signal(int)
    command_failed = Signal(str)
    live_changed = Signal(object)  # LiveState
    inspection_captured = Signal(object)  # dict: state, screenshot, live
    finished = Signal()

    def __init__(self, config: PlayConfig):
        super().__init__()
        self.config = config
        self.store = QuickSaveStore(config.store_dir)
        self.buttons = 0
        self.paused = False
        self.fast_forward = False
        self._stop = threading.Event()
        self._commands: queue.SimpleQueue = queue.SimpleQueue()
        self._image_lock = threading.Lock()
        self._image: QImage | None = None  # newest frame
        self._image_pending = False
        self._last_image: QImage | None = None
        self._card_hash = None
        self._live_key = None
        self.live: LiveState | None = None
        self.core: LibretroCore | None = None

    # -- called from the GUI thread ------------------------------------------------------------------------

    def request_stop(self):
        self._stop.set()

    def quick_save(self, slot: int, note: str = ""):
        self._commands.put(("save", slot, note))

    def quick_load(self, slot: int):
        self._commands.put(("load", slot))

    def reset(self):
        self._commands.put(("reset",))

    def inspect(self):
        self._commands.put(("inspect",))

    def take_frame(self) -> QImage | None:
        with self._image_lock:
            self._image_pending = False
            return self._image

    # -- worker thread -------------------------------------------------------------------------------------

    @Slot()
    def run(self):
        config = self.config
        try:
            config.store_dir.mkdir(parents=True, exist_ok=True)
            self.core = LibretroCore(config.core_path, config.bios_dir, config.store_dir, config.options,
                                     log=self.message.emit)
            self.core.start(config.disc_path)
        except (LibretroError, OSError) as exc:
            self.failed.emit(str(exc)); self.finished.emit()
            return
        core = self.core
        try:
            card = self.store.read_memory_card()
            if card is not None:
                core.load_save_ram(card); self._card_hash = hashlib.sha256(card).digest()
                self.message.emit(f"Memory card loaded from {self.store.memory_card_path}")
            try:
                if config.launch_slot is not None:
                    self._load(config.launch_slot)
                elif config.launch_inspection is not None:
                    self._load_inspection(config.launch_inspection)
            except (LibretroError, QuickSaveError, InspectionError, OSError) as exc:
                self.command_failed.emit(f"Started from power-on instead: {exc}")
            self.started_ok.emit({"core": core.info.name, "version": core.info.version, "fps": core.fps,
                                  "sample_rate": core.sample_rate, "aspect": core.aspect_ratio})
            self._loop(core)
        except Exception as exc:
            self.failed.emit(f"The game stopped unexpectedly: {exc}")
        finally:
            self._shutdown(core)
            self.finished.emit()

    def _loop(self, core: LibretroCore):
        frames = 0
        deadline = time.perf_counter()
        while not self._stop.is_set() and not core.shutdown_requested:
            self._run_commands(core)
            if self.paused:
                self._stop.wait(0.02); deadline = time.perf_counter()
                continue
            core.buttons = self.buttons
            frame, audio = core.run_frame()
            frames += 1
            if frame is not None:
                self._publish(frame_image(frame))
            fast = self.fast_forward
            if audio and not fast:
                self.audio_ready.emit(audio)
            if frames % MEMORY_CARD_FLUSH_FRAMES == 0:
                self._flush_memory_card(core)
            if self.config.live_ram and frames % LIVE_FRAMES == 0:
                self._read_live(core)
            # core.fps is read every frame: cores may change timing (Beetle PSX settles on 59.826 fps after boot).
            deadline += 1.0 / (core.fps * (FAST_FORWARD if fast else 1))
            delay = deadline - time.perf_counter()
            if delay > 0:
                self._stop.wait(delay)
            elif delay < -0.25:  # far behind (a slow frame or a breakpoint): do not try to catch up
                deadline = time.perf_counter()

    def _publish(self, image: QImage):
        self._last_image = image
        with self._image_lock:
            self._image, notify = image, not self._image_pending
            self._image_pending = True
        if notify:  # at most one queued notification; the GUI always takes the newest frame
            self.frame_ready.emit()

    def _run_commands(self, core: LibretroCore):
        while True:
            try:
                command = self._commands.get_nowait()
            except queue.Empty:
                return
            try:
                if command[0] == "save":
                    self.slot_saved.emit(self._save(command[1], command[2]))
                elif command[0] == "load":
                    self._load(command[1]); self.slot_loaded.emit(command[1])
                elif command[0] == "reset":
                    core.reset(); self.message.emit("Console reset")
                elif command[0] == "inspect":
                    if self.config.live_ram:
                        self._read_live(core)
                    self.inspection_captured.emit({"state": core.serialize(), "screenshot": png_bytes(self._last_image),
                                                   "live": self.live, "core": core.info.name,
                                                   "core_version": core.info.version})
            except (LibretroError, QuickSaveError, InspectionError, OSError) as exc:
                self.command_failed.emit(str(exc))

    def _save(self, slot: int, note: str = ""):
        core = self.core
        info = self.store.save(slot, core.serialize(), png_bytes(self._last_image), core.info.name,
                               core.info.version, note)
        self.message.emit(f"{info.label} saved ({info.size:,} bytes){f': {info.note}' if info.note else ''}")
        return info

    def _load(self, slot: int):
        core = self.core
        info = self.store.info(slot)
        core.unserialize(self.store.load(slot, core=core.info.name))
        if info and info.core_version != core.info.version:
            self.message.emit(f"{info.label} was made by core version {info.core_version}; "
                              f"running {core.info.version}")
        self.message.emit(f"{info.label if info else f'Slot {slot}'} loaded")

    def _load_inspection(self, inspection_id: str):
        store = InspectionStore(self.config.store_dir / "inspections")
        record = store.get(inspection_id)
        if record is not None and record.core != self.core.info.name:
            raise InspectionError(f"That inspection was saved by {record.core}, not {self.core.info.name}.")
        self.core.unserialize(store.state(inspection_id))
        self.message.emit(f"Started from the inspection at {record.map_name if record else inspection_id}")

    def _read_live(self, core: LibretroCore):
        try:
            live = read_live_state(core.read_memory)
        except LibretroError as exc:
            self.message.emit(f"Live map reading turned off: {exc}")
            self.config = PlayConfig(**{**self.config.__dict__, "live_ram": False})
            return
        key = (live.map_index, live.tamer_tile, live.partner_tile, hash(live.grid),
               tuple((npc.slot, npc.character_id, npc.tile) for npc in live.npcs))
        self.live = live
        if key != self._live_key:
            self._live_key = key
            self.live_changed.emit(live)

    def _flush_memory_card(self, core: LibretroCore):
        card = core.save_ram()
        if card is None:
            return
        digest = hashlib.sha256(card).digest()
        if digest != self._card_hash:
            self.store.write_memory_card(card); self._card_hash = digest
            self.message.emit("Memory card written")

    def _shutdown(self, core: LibretroCore):
        try:
            if self.config.resume_on_close and core.av is not None:
                self.slot_saved.emit(self._save(RESUME_SLOT, "Saved when the game was closed"))
        except (LibretroError, QuickSaveError, OSError) as exc:
            self.message.emit(f"Resume save failed: {exc}")
        try:
            if core.av is not None:
                self._flush_memory_card(core)
        except OSError as exc:
            self.message.emit(f"Memory card could not be written: {exc}")
        core.stop()
        self.message.emit("Game closed")


class AudioOutput:
    """Pushes the core's 16-bit stereo audio to the default output; drops audio rather than lagging."""

    def __init__(self, sample_rate: int, log):
        audio_format = QAudioFormat()
        audio_format.setSampleRate(sample_rate); audio_format.setChannelCount(2)
        audio_format.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        device = QMediaDevices.defaultAudioOutput()
        self.sink = self.device = None
        if device.isNull():
            log("No audio output device; playing without sound")
            return
        if not device.isFormatSupported(audio_format):
            log(f"The audio device does not accept {sample_rate} Hz stereo 16-bit; playing without sound")
            return
        self.sink = QAudioSink(device, audio_format)
        self.sink.setBufferSize(sample_rate * 4 // 6)  # about 170 ms
        self.device = self.sink.start()

    def write(self, data: bytes):
        if self.device is not None and self.sink.bytesFree() >= len(data):
            self.device.write(data)

    def stop(self):
        if self.sink is not None:
            self.sink.stop()


class FrameView(QWidget):
    """Draws the newest frame letterboxed at the core's aspect ratio; forwards keys to the window."""

    def __init__(self, key_handler, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(320, 240)
        self.image: QImage | None = None
        self.aspect = 4 / 3
        self.smooth = False
        self.text = "Starting…"
        self._keys = key_handler

    def target(self) -> QRectF:
        width, height = self.width(), self.height()
        if width / height > self.aspect:
            shown = height * self.aspect
            return QRectF((width - shown) / 2, 0, shown, height)
        shown = width / self.aspect
        return QRectF(0, (height - shown) / 2, width, shown)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("black"))
        if self.image is not None:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, self.smooth)
            painter.drawImage(self.target(), self.image)
        if self.text:
            painter.setPen(QColor("#e0e0e0"))
            painter.drawText(self.rect().adjusted(12, 12, -12, -12),
                             Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft, self.text)

    def keyPressEvent(self, event):
        if not self._keys(event, True):
            super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if not self._keys(event, False):
            super().keyReleaseEvent(event)

    def focusOutEvent(self, event):
        self._keys(None, False)  # release every held button so none sticks
        super().focusOutEvent(event)


NPC_COLOURS = {"chases you": QColor("#e8413c"), "watches you": QColor("#f2c94c"), "ignores you": QColor("#ff9a1f")}


def is_hostile(npc: LiveNpc) -> bool:
    """Chases the Tamer when it comes near, and starts its script (a battle) on contact."""
    return follow_kind(npc.follow_mode) == "chases you" and npc.autotalk


class LiveMapView(QWidget):
    """The running game's tile-trigger grid with the player (white), partner (cyan) and NPCs marked.

    NPCs are red when they chase the Tamer, yellow when they only turn to watch, orange otherwise; the box
    around each chaser or watcher is where the Tamer sets it off, drawn solid while it is reacting.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(300, 300)
        self.grid: QImage | None = None
        self._grid_bytes = None
        self.tamer = self.partner = None
        self.npcs: list[tuple[LiveNpc, str]] = []

    def show_live(self, live: LiveState, npc_names: list[str] | None = None):
        if live.grid != self._grid_bytes:
            self._grid_bytes = live.grid
            self.grid = QImage(ImageQt(trigger_grid_image(live.grid))).copy()
        self.tamer, self.partner = live.tamer_tile, live.partner_tile
        names = npc_names or [f"ID {npc.character_id}" for npc in live.npcs]
        self.npcs = list(zip(live.npcs, names))
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#20242b"))
        if self.grid is None:
            painter.setPen(QColor("#d0d4da"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Waiting for the game")
            return
        side = min(self.width(), self.height())
        cell = side / 100
        left, top = (self.width() - side) / 2, (self.height() - side) / 2
        painter.drawImage(QRectF(left, top, side, side), self.grid)
        radius = max(3.0, cell * 1.5)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for npc, _ in self.npcs:
            kind = follow_kind(npc.follow_mode)
            if not has_tracking_box(npc.follow_mode) or npc.tracking_range <= 0:
                continue
            (x0, y0), (x1, y1) = tracking_box(npc.post, npc.tracking_range)
            pen = QPen(NPC_COLOURS[kind], 2 if npc.reacting else 1,
                       Qt.PenStyle.SolidLine if npc.reacting else Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawRect(QRectF(left + x0 * cell, top + y0 * cell, (x1 - x0 + 1) * cell, (y1 - y0 + 1) * cell))
        markers = [(npc.tile, NPC_COLOURS[follow_kind(npc.follow_mode)], name + (" !" if npc.reacting else ""))
                   for npc, name in self.npcs]
        markers += [(self.partner, QColor("cyan"), None), (self.tamer, QColor("white"), None)]
        for tile, colour, name in markers:
            if tile is None:
                continue
            x, y = left + (tile[0] + 0.5) * cell, top + (tile[1] + 0.5) * cell
            # NPCs get a white ring so they stand apart from the coloured exit tiles.
            painter.setPen(QPen(QColor("white" if name else "black"), 1.5 if name else 1)); painter.setBrush(colour)
            painter.drawEllipse(QRectF(x - radius, y - radius, radius * 2, radius * 2))
            if name:
                painter.setPen(QColor("#ffd9a8")); painter.drawText(QPointF(x + radius + 2, y + 4), name)


class InspectDialog(QDialog):
    def __init__(self, summary: str, note: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Inspect here")
        form = QFormLayout(self)
        where = QLabel(summary); where.setWordWrap(True); form.addRow("Where", where)
        self.category = QComboBox(); self.category.addItems(CATEGORIES); form.addRow("What", self.category)
        self.note = QLineEdit(note)
        self.note.setPlaceholderText("What you see, e.g. gateway south to Native Forest, toilet behind the gym")
        form.addRow("Note", self.note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); form.addRow(buttons)
        self.resize(520, self.sizeHint().height())


class PlayWindow(QMainWindow):
    """One game session. Closing the window stops the core, writes the resume slot and the memory card."""

    slots_changed = Signal()
    inspections_changed = Signal()
    log = Signal(str)
    closed = Signal()

    def __init__(self, config: PlayConfig, title: str = "Digimon World", map_names: dict[int, str] | None = None,
                 decoded_grid=None, character_names: dict[int, str] | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{title} — DW1-coop player")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(960, 760)
        self.config = config
        self.keys = dict(DEFAULT_KEYS)
        self._held = 0
        self.audio: AudioOutput | None = None
        self.view = FrameView(self._handle_key)
        self.setCentralWidget(self.view)
        self._build_toolbar()
        self.status = QLabel("Starting the core…"); self.statusBar().addWidget(self.status, 1)
        self.map_names = map_names or {}
        self.character_names = character_names or {}
        self._decoded_grid = decoded_grid or (lambda index: None)
        self._decoded_cache: dict[int, bytes | None] = {}
        self.inspections = InspectionStore(config.store_dir / "inspections")
        self.live_label = QLabel("Live map: off (USA release only)" if not config.live_ram else "Live map: waiting")
        self.statusBar().addPermanentWidget(self.live_label)
        self.statusBar().addPermanentWidget(QLabel("F1: controls"))
        self.live_map = LiveMapView()
        self.npc_list = QLabel("NPCs on this map: —"); self.npc_list.setWordWrap(True)
        live_panel = QWidget(); live_layout = QVBoxLayout(live_panel)
        live_layout.addWidget(self.live_map, 1); live_layout.addWidget(self.npc_list)
        self.live_dock = QDockWidget("Live map", self); self.live_dock.setWidget(live_panel)
        self.live_dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetClosable)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.live_dock)
        self.live_dock.setVisible(config.live_ram)
        self._live: LiveState | None = None
        self.thread = QThread(self)
        self.worker = EmulatorWorker(config)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.frame_ready.connect(self._show_frame)
        self.worker.audio_ready.connect(self._play_audio)
        self.worker.message.connect(self._message)
        self.worker.started_ok.connect(self._started)
        self.worker.failed.connect(self._failed)
        self.worker.slot_saved.connect(self._slot_saved)
        self.worker.slot_loaded.connect(self._slot_loaded)
        self.worker.command_failed.connect(self._command_failed)
        self.worker.live_changed.connect(self._live_changed)
        self.worker.inspection_captured.connect(self._inspection_captured)
        # Direct: stop() blocks the GUI thread in wait(), so a queued quit would never be delivered.
        self.worker.finished.connect(self.thread.quit, Qt.ConnectionType.DirectConnection)
        self._finished = False
        self.thread.finished.connect(self._thread_finished)

    def start(self):
        self.show(); self.view.setFocus()
        self.thread.start()

    def _build_toolbar(self):
        bar = QToolBar("Quick saves"); bar.setMovable(False); self.addToolBar(bar)
        self.slot = QComboBox(); self.slot.addItems([f"Slot {index}" for index in range(1, SLOT_COUNT + 1)])
        self.note = QLineEdit(); self.note.setPlaceholderText("Note for the next quick save, e.g. the zone")
        self.note.setMaximumWidth(320); self.note.returnPressed.connect(self.view.setFocus)

        def action(text, shortcut, handler, checkable=False):
            item = QAction(text, self); item.setShortcut(QKeySequence(shortcut))
            item.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
            item.setCheckable(checkable); item.triggered.connect(handler); bar.addAction(item)
            return item

        bar.addWidget(self.slot)
        action("Save", "F5", lambda: self.worker.quick_save(self.current_slot, self.note.text()))
        action("Load", "F8", lambda: self.worker.quick_load(self.current_slot))
        previous = QAction(self); previous.setShortcut(QKeySequence("F6")); previous.triggered.connect(lambda: self._step_slot(-1))
        following = QAction(self); following.setShortcut(QKeySequence("F7")); following.triggered.connect(lambda: self._step_slot(1))
        self.addAction(previous); self.addAction(following)
        bar.addWidget(self.note); bar.addSeparator()
        self.pause = action("Pause", "P", self._toggle_pause, checkable=True)
        action("Reset", "Ctrl+R", self._reset)
        self.full = action("Full screen", "F11", self._toggle_full_screen, checkable=True)
        smooth = QCheckBox("Smooth"); smooth.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        smooth.toggled.connect(self._set_smooth); bar.addWidget(smooth)
        inspect = QAction(self); inspect.setShortcut(QKeySequence("F9")); inspect.triggered.connect(self.worker_inspect)
        live_toggle = QAction(self); live_toggle.setShortcut(QKeySequence("F10"))
        live_toggle.triggered.connect(lambda: self.live_dock.setVisible(not self.live_dock.isVisible()))
        self.addAction(inspect); self.addAction(live_toggle)
        help_action = QAction(self); help_action.setShortcut(QKeySequence("F1")); help_action.triggered.connect(self._show_help)
        self.addAction(help_action)
        for widget in (self.slot,):
            widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    @property
    def current_slot(self) -> int:
        return self.slot.currentIndex() + 1

    def _step_slot(self, step: int):
        self.slot.setCurrentIndex((self.slot.currentIndex() + step) % SLOT_COUNT)
        info = self.worker.store.info(self.current_slot)
        self.status.setText(f"Slot {self.current_slot}: " + (f"{info.created}{f' — {info.note}' if info.note else ''}"
                                                             if info else "empty"))

    def _handle_key(self, event, pressed: bool) -> bool:
        if event is None:
            self._held = 0; self.worker.buttons = 0; self.worker.fast_forward = False
            return True
        if event.isAutoRepeat():
            return event.key() in self.keys or event.key() == Qt.Key.Key_Tab
        if event.key() == Qt.Key.Key_Tab:
            self.worker.fast_forward = pressed
            return True
        button = self.keys.get(event.key())
        if button is None:
            return False
        bit = 1 << BUTTON[button]
        self._held = self._held | bit if pressed else self._held & ~bit
        self.worker.buttons = self._held
        return True

    def focusNextPrevChild(self, _next):
        return False  # Tab is the fast-forward key, not focus navigation

    def worker_inspect(self):
        self.worker.inspect()

    def map_label(self, index: int) -> str:
        return self.map_names.get(index, f"map {index}")

    def character_label(self, character_id: int) -> str:
        return self.character_names.get(character_id, f"ID {character_id}")

    def npc_description(self, npc: LiveNpc) -> str:
        kind = follow_kind(npc.follow_mode)
        parts = [f"{self.character_label(npc.character_id)} at {npc.tile[0]},{npc.tile[1]}"]
        if has_tracking_box(npc.follow_mode):
            parts.append(f"{kind} within ±{npc.tracking_range / 100:g} tiles of its post")
        elif kind != "ignores you":
            parts.append(f"{kind} (the game's radius test, range {npc.tracking_range})")
        if npc.autotalk:
            parts.append("starts its script (a battle for enemies) on contact")
        parts.append(f"HP {npc.hp}/{npc.max_hp}")
        if npc.reacting:
            parts.append("REACTING NOW")
        return ", ".join(parts)

    def npc_summary(self, live: LiveState) -> str:
        return ", ".join(f"{self.character_label(npc.character_id)} at {npc.tile[0]},{npc.tile[1]}"
                         for npc in live.npcs) or "none"

    def threat_summary(self, live: LiveState) -> str:
        chasing = [self.character_label(npc.character_id) for npc in live.npcs if npc.reacting and is_hostile(npc)]
        hostile = sum(is_hostile(npc) for npc in live.npcs)
        if chasing:
            return f"⚠ {', '.join(chasing)} {'is' if len(chasing) == 1 else 'are'} after you"
        return f"{hostile} hostile Digimon on this map" if hostile else ""

    def decoded_grid(self, index: int) -> bytes | None:
        if index not in self._decoded_cache:
            try:
                self._decoded_cache[index] = self._decoded_grid(index)
            except Exception:
                self._decoded_cache[index] = None
        return self._decoded_cache[index]

    def grid_difference_count(self, live: LiveState) -> int | None:
        decoded = self.decoded_grid(live.map_index)
        return None if decoded is None else len(grid_differences(live.grid, decoded))

    def live_summary(self, live: LiveState) -> str:
        parts = [f"{self.map_label(live.map_index)} (map {live.map_index})"]
        if live.tamer_tile is not None:
            trigger = live.trigger_at(live.tamer_tile)
            parts.append(f"tile {live.tamer_tile[0]},{live.tamer_tile[1]}"
                         + (f" — {trigger_description(trigger)}" if trigger is not None else ""))
        differences = self.grid_difference_count(live)
        parts.append("grid: no decoded MAP" if differences is None else
                     "grid matches the decoded MAP" if differences == 0 else
                     f"grid: {differences} tiles differ from the decoded MAP")
        return " · ".join(parts)

    @Slot(object)
    def _live_changed(self, live: LiveState):
        self._live = live
        threat = self.threat_summary(live)
        self.live_label.setText(self.live_summary(live) + (f" · {threat}" if threat else ""))
        self.live_map.show_live(live, [self.character_label(npc.character_id) for npc in live.npcs])
        self.npc_list.setText("Digimon on this map:\n" + ("\n".join(f"• {self.npc_description(npc)}" for npc in live.npcs)
                                                         or "none"))

    @Slot(object)
    def _inspection_captured(self, capture: dict):
        live: LiveState | None = capture["live"]
        was_paused = self.worker.paused
        self.worker.paused = True
        summary = (f"{self.live_summary(live)}\n" + "\n".join(self.npc_description(npc) for npc in live.npcs)
                   if live is not None
                   else "Position unknown (live map is off for this disc)")
        dialog = InspectDialog(summary, self.note.text(), self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                record = self.inspections.add(
                    map_index=live.map_index if live else -1, map_name=self.map_label(live.map_index) if live else "unknown",
                    position=(live.tamer.x, live.tamer.y, live.tamer.z) if live and live.tamer else None,
                    tile=live.tamer_tile if live else None,
                    trigger=live.trigger_at(live.tamer_tile) if live else None,
                    partner_tile=live.partner_tile if live else None,
                    npcs=[(npc.character_id, self.character_label(npc.character_id), npc.tile,
                           self.npc_description(npc)) for npc in live.npcs] if live else [],
                    grid_differences=self.grid_difference_count(live) if live else None,
                    category=dialog.category.currentText(), note=dialog.note.text(), core=capture["core"],
                    core_version=capture["core_version"], screenshot=capture["screenshot"], state=capture["state"],
                    live_grid=live.grid if live else None)
                self._message(f"Inspection saved: {record.category} at {record.map_name}"
                              + (f" — {record.note}" if record.note else ""))
                self.note.clear()
                self.inspections_changed.emit()
            except (InspectionError, OSError) as exc:
                QMessageBox.warning(self, "Inspection", f"The inspection could not be saved: {exc}")
        self.worker.paused = was_paused
        self.view.setFocus()

    def _toggle_pause(self, paused: bool):
        self.worker.paused = paused
        self.view.text = "Paused" if paused else ""; self.view.update()

    def _reset(self):
        if QMessageBox.question(self, "Reset console", "Reset the console? Unsaved progress is lost.") \
                == QMessageBox.StandardButton.Yes:
            self.worker.reset()
        self.view.setFocus()

    def _toggle_full_screen(self, full: bool):
        self.showFullScreen() if full else self.showNormal()

    def _set_smooth(self, smooth: bool):
        self.view.smooth = smooth; self.view.update(); self.view.setFocus()

    def _show_help(self):
        QMessageBox.information(self, "Controls", KEY_HELP.replace("  •  ", "\n"))

    @Slot()
    def _show_frame(self):
        image = self.worker.take_frame()
        if image is not None:
            self.view.image = image; self.view.update()

    @Slot(bytes)
    def _play_audio(self, data: bytes):
        if self.audio is not None:
            self.audio.write(data)

    @Slot(str)
    def _message(self, text: str):
        self.status.setText(text); self.log.emit(text)

    @Slot(object)
    def _started(self, info):
        self.view.aspect, self.view.text = info["aspect"], ""
        self.audio = AudioOutput(info["sample_rate"], self._message)
        self._message(f"Running {info['core']} {info['version']} at {info['fps']:.3f} fps — F1 shows the controls")

    @Slot(str)
    def _failed(self, message: str):
        self.log.emit(f"Failed: {message}")
        self.view.text = message; self.view.update()
        QMessageBox.critical(self, "The game could not run", message)

    @Slot(object)
    def _slot_saved(self, info):
        self.slots_changed.emit()
        if info.slot != RESUME_SLOT:
            self.note.clear()

    @Slot(int)
    def _slot_loaded(self, _slot):
        self.view.setFocus()

    @Slot(str)
    def _command_failed(self, message: str):
        self._message(message)
        QMessageBox.warning(self, "Quick save", message)
        self.view.setFocus()

    @Slot()
    def _thread_finished(self):
        self._finished = True
        if self.audio is not None:
            self.audio.stop()
        self.slots_changed.emit()

    def stop(self, wait_ms: int = 10000) -> bool:
        """Stop the core and wait for its shutdown saves; True once the worker thread has ended."""
        if not self.thread.isRunning():
            return True
        self.worker.request_stop()
        return self.thread.wait(wait_ms)

    def closeEvent(self, event):
        if not self.stop():
            self._message("The core is still shutting down…")
            event.ignore()
            return
        # Deliver the worker's last queued messages (resume save, memory card) before the window is deleted.
        QCoreApplication.sendPostedEvents(self)
        self.closed.emit()
        event.accept()
