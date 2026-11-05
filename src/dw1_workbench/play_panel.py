"""Workbench "Play" tab: core, BIOS and disc settings, quick-save slots, launch and launch log."""
from __future__ import annotations

import hashlib
import os
import sys
import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QLockFile, QObject, QSize, Qt, QThread, QUrl, Signal, Slot
from PIL import Image
from PySide6.QtGui import QDesktopServices, QIcon, QImage, QPixmap
from PySide6.QtWidgets import (QDialog, QDoubleSpinBox, QGroupBox, QScrollArea, QTabWidget, QCheckBox, QSpinBox, QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QMessageBox, QPlainTextEdit, QProgressDialog, QPushButton, QSplitter, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from .errors import Cancelled, DiscFormatError
from .graphics import DEFAULT_RESOLUTION, NATIVE_RESOLUTION, RESOLUTIONS, core_options
from .inspection import InspectionStore, SpawnLog
from .libretro import LibretroError, core_info
from .maps import trigger_description
from .avatars import render_character
from .controls_view import ControlsPanel
from .shared_data import app_settings, saves_root
from .tamer_skin import load_characters
from .tim import decode_tim
from .title_screens import SHOWN_ROWS, TITLE_SCREENS, TitleStore, disc_titles, encode_title, shown_part
from .graphics import SCREENS
from .player import DEFAULT_KEYS, KEY_HELP, SPLIT_KEYS, PlayConfig, PlayWindow
from .quicksave import RESUME_SLOT, SLOT_COUNT, QuickSaveStore

ASK_CHARACTER, DEFAULT_CHARACTER = "ask", "default"


def saved_resolution(settings) -> str:
    """The menu's choice. A checkbox (`play/fixed_x2`) replaced it for a while: honour what was ticked, once."""
    resolution = settings.value("play/resolution", DEFAULT_RESOLUTION)
    if settings.contains("play/fixed_x2"):
        if not settings.value("play/fixed_x2", type=bool):
            resolution = NATIVE_RESOLUTION
        elif resolution == NATIVE_RESOLUTION:
            resolution = DEFAULT_RESOLUTION
        settings.setValue("play/resolution", resolution)
        settings.remove("play/fixed_x2")
    if resolution not in RESOLUTIONS:
        # A core's own value ("2x") or anything else the menu doesn't list would otherwise leave it on its first item, Native.
        resolution = next((label for label, value in RESOLUTIONS.items() if value == resolution), DEFAULT_RESOLUTION)
    return resolution
CHARACTER_PICTURE = 240
TITLE_PREVIEW = 160, 120
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


FRAMERATE_OWN, FRAMERATE_MOST = 19.0, 360.0  # the box's lowest value stands for the game's own rate


class CharacterDialog(QDialog):
    """The choice at game start: each character, large, one click."""

    def __init__(self, characters, disc: Path, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Choose your character")
        self._choice = None
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Who do you want to play as?"))
        row = QHBoxLayout(); layout.addLayout(row)
        self._add(row, "Default character", DEFAULT_CHARACTER, self._default_picture(disc))
        for character in characters:
            self._add(row, character.name, character.name, QPixmap(str(character.preview)))

    @staticmethod
    def _default_picture(disc: Path) -> QPixmap:
        try:
            image = render_character(disc, 0, CHARACTER_PICTURE)
        except Exception:
            return QPixmap()  # the button still names them
        return QPixmap.fromImage(image) if image is not None else QPixmap()

    def _add(self, row, text, value, picture):
        button = QPushButton(text)
        if not picture.isNull():
            button.setIcon(QIcon(picture)); button.setIconSize(QSize(CHARACTER_PICTURE, CHARACTER_PICTURE))
        button.setStyleSheet("text-align: center; padding: 8px")
        button.clicked.connect(lambda _=False, chosen=value: self._pick(chosen))
        row.addWidget(button)

    def _pick(self, value):
        self._choice = value
        self.accept()

    def choice(self):
        """A character's name, DEFAULT_CHARACTER, or None when closed."""
        self.exec()
        return self._choice


class PlayPanel(QWidget):
    show_map_requested = Signal(int)  # map-table index
    inspections_changed = Signal()
    player_closed = Signal()  # the game window closed, or a launch ended without one

    def __init__(self, meta: dict, source_ok: bool, source_message: str, map_names: dict[int, str] | None = None,
                 decoded_grid=None, character_names: dict[int, str] | None = None,
                 item_names: list[str] | None = None, imports=None, hd_pictures: Path | None = None, parent=None):
        super().__init__(parent)
        self.meta, self.source_ok = meta, source_ok
        self.map_names, self.decoded_grid = map_names or {}, decoded_grid
        self.character_names = character_names or {}
        self.item_names = item_names or []
        # imports: (count of maps/characters with overrides, build(cancelled, progress) -> patched disc path,
        # and optionally forms() -> imported Digimon the patched disc adds to the bank, see EvolutionController)
        self.imports = imports or (lambda: 0, None)
        self.hd_pictures = hd_pictures
        self._patch_thread = self._patch_worker = self._patch_dialog = None
        self._pending_launch = None
        self._player_lock = None
        # Live RAM addresses come from the USA executable's decompilation; other releases differ.
        self.live_ram = meta.get("executable") == "SLUS_010.32"
        self.disc = Path(meta.get("source_path", ""))
        self.store = QuickSaveStore(saves_root() / disc_key(meta))
        self.inspections = InspectionStore(self.store.directory / "inspections")
        self.spawns = SpawnLog(self.store.directory / "item_spawns.json")
        self.settings = app_settings()
        self.window: PlayWindow | None = None

        layout = QVBoxLayout(self)
        self.pages = QTabWidget(); layout.addWidget(self.pages, 1)
        settings_page = QWidget(); settings_layout = QVBoxLayout(settings_page)
        intro = QLabel("Play your own disc through a libretro PlayStation core loaded by the workbench. The disc is "
                       "only read; memory card and quick saves are kept in the saves folder below.")
        intro.setWordWrap(True); settings_layout.addWidget(intro)

        def group(title):
            box = QGroupBox(title); box_form = QFormLayout(box); settings_layout.addWidget(box)
            return box_form

        form = group("Game files")
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
        self.resume.setToolTip("Off: closing the game keeps only what you saved, with Save or on the game's memory card.")
        self.resume.setChecked(self.settings.value("play/resume", False, type=bool))
        self.resume.toggled.connect(lambda value: self.settings.setValue("play/resume", value))
        form.addRow("", self.resume)
        self.use_imports = QCheckBox()
        self.use_imports.setChecked(self.settings.value("play/use_imports", False, type=bool))
        self.use_imports.toggled.connect(lambda value: self.settings.setValue("play/use_imports", value))
        form.addRow("", self.use_imports)
        form = group("Gameplay")
        self.game_rules = QCheckBox("Game rules (P6): partner levels and EXP from battles, no aging or death")
        self.game_rules.setToolTip(
            "Applied to the running game's memory (USA release only). Your partner starts at level 20 with at least "
            "the stats of its level: +100 max HP and MP and +10 Offense, Defense, Speed and Brains per level. "
            "Won battles give EXP: Rookie 10, Champion 30, Ultimate 90 per enemy, ×1.5 for 2 enemies, ×2 for 3, "
            "×3 for 4. EXP to the next level: (L + 1)(L + 20) / 2. Level and EXP are kept with the player's saves; "
            "lifetime and lives are held full, so the partner does not age or die.")
        self.game_rules.setEnabled(self.live_ram)
        self.game_rules.setChecked(self.live_ram and self.settings.value("play/game_rules", False, type=bool))
        self.game_rules.toggled.connect(lambda value: self.settings.setValue("play/game_rules", value))
        form.addRow("", self.game_rules)
        coop_row = QHBoxLayout()
        self.coop = QCheckBox("Co-op battle test: player 2's Digimon joins your fights")
        self.coop.setToolTip("A prototype in one game window. Player 2 has your battle controls on J and L (commands), K "
                             "(confirm) and U (finisher); the AI picks the skills. They gain the same EXP and Bits. USA only.")
        self.coop.setEnabled(self.live_ram)
        self.coop.setChecked(self.live_ram and self.settings.value("play/coop", False, type=bool))
        self.coop.toggled.connect(lambda value: self.settings.setValue("play/coop", value))
        self.coop_species = QComboBox()
        for character, name in sorted(self.character_names.items()):
            if 0 < character < 66 and name:
                self.coop_species.addItem(f"{name}", character)
        saved = self.coop_species.findData(self.settings.value("play/coop_species", 5, type=int))
        if saved >= 0:
            self.coop_species.setCurrentIndex(saved)
        self.coop_species.currentIndexChanged.connect(lambda _: self.settings.setValue("play/coop_species", self.coop_species.currentData()))
        self.coop_level = QSpinBox(); self.coop_level.setRange(0, 99); self.coop_level.setPrefix("Starts at Lv ")
        self.coop_level.setValue(self.settings.value("play/coop_level", 20, type=int))
        self.coop_level.valueChanged.connect(lambda value: self.settings.setValue("play/coop_level", value))
        for widget in (self.coop, self.coop_species, self.coop_level):
            coop_row.addWidget(widget)
        coop_row.addStretch()
        form.addRow("Co-op", coop_row)
        form = group("Graphics")
        picture = QHBoxLayout()
        self.resolution = QComboBox(); self.resolution.addItems(RESOLUTIONS)
        self.resolution.setCurrentText(saved_resolution(self.settings))
        self.resolution.setToolTip("Draws the 3D characters, HUD and menus at a higher resolution; applies next launch. "
                                   "Keeps the original camera view and HUD proportions. Backgrounds are pre-rendered "
                                   "pictures and keep their original detail. 4x can drop frames.")
        self.resolution.currentTextChanged.connect(lambda value: self.settings.setValue("play/resolution", value))
        self.double_frame = QCheckBox("×2 camera frame (experimental)")
        self.double_frame.setToolTip("Shows twice the world across and down while walking around, with smaller characters; "
                                     "the resolution menu sets how sharp it is. Battles, map changes and maps too small "
                                     "to fill the frame use the normal camera. Needs the 8 MB "
                                     "core. Applies next launch. Save often: this is still being tested.")
        self.double_frame.setChecked(self.settings.value("play/double_frame", False, type=bool))
        self.double_frame.toggled.connect(lambda value: self.settings.setValue("play/double_frame", value))
        self.smooth_colours = QCheckBox("Smooth colours (no dithering)")
        self.smooth_colours.setToolTip("Turns off the PlayStation's dithering, the fine grain pattern on shading.")
        self.smooth_colours.setChecked(self.settings.value("play/smooth_colours", True, type=bool))
        self.smooth_colours.toggled.connect(lambda value: self.settings.setValue("play/smooth_colours", value))
        self.steady_3d = QCheckBox("Steady 3D (PGXP)")
        self.steady_3d.setToolTip("Less wobbly polygons and warped textures on 3D characters. Slightly slower.")
        self.steady_3d.setChecked(self.settings.value("play/steady_3d", False, type=bool))
        self.steady_3d.toggled.connect(lambda value: self.settings.setValue("play/steady_3d", value))
        self.framerate = QDoubleSpinBox()
        self.framerate.setRange(FRAMERATE_OWN, FRAMERATE_MOST); self.framerate.setDecimals(2); self.framerate.setSuffix(" fps")
        self.framerate.setSpecialValueText("Game's own (about 59.83)")
        self.framerate.setKeyboardTracking(False)
        self.framerate.setValue(float(self.settings.value("play/framerate", FRAMERATE_OWN)))
        self.framerate.setToolTip("Type the frames per second to run at. The game's speed is tied to this, so it runs faster "
                                  "above about 59.83 and slower below it, with the sound's pitch following. Takes effect the "
                                  "next time a game starts.")
        self.framerate.valueChanged.connect(lambda value: self.settings.setValue("play/framerate", value))
        form.addRow("Frame rate", self.framerate)
        for widget in (self.resolution, self.double_frame, self.smooth_colours, self.steady_3d):
            picture.addWidget(widget)
        picture.addStretch(1)
        form.addRow("Picture", picture)
        self._add_game_screen(group("Game screen"))
        self._add_quick_saves(group("Quick saves"))
        self._add_during_game(group("During a game"))
        form = group("Character")
        self.character = QComboBox()
        self.character.setToolTip("Who you play as. They share the game's animations. Ask shows a choice each time a game starts.")
        form.addRow("Play as", self.character)
        form = group("Imported Digimon")
        self.offer_forms = QCheckBox("Offer imported Digimon in the Digi-Bank's egg list (WarGreymon)")
        self.offer_forms.setToolTip("Needs imported resources on. When off they aren't offered as new eggs; one you "
                                    "already have in the bank or as your partner keeps working.")
        self.offer_forms.setChecked(self.settings.value("play/offer_forms", False, type=bool))
        self.offer_forms.toggled.connect(lambda value: self.settings.setValue("play/offer_forms", value))
        form.addRow("", self.offer_forms)
        self._add_title_screens(group("Title screens"))
        settings_layout.addStretch(1)
        self.characters = load_characters(self.hd_pictures.parent / "characters" if self.hd_pictures else None)
        self.character.addItem("Ask when a game starts", ASK_CHARACTER)
        self.character.addItem("Default character", DEFAULT_CHARACTER)
        for character in self.characters:
            self.character.addItem(character.name, character.name)
        self.character.setCurrentIndex(max(self.character.findData(self.settings.value("play/character", ASK_CHARACTER)), 0))
        self.character.currentIndexChanged.connect(lambda _: self.settings.setValue("play/character", self.character.currentData()))
        self.character.setEnabled(bool(self.characters))
        self._chosen_character = None
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
        self.pages.addTab(lists, "Play")
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(settings_page)
        self.pages.addTab(scroll, "Settings")
        self.controls = ControlsPanel(self.settings, [DEFAULT_KEYS, SPLIT_KEYS], KEY_HELP)
        controls_scroll = QScrollArea(); controls_scroll.setWidgetResizable(True); controls_scroll.setWidget(self.controls)
        self.pages.addTab(controls_scroll, "Controls")
        self.log = QPlainTextEdit(); self.log.setReadOnly(True)
        self.pages.addTab(self.log, "Log")
        self._check(); self.refresh_slots(); self.refresh_inspections()
        if not self._core_ok or not bios_report(Path(self.bios.text()) if self.bios.text() else None)[0]:
            self.pages.setCurrentIndex(1)  # not ready to play yet: what's missing is on Settings

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

    def _add_game_screen(self, form):
        """How the game window shows the game. Changes reach an open game straight away."""
        def check(text, tip, key, setter):
            box = QCheckBox(text); box.setToolTip(tip)
            box.setChecked(self.settings.value(key, True, type=bool))
            box.toggled.connect(lambda value: self._game_setting(key, value, setter))
            form.addRow("", box)
            return box
        self.smooth = check("Smooth", "Blend pixels when the picture is enlarged (softer, less blocky)", "play/smooth", "_set_smooth")
        self.hd_backgrounds = check("HD backgrounds", "Show your full-size zone and title pictures instead of the game's 8-bit ones",
                                    "play/hd_backgrounds", "_set_hd")
        self.hud = check("Map and HP/MP", "Show the map and your partner's HP and MP on the game screen, with the game's own status bars",
                         "play/hud", "_set_hud")
        self.screen = QComboBox(); self.screen.addItems(SCREENS)
        self.screen.setToolTip("4:3: explicit 4:3 framing. 16:9 fill: a 4:3 game with blurred side panels (no extra scenery). "
                               "16:9 stretch: stretches the entire picture. Core aspect: use the emulator's reported "
                               "pixel-aspect correction, which may be narrower than 4:3.")
        self.screen.setCurrentText(self.settings.value("play/screen", SCREENS[0]))
        self.screen.currentTextChanged.connect(lambda value: self._game_setting("play/screen", value, "_set_screen"))
        form.addRow("Screen", self.screen)
        self.game_view_size = QPushButton("640 × 480 game view")
        self.game_view_size.setToolTip("Resize the running game's display area and select 4:3. Does not zoom the camera "
                                      "or change game positions. Stop split screen first.")
        self.game_view_size.clicked.connect(self._size_game_view)
        form.addRow("View size", self.game_view_size)

    def _size_game_view(self):
        if self.window is not None and self.window.set_game_view_size():
            self.screen.setCurrentText("4:3")

    def _game_setting(self, key, value, setter):
        self.settings.setValue(key, value)
        if self.window is not None:
            getattr(self.window, setter)(value)

    def _picture_options(self):
        return core_options(self.resolution.currentText(), self.smooth_colours.isChecked(), self.steady_3d.isChecked())

    def _add_quick_saves(self, form):
        """Which slot the game window's Save and Load use. F6 and F7 in the game change it too."""
        self.quick_slot = QComboBox(); self.quick_slot.addItems([f"Slot {index}" for index in range(1, SLOT_COUNT + 1)])
        self.quick_note = QLineEdit(); self.quick_note.setPlaceholderText("Note for the next quick save, e.g. the zone")
        self._sync_quick_save()
        self.quick_slot.currentIndexChanged.connect(self._quick_slot_chosen)
        self.quick_note.textChanged.connect(lambda text: self.settings.setValue("play/save_note", text))
        form.addRow("Save and Load use", self.quick_slot)
        form.addRow("Note", self.quick_note)

    def _quick_slot_chosen(self, index):
        self.settings.setValue("play/slot", index + 1)
        if self.window is not None:
            self.window.set_slot(index + 1)

    def _sync_quick_save(self):
        self.settings.sync()
        for widget, set_value, value in (
                (self.quick_slot, self.quick_slot.setCurrentIndex, min(max(self.settings.value("play/slot", 1, type=int), 1), SLOT_COUNT) - 1),
                (self.quick_note, self.quick_note.setText, self.settings.value("play/save_note", "") or "")):
            widget.blockSignals(True); set_value(value); widget.blockSignals(False)

    def _add_during_game(self, form):
        """For the game that is open. In the game's window these are P, Ctrl+R and F4."""
        row = QHBoxLayout()
        self.game_pause = QPushButton("Pause"); self.game_pause.setCheckable(True); self.game_pause.setToolTip("P in the game window")
        self.game_reset = QPushButton("Reset"); self.game_reset.setToolTip("Ctrl+R in the game window")
        self.game_dex = QPushButton("Digi-Dex"); self.game_dex.setToolTip("F4 in the game window")
        self.game_pause.clicked.connect(lambda checked: self._game_action(lambda window: window.pause.setChecked(checked) or window._toggle_pause(checked)))
        self.game_reset.clicked.connect(lambda: self._game_action(lambda window: window._reset()))
        self.game_dex.clicked.connect(lambda: self._game_action(lambda window: window._show_digidex()))
        for button in (self.game_pause, self.game_reset, self.game_dex):
            row.addWidget(button)
        row.addStretch(1)
        form.addRow(row)
        self._update_game_controls()

    def _game_action(self, call):
        if self.window is not None:
            call(self.window)

    def _update_game_controls(self):
        window = self.window
        self.game_pause.setEnabled(window is not None)
        self.game_reset.setEnabled(window is not None)
        self.game_view_size.setEnabled(window is not None)
        self.game_dex.setEnabled(window is not None and window.dex_action.isEnabled())
        if window is None:
            self.game_pause.setChecked(False)

    def _add_title_screens(self, form):
        """Each title picture beside its buttons. Needs the project's overrides folder and the disc to read the originals from."""
        self.title_store = TitleStore(self.hd_pictures.parent) if self.hd_pictures else None
        self._title_originals = None
        self._title_previews = {}
        form.addRow(QLabel("Your own pictures, shown when the game starts from the disc (Play), not when it resumes a save. "
                           "Any size works: with HD backgrounds on you see your picture at full size. On the Press Start "
                           "screen, the game draws its text over the lower middle."))
        for screen in TITLE_SCREENS:
            row = QHBoxLayout()
            preview = QLabel(); preview.setFixedSize(*TITLE_PREVIEW); row.addWidget(preview)
            self._title_previews[screen.name] = preview
            choose = QPushButton("Choose picture…"); choose.clicked.connect(lambda _=False, s=screen: self._choose_title(s))
            save = QPushButton("Save as PNG…"); save.clicked.connect(lambda _=False, s=screen: self._save_title(s))
            reset = QPushButton("Use the disc's"); reset.clicked.connect(lambda _=False, s=screen: self._reset_title(s))
            actions = QVBoxLayout()
            for button in (choose, save, reset):
                button.setEnabled(self.title_store is not None)
                actions.addWidget(button)
            actions.addStretch(1)
            row.addLayout(actions); row.addStretch(1)
            form.addRow(screen.label, row)
        self._show_titles()

    def _title_original(self, screen) -> bytes | None:
        if self._title_originals is None:
            try:
                self._title_originals = disc_titles(self.disc)
            except (OSError, ValueError, KeyError, DiscFormatError):
                self._title_originals = {}
        return self._title_originals.get(screen.name)

    def _title_now(self, screen) -> bytes | None:
        return (self.title_store.get(screen) if self.title_store else None) or self._title_original(screen)

    def _show_titles(self):
        for screen in TITLE_SCREENS:
            tim = self._title_now(screen)
            if tim is None:
                continue
            image = decode_tim(tim).image.convert("RGB").crop((0, 0, 320, SHOWN_ROWS)).resize(TITLE_PREVIEW, Image.LANCZOS)
            picture = QImage(image.tobytes(), image.width, image.height, image.width * 3, QImage.Format.Format_RGB888).copy()
            self._title_previews[screen.name].setPixmap(QPixmap.fromImage(picture))

    def _choose_title(self, screen):
        original = self._title_original(screen)
        if original is None:
            QMessageBox.warning(self, "Title screens", "The disc's title pictures couldn't be read."); return
        path, _ = QFileDialog.getOpenFileName(self, f"Picture for the {screen.label}", "", "Pictures (*.png *.jpg *.jpeg *.bmp *.webp)")
        if not path:
            return
        try:
            with Image.open(path) as picture:
                self.title_store.set(screen, encode_title(original, picture), shown_part(picture, original))
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Title screens", f"That picture couldn't be used: {exc}"); return
        self._show_titles(); self.refresh_imports()

    def _save_title(self, screen):
        tim = self._title_now(screen)
        if tim is None:
            QMessageBox.warning(self, "Title screens", "The disc's title pictures couldn't be read."); return
        path, _ = QFileDialog.getSaveFileName(self, f"Save the {screen.label}", f"{screen.name}.png", "PNG picture (*.png)")
        if path:
            decode_tim(tim).image.convert("RGB").save(path)

    def _reset_title(self, screen):
        self.title_store.remove(screen)
        self._show_titles(); self.refresh_imports()

    def refresh_imports(self):
        count = self.imports[0]()
        self.use_imports.setText(f"Play with imported resources ({count} map/Digimon/title override{'s' if count != 1 else ''}; a patched "
                                 "copy of the disc is built, the original is untouched)")
        self.use_imports.setEnabled(count > 0)

    def _choose_character(self) -> bool:
        """Who to play as, from Settings or asked now. False when the player closed the question."""
        self._chosen_character = None
        if not self.characters:
            return True
        wanted = self.character.currentData()
        if wanted == ASK_CHARACTER:
            wanted = CharacterDialog(self.characters, self.disc, self).choice()
            if wanted is None:
                return False
        self._chosen_character = next((c for c in self.characters if c.name == wanted), None)
        return True

    def launch_directly(self) -> bool:
        """DW1-play: power on, to the game's own title screens. False when no game is starting."""
        self.launch(None)
        return self.window is not None or self._patch_thread is not None

    def launch(self, slot: int | None, inspection_id: str | None = None, disc: Path | None = None):
        if self.window is not None:
            self.window.raise_(); self.window.activateWindow()
            return
        if self._patch_thread is not None:
            return
        if not self._check() or not self.source_ok:
            QMessageBox.warning(self, "Cannot play yet", "Choose a working core and make sure the disc image is available.")
            return
        if disc is None and not self._choose_character():
            return
        if disc is None and self.use_imports.isChecked() and self.imports[0]() > 0:
            self._build_patched(slot, inspection_id)
            return
        config = PlayConfig(Path(self.core.text()), Path(self.bios.text() or "."), disc or self.disc, self.store.directory,
                            launch_slot=slot, launch_inspection=inspection_id,
                            resume_on_close=self.resume.isChecked(), live_ram=self.live_ram,
                            game_rules=self.game_rules.isChecked(), hd_pictures=self.hd_pictures,
                            framerate=0.0 if self.framerate.value() <= FRAMERATE_OWN else self.framerate.value(),
                            forms=self.imports[2]() if disc is not None and len(self.imports) > 2 else {},
                            offer_forms=self.offer_forms.isChecked(),
                            character=self._chosen_character.folder if self._chosen_character else None,
                            keep_tamer=not self.characters,
                            coop_species=self.coop_species.currentData() if self.coop.isChecked() else None,
                            coop_level=self.coop_level.value(),
                            double_frame=self.double_frame.isChecked(), options=self._picture_options())
        start = (f" from slot {slot}" if slot is not None else
                 f" from inspection {inspection_id}" if inspection_id is not None else "")
        self._write_log(f"Launching {config.disc_path.name}{start}"
                        + (" with imported resources (patched copy)" if disc is not None else ""))
        # DW1-settings and DW1-play share these saves: two games at once would overwrite each other's memory card.
        self.store.directory.mkdir(parents=True, exist_ok=True)
        lock = QLockFile(str(self.store.directory / "player.lock"))
        lock.setStaleLockTime(0)  # only a dead holder (a crash) frees it
        if not lock.tryLock(0):
            QMessageBox.information(self, "Already playing", "This game is already running in DW1-settings or DW1-play.")
            return
        self._player_lock = lock
        window = PlayWindow(config, self.meta.get("release", "Digimon World"), self.map_names, self.decoded_grid,
                            self.character_names, self.item_names)
        window.log.connect(self._write_log)
        window.slots_changed.connect(self.refresh_slots)
        window.inspections_changed.connect(self.refresh_inspections)
        window.closed.connect(self._window_closed)
        window.quick_save_changed.connect(self._sync_quick_save)
        window.pause.toggled.connect(self.game_pause.setChecked)
        self.window = window
        self._update_game_controls()
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
        if self.window is None:
            self.player_closed.emit()  # nothing started: the build failed or was cancelled

    def wait_for_patch(self, timeout_ms: int = 60000):
        if self._patch_thread is not None:
            self._patch_worker.request_cancel(); self._patch_thread.wait(timeout_ms)

    @Slot()
    def _window_closed(self):
        self.window = None
        self._update_game_controls()
        if self._player_lock is not None:
            self._player_lock.unlock()
            self._player_lock = None
        self.player_closed.emit()
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
