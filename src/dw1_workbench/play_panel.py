"""Workbench "Play" tab: core, BIOS and disc settings, quick-save slots, launch and launch log."""
from __future__ import annotations

import hashlib
import os
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSettings, QStandardPaths, Qt, QUrl, Slot
from PySide6.QtGui import QDesktopServices, QIcon, QPixmap
from PySide6.QtWidgets import (QCheckBox, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QMessageBox, QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from .libretro import LibretroError, core_info
from .player import KEY_HELP, PlayConfig, PlayWindow
from .quicksave import QuickSaveStore

LIBRARY_SUFFIX = ".dll" if sys.platform == "win32" else ".dylib" if sys.platform == "darwin" else ".so"
CORE_NAMES = ("mednafen_psx_libretro", "swanstation_libretro", "pcsx_rearmed_libretro")
# Beetle PSX (mednafen_psx) picks its firmware by disc region; USA discs need scph5501.bin.
BIOS_FOR_REGION = {"USA": "scph5501.bin", "Japan": "scph5500.bin", "Europe": "scph5502.bin"}


def _core_folders() -> list[Path]:
    home = Path.home()
    if sys.platform == "win32":
        appdata = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
        return [appdata / "RetroArch" / "cores", Path("C:/RetroArch-Win64/cores")]
    return [Path("/usr/lib/libretro"), Path("/usr/lib64/libretro"), Path("/usr/local/lib/libretro"),
            home / ".config" / "retroarch" / "cores"]


def find_installed_core() -> Path | None:
    for folder in _core_folders():
        for name in CORE_NAMES:
            if (candidate := folder / f"{name}{LIBRARY_SUFFIX}").is_file():
                return candidate
    return None


def saves_root() -> Path:
    return Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericDataLocation)) / "DW1-coop" / "play"


def disc_key(meta: dict) -> str:
    """Saves are kept per disc image; the catalogue's SHA-256 identifies it independently of its path."""
    digest = meta.get("source_sha256") or hashlib.sha256(meta.get("source_path", "").encode()).hexdigest()
    return digest[:16]


def bios_report(folder: Path | None) -> tuple[bool, str]:
    if folder is None or not folder.is_dir():
        return False, "Choose the folder that holds your PlayStation BIOS file."
    found = sorted(path.name for path in folder.iterdir() if path.is_file() and path.suffix.lower() == ".bin"
                   and path.name.lower().startswith(("scph", "ps1_rom", "psxonpsp")))
    wanted = BIOS_FOR_REGION["USA"]
    if wanted in (name.lower() for name in found):
        return True, f"Found {wanted} (needed for the USA disc)."
    listed = ", ".join(found) if found else "no BIOS files"
    return False, f"{wanted} is missing ({listed} found). Beetle PSX needs it for the USA disc; other cores may differ."


class PlayPanel(QWidget):
    def __init__(self, meta: dict, source_ok: bool, source_message: str, parent=None):
        super().__init__(parent)
        self.meta, self.source_ok = meta, source_ok
        self.disc = Path(meta.get("source_path", ""))
        self.store = QuickSaveStore(saves_root() / disc_key(meta))
        self.settings = QSettings("DW1-coop", "workbench")
        self.window: PlayWindow | None = None

        layout = QVBoxLayout(self)
        intro = QLabel("Play your own disc through a libretro PlayStation core loaded by the workbench. The disc is "
                       "only read; memory card and quick saves are kept in the saves folder below.")
        intro.setWordWrap(True); layout.addWidget(intro)
        form = QFormLayout(); layout.addLayout(form)
        self.core = QLineEdit(self.settings.value("play/core_path", "") or str(find_installed_core() or ""))
        self.core_status = QLabel(); self.core_status.setWordWrap(True)
        form.addRow("Core", self._with_browse(self.core, self._browse_core)); form.addRow("", self.core_status)
        self.bios = QLineEdit(self.settings.value("play/bios_dir", ""))
        self.bios_status = QLabel(); self.bios_status.setWordWrap(True)
        form.addRow("BIOS folder", self._with_browse(self.bios, self._browse_bios)); form.addRow("", self.bios_status)
        disc = QLabel(f"{self.disc}\n{source_message}"); disc.setWordWrap(True)
        disc.setStyleSheet("" if source_ok else "color: #a33")
        form.addRow("Disc", disc)
        saves = QHBoxLayout(); saves.addWidget(QLabel(str(self.store.directory)), 1)
        open_saves = QPushButton("Open folder"); open_saves.clicked.connect(self._open_saves); saves.addWidget(open_saves)
        form.addRow("Saves", saves)
        self.resume = QCheckBox("Keep a Resume slot when the game is closed")
        self.resume.setChecked(self.settings.value("play/resume", True, type=bool))
        self.resume.toggled.connect(lambda value: self.settings.setValue("play/resume", value))
        form.addRow("", self.resume)
        self.core.editingFinished.connect(self._check); self.bios.editingFinished.connect(self._check)

        self.slots = QTableWidget(0, 4)
        self.slots.setHorizontalHeaderLabels(["Slot", "Saved", "Note", "Core"])
        self.slots.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.slots.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.slots.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.slots.verticalHeader().setVisible(False)
        self.slots.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.slots.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.slots.setIconSize(QPixmap(64, 48).size())
        self.slots.itemSelectionChanged.connect(self._update_buttons)
        self.slots.itemDoubleClicked.connect(lambda *_: self.launch(self._selected_slot()))
        layout.addWidget(QLabel("Quick saves")); layout.addWidget(self.slots, 1)
        buttons = QHBoxLayout(); layout.addLayout(buttons)
        self.play = QPushButton("Play"); self.play.clicked.connect(lambda: self.launch(None))
        self.play_slot = QPushButton("Play from selected slot")
        self.play_slot.clicked.connect(lambda: self.launch(self._selected_slot()))
        self.delete = QPushButton("Delete slot"); self.delete.clicked.connect(self._delete_slot)
        for button in (self.play, self.play_slot, self.delete):
            buttons.addWidget(button)
        buttons.addStretch()
        controls = QLabel(KEY_HELP); controls.setWordWrap(True); layout.addWidget(controls)
        layout.addWidget(QLabel("Launch log"))
        self.log = QPlainTextEdit(); self.log.setReadOnly(True); self.log.setMaximumHeight(150)
        layout.addWidget(self.log)
        self._check(); self.refresh_slots()

    @staticmethod
    def _with_browse(edit: QLineEdit, handler):
        row = QWidget(); box = QHBoxLayout(row); box.setContentsMargins(0, 0, 0, 0)
        browse = QPushButton("Browse…"); browse.clicked.connect(handler)
        box.addWidget(edit, 1); box.addWidget(browse)
        return row

    def _browse_core(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose a libretro PlayStation core", self.core.text(),
                                              f"libretro cores (*{LIBRARY_SUFFIX});;All files (*)")
        if path:
            self.core.setText(path); self._check()

    def _browse_bios(self):
        path = QFileDialog.getExistingDirectory(self, "Choose your BIOS folder", self.bios.text())
        if path:
            self.bios.setText(path); self._check()

    def _open_saves(self):
        self.store.directory.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.store.directory)))

    def _check(self) -> bool:
        self.settings.setValue("play/core_path", self.core.text()); self.settings.setValue("play/bios_dir", self.bios.text())
        core_ok = False
        path = Path(self.core.text())
        if not self.core.text():
            text = (f"Choose a libretro PlayStation core (*{LIBRARY_SUFFIX}). Beetle PSX is recommended: on Arch, "
                    "the libretro-beetle-psx package; on Windows, mednafen_psx_libretro.dll from libretro.")
        elif not path.is_file():
            text = "That core file does not exist."
        else:
            try:
                info = core_info(path)
                core_ok, text = True, f"{info.name} {info.version}"
            except LibretroError as exc:
                text = str(exc)
        self.core_status.setText(text); self.core_status.setStyleSheet("" if core_ok else "color: #a33")
        bios_ok, bios_text = bios_report(Path(self.bios.text()) if self.bios.text() else None)
        self.bios_status.setText(bios_text); self.bios_status.setStyleSheet("" if bios_ok else "color: #a60")
        self._core_ok = core_ok
        self._update_buttons()
        return core_ok

    def refresh_slots(self):
        slots = self.store.slots()
        self.slots.setRowCount(len(slots))
        for row, info in enumerate(slots):
            saved = datetime.fromisoformat(info.created).astimezone().strftime("%Y-%m-%d %H:%M:%S")
            for column, value in enumerate((info.label, saved, info.note, f"{info.core} {info.core_version}")):
                item = QTableWidgetItem(value); item.setData(Qt.ItemDataRole.UserRole, info.slot)
                self.slots.setItem(row, column, item)
            if (image := self.store.screenshot_path(info.slot)) is not None:
                self.slots.item(row, 0).setIcon(QIcon(QPixmap(str(image))))
        self.slots.resizeRowsToContents()
        self._update_buttons()

    def _selected_slot(self) -> int | None:
        items = self.slots.selectedItems()
        return items[0].data(Qt.ItemDataRole.UserRole) if items else None

    def _update_buttons(self):
        running = self.window is not None
        ready = getattr(self, "_core_ok", False) and self.source_ok and not running
        self.play.setEnabled(ready)
        self.play_slot.setEnabled(ready and self._selected_slot() is not None)
        self.delete.setEnabled(not running and self._selected_slot() is not None)

    def _delete_slot(self):
        slot = self._selected_slot()
        info = self.store.info(slot) if slot is not None else None
        if info and QMessageBox.question(self, "Delete quick save", f"Delete {info.label}?") == QMessageBox.StandardButton.Yes:
            self.store.delete(slot); self.refresh_slots()

    def launch(self, slot: int | None):
        if self.window is not None:
            self.window.raise_(); self.window.activateWindow()
            return
        if not self._check() or not self.source_ok:
            QMessageBox.warning(self, "Cannot play yet", "Choose a working core and make sure the disc image is available.")
            return
        config = PlayConfig(Path(self.core.text()), Path(self.bios.text() or "."), self.disc, self.store.directory,
                            launch_slot=slot, resume_on_close=self.resume.isChecked())
        self._write_log(f"Launching {config.disc_path.name}" + (f" from slot {slot}" if slot is not None else ""))
        window = PlayWindow(config, self.meta.get("release", "Digimon World"))
        window.log.connect(self._write_log)
        window.slots_changed.connect(self.refresh_slots)
        window.closed.connect(self._window_closed)
        self.window = window
        self._update_buttons()
        window.start()

    @Slot()
    def _window_closed(self):
        self.window = None
        self.refresh_slots()

    @Slot(str)
    def _write_log(self, text: str):
        line = f"{datetime.now().strftime('%H:%M:%S')} {text}"
        self.log.appendPlainText(line)
        try:
            self.store.directory.mkdir(parents=True, exist_ok=True)
            with open(self.store.directory / "launch.log", "a", encoding="utf-8") as stream:
                stream.write(line + "\n")
        except OSError:
            pass

    def close_player(self):
        """Stop a running game (writing its resume slot and memory card) before the workbench closes."""
        if self.window is not None:
            self.window.close()
