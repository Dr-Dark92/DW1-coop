"""Workbench "Play" tab: core, BIOS and disc settings, quick-save slots, launch and launch log."""
from __future__ import annotations

import hashlib
import os
import sys
import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, QStandardPaths, Qt, QThread, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QIcon, QPixmap
from PySide6.QtWidgets import (QCheckBox, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QMessageBox, QPlainTextEdit, QProgressDialog, QPushButton, QSplitter, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from .errors import Cancelled
from .inspection import InspectionStore, SpawnLog
from .libretro import LibretroError, core_info
from .maps import trigger_description
from .player import KEY_HELP, PlayConfig, PlayWindow
from .quicksave import QuickSaveStore

LIBRARY_SUFFIX = ".dll" if sys.platform == "win32" else ".dylib" if sys.platform == "darwin" else ".so"
CORE_NAMES = ("mednafen_psx_libretro", "swanstation_libretro", "pcsx_rearmed_libretro")
# North American BIOS files. Beetle PSX looks for scph5501.bin first; the game also booted with SCPH1001.BIN
# on the development machine (2026-11-03).
USA_BIOS = ("scph5501.bin", "scph1001.bin", "scph7003.bin", "scph7001.bin", "scph101.bin")


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
    usable = [name for name in found if name.lower() in USA_BIOS]
    if usable:
        return True, f"Found {', '.join(usable)} (a North American BIOS, for the USA disc)."
    listed = ", ".join(found) if found else "no BIOS files"
    return False, (f"No North American BIOS found ({listed}). The USA disc needs one of "
                   f"{', '.join(USA_BIOS)}; Beetle PSX looks for scph5501.bin first.")


def _table(headers: list[str], stretch: int) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.verticalHeader().setVisible(False)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    table.horizontalHeader().setSectionResizeMode(stretch, QHeaderView.ResizeMode.Stretch)
    table.setIconSize(QPixmap(64, 48).size())
    return table


def _local_time(created: str) -> str:
    return datetime.fromisoformat(created).astimezone().strftime("%Y-%m-%d %H:%M:%S")


class PatchWorker(QObject):
    progress = Signal(int, str)
    succeeded = Signal(str)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, build):
        super().__init__()
        self.build, self.cancel_event = build, threading.Event()

    def request_cancel(self):
        self.cancel_event.set()

    @Slot()
    def run(self):
        try:
            self.succeeded.emit(str(self.build(self.cancel_event.is_set, self.progress.emit)))
        except Cancelled:
            pass
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()


class PlayPanel(QWidget):
    show_map_requested = Signal(int)  # map-table index
    inspections_changed = Signal()

    def __init__(self, meta: dict, source_ok: bool, source_message: str, map_names: dict[int, str] | None = None,
                 decoded_grid=None, character_names: dict[int, str] | None = None,
                 item_names: list[str] | None = None, imports=None, parent=None):
        super().__init__(parent)
        self.meta, self.source_ok = meta, source_ok
        self.map_names, self.decoded_grid = map_names or {}, decoded_grid
        self.character_names = character_names or {}
        self.item_names = item_names or []
        # imports: (count of maps/characters with overrides, build(cancelled, progress) -> patched disc path)
        self.imports = imports or (lambda: 0, None)
        self._patch_thread = self._patch_worker = self._patch_dialog = None
        self._pending_launch = None
        # Live RAM addresses come from the USA executable's decompilation; other releases differ.
        self.live_ram = meta.get("executable") == "SLUS_010.32"
        self.disc = Path(meta.get("source_path", ""))
        self.store = QuickSaveStore(saves_root() / disc_key(meta))
        self.inspections = InspectionStore(self.store.directory / "inspections")
        self.spawns = SpawnLog(self.store.directory / "item_spawns.json")
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
        self.use_imports = QCheckBox()
        self.use_imports.setChecked(self.settings.value("play/use_imports", False, type=bool))
        self.use_imports.toggled.connect(lambda value: self.settings.setValue("play/use_imports", value))
        form.addRow("", self.use_imports)
        self.refresh_imports()
        self.core.editingFinished.connect(self._check); self.bios.editingFinished.connect(self._check)

        self.slots = _table(["Slot", "Saved", "Note", "Core"], 2)
        self.slots.itemSelectionChanged.connect(self._update_buttons)
        self.slots.itemDoubleClicked.connect(lambda *_: self.launch(self._selected_slot()))
        slots_page = QWidget(); slots_layout = QVBoxLayout(slots_page); slots_layout.setContentsMargins(0, 0, 0, 0)
        slots_layout.addWidget(QLabel("Quick saves")); slots_layout.addWidget(self.slots, 1)
        buttons = QHBoxLayout(); slots_layout.addLayout(buttons)
        self.play = QPushButton("Play"); self.play.clicked.connect(lambda: self.launch(None))
        self.play_slot = QPushButton("Play from selected slot")
        self.play_slot.clicked.connect(lambda: self.launch(self._selected_slot()))
        self.delete = QPushButton("Delete slot"); self.delete.clicked.connect(self._delete_slot)
        for button in (self.play, self.play_slot, self.delete):
            buttons.addWidget(button)
        buttons.addStretch()

        self.inspection_table = _table(["Inspected", "Map", "Tile", "Trigger there", "What", "Note", "Grid vs MAP"], 5)
        self.inspection_table.itemSelectionChanged.connect(self._update_buttons)
        self.inspection_table.itemDoubleClicked.connect(lambda *_: self.launch_inspection(self._selected_inspection()))
        inspections_page = QWidget(); inspections_layout = QVBoxLayout(inspections_page)
        inspections_layout.setContentsMargins(0, 0, 0, 0)
        inspections_layout.addWidget(QLabel("Zone inspections (F9 while playing): what you observed, kept apart from "
                                            "the decoded data, with a save state to return to the spot"))
        inspections_layout.addWidget(self.inspection_table, 1)
        inspection_buttons = QHBoxLayout(); inspections_layout.addLayout(inspection_buttons)
        self.play_inspection = QPushButton("Play from inspection")
        self.play_inspection.clicked.connect(lambda: self.launch_inspection(self._selected_inspection()))
        self.show_map = QPushButton("Show map in Maps and Zones")
        self.show_map.clicked.connect(self._show_inspection_map)
        self.delete_inspection = QPushButton("Delete inspection"); self.delete_inspection.clicked.connect(self._delete_inspection)
        for button in (self.play_inspection, self.show_map, self.delete_inspection):
            inspection_buttons.addWidget(button)
        inspection_buttons.addStretch()
        lists = QSplitter(Qt.Orientation.Vertical); lists.addWidget(slots_page); lists.addWidget(inspections_page)
        layout.addWidget(lists, 1)
        controls = QLabel(KEY_HELP); controls.setWordWrap(True); layout.addWidget(controls)
        layout.addWidget(QLabel("Launch log"))
        self.log = QPlainTextEdit(); self.log.setReadOnly(True); self.log.setMaximumHeight(150)
        layout.addWidget(self.log)
        self._check(); self.refresh_slots(); self.refresh_inspections()

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

    def refresh_inspections(self):
        records = list(reversed(self.inspections.list()))
        table = self.inspection_table
        table.setRowCount(len(records))
        for row, record in enumerate(records):
            differences = ("unknown" if record.grid_differences is None else
                           "matches" if record.grid_differences == 0 else f"{record.grid_differences} tiles differ")
            values = (_local_time(record.created), f"{record.map_name} ({record.map_index})",
                      f"{record.tile[0]},{record.tile[1]}" if record.tile else "—",
                      trigger_description(record.trigger) if record.trigger is not None else "—",
                      record.category, record.note, differences)
            for column, value in enumerate(values):
                item = QTableWidgetItem(value); item.setData(Qt.ItemDataRole.UserRole, record.id)
                table.setItem(row, column, item)
            if (image := self.inspections.screenshot_path(record.id)) is not None:
                table.item(row, 0).setIcon(QIcon(QPixmap(str(image))))
        table.resizeRowsToContents()
        self._update_buttons()
        self.inspections_changed.emit()

    def _selected_inspection(self) -> str | None:
        items = self.inspection_table.selectedItems()
        return items[0].data(Qt.ItemDataRole.UserRole) if items else None

    def _show_inspection_map(self):
        record = self.inspections.get(self._selected_inspection() or "")
        if record is not None and record.map_index >= 0:
            self.show_map_requested.emit(record.map_index)

    def _delete_inspection(self):
        record = self.inspections.get(self._selected_inspection() or "")
        if record and QMessageBox.question(self, "Delete inspection",
                                           f"Delete the {record.category.lower()} inspection at {record.map_name}?") \
                == QMessageBox.StandardButton.Yes:
            self.inspections.delete(record.id); self.refresh_inspections()

    def refresh_slots(self):
        slots = self.store.slots()
        self.slots.setRowCount(len(slots))
        for row, info in enumerate(slots):
            saved = _local_time(info.created)
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
        if hasattr(self, "inspection_table"):
            selected = self._selected_inspection() is not None
            self.play_inspection.setEnabled(ready and selected and self.inspections.has_state(self._selected_inspection()))
            self.show_map.setEnabled(selected)
            self.delete_inspection.setEnabled(selected and not running)

    def _delete_slot(self):
        slot = self._selected_slot()
        info = self.store.info(slot) if slot is not None else None
        if info and QMessageBox.question(self, "Delete quick save", f"Delete {info.label}?") == QMessageBox.StandardButton.Yes:
            self.store.delete(slot); self.refresh_slots()

    def launch_inspection(self, inspection_id: str | None):
        if inspection_id is not None:
            self.launch(None, inspection_id)

    def refresh_imports(self):
        count = self.imports[0]()
        self.use_imports.setText(f"Play with imported resources ({count} map/Digimon override{'s' if count != 1 else ''}; a patched "
                                 "copy of the disc is built, the original is untouched)")
        self.use_imports.setEnabled(count > 0)

    def launch(self, slot: int | None, inspection_id: str | None = None, disc: Path | None = None):
        if self.window is not None:
            self.window.raise_(); self.window.activateWindow()
            return
        if self._patch_thread is not None:
            return
        if not self._check() or not self.source_ok:
            QMessageBox.warning(self, "Cannot play yet", "Choose a working core and make sure the disc image is available.")
            return
        if disc is None and self.use_imports.isChecked() and self.imports[0]() > 0:
            self._build_patched(slot, inspection_id)
            return
        config = PlayConfig(Path(self.core.text()), Path(self.bios.text() or "."), disc or self.disc, self.store.directory,
                            launch_slot=slot, launch_inspection=inspection_id,
                            resume_on_close=self.resume.isChecked(), live_ram=self.live_ram)
        start = (f" from slot {slot}" if slot is not None else
                 f" from inspection {inspection_id}" if inspection_id is not None else "")
        self._write_log(f"Launching {config.disc_path.name}{start}"
                        + (" with imported resources (patched copy)" if disc is not None else ""))
        window = PlayWindow(config, self.meta.get("release", "Digimon World"), self.map_names, self.decoded_grid,
                            self.character_names, self.item_names)
        window.log.connect(self._write_log)
        window.slots_changed.connect(self.refresh_slots)
        window.inspections_changed.connect(self.refresh_inspections)
        window.closed.connect(self._window_closed)
        self.window = window
        self._update_buttons()
        window.start()

    def _build_patched(self, slot, inspection_id):
        dialog = QProgressDialog("Building the patched disc", "Cancel", 0, 100, self)
        dialog.setWindowModality(Qt.WindowModality.WindowModal); dialog.setAutoClose(False); dialog.show()
        thread, worker = QThread(self), PatchWorker(self.imports[1])
        worker.moveToThread(thread); thread.started.connect(worker.run)
        # Bound slots, not lambdas: lambdas would run on the worker thread and touch widgets there.
        worker.progress.connect(self._patch_progress)
        dialog.canceled.connect(worker.request_cancel)
        worker.succeeded.connect(self._patched_ready)
        self._patch_request = (slot, inspection_id)
        worker.failed.connect(self._patch_failed)
        worker.finished.connect(thread.quit); worker.finished.connect(dialog.close)
        thread.finished.connect(self._patch_finished)
        self._patch_thread, self._patch_worker, self._patch_dialog = thread, worker, dialog
        self._write_log("Building the patched disc with imported resources")
        thread.start()

    @Slot(int, str)
    def _patch_progress(self, value, text):
        if self._patch_dialog is not None:
            self._patch_dialog.setValue(value); self._patch_dialog.setLabelText(text)

    @Slot(str)
    def _patched_ready(self, path):
        self._write_log(f"Patched disc ready: {path}")
        self._pending_launch = (*self._patch_request, Path(path))

    @Slot(str)
    def _patch_failed(self, message):
        self._write_log(f"Patched disc failed: {message}")
        QMessageBox.critical(self, "Imported resources", message)

    @Slot()
    def _patch_finished(self):
        self._patch_thread = self._patch_worker = self._patch_dialog = None
        pending, self._pending_launch = getattr(self, "_pending_launch", None), None
        if pending is not None:
            self.launch(*pending)

    def wait_for_patch(self, timeout_ms: int = 60000):
        if self._patch_thread is not None:
            self._patch_worker.request_cancel(); self._patch_thread.wait(timeout_ms)

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
        self.wait_for_patch()
        if self.window is not None:
            self.window.close()
