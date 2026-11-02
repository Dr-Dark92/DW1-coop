from __future__ import annotations

import html
import math
import threading
import traceback
from pathlib import Path

from PIL.ImageQt import ImageQt
from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QCloseEvent, QPixmap, QVector3D
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QProgressDialog, QPushButton,
    QScrollArea, QSplitter, QStackedWidget, QTableWidget, QTableWidgetItem, QTabWidget, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .catalog import Catalogue, open_catalogue
from .errors import Cancelled, DiscFormatError
from .iso9660 import IsoEntry, safe_export
from .maps import (EXIT_COLOURS, MAX_MAP_FILE, MAX_TFS_FILE, TRIGGER_COLOURS, export_background,
                   floor_grid_image, layout_zones, map_exits, map_links, parse_map, parse_map_table, parse_tfs,
                   render_background, trigger_summary, zone_links)
from .relation_view import RelationView
from .animation import TICKS_PER_SECOND, AnimationPlayer, SoundEvent, TextureEvent, parse_animations
from .mmd import StaticPose, Vector3, parse_mmd_static, parse_usa_skeleton, validate_texture_materials
from .model_view import StaticModelView
from .project import import_project, layout_from_catalogue, source_state
from .tim import decode_tim, export_png

HEX_LIMIT = 256 * 1024
PREVIEW_LIMIT = 64 * 1024 * 1024


class ImportWorker(QObject):
    progress = Signal(int, str)
    succeeded = Signal(object)
    failed = Signal(str, str)
    cancelled = Signal(str)
    finished = Signal()

    def __init__(self, source: Path, project: Path):
        super().__init__()
        self.source, self.project = source, project
        self.cancel_event = threading.Event()

    def request_cancel(self):
        """Thread-safe; intentionally callable directly from the GUI thread."""
        self.cancel_event.set()

    @Slot()
    def run(self):
        try:
            result = import_project(self.source, self.project, self.cancel_event.is_set, self.progress.emit)
            self.succeeded.emit(result)
        except Cancelled as exc:
            self.cancelled.emit(str(exc))
        except Exception as exc:
            self.failed.emit(str(exc), traceback.format_exc())
        finally:
            self.finished.emit()

class ExportWorker(QObject):
    progress = Signal(int, str)
    succeeded = Signal(str)
    failed = Signal(str)
    cancelled = Signal(str)
    finished = Signal()

    def __init__(self, catalogue: Catalogue, entry: IsoEntry, destination: Path):
        super().__init__()
        self.catalogue, self.entry, self.destination = catalogue, entry, destination
        self.cancel_event = threading.Event()

    def request_cancel(self):
        self.cancel_event.set()

    @Slot()
    def run(self):
        try:
            with layout_from_catalogue(self.catalogue).open() as reader:
                result = safe_export(reader, self.entry, self.destination,
                                     cancelled=self.cancel_event.is_set, progress=self.progress.emit)
            self.succeeded.emit(str(result))
        except Cancelled as exc:
            self.cancelled.emit(str(exc))
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()


class ModelWorker(QObject):
    progress = Signal(int, str)
    succeeded = Signal(object)
    failed = Signal(str)
    cancelled = Signal(str)
    finished = Signal()

    def __init__(self, catalogue: Catalogue, executable: IsoEntry, model: IsoEntry,
                 texture: IsoEntry, resource_id: int):
        super().__init__()
        self.catalogue = catalogue
        self.executable, self.model, self.texture = executable, model, texture
        self.resource_id = resource_id
        self.cancel_event = threading.Event()

    def request_cancel(self):
        self.cancel_event.set()

    @Slot()
    def run(self):
        try:
            with layout_from_catalogue(self.catalogue).open() as reader:
                self.progress.emit(10, "Reading executable skeleton data")
                executable = reader.read_extent_range(self.executable.extent, self.executable.extent_size,
                                                      self.executable.offset, self.executable.size,
                                                      cancelled=self.cancel_event.is_set)
                self.progress.emit(35, "Reading model container")
                model_data = reader.read_extent_range(self.model.extent, self.model.extent_size,
                                                       self.model.offset, self.model.size,
                                                       cancelled=self.cancel_event.is_set)
                self.progress.emit(55, "Reading associated texture")
                texture_data = reader.read_extent_range(self.texture.extent, self.texture.extent_size,
                                                         self.texture.offset, self.texture.size,
                                                         cancelled=self.cancel_event.is_set)
            if self.cancel_event.is_set():
                raise Cancelled("Model preview cancelled.")
            self.progress.emit(70, "Validating geometry and materials")
            skeleton = parse_usa_skeleton(executable, self.resource_id)
            model = parse_mmd_static(model_data, skeleton, self.cancel_event.is_set)
            first = decode_tim(texture_data)
            used_palettes = validate_texture_materials(model, first)
            palettes = tuple(decode_tim(texture_data, index).image for index in range(first.palette_count))
            self.progress.emit(90, "Reading animation clips")
            try:
                clips, clip_error = parse_animations(model_data, model.animation_offset, len(skeleton)), None
            except DiscFormatError as exc:
                clips, clip_error = [], str(exc)
            self.progress.emit(100, "Static model ready")
            self.succeeded.emit((model, palettes, first.clut_y, used_palettes, first.bits_per_pixel, clips, clip_error))
        except Cancelled as exc:
            self.cancelled.emit(str(exc))
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()


class RelationWorker(QObject):
    """Reads every catalogued MAP file and builds the zone relation graph and its layout."""
    progress = Signal(int, str)
    succeeded = Signal(object)
    failed = Signal(str)
    cancelled = Signal(str)
    finished = Signal()

    def __init__(self, catalogue: Catalogue, executable: IsoEntry, maps: dict[int, IsoEntry]):
        super().__init__()
        self.catalogue, self.executable, self.maps = catalogue, executable, maps
        self.cancel_event = threading.Event()

    def request_cancel(self):
        self.cancel_event.set()

    @Slot()
    def run(self):
        try:
            with layout_from_catalogue(self.catalogue).open() as reader:
                self.progress.emit(2, "Reading the map table")
                entries = parse_map_table(reader.read_extent_range(
                    self.executable.extent, self.executable.extent_size, self.executable.offset,
                    self.executable.size, cancelled=self.cancel_event.is_set))
                scenes = {}
                for done, (index, entry) in enumerate(sorted(self.maps.items())):
                    if self.cancel_event.is_set():
                        raise Cancelled("Relation map cancelled.")
                    data = reader.read_extent_range(entry.extent, entry.extent_size, entry.offset, entry.size,
                                                    cancelled=self.cancel_event.is_set)
                    scenes[index] = parse_map(data, entries[index])
                    self.progress.emit(5 + 85 * (done + 1) // len(self.maps), f"Reading {entries[index].name}")
            self.progress.emit(92, "Laying out zones")
            links = map_links(entries, scenes)
            zones = zone_links(entries, links)
            linked = sorted({zone for link in zones for zone in (link.source, link.target)})
            counts = {}
            for entry in entries:
                if entry.name:
                    counts[entry.area_name] = counts.get(entry.area_name, 0) + 1
            self.progress.emit(100, "Relation map ready")
            self.succeeded.emit((counts, zones, layout_zones(linked, zones), links))
        except Cancelled as exc:
            self.cancelled.emit(str(exc))
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()


class WelcomePage(QWidget):
    import_requested = Signal()
    open_requested = Signal()

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.addStretch()
        title = QLabel("DW1-coop Asset Workbench")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 26px; font-weight: 600")
        subtitle = QLabel("Import your own Digimon World disc image to create a read-only analysis project.\n"
                          "Supported first milestone: USA SLUS-01032 in ISO or supported BIN/CUE layouts.")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        buttons = QHBoxLayout()
        import_button, open_button = QPushButton("Import game image…"), QPushButton("Open analysis project…")
        import_button.clicked.connect(self.import_requested)
        open_button.clicked.connect(self.open_requested)
        buttons.addStretch(); buttons.addWidget(import_button); buttons.addWidget(open_button); buttons.addStretch()
        layout.addLayout(buttons)
        note = QLabel("No game data is bundled, downloaded, or modified. Projects store metadata and selected exports only.")
        note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(note)
        layout.addStretch()


class UnsupportedPage(QWidget):
    def __init__(self, heading: str, explanation: str):
        super().__init__()
        layout = QVBoxLayout(self)
        label = QLabel(f"<h2>{html.escape(heading)}</h2><p>{html.escape(explanation)}</p>")
        label.setWordWrap(True)
        layout.addWidget(label); layout.addStretch()


class Workspace(QWidget):
    background_finished = Signal()

    def __init__(self, catalogue: Catalogue, parent=None):
        super().__init__(parent)
        self.catalogue = catalogue
        self.rows = catalogue.resources()
        self.rows_by_id = {row["id"]: row for row in self.rows}
        self.associations = catalogue.associations()
        self.labels = {(row["resource_id"], row["kind"]): row for row in catalogue.labels()}
        self.export_thread: QThread | None = None
        self.export_worker: ExportWorker | None = None
        self.export_progress: QProgressDialog | None = None
        self.model_thread: QThread | None = None
        self.model_worker: ModelWorker | None = None
        self.model_progress: QProgressDialog | None = None
        self.relation_thread: QThread | None = None
        self.relation_worker: RelationWorker | None = None
        self.relation_progress: QProgressDialog | None = None
        self.tabs = QTabWidget()
        self.tabs.addTab(self._overview(), "Overview")
        self.tabs.addTab(self._disc_files(), "Disc Files")
        self.tabs.addTab(self._characters(), "Characters and Digimon")
        self.tabs.addTab(UnsupportedPage("Animations", "Character animation clips are decoded from each MMD and played "
                                         "per model: open Characters and Digimon, select an entry, and use Load model "
                                         "and animations. Clips follow the game's frame-by-frame stepping and match an "
                                         "independent converter for all 4,843 clips of the 178 models. Sound cues are "
                                         "listed but not played; clip names are not established, so clips are shown by "
                                         "index."), "Animations")
        self.tabs.addTab(self._textures(), "Textures")
        self.tabs.addTab(self._maps(), "Maps and Zones")
        self.tabs.addTab(self._executable(), "Executable Analysis")
        layout = QVBoxLayout(self); layout.addWidget(self.tabs)

    def _overview(self):
        page, layout = QWidget(), QVBoxLayout()
        page.setLayout(layout)
        meta = self.catalogue.meta()
        ok, source_message = source_state(self.catalogue)
        form = QFormLayout()
        form.addRow("Source", QLabel(meta.get("source_path", "unknown")))
        form.addRow("Release", QLabel(meta.get("release", "not established")))
        form.addRow("Executable", QLabel(meta.get("executable", "unknown")))
        form.addRow("Evidence", QLabel(meta.get("release_evidence", "none")))
        form.addRow("Layout", QLabel(meta.get("source_kind", "unknown")))
        form.addRow("Import", QLabel(meta.get("import_status", "unknown")))
        counts = {"identified": 0, "decoded": 0, "unsupported": 0, "unknown": 0}
        for row in self.rows:
            counts[row["status"] if row["status"] in counts else "unsupported"] += 1
        form.addRow("Resources", QLabel(", ".join(f"{key}: {value}" for key, value in counts.items())))
        status = QLabel(source_message); status.setWordWrap(True)
        status.setStyleSheet("color: #287a31" if ok else "color: #a33")
        form.addRow("Source status", status)
        layout.addLayout(form)
        logs = QPlainTextEdit(); logs.setReadOnly(True)
        logs.setPlainText("\n".join(f"{row['created_at']} [{row['level']}] {row['message']}" for row in reversed(self.catalogue.logs())))
        layout.addWidget(QLabel("Analysis log")); layout.addWidget(logs)
        return page

    def _disc_files(self):
        page, layout = QWidget(), QVBoxLayout(); page.setLayout(layout)
        self.file_table = QTableWidget(len(self.rows), 4)
        self.file_table.setHorizontalHeaderLabels(["Path", "Size", "SHA-256", "Detected format"])
        for r, row in enumerate(self.rows):
            for c, value in enumerate((row["original_path"], row["size"], row["content_hash"], row["detected_format"])):
                self.file_table.setItem(r, c, QTableWidgetItem(str(value)))
        self.file_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.file_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        buttons = QHBoxLayout(); export = QPushButton("Export selected…"); hex_button = QPushButton("Bounded hex view")
        export.clicked.connect(self._export_selected); hex_button.clicked.connect(self._hex_selected)
        buttons.addWidget(export); buttons.addWidget(hex_button); buttons.addStretch()
        layout.addLayout(buttons); layout.addWidget(self.file_table)
        return page

    def _characters(self):
        page, layout = QWidget(), QVBoxLayout(); page.setLayout(layout)
        characters = [row for row in self.rows if row["detected_format"] == "DW1 character resource entry"]
        self.character_rows = characters
        if not characters:
            layout.addWidget(QLabel("No verified character resource table is present. Models and animations remain unsupported."))
            layout.addStretch()
            return page
        info = QLabel("Verified USA executable resource IDs and internal names, associated by index with ALLTIM texture "
                      "slots. Static models (151 of 178 supported) and their animation clips can be loaded and played; "
                      "both were verified against an independent converter.")
        info.setWordWrap(True); layout.addWidget(info)
        self.character_table = QTableWidget(len(characters), 5)
        self.character_table.setHorizontalHeaderLabels(["Resource ID", "Display name", "Internal name", "Model container", "Texture resource"])
        for index, row in enumerate(characters):
            resource_id = row["original_path"].split("/")[-2]
            name = row["original_path"].split("/")[-1]
            label = self.labels.get((row["id"], "display_name"))
            related = {association["kind"]: self.rows_by_id[association["target_id"]]
                       for association in self.associations
                       if association["source_id"] == row["id"] and association["target_id"] in self.rows_by_id}
            model = related.get("model_container")
            texture = related.get("texture")
            values = (resource_id, label["value"] if label else "not recorded", name,
                      model["original_path"] if model else "unsupported / absent",
                      texture["original_path"] if texture else "unavailable")
            for column, value in enumerate(values):
                self.character_table.setItem(index, column, QTableWidgetItem(str(value)))
        self.character_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.character_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.character_preview = QLabel("Select a verified resource entry")
        self.character_preview.setAlignment(Qt.AlignmentFlag.AlignCenter); self.character_preview.setMinimumSize(320, 240)
        self.character_details = QPlainTextEdit(); self.character_details.setReadOnly(True); self.character_details.setMaximumHeight(170)
        self.character_table.itemSelectionChanged.connect(self._preview_character)
        export = QPushButton("Export associated texture PNG + provenance metadata…")
        export.clicked.connect(self._export_character_texture)
        model_page = QWidget(); model_layout = QVBoxLayout(model_page)
        self.model_view = StaticModelView()
        model_controls = QHBoxLayout()
        self.model_mode = QComboBox(); self.model_mode.addItems(["Textured", "Untextured"])
        self.model_wireframe = QCheckBox("Wireframe overlay")
        model_reset = QPushButton("Reset view")
        model_load = QPushButton("Load model and animations")
        self.model_mode.currentIndexChanged.connect(self._update_model_modes)
        self.model_wireframe.toggled.connect(self._update_model_modes)
        model_reset.clicked.connect(self.model_view.reset_view)
        model_load.clicked.connect(self._load_character_model)
        model_controls.addWidget(self.model_mode); model_controls.addWidget(self.model_wireframe)
        model_controls.addWidget(model_reset); model_controls.addStretch(); model_controls.addWidget(model_load)
        self.model_status = QLabel("Model geometry is loaded on request in a cancellable background worker.")
        self.model_status.setWordWrap(True)
        anim_controls = QHBoxLayout()
        self.anim_clip = QComboBox(); self.anim_clip.setEnabled(False)
        self.anim_clip.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.anim_clip.currentIndexChanged.connect(self._select_clip)
        self.anim_play = QPushButton("Play"); self.anim_play.clicked.connect(self._toggle_animation)
        anim_step = QPushButton("Step"); anim_step.clicked.connect(self._step_animation)
        anim_restart = QPushButton("Restart"); anim_restart.clicked.connect(self._restart_animation)
        self.anim_root = QCheckBox("Apply root motion"); self.anim_root.toggled.connect(self._restart_animation)
        for widget in (anim_step, anim_restart, self.anim_play, self.anim_root):
            widget.setEnabled(False)
        self._anim_widgets = (anim_step, anim_restart, self.anim_play, self.anim_root)
        anim_controls.addWidget(QLabel("Animation")); anim_controls.addWidget(self.anim_clip)
        anim_controls.addWidget(self.anim_play); anim_controls.addWidget(anim_step); anim_controls.addWidget(anim_restart)
        anim_controls.addWidget(self.anim_root); anim_controls.addStretch()
        self.anim_status = QLabel("Load a model to play its animation clips.")
        self.anim_status.setWordWrap(True)
        self.anim_timer = QTimer(self); self.anim_timer.setInterval(1000 // TICKS_PER_SECOND)
        self.anim_timer.timeout.connect(self._step_animation)
        self._anim_clips, self._anim_player = [], None
        model_layout.addLayout(model_controls); model_layout.addWidget(self.model_view, 1)
        model_layout.addLayout(anim_controls); model_layout.addWidget(self.anim_status)
        model_layout.addWidget(self.model_status)
        texture_page = QWidget(); texture_layout = QVBoxLayout(texture_page)
        texture_layout.addWidget(self.character_preview); texture_layout.addWidget(self.character_details)
        preview_tabs = QTabWidget(); preview_tabs.addTab(model_page, "Model and animation"); preview_tabs.addTab(texture_page, "Texture")
        right = QWidget(); right_layout = QVBoxLayout(right)
        right_layout.addWidget(preview_tabs)
        split = QSplitter(); split.addWidget(self.character_table); split.addWidget(right)
        layout.addWidget(split, 1); layout.addWidget(export)
        return page

    def _selected_character(self):
        index = self.character_table.currentRow()
        if index < 0:
            raise ValueError("Select a character resource entry first.")
        return self.character_rows[index]

    def _character_related(self, row, kind):
        association = next((item for item in self.associations
                            if item["source_id"] == row["id"] and item["kind"] == kind), None)
        return self.rows_by_id.get(association["target_id"]) if association else None

    @staticmethod
    def _resource_provenance(row):
        return {key: row[key] for key in ("original_path", "parent_path", "offset", "size",
                                           "detected_format", "decoder", "decoder_version",
                                           "mapping_evidence", "confidence")}

    def _preview_character(self):
        try:
            character = self._selected_character()
            texture = self._character_related(character, "texture")
            model = self._character_related(character, "model_container")
            label = self.labels.get((character["id"], "display_name"))
            if texture is None:
                raise ValueError("This entry has no verified texture association.")
            tim = decode_tim(self._read_row(texture, PREVIEW_LIMIT))
            pixmap = QPixmap.fromImage(ImageQt(tim.image))
            self.character_preview.setPixmap(pixmap.scaled(self.character_preview.size(),
                                                            Qt.AspectRatioMode.KeepAspectRatio,
                                                            Qt.TransformationMode.SmoothTransformation))
            details = [
                f"source: {character['parent_path']}",
                f"display name: {label['value'] if label else 'not recorded'}",
                f"display-name evidence: {label['evidence'] if label else 'none'}",
                f"table offset: 0x{character['offset']:x}",
                f"entry size: {character['size']} bytes",
                f"format: {character['detected_format']}",
                f"parser: {character['decoder']} {character['decoder_version']}",
                f"texture: {texture['original_path']} @ 0x{texture['offset']:x}, {texture['size']} bytes",
                f"texture parser: {texture['decoder']} {texture['decoder_version']}",
                f"model: {model['original_path'] if model else 'absent / unsupported'}",
                f"evidence: {character['mapping_evidence']}",
            ]
            self.character_details.setPlainText("\n".join(details))
        except Exception as exc:
            self.character_preview.setText(f"Preview unavailable:\n{exc}")

    def _export_character_texture(self):
        try:
            character = self._selected_character()
            texture = self._character_related(character, "texture")
            if texture is None:
                raise ValueError("This entry has no verified texture association.")
            tim = decode_tim(self._read_row(texture, PREVIEW_LIMIT))
            name = character["original_path"].split("/")[-1]
            path, _ = QFileDialog.getSaveFileName(self, "Export associated texture", f"{name}-{texture['offset']//0x4800:03d}.png", "PNG image (*.png)")
            if not path: return
            export_png(tim, Path(path), texture["id"], texture["content_hash"],
                       provenance=self._resource_provenance(texture))
            QMessageBox.information(self, "Texture exported", f"Wrote {path} and provenance JSON.")
        except Exception as exc:
            QMessageBox.critical(self, "Texture export failed", str(exc))

    def _maps(self):
        page, layout = QWidget(), QVBoxLayout(); page.setLayout(layout)
        maps = [row for row in self.rows if row["detected_format"] == "DW1 map table entry"]
        self.map_rows = {row["id"]: row for row in maps}
        self._map_entries = None
        self._map_scene = self._map_tfs = None
        if not maps:
            layout.addWidget(QLabel("No verified map table is catalogued. Projects imported before map support "
                                    "must be re-imported; 3D scene geometry, scripts, and gameplay are not decoded."))
            layout.addStretch()
            return page
        info = QLabel("Maps from the USA executable map table, grouped into zones by their area name. Backgrounds, "
                      "camera, spawns, warps, Digimon placements, sprites, and the floor grid are decoded and were "
                      "verified against DW1ModelConverter output; scripts and 3D geometry are not decoded.")
        info.setWordWrap(True); layout.addWidget(info)
        self.map_search = QLineEdit(); self.map_search.setPlaceholderText("Find a zone or map (e.g. Green Gym, TRAI00)")
        self.map_search.setClearButtonEnabled(True); self.map_search.textChanged.connect(self._filter_maps)
        self.map_tree = QTreeWidget(); self.map_tree.setHeaderLabels(["Zone / map", "Table index", "Status"])
        zones = {}
        for row in maps:
            label = self.labels.get((row["id"], "area_name"))
            zones.setdefault(label["value"] if label else "Unnamed area", []).append(row)
        self._map_items, self._zone_items = {}, {}
        for zone in sorted(zones):
            parent = QTreeWidgetItem([f"{zone} ({len(zones[zone])})", "", ""])
            parent.setData(0, Qt.ItemDataRole.UserRole + 1, zone)
            self.map_tree.addTopLevelItem(parent); self._zone_items[zone] = parent
            for row in sorted(zones[zone], key=lambda item: item["offset"]):
                _, index, name = row["original_path"].rsplit("/", 2)
                item = QTreeWidgetItem([name, index, row["status"]])
                item.setData(0, Qt.ItemDataRole.UserRole, row["id"])
                parent.addChild(item)
                self._map_items[row["id"]] = item
        self.map_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.map_tree.currentItemChanged.connect(self._preview_map)
        self.map_palette = QComboBox(); self.map_palette.currentIndexChanged.connect(self._render_map_background)
        self.map_palette.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.map_background = QLabel("Select a map"); self.map_background.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Zero colour words are transparent; show them over a dark backdrop like the cleared PS1 screen.
        self.map_background.setStyleSheet("background: #20242b; color: #d0d4da")
        background_scroll = QScrollArea(); background_scroll.setWidget(self.map_background)
        background_scroll.setWidgetResizable(True)
        background_page = QWidget(); background_layout = QVBoxLayout(background_page)
        palette_row = QHBoxLayout(); palette_row.addWidget(QLabel("Background palette")); palette_row.addWidget(self.map_palette)
        palette_row.addStretch(); background_layout.addLayout(palette_row); background_layout.addWidget(background_scroll)
        self.map_details = QPlainTextEdit(); self.map_details.setReadOnly(True)
        self.map_warps = QListWidget(); self.map_warps.itemDoubleClicked.connect(self._follow_warp)
        self.map_inbound = QListWidget(); self.map_inbound.itemDoubleClicked.connect(self._follow_warp)
        self.map_inbound.setSortingEnabled(True)
        scene_page = QWidget(); scene_layout = QVBoxLayout(scene_page)
        links_row = QHBoxLayout()
        for title, widget in (("Warps out of this map (double-click to open)", self.map_warps),
                              ("Linked from (maps whose warps lead here)", self.map_inbound)):
            column = QVBoxLayout(); column.addWidget(QLabel(title)); column.addWidget(widget); links_row.addLayout(column)
        scene_layout.addLayout(links_row, 2); scene_layout.addWidget(self.map_details, 3)
        self.map_floor = QLabel(); self.map_floor.setAlignment(Qt.AlignmentFlag.AlignCenter)
        floor_page = QWidget(); floor_layout = QVBoxLayout(floor_page)
        swatch = lambda colour: f"<span style='background: rgb{colour}'>&nbsp;&nbsp;&nbsp;&nbsp;</span>"
        legend = " ".join(f"{swatch(colour)} {html.escape(kind)}" for kind, colour in TRIGGER_COLOURS.items()
                          if kind != "unknown")
        exits = " ".join(f"{swatch(colour)} {slot}" for slot, colour in enumerate(EXIT_COLOURS))
        floor_note = QLabel("100×100 floor grid, one cell per pixel; top is the far edge (north). Values follow the "
                            "game's tile-trigger rules (DW1-SydPatches): 0 walkable, 128 blocked, 50–79 step-on "
                            "script, 80–109 interact script, 110–119 exit through warp slot 0–9, 120 toilet.<br>"
                            f"{legend}<br>exit slots: {exits}")
        floor_note.setWordWrap(True); floor_note.setTextFormat(Qt.TextFormat.RichText)
        floor_layout.addWidget(floor_note); floor_layout.addWidget(self.map_floor, 1)
        self.map_preview_tabs = QTabWidget()
        self.map_preview_tabs.addTab(background_page, "Background"); self.map_preview_tabs.addTab(scene_page, "Scene data")
        self.map_preview_tabs.addTab(floor_page, "Floor grid")
        tree_panel = QWidget(); tree_layout = QVBoxLayout(tree_panel); tree_layout.setContentsMargins(0, 0, 0, 0)
        tree_layout.addWidget(self.map_search); tree_layout.addWidget(self.map_tree)
        split = QSplitter(); split.addWidget(tree_panel); split.addWidget(self.map_preview_tabs)
        split.setStretchFactor(1, 3); split.setSizes([360, 940])
        export = QPushButton("Export background PNG + provenance metadata…")
        export.clicked.connect(self._export_map_background)
        browser = QWidget(); browser_layout = QVBoxLayout(browser); browser_layout.setContentsMargins(0, 0, 0, 0)
        browser_layout.addWidget(split, 1); browser_layout.addWidget(export)
        self.maps_tabs = QTabWidget()
        self.maps_tabs.addTab(browser, "Browser"); self.maps_tabs.addTab(self._relation_page(), "Relation map")
        self.maps_tabs.currentChanged.connect(self._maps_tab_changed)
        layout.addWidget(self.maps_tabs, 1)
        return page

    def _relation_page(self):
        page = QWidget(); layout = QVBoxLayout(page)
        controls = QHBoxLayout()
        self.relation_scripted = QCheckBox("Show warp-table links without exit tiles (dashed)")
        self.relation_scripted.setChecked(True)
        self.relation_scripted.toggled.connect(lambda visible: self.relation_view.set_script_links_visible(visible))
        open_zone = QPushButton("Open selected zone in browser")
        open_zone.clicked.connect(lambda: self._open_zone(self.relation_view.selected_zone()))
        rebuild = QPushButton("Rebuild")
        rebuild.clicked.connect(self._build_relations)
        self.relation_view = RelationView()
        fit = QPushButton("Fit all"); fit.clicked.connect(self.relation_view.fit_all)
        controls.addWidget(self.relation_scripted); controls.addStretch(); controls.addWidget(fit)
        controls.addWidget(open_zone); controls.addWidget(rebuild)
        self.relation_status = QLabel("Zones are linked by the warps between their maps. Solid green lines are "
                                      "walk-through exits (thicker = more exits) and are laid out by exit direction; "
                                      "dashed lines are warp-table links with no exit tiles, used by map scripts or "
                                      "unused. Script-only transitions are not decoded, so some zones have no links. "
                                      "Wheel to zoom, drag to pan, hover a line for details, double-click a zone to open it.")
        self.relation_status.setWordWrap(True)
        layout.addLayout(controls); layout.addWidget(self.relation_view, 1); layout.addWidget(self.relation_status)
        self._relations_built = False
        return page

    def _maps_tab_changed(self, index):
        if index == 1 and not self._relations_built:
            self._build_relations()

    def _build_relations(self):
        try:
            if self.relation_thread and self.relation_thread.isRunning():
                raise ValueError("The relation map is already loading.")
            executable = next(row for row in self.rows
                              if row["detected_format"] == "PS-X EXE" and row["offset"] is None)
            maps = {}
            for row in self.map_rows.values():
                scene_row = self._character_related(row, "scene")
                if scene_row is not None:
                    maps[int(row["original_path"].rsplit("/", 2)[1])] = self._entry(scene_row)
            dialog = QProgressDialog("Building relation map", "Cancel", 0, 100, self)
            dialog.setWindowModality(Qt.WindowModality.WindowModal); dialog.setAutoClose(False); dialog.show()
            thread = QThread(self)
            worker = RelationWorker(self.catalogue, self._entry(executable), maps)
            worker.moveToThread(thread); thread.started.connect(worker.run)
            worker.progress.connect(self._relation_progress_changed)
            dialog.canceled.connect(lambda: worker.request_cancel())
            worker.succeeded.connect(self._relations_loaded)
            worker.failed.connect(self._relations_failed)
            worker.cancelled.connect(self.relation_status.setText)
            worker.finished.connect(thread.quit); worker.finished.connect(worker.deleteLater)
            thread.finished.connect(thread.deleteLater); thread.finished.connect(self._relations_finished)
            self.relation_thread, self.relation_worker, self.relation_progress = thread, worker, dialog
            thread.start()
        except Exception as exc:
            QMessageBox.critical(self, "Relation map failed", str(exc))

    @Slot(int, str)
    def _relation_progress_changed(self, value, message):
        if self.relation_progress:
            self.relation_progress.setValue(value); self.relation_progress.setLabelText(message)

    @Slot(object)
    def _relations_loaded(self, result):
        counts, zones, positions, links = result
        self.relation_view.show_relations(counts, zones, positions, self._open_zone)
        self.relation_view.set_script_links_visible(self.relation_scripted.isChecked())
        self._relations_built = True
        walk = sum(link.walk_through for link in links)
        unlinked = len(counts) - len(positions)
        self.relation_status.setText(
            f"{len(counts)} zones, {len(zones)} zone links from {len(links)} map warps ({walk} walk-through exits, "
            f"{len(links) - walk} without exit tiles). {unlinked} zones have no decoded warp links and are entered "
            "through map scripts, which are not decoded. Layout is approximate: exit directions guide placement. "
            "Wheel to zoom, drag to pan, hover a line for details, double-click a zone to open it.")

    @Slot(str)
    def _relations_failed(self, message):
        self.relation_status.setText(f"Relation map unavailable: {message}")

    @Slot()
    def _relations_finished(self):
        if self.relation_progress:
            self.relation_progress.close(); self.relation_progress.deleteLater()
        self.relation_progress = None; self.relation_worker = None; self.relation_thread = None
        self.background_finished.emit()

    def _open_zone(self, zone):
        parent = self._zone_items.get(zone) if zone else None
        if parent is None:
            return
        self.map_search.clear(); self.maps_tabs.setCurrentIndex(0)
        parent.setExpanded(True); self.map_tree.setCurrentItem(parent.child(0) if parent.childCount() else parent)
        self.map_tree.scrollToItem(parent, QTreeWidget.ScrollHint.PositionAtTop)

    def _filter_maps(self, text):
        needle = text.strip().lower()
        for zone, parent in self._zone_items.items():
            zone_match = needle in zone.lower()
            any_child = False
            for index in range(parent.childCount()):
                child = parent.child(index)
                show = not needle or zone_match or needle in child.text(0).lower()
                child.setHidden(not show); any_child |= show
            parent.setHidden(bool(needle) and not any_child)
            parent.setExpanded(bool(needle) and any_child)

    def _selected_map(self):
        item = self.map_tree.currentItem()
        row_id = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        if row_id is None:
            raise ValueError("Select a map first.")
        return self.map_rows[row_id]

    def _map_entry(self, row):
        if self._map_entries is None:
            executable = next(candidate for candidate in self.rows
                              if candidate["original_path"] == row["parent_path"] and candidate["offset"] is None)
            self._map_entries = parse_map_table(self._read_row(executable, PREVIEW_LIMIT))
        return self._map_entries[int(row["original_path"].rsplit("/", 2)[1])]

    def _character_name(self, character_id):
        prefix = f"#character/{character_id:03d}/"
        row = next((row for row in self.rows if prefix in row["original_path"]), None)
        if row is None:
            return f"character {character_id}"
        label = self.labels.get((row["id"], "display_name"))
        return f"{label['value'] if label else row['original_path'].rsplit('/', 1)[1]} (ID {character_id})"

    def _preview_map(self, *_):
        self._map_scene = self._map_tfs = None
        self.map_warps.clear(); self.map_inbound.clear(); self.map_palette.blockSignals(True); self.map_palette.clear()
        self.map_palette.blockSignals(False)
        try:
            row = self._selected_map()
        except ValueError:
            return
        for association in self.associations:
            if association["kind"] == "warp" and association["target_id"] == row["id"]:
                source = self.map_rows.get(association["source_id"])
                if source is None:
                    continue
                _, index, name = source["original_path"].rsplit("/", 2)
                zone = self.labels.get((source["id"], "area_name"))
                item = QListWidgetItem(f"{name} [{zone['value'] if zone else 'unnamed'}]")
                item.setData(Qt.ItemDataRole.UserRole, int(index)); self.map_inbound.addItem(item)
        if not self.map_inbound.count():
            self.map_inbound.addItem("No warp-table links lead here; it is entered through map scripts or not at all.")
        try:
            entry = self._map_entry(row)
            scene_row, tfs_row = self._character_related(row, "scene"), self._character_related(row, "background")
            if scene_row is None or tfs_row is None:
                raise ValueError(f"{entry.map_path} / .TFS are absent from this disc; only the table entry exists.")
            scene = parse_map(self._read_row(scene_row, MAX_MAP_FILE), entry)
            tfs = parse_tfs(self._read_row(tfs_row, MAX_TFS_FILE))
            self._map_scene, self._map_tfs = scene, tfs
            self.map_palette.blockSignals(True)
            self.map_palette.addItems([f"Palette {index}" for index in range(len(tfs.palettes))])
            self.map_palette.blockSignals(False)
            self._render_map_background()
            floor = QPixmap.fromImage(ImageQt(floor_grid_image(scene)))
            self.map_floor.setPixmap(floor.scaled(400, 400, Qt.AspectRatioMode.KeepAspectRatio,
                                                  Qt.TransformationMode.FastTransformation))
            entries = self._map_entries
            for warp_exit in map_exits(scene):
                target = entries[warp_exit.warp.map_index]
                how = (f"walk-through exit, {warp_exit.direction} ({warp_exit.cell_count} tiles)"
                       if warp_exit.walk_through else "no exit tiles: map script or unused")
                text = (f"slot {warp_exit.slot} → {target.name or '(unnamed)'} [{target.area_name}], "
                        f"spawn {warp_exit.warp.spawn_index} — {how}")
                item = QListWidgetItem(text); item.setData(Qt.ItemDataRole.UserRole, warp_exit.warp.map_index)
                self.map_warps.addItem(item)
            if not self.map_warps.count():
                self.map_warps.addItem("No warp-table exits; transitions from this map are scripted.")
            triggers = trigger_summary(scene)
            setup = scene.setup
            lines = [
                f"map: {entry.name} (table index {entry.index}, area {entry.area_id}: {entry.area_name})",
                f"files: {entry.map_path}, {entry.tfs_path}",
                f"flags: sound {entry.sound_id}, day/night {'yes' if entry.has_time_cycle else 'no'}, "
                f"Digimon {'yes' if entry.has_digimon else 'no'}",
                f"background: {setup.grid_width}×{setup.grid_height} tiles of 128×128, {len(tfs.palettes)} palette(s)",
                f"camera: origin {setup.camera_origin}, target {setup.camera_target}, distance {setup.viewer_distance}",
                *(f"light {i}: position {light.position}, colour {light.color}" for i, light in enumerate(setup.lights)),
                f"liked area {setup.liked_area}, disliked area {setup.disliked_area}",
                f"embedded images: {len(scene.image_offsets)} 8-bpp, {len(scene.object_image_offsets)} 4-bpp; "
                f"sprite objects {len(scene.objects)}, instances {len(scene.instances)}",
                f"toilet: {entry.toilet if entry.toilet else 'none'}",
                f"doors: {', '.join(f'model {d.model_id} at {d.position}' for d in entry.doors) or 'none'}",
                "floor-grid triggers (value: tiles):",
                *(f"  {kind}: " + ", ".join(f"{value}: {count}" for value, count in sorted(values.items()))
                  for kind, values in sorted(triggers.items())),
                "spawn points:",
                *(f"  {i}: position {spawn.position}, rotation {spawn.rotation}" for i, spawn in enumerate(scene.spawns)),
                f"Digimon placements ({len(scene.digimon)}):",
                *(f"  {self._character_name(d.character_id)} at {d.position}; HP {d.stats['hp']}/{d.stats['max_hp']}, "
                  f"MP {d.stats['mp']}/{d.stats['max_mp']}, off {d.stats['offense']}, def {d.stats['defense']}, "
                  f"spd {d.stats['speed']}, brn {d.stats['brains']}, bits {d.bits}, script {d.script_id}, "
                  f"{len(d.waypoints)} waypoint(s)" for d in scene.digimon),
                f"parser: {row['decoder']} {row['decoder_version']}; MAP {scene_row['decoder_version']}; "
                f"TFS {tfs_row['decoder_version']}",
                f"evidence: {row['mapping_evidence']}",
            ]
            self.map_details.setPlainText("\n".join(lines))
        except Exception as exc:
            self.map_background.setPixmap(QPixmap()); self.map_floor.setPixmap(QPixmap())
            self.map_background.setText(f"Preview unavailable:\n{exc}")
            self.map_details.setPlainText(str(exc))

    def _render_map_background(self, *_):
        if self._map_scene is None or self._map_tfs is None or self.map_palette.currentIndex() < 0:
            return
        image = render_background(self._map_tfs, self._map_scene.setup, self.map_palette.currentIndex())
        self.map_background.setPixmap(QPixmap.fromImage(ImageQt(image)))

    def _follow_warp(self, item):
        index = item.data(Qt.ItemDataRole.UserRole)
        target = next((row_id for row_id, row in self.map_rows.items()
                       if int(row["original_path"].rsplit("/", 2)[1]) == index), None) if index is not None else None
        if target is not None:
            if self._map_items[target].isHidden() or self._map_items[target].parent().isHidden():
                self.map_search.clear()
            self.map_tree.setCurrentItem(self._map_items[target])

    def _export_map_background(self):
        try:
            row = self._selected_map()
            if self._map_scene is None or self._map_tfs is None:
                raise ValueError("This map has no decoded background.")
            entry, palette = self._map_entry(row), self.map_palette.currentIndex()
            path, _ = QFileDialog.getSaveFileName(self, "Export background", f"{entry.name}-palette{palette}.png",
                                                  "PNG image (*.png)")
            if not path: return
            tfs_row, scene_row = self._character_related(row, "background"), self._character_related(row, "scene")
            export_background(render_background(self._map_tfs, self._map_scene.setup, palette), Path(path), {
                "map": entry.name, "table_index": entry.index, "area_name": entry.area_name, "palette_index": palette,
                "palette_count": len(self._map_tfs.palettes),
                "source_resource_id": tfs_row["id"], "source_sha256": tfs_row["content_hash"],
                "tile_grid_source": self._resource_provenance(scene_row) | {"sha256": scene_row["content_hash"]},
                "provenance": self._resource_provenance(tfs_row),
            })
            QMessageBox.information(self, "Background exported", f"Wrote {path} and provenance JSON.")
        except Exception as exc:
            QMessageBox.critical(self, "Background export failed", str(exc))

    def _update_model_modes(self):
        self.model_view.set_modes(self.model_mode.currentIndex() == 0,
                                  self.model_wireframe.isChecked())

    def _load_character_model(self):
        try:
            if self.model_thread and self.model_thread.isRunning():
                raise ValueError("A model preview is already loading.")
            character = self._selected_character()
            model = self._character_related(character, "model_container")
            texture = self._character_related(character, "texture")
            executable = next((row for row in self.rows
                               if row["original_path"] == character["parent_path"] and row["offset"] is None), None)
            if model is None or texture is None or executable is None:
                raise ValueError("This resource lacks a verified model, texture, or executable association.")
            resource_id = int(character["original_path"].split("/")[-2])
            dialog = QProgressDialog("Loading static model", "Cancel", 0, 100, self)
            dialog.setWindowModality(Qt.WindowModality.WindowModal); dialog.setAutoClose(False); dialog.show()
            thread = QThread(self)
            worker = ModelWorker(self.catalogue, self._entry(executable), self._entry(model),
                                 self._entry(texture), resource_id)
            worker.moveToThread(thread); thread.started.connect(worker.run)
            # Worker signals must reach bound slots of GUI-thread objects: a lambda has no
            # receiver, so PySide would run it, and its widget repaints, in the worker thread.
            worker.progress.connect(self._model_progress_changed)
            dialog.canceled.connect(lambda: worker.request_cancel())
            worker.succeeded.connect(self._model_loaded)
            worker.failed.connect(self._model_failed)
            worker.cancelled.connect(self.model_status.setText)
            worker.finished.connect(thread.quit); worker.finished.connect(worker.deleteLater)
            thread.finished.connect(thread.deleteLater); thread.finished.connect(self._model_finished)
            self.model_thread, self.model_worker, self.model_progress = thread, worker, dialog
            thread.start()
        except Exception as exc:
            QMessageBox.critical(self, "Model preview failed", str(exc))

    @Slot(int, str)
    def _model_progress_changed(self, value, message):
        if self.model_progress:
            self.model_progress.setValue(value); self.model_progress.setLabelText(message)

    @Slot(str)
    def _model_failed(self, message):
        self.model_status.setText(f"Static preview unsupported: {message}")

    @Slot(object)
    def _model_loaded(self, result):
        model, images, clut_y, palettes, bits_per_pixel, clips, clip_error = result
        self.anim_timer.stop()
        self._anim_images, self._anim_texels_per_unit = tuple(images), 16 // bits_per_pixel
        qimages = tuple(ImageQt(image).copy() for image in images)
        self.model_view.set_model(model, qimages, clut_y)
        self.model_status.setText(
            f"Experimental static preview — {len(model.objects)} objects, {model.vertex_count} vertices, "
            f"{model.primitive_count} primitives / {model.triangle_count} triangles; verified palettes "
            f"{', '.join(map(str, palettes))}. {model.warnings[0]}")
        self._anim_clips = [clip for clip in clips if clip is not None]
        self.anim_clip.blockSignals(True); self.anim_clip.clear()
        for clip in self._anim_clips:
            self.anim_clip.addItem(f"Clip {clip.index} — {clip.frame_count} frames"
                                   f"{', loops' if clip.loops_forever else ''}", clip.index)
        self.anim_clip.blockSignals(False)
        enabled = bool(self._anim_clips)
        self.anim_clip.setEnabled(enabled)
        for widget in self._anim_widgets:
            widget.setEnabled(enabled)
        self._anim_player = None
        if clip_error:
            self.anim_status.setText(f"Animation clips unsupported: {clip_error}")
        elif enabled:
            self._select_clip(0)
        else:
            self.anim_status.setText("This model has no animation clips.")

    def _select_clip(self, index):
        if not 0 <= index < len(self._anim_clips):
            return
        self._anim_player = AnimationPlayer(self._anim_clips[index], len(self.model_view.model.skeleton))
        self._restart_animation()

    def _restart_animation(self, *_):
        if self._anim_player is None:
            return
        self._anim_player.reset()
        self._anim_events_seen, self._anim_offset = 0, [0.0, 0.0, 0.0]
        self._anim_working = [image.copy() for image in self._anim_images]
        self.model_view.set_palettes(tuple(ImageQt(image).copy() for image in self._anim_working))
        self._show_animation_pose()

    def _toggle_animation(self):
        if self.anim_timer.isActive():
            self.anim_timer.stop(); self.anim_play.setText("Play")
        elif self._anim_player is not None:
            if not self._anim_player.running:
                self._restart_animation()
            self.anim_timer.start(); self.anim_play.setText("Pause")

    def _step_animation(self):
        player = self._anim_player
        if player is None:
            return
        if not player.running:
            self.anim_timer.stop(); self.anim_play.setText("Play")
            return
        player.tick()
        if self.anim_root.isChecked():
            # applyRootMomentum rotates the root velocity by the root bone's matrix
            root = player.pose(0)
            matrix = StaticModelView._local_matrix(self._static_pose(root, translation=(0, 0, 0)))
            moved = matrix.map(QVector3D(*player.root_velocity))
            self._anim_offset = [self._anim_offset[0] + moved.x(), self._anim_offset[1] + moved.y(),
                                 self._anim_offset[2] + moved.z()]
        new_events = player.events[self._anim_events_seen:]
        self._anim_events_seen = len(player.events)
        copies = [event for event in new_events if isinstance(event, TextureEvent)]
        for event in copies:
            self._apply_texture_copy(event)
        if copies:
            self.model_view.set_palettes(tuple(ImageQt(image).copy() for image in self._anim_working))
        self._show_animation_pose()

    def _apply_texture_copy(self, event):
        # The game moves a VRAM rectangle (16-bit units) inside the model's texture; at 4 bpp a unit is 4 texels.
        unit = self._anim_texels_per_unit
        left, top = event.source[0] * unit, event.source[1]
        box = (left, top, left + event.size[0] * unit, top + event.size[1])
        target = (event.destination[0] * unit, event.destination[1])
        for image in self._anim_working:
            width, height = image.size
            if box[2] <= width and box[3] <= height and target[0] + box[2] - box[0] <= width \
                    and target[1] + box[3] - box[1] <= height:
                image.paste(image.crop(box), target)

    @staticmethod
    def _static_pose(pose, translation=None):
        angle = 2 * math.pi / 4096
        return StaticPose(Vector3(*(value / 4096 for value in pose.scale)),
                          Vector3(*(value * angle for value in pose.rotation)),
                          Vector3(*(float(value) for value in (translation or pose.translation))))

    def _show_animation_pose(self):
        player = self._anim_player
        poses = [self._static_pose(player.pose(bone)) for bone in range(player.bone_count)]
        poses[0] = self._static_pose(player.pose(0), translation=(0, 0, 0))
        self.model_view.set_pose(poses, tuple(self._anim_offset))
        sounds = sum(isinstance(event, SoundEvent) for event in player.events)
        copies = sum(isinstance(event, TextureEvent) for event in player.events)
        clip = player.clip
        state = "finished" if not player.running else ("playing" if self.anim_timer.isActive() else "paused")
        self.anim_status.setText(
            f"Clip {clip.index}: frame {player.frame}/{clip.frame_count}, tick {player.ticks} "
            f"({player.ticks / TICKS_PER_SECOND:.2f} s at an assumed {TICKS_PER_SECOND} ticks/s), {state}"
            f"{'; loops forever' if clip.loops_forever else ''}. Events so far: {sounds} sound cue(s) (not played), "
            f"{copies} texture copy(ies) applied. Stepping follows the game's integer momentum code.")

    @Slot()
    def _model_finished(self):
        if self.model_progress:
            self.model_progress.close(); self.model_progress.deleteLater()
        self.model_progress = None; self.model_worker = None; self.model_thread = None
        self.background_finished.emit()

    def _selected_row(self):
        index = self.file_table.currentRow()
        if index < 0:
            raise ValueError("Select a disc file first.")
        return self.rows[index]

    def _entry(self, row):
        extent_size = row["size"]
        if row["offset"] is not None:
            parent = next((candidate for candidate in self.rows
                           if candidate["original_path"] == row["parent_path"] and candidate["offset"] is None), None)
            if parent is None:
                raise ValueError("Parent resource is missing from the catalogue.")
            extent_size = parent["size"]
        return IsoEntry(row["original_path"], row["extent"], row["size"], False,
                        row["offset"] or 0, extent_size)

    def _read_row(self, row, limit=None):
        if limit is not None and row["size"] > limit:
            raise ValueError(f"File is {row['size']:,} bytes; preview limit is {limit:,} bytes.")
        with layout_from_catalogue(self.catalogue).open() as reader:
            entry = self._entry(row)
            return reader.read_extent_range(entry.extent, entry.extent_size, entry.offset, entry.size)

    def _read_row_prefix(self, row, limit):
        with layout_from_catalogue(self.catalogue).open() as reader:
            entry = self._entry(row)
            return reader.read_extent_range(entry.extent, entry.extent_size, entry.offset,
                                            min(entry.size, limit))

    def _export_selected(self):
        try:
            row = self._selected_row()
            path, _ = QFileDialog.getSaveFileName(self, "Export disc file", Path(row["original_path"]).name)
            if not path: return
            self._start_export(self._entry(row), Path(path))
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", str(exc))

    def _start_export(self, entry: IsoEntry, destination: Path):
        if self.export_thread and self.export_thread.isRunning():
            raise ValueError("Another export is already running.")
        if destination.exists():
            raise ValueError(f"Export already exists: {destination}")
        dialog = QProgressDialog(f"Exporting {entry.path}", "Cancel", 0, 100, self)
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setAutoClose(False); dialog.setValue(0); dialog.show()
        thread, worker = QThread(self), ExportWorker(self.catalogue, entry, destination)
        worker.moveToThread(thread); thread.started.connect(worker.run)
        worker.progress.connect(self._export_progress_changed)
        dialog.canceled.connect(lambda: worker.request_cancel())
        worker.succeeded.connect(self._export_succeeded)
        worker.failed.connect(self._export_failed)
        worker.cancelled.connect(self._export_cancelled)
        worker.finished.connect(thread.quit); worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater); thread.finished.connect(self._export_finished)
        self.export_thread, self.export_worker, self.export_progress = thread, worker, dialog
        thread.start()

    @Slot(int, str)
    def _export_progress_changed(self, value, message):
        if self.export_progress:
            self.export_progress.setValue(value); self.export_progress.setLabelText(message)

    @Slot(str)
    def _export_succeeded(self, path):
        self._export_message("Export complete", f"Exported to {path}")

    @Slot(str)
    def _export_failed(self, message):
        self._export_message("Export failed", message, critical=True)

    @Slot(str)
    def _export_cancelled(self, message):
        self._export_message("Export cancelled", message)

    def _export_message(self, title, message, critical=False):
        window = self.window()
        if isinstance(window, MainWindow) and window._close_pending:
            return
        function = QMessageBox.critical if critical else QMessageBox.information
        function(self, title, message)

    @Slot()
    def _export_finished(self):
        if self.export_progress:
            self.export_progress.close(); self.export_progress.deleteLater()
        self.export_progress = None; self.export_worker = None; self.export_thread = None
        self.background_finished.emit()

    def cancel_background(self):
        if self.export_worker:
            self.export_worker.request_cancel()
        if self.model_worker:
            self.model_worker.request_cancel()
        if self.relation_worker:
            self.relation_worker.request_cancel()

    def has_background_work(self):
        return bool((self.export_thread and self.export_thread.isRunning()) or
                    (self.model_thread and self.model_thread.isRunning()) or
                    (self.relation_thread and self.relation_thread.isRunning()))

    def _hex_selected(self):
        try:
            row = self._selected_row()
            data = self._read_row_prefix(row, HEX_LIMIT)
            lines = []
            for offset in range(0, len(data), 16):
                chunk = data[offset:offset+16]
                lines.append(f"{offset:08x}  {' '.join(f'{b:02x}' for b in chunk):47}  {''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)}")
            viewer = QPlainTextEdit(); viewer.setReadOnly(True); viewer.setPlainText("\n".join(lines))
            dialog = QMessageBox(self); dialog.setWindowTitle(f"Hex — {row['original_path']}")
            dialog.setText(f"Showing {len(data):,} of {row['size']:,} bytes (limit {HEX_LIMIT:,}).")
            dialog.layout().addWidget(viewer, 1, 0, 1, dialog.layout().columnCount()); dialog.exec()
        except Exception as exc:
            QMessageBox.critical(self, "Preview failed", str(exc))

    def _textures(self):
        page, layout = QWidget(), QVBoxLayout(); page.setLayout(layout)
        textures = [r for r in self.rows
                    if r["detected_format"] in {"TIM", "DW1 ALLTIM TIM slot"}]
        info = QLabel("TIM resources detected at file start plus confirmed ALLTIM fixed slots. Indexed palettes and PS1 STP are preserved in preview/export metadata." if textures else
                      "No supported TIM resources were identified. Unknown embedded/container textures are not guessed or decoded.")
        layout.addWidget(info)
        self.texture_table = QTableWidget(len(textures), 2); self.texture_rows = textures
        self.texture_table.setHorizontalHeaderLabels(["Path", "Status"])
        for i, row in enumerate(textures):
            self.texture_table.setItem(i, 0, QTableWidgetItem(row["original_path"])); self.texture_table.setItem(i, 1, QTableWidgetItem(row["status"]))
        self.texture_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        preview = QLabel("Select a TIM resource"); preview.setAlignment(Qt.AlignmentFlag.AlignCenter); preview.setMinimumSize(320, 240)
        self.texture_preview = preview
        self.texture_table.itemSelectionChanged.connect(self._preview_texture)
        export = QPushButton("Export selected PNG + provenance metadata…"); export.clicked.connect(self._export_texture)
        split = QSplitter(); split.addWidget(self.texture_table); split.addWidget(preview)
        layout.addWidget(split); layout.addWidget(export)
        return page

    def _selected_texture(self):
        index = self.texture_table.currentRow()
        if index < 0: raise ValueError("Select a TIM resource first.")
        return self.texture_rows[index]

    def _preview_texture(self):
        try:
            tim = decode_tim(self._read_row(self._selected_texture(), PREVIEW_LIMIT))
            pixmap = QPixmap.fromImage(ImageQt(tim.image))
            self.texture_preview.setPixmap(pixmap.scaled(self.texture_preview.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            self.texture_preview.setToolTip(f"{tim.image.width}×{tim.image.height}, {tim.bits_per_pixel} bpp, {tim.palette_count} palette(s)")
        except Exception as exc:
            self.texture_preview.setText(f"Unsupported or malformed TIM:\n{exc}")

    def _export_texture(self):
        try:
            row = self._selected_texture(); tim = decode_tim(self._read_row(row, PREVIEW_LIMIT))
            path, _ = QFileDialog.getSaveFileName(self, "Export PNG", Path(row["original_path"]).stem + ".png", "PNG image (*.png)")
            if not path: return
            export_png(tim, Path(path), row["id"], row["content_hash"],
                       provenance=self._resource_provenance(row))
            QMessageBox.information(self, "Texture exported", f"Wrote {path} and provenance JSON.")
        except Exception as exc:
            QMessageBox.critical(self, "Texture export failed", str(exc))

    def _executable(self):
        page, layout = QWidget(), QFormLayout(); page.setLayout(layout)
        meta = self.catalogue.meta()
        executable = next((r for r in self.rows if r["detected_format"] == "PS-X EXE"), None)
        layout.addRow("Executable", QLabel(meta.get("executable", "unknown")))
        layout.addRow("Format", QLabel("PS-X EXE" if executable else "not identified"))
        layout.addRow("Status", QLabel("Header identified; load address/section parsing, disassembly, and decompilation are not implemented."))
        layout.addRow("Decompilation", QLabel("No tool configured. This does not block disc inventory or texture work."))
        return page


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__(); self.setWindowTitle("DW1-coop Asset Workbench"); self.resize(1100, 720)
        self.stack = QStackedWidget(); self.setCentralWidget(self.stack)
        welcome = WelcomePage(); welcome.import_requested.connect(self.start_import); welcome.open_requested.connect(self.open_project)
        self.stack.addWidget(welcome)
        self.thread = self.worker = None
        self._close_pending = False
        self._import_page = self._import_bar = self._import_label = None

    def start_import(self):
        source, _ = QFileDialog.getOpenFileName(self, "Choose your Digimon World image", "", "Disc images (*.iso *.bin *.cue);;All files (*)")
        if not source: return
        project = QFileDialog.getExistingDirectory(self, "Choose an empty analysis-project directory")
        if not project: return
        page, layout = QWidget(), QVBoxLayout(); page.setLayout(layout)
        label, bar, cancel = QLabel("Preparing import…"), QProgressBar(), QPushButton("Cancel")
        layout.addStretch(); layout.addWidget(label); layout.addWidget(bar); layout.addWidget(cancel); layout.addStretch()
        self.stack.addWidget(page); self.stack.setCurrentWidget(page)
        self._import_page, self._import_bar, self._import_label = page, bar, label
        self.thread, self.worker = QThread(self), ImportWorker(Path(source), Path(project))
        self.worker.moveToThread(self.thread); self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self._import_progress_changed)
        cancel.clicked.connect(lambda: self._request_import_cancel(cancel, label))
        self.worker.succeeded.connect(self.show_workspace)
        self.worker.failed.connect(self.import_failed); self.worker.cancelled.connect(self.import_cancelled)
        self.worker.finished.connect(self.thread.quit); self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater); self.thread.finished.connect(self._import_thread_finished)
        self.thread.start()

    def _request_import_cancel(self, button=None, label=None):
        if self.worker:
            self.worker.request_cancel()
        if button:
            button.setEnabled(False)
        if label:
            label.setText("Cancelling safely…")

    @Slot(int, str)
    def _import_progress_changed(self, value, message):
        if self._import_bar:
            self._import_bar.setValue(value); self._import_label.setText(message)

    @Slot(str, str)
    def import_failed(self, message, detail):
        if self._close_pending:
            return
        QMessageBox.critical(self, "Import failed", message + "\n\nThe incomplete project records this failure and can be inspected or removed.")
        self.stack.setCurrentIndex(0)

    @Slot(str)
    def import_cancelled(self, message):
        if not self._close_pending:
            QMessageBox.information(self, "Import cancelled", message + "\n\nThe project records the import as cancelled.")
            self.stack.setCurrentIndex(0)

    @Slot()
    def _import_thread_finished(self):
        self.worker = None; self.thread = None
        self._maybe_finish_close()

    def open_project(self):
        path = QFileDialog.getExistingDirectory(self, "Open analysis project")
        if not path: return
        try: self.show_workspace(open_catalogue(Path(path)))
        except Exception as exc: QMessageBox.critical(self, "Could not open project", str(exc))

    @Slot(object)
    def show_workspace(self, catalogue):
        workspace = Workspace(catalogue, self)
        workspace.background_finished.connect(self._maybe_finish_close)
        self.stack.addWidget(workspace); self.stack.setCurrentWidget(workspace)

    def _active_workspaces(self):
        return [self.stack.widget(index) for index in range(self.stack.count())
                if isinstance(self.stack.widget(index), Workspace)]

    def _has_active_work(self):
        return bool(self.thread and self.thread.isRunning()) or any(page.has_background_work() for page in self._active_workspaces())

    @Slot()
    def _maybe_finish_close(self):
        if self._close_pending and not self._has_active_work():
            QTimer.singleShot(0, self.close)

    def closeEvent(self, event: QCloseEvent):
        if self._has_active_work():
            self._close_pending = True
            self._request_import_cancel()
            for workspace in self._active_workspaces():
                workspace.cancel_background()
            event.ignore()
            return
        event.accept()
