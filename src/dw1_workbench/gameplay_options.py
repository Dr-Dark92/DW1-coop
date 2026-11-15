"""Immutable per-launch options; HUD toggles never change care mechanics."""
from dataclasses import dataclass, fields

from PySide6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QLabel, QVBoxLayout


@dataclass(frozen=True)
class RoamingOptions:
    hp_mp: bool = True
    lifespan: bool = False
    hunger: bool = False
    poop: bool = False
    fatigue: bool = False

    @classmethod
    def load(cls, settings):
        return cls(**{f.name: settings.value(f"play/roaming/{f.name}", f.default, type=bool) for f in fields(cls)})

    def save(self, settings):
        for f in fields(self):
            settings.setValue(f"play/roaming/{f.name}", getattr(self, f.name))


class RoamingDialog(QDialog):
    def __init__(self, options, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Roaming HUD")
        self.setMinimumWidth(380)
        self.setStyleSheet("QDialog {background:#091526;color:#fad990;} QLabel,QCheckBox {color:#eee; font-size:15px;} "
                           "QCheckBox {padding:8px;} QCheckBox::indicator {width:14px;height:14px;border:1px solid #fad990;background:#102238;} "
                           "QCheckBox::indicator:checked {background:#fad990;border:2px solid #284c70;} "
                           "QPushButton {background:#284c70;color:#fad990;padding:8px;border:1px solid #fad990;}")
        layout = QVBoxLayout(self)
        title = QLabel("ROAMING HUD"); title.setStyleSheet("color:#fad990;font-size:20px;")
        layout.addWidget(title)
        note = QLabel("Visibility only — care needs remain active.\nGameplay style and lifespan are set before launch.")
        layout.addWidget(note)
        self.boxes = {}
        for key, label in (("hp_mp", "HP and MP"), ("lifespan", "Remaining lifespan (hours)"),
                           ("hunger", "Hunger / food energy"), ("poop", "Toilet countdown"), ("fatigue", "Fatigue")):
            box = QCheckBox(label); box.setChecked(getattr(options, key))
            self.boxes[key] = box; layout.addWidget(box)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)

    def options(self):
        return RoamingOptions(**{key: box.isChecked() for key, box in self.boxes.items()})
