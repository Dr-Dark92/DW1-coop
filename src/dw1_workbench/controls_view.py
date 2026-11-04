"""The Controls page: each player's controller, and which input is which button."""
from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (QComboBox, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget)

from .controls import (BUTTON_LABELS, DEFAULT_MAPPING, PLAYERS, describe_inputs, load_controls, save_controls, system_pads)

POLL_MS = 30
CAPTURE_MS = 6000


def key_name(key) -> str:
    return QKeySequence(key).toString()


class PlayerBox(QGroupBox):
    def __init__(self, panel, player: int, keys: dict):
        super().__init__(f"Player {player + 1}")
        self.panel, self.player = panel, player
        self.controls = panel.players[player]
        grid = QGridLayout(self)
        grid.addWidget(QLabel("Controller"), 0, 0)
        self.pad = QComboBox(); self.pad.setToolTip("Which connected controller this player uses")
        self.pad.activated.connect(self._pad_chosen)
        grid.addWidget(self.pad, 0, 1, 1, 3)
        self.live = QLabel("Pressed: —"); grid.addWidget(self.live, 1, 0, 1, 4)
        for column, title in enumerate(("Button", "Keyboard", "Controller", "")):
            heading = QLabel(title); heading.setStyleSheet("font-weight: 600"); grid.addWidget(heading, 2, column)
        self.values, self.setters = {}, {}
        by_button = {}
        for key, button in keys.items():
            by_button.setdefault(button, key_name(key))
        for row, (button, label) in enumerate(BUTTON_LABELS.items(), 3):
            grid.addWidget(QLabel(label), row, 0)
            grid.addWidget(QLabel(by_button.get(button, "—")), row, 1)
            self.values[button] = QLabel(); grid.addWidget(self.values[button], row, 2)
            setter = QPushButton("Set"); setter.setToolTip(f"Then press the input for {label} on the controller")
            setter.clicked.connect(lambda _=False, chosen=button: panel.capture(self, chosen))
            self.setters[button] = setter; grid.addWidget(setter, row, 3)
        reset = QPushButton("Reset to default buttons"); reset.clicked.connect(self._reset)
        grid.addWidget(reset, len(BUTTON_LABELS) + 3, 0, 1, 4)
        self.refresh()

    def refresh(self):
        for button, label in self.values.items():
            label.setText(describe_inputs(self.controls.mapping.get(button, [])))

    def refresh_pads(self, names: list[str]):
        wanted = [("No controller", None), *[(name, index) for index, name in enumerate(names)]]
        if self.controls.pad is not None and self.controls.pad >= len(names):
            wanted.append((f"Controller {self.controls.pad + 1} (not connected)", self.controls.pad))
        if [(self.pad.itemText(i), self.pad.itemData(i)) for i in range(self.pad.count())] != wanted:
            self.pad.clear()
            for text, data in wanted:
                self.pad.addItem(text, data)
        self.pad.setCurrentIndex(max(self.pad.findData(self.controls.pad), 0))

    def _pad_chosen(self, _index):
        self.controls.pad = self.pad.currentData()
        self.panel.save()

    def _reset(self):
        self.controls.mapping = {button: list(names) for button, names in DEFAULT_MAPPING.items()}
        self.refresh(); self.panel.save()


class ControlsPanel(QWidget):
    def __init__(self, settings, key_sets: list[dict], help_text: str, pads=None, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.pads = pads if pads is not None else system_pads()
        self.players = load_controls(settings)
        layout = QVBoxLayout(self)
        note = QLabel("Choose each player's controller, then Set a button and press what you want for it. "
                      "A controller works alongside the keyboard. Changes apply the next time a game starts.")
        note.setWordWrap(True); layout.addWidget(note)
        self.status = QLabel(); layout.addWidget(self.status)
        row = QHBoxLayout(); layout.addLayout(row)
        self.boxes = [PlayerBox(self, player, key_sets[player]) for player in range(PLAYERS)]
        for box in self.boxes:
            row.addWidget(box)
        hints = QLabel(help_text); hints.setWordWrap(True); hints.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(hints); layout.addStretch(1)
        self._capturing = None  # (box, button, inputs held when it began, time left)
        self._timer = QTimer(self); self._timer.timeout.connect(self.poll)

    def showEvent(self, event):
        super().showEvent(event); self.poll(); self._timer.start(POLL_MS)

    def hideEvent(self, event):
        super().hideEvent(event); self._timer.stop(); self._cancel_capture()

    def save(self):
        save_controls(self.settings, self.players)

    def capture(self, box: PlayerBox, button: str):
        self._cancel_capture()
        if box.controls.pad is None:
            self.status.setText(f"Choose a controller for Player {box.player + 1} first.")
            return
        self._capturing = [box, button, self.pads.read(box.controls.pad), CAPTURE_MS]
        box.values[button].setText("Press an input…")
        self.status.setText(f"Player {box.player + 1}: press the input for {BUTTON_LABELS[button]}.")

    def _cancel_capture(self):
        if self._capturing is not None:
            box = self._capturing[0]
            self._capturing = None
            box.refresh()

    def poll(self):
        names = self.pads.names()
        for box in self.boxes:
            box.refresh_pads(names)
            inputs = self.pads.read(box.controls.pad) if box.controls.pad is not None else set()
            held = [BUTTON_LABELS[button] for button in BUTTON_LABELS if button in box.controls.held(inputs)]
            box.live.setText("Pressed: " + (", ".join(held) or "—"))
        if self._capturing is None:
            if not names:
                self.status.setText("No controller found. Connect one and it appears here.")
            elif self.status.text().startswith("No controller"):
                self.status.setText(f"{len(names)} controller{'s' if len(names) != 1 else ''} found.")
            return
        box, button, ignored, left = self._capturing
        inputs = self.pads.read(box.controls.pad)
        fresh = inputs - ignored
        self._capturing[2] = ignored & inputs  # a held input counts once it is let go and pressed again
        if fresh:
            box.controls.mapping[button] = [sorted(fresh)[0]]
            self._capturing = None
            box.refresh(); self.save()
            self.status.setText(f"Player {box.player + 1}: {BUTTON_LABELS[button]} is {describe_inputs(box.controls.mapping[button])}.")
            return
        self._capturing[3] = left - POLL_MS
        if self._capturing[3] <= 0:
            self._cancel_capture()
            self.status.setText("Nothing pressed; that button is unchanged.")
