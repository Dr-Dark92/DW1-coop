from __future__ import annotations

import hashlib
import html
import io
import struct
import subprocess
import sys
import threading
import traceback
from pathlib import Path

from PIL import Image
from PIL.ImageQt import ImageQt
from PySide6.QtCore import QObject, QSettings, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QCloseEvent, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QProgressDialog, QPushButton,
    QScrollArea, QSplitter, QStackedWidget, QTableWidget, QTableWidgetItem, QTabWidget, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .app_icon import apply_icon, create_shortcuts
from .catalog import Catalogue, open_catalogue
from .characters import PARTNER_COUNT, STAGES, parse_character_table, parse_move_names
from .clip_names import clip_name
from .errors import Cancelled, DiscFormatError
from .iso9660 import IsoEntry, safe_export
from .live import model_tile
from .maps import (EXIT_COLOURS, FOLLOW_COLOURS, MAX_MAP_FILE, MAX_TFS_FILE, TRIGGER_COLOURS, export_background,
                   floor_grid_image, floor_grid_with_digimon, follow_description, layout_zones, map_exits, map_links,
                   parse_map, parse_map_table, parse_tfs, render_background, trigger_summary, zone_links)
from .relation_view import RelationView
from .animation import parse_animations
from .animation_view import ModelAnimationPanel, ModelLoad
from .items import parse_item_names
from .pathing import map_pathing, plan_route
from .play_panel import PlayPanel
from .disc_patch import build_patched_image
from .digimon_package import (BUNDLE_SUFFIX as DIGIMON_BUNDLE_SUFFIX, DigimonOverrides,
                              export_bundle as export_digimon_bundle, export_package as export_digimon_package,
                              load_source as load_digimon_source, prepare_bundle_import as prepare_digimon_bundle,
                              prepare_import as prepare_digimon_import)
from .characters import STAGES
from .hd_background import HdPictures
from .shared_data import app_settings
from .title_screens import TitleStore
from .zone_import import OverrideStore, check_package
from .zone_export import SUFFIX as ZONE_SUFFIX, MapSource, export_package, pathing_image, slug as zone_slug
from .mmd import parse_mmd_static, parse_usa_skeleton, validate_texture_materials
from .project import import_project, layout_from_catalogue, source_state
from .tim import decode_tim, export_png

HEX_LIMIT = 256 * 1024
PREVIEW_LIMIT = 64 * 1024 * 1024


APP_NAME = "DW1-settings"
LAST_PROJECT = "app/last_project"


def workbench_settings() -> QSettings:
    """What DW1-settings saves and DW1-play runs from."""
    return app_settings()


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
                 texture: IsoEntry, resource_id: int, label: str = ""):
        super().__init__()
        self.catalogue = catalogue
        self.executable, self.model, self.texture = executable, model, texture
        self.resource_id, self.label = resource_id, label
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
            imported = DigimonOverrides(self.catalogue.project_dir, self.catalogue.meta().get('source_sha256'))
            model_data = imported.file(self.resource_id, 'MMD') or model_data
            texture_data = imported.file(self.resource_id, 'TIM') or texture_data
            if self.cancel_event.is_set():
                raise Cancelled("Model preview cancelled.")
            self.progress.emit(70, "Validating geometry and materials")
            skeleton = parse_usa_skeleton(executable, self.resource_id)
            first = decode_tim(texture_data)
            palettes = tuple(decode_tim(texture_data, index).image for index in range(first.palette_count))
            try:
                model = parse_mmd_static(model_data, skeleton, self.cancel_event.is_set)
                used_palettes, geometry_error = validate_texture_materials(model, first), None
            except DiscFormatError as exc:
                # Clips do not depend on geometry, so they are still listed for unsupported models.
                model, used_palettes, geometry_error = None, (), str(exc)
            self.progress.emit(90, "Reading animation clips")
            try:
                if len(model_data) < 8:
                    raise DiscFormatError("MMD header is truncated.")
                animation_offset = struct.unpack_from("<I", model_data, 4)[0]
                clips, clip_error = tuple(parse_animations(model_data, animation_offset, len(skeleton),
                                                           self.cancel_event.is_set)), None
            except DiscFormatError as exc:
                clips, clip_error = (), str(exc)
            try:
                character = parse_character_table(executable)[self.resource_id]
                move_names = parse_move_names(executable)
                names = {clip.index: name for clip in clips if clip is not None
                         if (name := clip_name(clip.index, self.resource_id, character.moves, move_names))}
            except DiscFormatError:
                names = {}
            self.progress.emit(100, "Model ready")
            self.succeeded.emit(ModelLoad(self.label, model, geometry_error, palettes, first.clut_y, used_palettes,
                                          first.bits_per_pixel, len(skeleton), clips, clip_error, names))
        except Cancelled as exc:
            self.cancelled.emit(str(exc))
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()


class DigimonWorker(QObject):
    """Package I/O, parsing and staged writes run here; every GUI update uses bound slots."""
    progress = Signal(int, str)
    succeeded = Signal(object)
    failed = Signal(str)
    cancelled = Signal(str)
    finished = Signal()

    def __init__(self, catalogue, character_id, task, path=None, report=None, stages=()):
        super().__init__()
        self.catalogue, self.character_id, self.task = catalogue, character_id, task
        self.path, self.report, self.stages = path, report, stages
        self.cancel_event = threading.Event()

    def request_cancel(self):
        self.cancel_event.set()

    @Slot()
    def run(self):
        try:
            store = DigimonOverrides(self.catalogue.project_dir, self.catalogue.meta().get('source_sha256'))
            options = {'cancelled': self.cancel_event.is_set, 'progress': self.progress.emit}
            if self.task == 'export-bundle':
                result = export_digimon_bundle(self.path, self.catalogue, self.stages, store, **options)
            elif self.task == 'validate-bundle':
                result = prepare_digimon_bundle(self.path, self.catalogue, **options)
            elif self.task == 'apply-bundle':
                # Validate again so the bundle and the disc are exactly what was reviewed.
                fresh = prepare_digimon_bundle(self.path, self.catalogue, **options)
                if [(r.source.character, r.model, r.texture) for r in fresh.reports] != \
                        [(r.source.character, r.model, r.texture) for r in self.report.reports]:
                    raise ValueError('The bundle or its characters changed since review; validate it again.')
                store.apply_many(fresh.reports, **options)
                result = fresh
                try:
                    self.catalogue.log('INFO', f"Digimon bundle import: {len(fresh.changed)} of {len(fresh.reports)} "
                                       f"characters changed; {fresh.package}")
                except Exception as exc:
                    self.progress.emit(100, f'Import applied; catalogue log unavailable: {exc}')
            elif self.task == 'apply':
                fresh = load_digimon_source(self.catalogue, self.character_id, **options)
                if fresh.character != self.report.source.character or fresh.meta.get('source_sha256') != self.report.source.meta.get('source_sha256'):
                    raise ValueError('The selected character/source changed since validation; validate the package again.')
                result = store.apply(self.report, **options)
                try:
                    self.catalogue.log('INFO', f"Digimon {result.source.character.resource_id} import: "
                                       f"{', '.join(result.changed) or 'restored original'}; {result.package}")
                except Exception as exc:
                    # The atomic override index is already committed and retains full provenance.
                    self.progress.emit(100, f'Import applied; catalogue log unavailable: {exc}')
            else:
                source = load_digimon_source(self.catalogue, self.character_id, **options)
                if self.task == 'export':
                    result = export_digimon_package(self.path, source,
                        store.file(source.character.resource_id, 'MMD'), store.file(source.character.resource_id, 'TIM'),
                        **options)
                else:
                    result = prepare_digimon_import(self.path, source, **options)
            self.succeeded.emit((self.task, result))
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


class ZoneWorker(QObject):
    """Reads MAP/TFS files for zone export ("export") or computes pathing for every map ("pathing")."""
    progress = Signal(int, str)
    succeeded = Signal(object)
    failed = Signal(str)
    cancelled = Signal(str)
    finished = Signal()

    def __init__(self, catalogue: Catalogue, executable: IsoEntry, maps: dict, task: str, export: dict | None = None):
        super().__init__()
        self.catalogue, self.executable, self.maps, self.task, self.export = catalogue, executable, maps, task, export or {}
        self.cancel_event = threading.Event()

    def request_cancel(self):
        self.cancel_event.set()

    @Slot()
    def run(self):
        try:
            cancelled = self.cancel_event.is_set
            with layout_from_catalogue(self.catalogue).open() as reader:
                read = lambda item: reader.read_extent_range(item.extent, item.extent_size, item.offset, item.size,
                                                             cancelled=cancelled)
                entries = parse_map_table(read(self.executable))
                zones = set(self.export.get("zones", ()))
                scenes, sources, pathing = {}, {}, {}
                for done, (index, (map_item, tfs_item, map_row, tfs_row)) in enumerate(sorted(self.maps.items())):
                    if cancelled():
                        raise Cancelled("Cancelled.")
                    self.progress.emit(3 * done // len(self.maps) if self.task == "export" else 95 * done // len(self.maps),
                                       f"Reading {entries[index].name}")
                    imported = self.export.get("overrides", {}).get(index, {})
                    map_bytes = imported.get("MAP") or read(map_item)
                    if "MAP" in imported:
                        map_row = {**map_row, "content_hash": hashlib.sha256(map_bytes).hexdigest(),
                                   "size": len(map_bytes), "imported": True}
                    scene = scenes[index] = parse_map(map_bytes, entries[index])
                    if self.task == "pathing":
                        pathing[index] = map_pathing(entries[index], scene, self.export.get("map_names"), with_paths=False)
                    elif entries[index].area_name in zones:
                        tfs_bytes = imported.get("TFS") or (read(tfs_item) if tfs_item is not None else None)
                        if "TFS" in imported:
                            tfs_row = {**tfs_row, "content_hash": hashlib.sha256(tfs_bytes).hexdigest(),
                                       "size": len(tfs_bytes), "imported": True}
                        sources[index] = MapSource(entries[index], scene, parse_tfs(tfs_bytes) if tfs_bytes else None,
                                                   map_bytes, tfs_bytes, map_row, tfs_row)
            if self.task == "pathing":
                self.progress.emit(100, "Pathing ready")
                self.succeeded.emit(pathing)
                return
            options = self.export
            manifest = export_package(options["destination"], options["zones"], sources, scenes, entries,
                                      options["map_names"], options["character_names"], options["meta"],
                                      options.get("all_palettes", False), cancelled,
                                      lambda value, text: self.progress.emit(3 + value * 97 // 100, text))
            self.succeeded.emit(manifest)
        except Cancelled as exc:
            self.cancelled.emit(str(exc))
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()


class ZoneExportDialog(QDialog):
    """Choose the zones for a package: one zone, or several as a bundle."""

    def __init__(self, zones: dict[str, int], selected: str | None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export zones")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Pick one zone, or several for a bundle. The package keeps the original MAP/TFS "
                                "files, decoded data, pathing and routes between its maps."))
        self.search = QLineEdit(); self.search.setPlaceholderText("Filter zones"); layout.addWidget(self.search)
        self.list = QListWidget(); layout.addWidget(self.list, 1)
        for zone, count in sorted(zones.items()):
            item = QListWidgetItem(f"{zone} ({count} maps)"); item.setData(Qt.ItemDataRole.UserRole, zone)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if zone == selected else Qt.CheckState.Unchecked)
            self.list.addItem(item)
        self.search.textChanged.connect(lambda text: [self.list.item(row).setHidden(
            text.lower() not in self.list.item(row).text().lower()) for row in range(self.list.count())])
        self.palettes = QCheckBox("Render backgrounds in every palette (larger; the original TFS is always included)")
        layout.addWidget(self.palettes)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Export…")
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)
        self.resize(480, 560)

    def zones(self) -> list[str]:
        return [self.list.item(row).data(Qt.ItemDataRole.UserRole) for row in range(self.list.count())
                if self.list.item(row).checkState() == Qt.CheckState.Checked]


class ZoneImportDialog(QDialog):
    """Shows what a package would change before anything is stored."""

    def __init__(self, report, current: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Import {report.path.name}")
        layout = QVBoxLayout(self)
        manifest = report.manifest
        summary = QLabel(f"{manifest.get('kind', 'zone').capitalize()} with {len(manifest.get('zones', []))} zone(s), "
                         f"{len(report.maps)} map(s), made {manifest.get('created', '?')} from "
                         f"{(manifest.get('source') or {}).get('release', 'an unknown release')}. "
                         f"{len(report.changed_maps)} map(s) differ from the disc."
                         + (f" {sum(1 for item in report.maps if item.skipped)} map(s) can't be imported and stay as they are."
                            if any(item.skipped for item in report.maps) else ""))
        summary.setWordWrap(True); layout.addWidget(summary)
        table = QTableWidget(len(report.maps), 4)
        table.setHorizontalHeaderLabels(["Map", "MAP", "TFS", "Checks"])
        table.verticalHeader().setVisible(False); table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        for row, item in enumerate(report.maps):
            checks = item.skipped or "; ".join(item.problems) or "; ".join(item.notes) or "ok"
            if item.index in current and not item.changed and not item.skipped:
                checks += "; replaces the earlier import with the disc's files"
            for column, value in enumerate((item.label, item.status.get("MAP", "—"), item.status.get("TFS", "—"), checks)):
                cell = QTableWidgetItem(value)
                if item.problems:
                    cell.setForeground(Qt.GlobalColor.red)
                elif item.skipped:
                    cell.setForeground(Qt.GlobalColor.darkYellow)
                table.setItem(row, column, cell)
        layout.addWidget(table, 1)
        if report.problems:
            problems = QLabel("Package problems:\n" + "\n".join(f"• {problem}" for problem in report.problems))
            problems.setStyleSheet("color: #a33"); problems.setWordWrap(True); layout.addWidget(problems)
        note = QLabel("Imported files are kept in the project's overrides folder; the disc image is never changed. "
                      "Files must fit the sectors of the originals they replace.")
        note.setWordWrap(True); layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Import")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(report.ok)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)
        self.resize(760, 520)


class ImportedZonesDialog(QDialog):
    def __init__(self, overrides, parent=None):
        super().__init__(parent)
        self.overrides = overrides
        self.setWindowTitle("Imported zones")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Maps that use imported files instead of the disc's. Removing one goes back to the disc."))
        self.list = QListWidget(); layout.addWidget(self.list, 1)
        remove = QPushButton("Remove selected"); remove.clicked.connect(self._remove)
        close = QPushButton("Close"); close.clicked.connect(self.accept)
        row = QHBoxLayout(); row.addWidget(remove); row.addStretch(); row.addWidget(close); layout.addLayout(row)
        self._fill(); self.resize(620, 360)

    def _fill(self):
        self.list.clear()
        for index, record in sorted(self.overrides.records().items()):
            item = QListWidgetItem(f"{record['label']} — {', '.join(record['files'])} from {record['package']} "
                                   f"({record['imported']})")
            item.setData(Qt.ItemDataRole.UserRole, index); self.list.addItem(item)
        if not self.list.count():
            self.list.addItem("No imported zones.")

    def _remove(self):
        item = self.list.currentItem()
        if item is not None and item.data(Qt.ItemDataRole.UserRole) is not None:
            self.overrides.remove(item.data(Qt.ItemDataRole.UserRole)); self._fill()


class WelcomePage(QWidget):
    import_requested = Signal()
    open_requested = Signal()

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.addStretch()
        title = QLabel(APP_NAME)
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
        self.overrides = OverrideStore(catalogue.project_dir)
        self.title_store = TitleStore(self.overrides.directory)
        self.hd_pictures = HdPictures(self.overrides.directory / "hd")
        self.digimon_overrides = DigimonOverrides(catalogue.project_dir, catalogue.meta().get('source_sha256'))
        self.digimon_thread = self.digimon_worker = self.digimon_progress = None
        self._digimon_result = None
        self.export_thread: QThread | None = None
        self.export_worker: ExportWorker | None = None
        self.export_progress: QProgressDialog | None = None
        self.model_thread: QThread | None = None
        self.model_worker: ModelWorker | None = None
        self.model_progress: QProgressDialog | None = None
        self._model_panel = None
        self._pending_model = None  # (character, panel) to load once the running load stops
        self._closing = False
        self.relation_thread: QThread | None = None
        self.relation_worker: RelationWorker | None = None
        self.relation_progress: QProgressDialog | None = None
        self.zone_thread: QThread | None = None
        self.zone_worker: ZoneWorker | None = None
        self.zone_progress: QProgressDialog | None = None
        self._all_pathing = None
        self._map_pathing = None
        self.tabs = QTabWidget()
        self.tabs.addTab(self._overview(), "Overview")
        self.tabs.addTab(self._disc_files(), "Disc Files")
        self.tabs.addTab(self._characters(), "Characters and Digimon")
        self.tabs.addTab(self._animations(), "Animations")
        self.tabs.addTab(self._textures(), "Textures")
        self.maps_page = self._maps()
        self.tabs.addTab(self.maps_page, "Maps and Zones")
        self.tabs.addTab(self._executable(), "Executable Analysis")
        self.play_panel = PlayPanel(catalogue.meta(), *source_state(catalogue), self._map_names(), self._decoded_grid,
                                    self._character_names(), self._item_names(),
                                    (lambda: len(self.overrides.records()) + len(self.digimon_overrides.records()) + self.title_store.count(),
                                     self._build_patched_disc, self._imported_forms), self.hd_pictures.directory)
        self.play_panel.show_map_requested.connect(self.show_map)
        self.tabs.addTab(self.play_panel, "Play")
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

    def _animations(self):
        page, layout = QWidget(), QVBoxLayout(); page.setLayout(layout)
        animated = [row for row in self.rows if row["detected_format"] == "DW1 character resource entry"
                    and self._character_related(row, "model_container") is not None]
        if not animated:
            layout.addWidget(QLabel("No verified character models are catalogued, so there are no animation clips to show."))
            layout.addStretch()
            return page
        info = QLabel("Animation clips decoded from each Digimon's MMD and played with the game's frame-by-frame "
                      "stepping; all 4,843 clips of the 178 models match an independent converter. Select a Digimon "
                      "to load it, then pick a clip. Clip names come from how the game code uses each animation ID "
                      "(hover a row for the evidence); technique clips use the game's technique names. Sound cues are "
                      "counted, not played. Models whose geometry is unsupported list their clips only.")
        info.setWordWrap(True); layout.addWidget(info)
        self.animation_search = QLineEdit(); self.animation_search.setPlaceholderText("Find a Digimon (e.g. Agumon, AGUM)")
        self.animation_search.setClearButtonEnabled(True); self.animation_search.textChanged.connect(self._filter_animation_models)
        self.animation_stage = self._class_filter(self._filter_animation_models)
        self.animation_partner = QCheckBox("Partner range only (IDs 1–65)")
        self.animation_partner.setToolTip("Resource IDs below dw_decomp's NUM_PARTNER_DIGIMON (66); ID 0 is the player character.")
        self.animation_partner.toggled.connect(self._filter_animation_models)
        self.animation_models = QTableWidget(len(animated), 4)
        self.animation_models.setHorizontalHeaderLabels(["ID", "Display name", "Internal name", "Stage"])
        self.animation_models.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.animation_models.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.animation_models.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.animation_models.verticalHeader().setVisible(False)
        header = self.animation_models.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._animation_rows = animated
        stages = self._character_stages()
        for index, row in enumerate(animated):
            label = self.labels.get((row["id"], "display_name"))
            values = (row["original_path"].split("/")[-2], label["value"] if label else "not recorded",
                      row["original_path"].split("/")[-1], self._class_title(stages.get(row["id"])))
            for column, value in enumerate(values):
                self.animation_models.setItem(index, column, QTableWidgetItem(value))
        self.animation_models.currentCellChanged.connect(self._animation_model_selected)
        self.animation_panel = ModelAnimationPanel(clip_table=True)
        self.animation_panel.model_status.setText("Select a Digimon to load its model and animation clips.")
        left = QWidget(); left_layout = QVBoxLayout(left); left_layout.setContentsMargins(0, 0, 0, 0)
        animation_bundle = QPushButton("Export class bundle…")
        animation_bundle.setToolTip("Export every Digimon of the selected class (models with their animations and "
                                    "textures) as one .dw1digimons bundle.")
        animation_bundle.clicked.connect(lambda: self._export_digimon_bundle(self.animation_stage.currentData()))
        filters = QHBoxLayout(); filters.addWidget(self.animation_stage); filters.addWidget(self.animation_partner)
        filters.addWidget(animation_bundle)
        left_layout.addWidget(self.animation_search); left_layout.addLayout(filters)
        left_layout.addWidget(self.animation_models)
        split = QSplitter(); split.addWidget(left); split.addWidget(self.animation_panel)
        split.setStretchFactor(1, 3); split.setSizes([420, 880])
        layout.addWidget(split, 1)
        return page

    def _animation_model_selected(self, row, *_):
        if 0 <= row < len(self._animation_rows):
            self._load_character_model(self._animation_rows[row], self.animation_panel, show_dialog=False)

    def _filter_animation_models(self, *_):
        needle = self.animation_search.text().strip().lower()
        chosen = self.animation_stage.currentData()
        partner_only = self.animation_partner.isChecked()
        stages = self._character_stages()
        for index in range(self.animation_models.rowCount()):
            cells = [self.animation_models.item(index, column).text() for column in range(4)]
            visible = (not needle or any(needle in cell.lower() for cell in cells[:3]))
            visible &= self._class_matches(chosen, stages.get(self._animation_rows[index]["id"]))
            visible &= not partner_only or 0 < int(cells[0]) < PARTNER_COUNT
            self.animation_models.setRowHidden(index, not visible)

    ALL_CLASSES, NO_CLASS = "All classes", "No class"

    @staticmethod
    def _class_title(stage):
        """How an evolution stage is shown: Fresh is the newborn class."""
        return {"Fresh": "Newborn (Fresh)"}.get(stage, stage) if stage else "—"

    def _class_filter(self, changed):
        """Class (evolution-stage) picker; item data is the catalogue stage, or a marker for all / none."""
        box = QComboBox()
        box.addItem(self.ALL_CLASSES, self.ALL_CLASSES)
        for stage in STAGES.values():
            box.addItem(self._class_title(stage), stage)
        box.addItem(self.NO_CLASS, self.NO_CLASS)
        box.currentIndexChanged.connect(changed)
        return box

    @classmethod
    def _class_matches(cls, chosen, stage):
        return chosen == cls.ALL_CLASSES or (stage or cls.NO_CLASS) == chosen

    def _character_stages(self):
        """Stage per character row: catalogue label, or the executable's level byte for older projects."""
        if getattr(self, "_stages", None) is None:
            rows = [row for row in self.rows if row["detected_format"] == "DW1 character resource entry"]
            self._stages = {row["id"]: label["value"] for row in rows
                            if (label := self.labels.get((row["id"], "stage")))}
            if rows and not self._stages:
                try:
                    executable = next(r for r in self.rows if r["original_path"] == rows[0]["parent_path"]
                                      and r["offset"] is None)
                    entries = {entry.resource_id: entry for entry in
                               parse_character_table(self._read_row(executable, PREVIEW_LIMIT))}
                    self._stages = {row["id"]: entries[int(row["original_path"].split("/")[-2])].stage for row in rows}
                except Exception:
                    self._stages = {}
        return self._stages

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
        self.character_table = QTableWidget(len(characters), 6)
        self.character_table.setHorizontalHeaderLabels(["Resource ID", "Display name", "Internal name", "Stage",
                                                        "Model container", "Texture resource"])
        stages = self._character_stages()
        for index, row in enumerate(characters):
            resource_id = row["original_path"].split("/")[-2]
            name = row["original_path"].split("/")[-1]
            label = self.labels.get((row["id"], "display_name"))
            related = {association["kind"]: self.rows_by_id[association["target_id"]]
                       for association in self.associations
                       if association["source_id"] == row["id"] and association["target_id"] in self.rows_by_id}
            model = related.get("model_container")
            texture = related.get("texture")
            values = (resource_id, label["value"] if label else "not recorded", name, self._class_title(stages.get(row["id"])),
                      model["original_path"] if model else "unsupported / absent",
                      texture["original_path"] if texture else "unavailable")
            for column, value in enumerate(values):
                self.character_table.setItem(index, column, QTableWidgetItem(str(value)))
        self.character_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.character_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.character_preview = QLabel("Select a verified resource entry")
        self.character_preview.setAlignment(Qt.AlignmentFlag.AlignCenter); self.character_preview.setMinimumSize(320, 240)
        self.character_details = QPlainTextEdit(); self.character_details.setReadOnly(True); self.character_details.setMaximumHeight(170)
        self.character_table.itemSelectionChanged.connect(self._preview_character)
        export = QPushButton("Export texture PNG + metadata…")
        export.clicked.connect(self._export_character_texture)
        package_export = QPushButton('Export Digimon package…')
        package_export.clicked.connect(self._export_digimon)
        package_import = QPushButton('Import Digimon package…')
        package_import.clicked.connect(self._import_digimon)
        self.character_stage = self._class_filter(self._filter_characters)
        bundle_export = QPushButton('Export class bundle…')
        bundle_export.setToolTip('Export every Digimon of the class chosen in the filter as one .dw1digimons bundle.')
        bundle_export.clicked.connect(lambda: self._export_digimon_bundle(self.character_stage.currentData()))
        restore = QPushButton('Restore selected Digimon from disc')
        restore.clicked.connect(self._restore_digimon)
        model_page = ModelAnimationPanel("Load model and animations")
        model_page.load_requested.connect(self._load_character_model)
        self.character_panel = model_page
        texture_page = QWidget(); texture_layout = QVBoxLayout(texture_page)
        texture_layout.addWidget(self.character_preview); texture_layout.addWidget(self.character_details)
        preview_tabs = QTabWidget(); preview_tabs.addTab(model_page, "Model and animation"); preview_tabs.addTab(texture_page, "Texture")
        right = QWidget(); right_layout = QVBoxLayout(right)
        right_layout.addWidget(preview_tabs)
        left = QWidget(); left_layout = QVBoxLayout(left); left_layout.setContentsMargins(0, 0, 0, 0)
        class_row = QHBoxLayout(); class_row.addWidget(QLabel("Class")); class_row.addWidget(self.character_stage, 1)
        left_layout.addLayout(class_row); left_layout.addWidget(self.character_table)
        split = QSplitter(); split.addWidget(left); split.addWidget(right)
        layout.addWidget(split, 1)
        actions = QHBoxLayout()
        for button in (export, package_export, bundle_export, package_import, restore):
            actions.addWidget(button)
        layout.addLayout(actions)
        return page

    def _filter_characters(self, *_):
        chosen = self.character_stage.currentData()
        stages = self._character_stages()
        for index, row in enumerate(self.character_rows):
            self.character_table.setRowHidden(index, not self._class_matches(chosen, stages.get(row["id"])))

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
            resource_id = int(character['original_path'].split('/')[-2])
            tim = decode_tim(self.digimon_overrides.file(resource_id, 'TIM') or self._read_row(texture, PREVIEW_LIMIT))
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
                f"imported override: {self.digimon_overrides.records().get(resource_id, 'none')}",
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
            resource_id = int(character['original_path'].split('/')[-2])
            data = self.digimon_overrides.file(resource_id, 'TIM') or self._read_row(texture, PREVIEW_LIMIT)
            tim = decode_tim(data)
            name = character["original_path"].split("/")[-1]
            path, _ = QFileDialog.getSaveFileName(self, "Export associated texture", f"{name}-{texture['offset']//0x4800:03d}.png", "PNG image (*.png)")
            if not path: return
            export_png(tim, Path(path), texture["id"], hashlib.sha256(data).hexdigest(),
                       provenance=self._resource_provenance(texture))
            QMessageBox.information(self, "Texture exported", f"Wrote {path} and provenance JSON.")
        except Exception as exc:
            QMessageBox.critical(self, "Texture export failed", str(exc))

    def _export_digimon(self):
        try:
            character = self._selected_character()
            name = character['original_path'].split('/')[-1]
            path, _ = QFileDialog.getSaveFileName(self, 'Export native Digimon package', f'{name}.dw1digimon',
                                                 'DW1 Digimon package (*.dw1digimon)')
            if path:
                self._start_digimon('export', Path(path))
        except Exception as exc:
            QMessageBox.critical(self, 'Digimon export failed', str(exc))

    def _export_digimon_bundle(self, chosen):
        if chosen == self.NO_CLASS:
            QMessageBox.information(self, 'Export class bundle', 'Choose a class (Newborn to Ultimate) or All classes '
                                    'in the filter; characters without a class are not exported as a bundle.')
            return
        stages = tuple(STAGES.values()) if chosen == self.ALL_CLASSES else (chosen,)
        name = 'all-classes' if len(stages) > 1 else {'Fresh': 'newborn'}.get(chosen, chosen.lower())
        path, _ = QFileDialog.getSaveFileName(self, 'Export Digimon class bundle', f'{name}{DIGIMON_BUNDLE_SUFFIX}',
                                              f'DW1 Digimon bundle (*{DIGIMON_BUNDLE_SUFFIX})')
        if path:
            try:
                self._start_digimon('export-bundle', Path(path), stages=stages)
            except Exception as exc:
                QMessageBox.critical(self, 'Digimon bundle export failed', str(exc))

    def _import_digimon(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Import Digimon package or bundle', '',
                                             f'DW1 Digimon package or bundle (*.dw1digimon *{DIGIMON_BUNDLE_SUFFIX})')
        if path:
            try:
                bundle = path.endswith(DIGIMON_BUNDLE_SUFFIX)
                self._start_digimon('validate-bundle' if bundle else 'validate', Path(path))
            except Exception as exc:
                QMessageBox.critical(self, 'Digimon import failed', str(exc))

    def _start_digimon(self, task, path=None, report=None, stages=()):
        if self.digimon_thread is not None:
            raise ValueError('Another Digimon package operation is still finishing.')
        character_id = None if task.endswith('bundle') else self._selected_character()['id']
        dialog = QProgressDialog('Processing Digimon package', 'Cancel', 0, 100, self)
        dialog.setWindowModality(Qt.WindowModality.WindowModal); dialog.setAutoClose(False); dialog.show()
        thread = QThread(self)
        worker = DigimonWorker(self.catalogue, character_id, task, path, report, stages)
        worker.moveToThread(thread); thread.started.connect(worker.run)
        dialog.canceled.connect(lambda: worker.request_cancel())
        worker.progress.connect(self._digimon_progress_changed)
        worker.succeeded.connect(self._digimon_succeeded)
        worker.failed.connect(self._digimon_failed)
        worker.cancelled.connect(self._digimon_cancelled)
        worker.finished.connect(thread.quit); worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater); thread.finished.connect(self._digimon_finished)
        self.digimon_thread, self.digimon_worker, self.digimon_progress = thread, worker, dialog
        self._digimon_result = None
        thread.start()

    @Slot(int, str)
    def _digimon_progress_changed(self, value, text):
        self.digimon_progress.setValue(value); self.digimon_progress.setLabelText(text)

    @Slot(object)
    def _digimon_succeeded(self, result):
        self._digimon_result = result

    @Slot(str)
    def _digimon_failed(self, text):
        if not self._closing:
            QMessageBox.critical(self, 'Digimon operation failed', text)

    @Slot(str)
    def _digimon_cancelled(self, text):
        if not self._closing:
            QMessageBox.information(self, 'Digimon operation cancelled', text)

    @Slot()
    def _digimon_finished(self):
        self.digimon_progress.close(); self.digimon_progress.deleteLater()
        self.digimon_thread = self.digimon_worker = self.digimon_progress = None
        result, self._digimon_result = self._digimon_result, None
        if result is not None and not self._closing:
            task, value = result
            if task == 'export-bundle':
                path, count, skipped = value
                text = f'Wrote {path}\n{count} Digimon packaged.'
                if skipped:
                    text += (f'\n{len(skipped)} skipped (listed in bundle.json): '
                             + ', '.join(item['character'].rsplit('/', 1)[-1] for item in skipped))
                QMessageBox.information(self, 'Digimon bundle exported', text)
            elif task == 'validate-bundle':
                self._review_digimon_bundle(value)
            elif task == 'apply-bundle':
                self._digimon_changed()
                QMessageBox.information(self, 'Digimon bundle imported',
                                        f'{len(value.changed)} of {len(value.reports)} characters now use imported '
                                        'resources; the others use the disc originals. Enable “Play with imported '
                                        'resources” to play them.')
            elif task == 'validate':
                dialog = QDialog(self); dialog.setWindowTitle('Review Digimon import')
                layout = QVBoxLayout(dialog)
                summary = QLabel(f"ID {value.source.character.resource_id} — {value.source.character.internal_name}\n"
                                 f"Changes from disc: {', '.join(value.changed) or 'none (restores original)'}\n"
                                 + '\n'.join(value.notes))
                summary.setWordWrap(True); layout.addWidget(summary)
                preview = QLabel(); pixmap = QPixmap.fromImage(ImageQt(decode_tim(value.texture).image))
                preview.setPixmap(pixmap.scaled(512, 256, Qt.AspectRatioMode.KeepAspectRatio))
                layout.addWidget(preview)
                layout.addWidget(QLabel('Stored in project overrides. Enable “Play with imported resources” to play it.\n'
                                       'Reload the character or start normal play; old save states may retain old assets.'))
                buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
                buttons.button(QDialogButtonBox.StandardButton.Ok).setText('Apply import')
                buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject); layout.addWidget(buttons)
                if dialog.exec() == QDialog.DialogCode.Accepted:
                    self._start_digimon('apply', report=value)
            elif task == 'apply':
                self._digimon_changed()
                QMessageBox.information(self, 'Digimon imported', 'Preview uses the imported resources. Enable '
                                        '“Play with imported resources” to use them on a patched disc copy.')
            else:
                QMessageBox.information(self, 'Digimon exported', f'Wrote {value}')
        self.background_finished.emit()

    def _review_digimon_bundle(self, value):
        dialog = QDialog(self); dialog.setWindowTitle('Review Digimon bundle import'); dialog.resize(640, 480)
        layout = QVBoxLayout(dialog)
        stages = ', '.join(value.manifest.get('stages') or []) or 'unknown'
        summary = QLabel(f"{value.package}: {len(value.reports)} Digimon ({stages}); {len(value.changed)} differ from "
                         "the disc. Applying makes every listed character match the bundle: unchanged members drop "
                         "any earlier import of that character.")
        summary.setWordWrap(True); layout.addWidget(summary)
        table = QTableWidget(len(value.reports), 3)
        table.setHorizontalHeaderLabels(['ID', 'Digimon', 'Changes from disc'])
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        for row, report in enumerate(value.reports):
            character = report.source.character
            for column, text in enumerate((str(character.resource_id),
                                           character.display_name or character.internal_name,
                                           ', '.join(report.changed) or 'none')):
                table.setItem(row, column, QTableWidgetItem(text))
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(table, 1)
        skipped = value.manifest.get('skipped') or []
        if skipped:
            note = QLabel(f"Not in the bundle (unsupported when exported): "
                          + ', '.join(str(item.get('character', '')).rsplit('/', 1)[-1] for item in skipped))
            note.setWordWrap(True); layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('Apply bundle')
        buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject); layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._start_digimon('apply-bundle', value.path, report=value)

    def _restore_digimon(self):
        try:
            character = self._selected_character()
            resource_id = int(character['original_path'].split('/')[-2])
            self.digimon_overrides.remove(resource_id)
            self.catalogue.log('INFO', f'Digimon {resource_id}: restored original disc resources')
            self._digimon_changed()
        except Exception as exc:
            QMessageBox.critical(self, 'Cannot restore Digimon', str(exc))

    def _digimon_changed(self):
        self.play_panel.refresh_imports()
        self._preview_character()
        self._load_character_model(show_dialog=False)

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
                            f"{legend}<br>exit slots: {exits}<br>Digimon placements: "
                            f"{swatch(FOLLOW_COLOURS['chases you'])} chases you (box: where it notices you) "
                            f"{swatch(FOLLOW_COLOURS['watches you'])} watches you "
                            f"{swatch(FOLLOW_COLOURS['ignores you'])} ignores you")
        floor_note.setWordWrap(True); floor_note.setTextFormat(Qt.TextFormat.RichText)
        floor_layout.addWidget(floor_note); floor_layout.addWidget(self.map_floor, 1)
        self.map_preview_tabs = QTabWidget()
        self.map_preview_tabs.addTab(background_page, "Background"); self.map_preview_tabs.addTab(scene_page, "Scene data")
        self.map_preview_tabs.addTab(floor_page, "Floor grid")
        self.map_preview_tabs.addTab(self._pathing_page(), "Pathing")
        tree_panel = QWidget(); tree_layout = QVBoxLayout(tree_panel); tree_layout.setContentsMargins(0, 0, 0, 0)
        tree_layout.addWidget(self.map_search); tree_layout.addWidget(self.map_tree)
        split = QSplitter(); split.addWidget(tree_panel); split.addWidget(self.map_preview_tabs)
        split.setStretchFactor(1, 3); split.setSizes([360, 940])
        export = QPushButton("Export background PNG + provenance metadata…")
        export.clicked.connect(self._export_map_background)
        browser = QWidget(); browser_layout = QVBoxLayout(browser); browser_layout.setContentsMargins(0, 0, 0, 0)
        export_zone = QPushButton("Export zone or bundle…"); export_zone.clicked.connect(self._export_zones)
        import_zone = QPushButton("Import zone or bundle…"); import_zone.clicked.connect(self._import_zones)
        self.imported_button = QPushButton(f"Imported zones ({len(self.overrides.records())})…")
        self.imported_button.clicked.connect(self._manage_overrides)
        export_row = QHBoxLayout()
        set_hd = QPushButton("Set HD picture…"); set_hd.clicked.connect(self._set_hd_picture)
        set_hd.setToolTip("Show a full-size picture for this map in the player instead of its 8-bit tiles. "
                          "It must line up with the map's background.")
        remove_hd = QPushButton("Remove HD picture"); remove_hd.clicked.connect(self._remove_hd_picture)
        for button in (export, export_zone, import_zone, self.imported_button, set_hd, remove_hd):
            export_row.addWidget(button)
        browser_layout.addWidget(split, 1); browser_layout.addLayout(export_row)
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
    def _pathing_page(self):
        page = QWidget(); layout = QVBoxLayout(page)
        note = QLabel("Walkable areas in colour (the game blocks tiles whose value has bit 7 set). Routes go from each "
                      "spawn point (where you arrive, cyan) to each walk-through exit (red) and toilet (violet); "
                      "distances are in tiles. Script-driven exits are not decoded and are not followed.")
        note.setWordWrap(True); layout.addWidget(note)
        row = QHBoxLayout()
        self.path_from, self.path_to = QComboBox(), QComboBox()
        for combo in (self.path_from, self.path_to):
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
            combo.currentIndexChanged.connect(self._show_pathing)
        row.addWidget(QLabel("From")); row.addWidget(self.path_from); row.addWidget(QLabel("to")); row.addWidget(self.path_to)
        row.addStretch(); layout.addLayout(row)
        self.path_result = QLabel(); self.path_result.setWordWrap(True); layout.addWidget(self.path_result)
        self.path_image = QLabel(); self.path_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.path_image, 1)
        planner = QHBoxLayout()
        self.route_target = QComboBox(); self.route_target.setEditable(True)
        self.route_target.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        for index, label in sorted(self._map_names().items(), key=lambda item: item[1]):
            self.route_target.addItem(label, index)
        plan = QPushButton("Plan route from this map"); plan.clicked.connect(self._plan_route)
        planner.addWidget(QLabel("Walk to")); planner.addWidget(self.route_target, 1); planner.addWidget(plan)
        layout.addLayout(planner)
        self.route_text = QPlainTextEdit(); self.route_text.setReadOnly(True); self.route_text.setMaximumHeight(140)
        layout.addWidget(self.route_text)
        return page

    def _update_pathing(self, entry, scene):
        self._map_pathing = (scene, map_pathing(entry, scene, self._map_names()))
        _, pathing = self._map_pathing
        for combo, places in ((self.path_from, [p for p in pathing.places if p.kind == "spawn"]),
                              (self.path_to, [p for p in pathing.places if p.kind != "spawn"])):
            combo.blockSignals(True); combo.clear()
            for place in places:
                combo.addItem(place.label + (" — on a blocked tile (unused?)" if place.blocked else ""), place.id)
            combo.blockSignals(False)
        self._show_pathing()

    def _show_pathing(self, *_):
        if self._map_pathing is None:
            return
        scene, pathing = self._map_pathing
        route = pathing.route(self.path_from.currentData(), self.path_to.currentData())
        if self.path_to.count() == 0:
            text = "This map has no walk-through exits or toilets; it is left through map scripts."
        elif route is None:
            text = "No route: the spawn is on a blocked tile."
        elif route.distance is None:
            text = "Unreachable: that spawn is in a separate walkable area (the game connects them with a script)."
        else:
            text = f"{route.distance:g} tiles, {len(route.path)} steps."
        sizes = ", ".join(map(str, pathing.region_sizes))
        self.path_result.setText(f"{text}  Walkable areas: {len(pathing.region_sizes)} ({sizes} tiles).")
        self.path_image.setPixmap(QPixmap.fromImage(ImageQt(pathing_image(scene, pathing, highlight=route))))

    def _zone_maps(self, zones: set[str] | None = None) -> tuple[IsoEntry, dict]:
        executable = next(row for row in self.rows if row["detected_format"] == "PS-X EXE" and row["offset"] is None)
        maps = {}
        for row in self.map_rows.values():
            scene_row, tfs_row = self._character_related(row, "scene"), self._character_related(row, "background")
            if scene_row is not None:
                maps[int(row["original_path"].rsplit("/", 2)[1])] = (
                    self._entry(scene_row), self._entry(tfs_row) if tfs_row is not None else None,
                    dict(scene_row), dict(tfs_row) if tfs_row is not None else None)
        return self._entry(executable), maps

    def _start_zone_worker(self, task: str, title: str, export: dict | None, on_success):
        if self.zone_thread and self.zone_thread.isRunning():
            raise ValueError("A zone export or route planning is already running.")
        executable, maps = self._zone_maps()
        dialog = QProgressDialog(title, "Cancel", 0, 100, self)
        dialog.setWindowModality(Qt.WindowModality.WindowModal); dialog.setAutoClose(False); dialog.show()
        export = {**(export or {}), "overrides": self._override_files()}
        thread, worker = QThread(self), ZoneWorker(self.catalogue, executable, maps, task, export)
        worker.moveToThread(thread); thread.started.connect(worker.run)
        worker.progress.connect(self._zone_progress_changed)
        dialog.canceled.connect(lambda: worker.request_cancel())
        worker.succeeded.connect(on_success)
        worker.failed.connect(self._zone_failed); worker.cancelled.connect(self._zone_cancelled)
        worker.finished.connect(thread.quit); worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater); thread.finished.connect(self._zone_finished)
        self.zone_thread, self.zone_worker, self.zone_progress = thread, worker, dialog
        thread.start()

    def _plan_route(self):
        if self._all_pathing is None:
            try:
                self._start_zone_worker("pathing", "Computing pathing for every map", {"map_names": self._map_names()},
                                        self._all_pathing_ready)
            except Exception as exc:
                QMessageBox.critical(self, "Route planning failed", str(exc))
            return
        try:
            start = int(self._selected_map()["original_path"].rsplit("/", 2)[1])
        except ValueError as exc:
            self.route_text.setPlainText(str(exc)); return
        end, names = self.route_target.currentData(), self._map_names()
        legs = plan_route(self._all_pathing, start, end)
        if legs is None:
            self.route_text.setPlainText(f"No walk-through route from {names.get(start)} to {names.get(end)}. Many "
                                         "transitions are driven by map scripts, which are not decoded yet.")
            return
        lines = [f"{names.get(start)} → {names.get(end)}: {sum(leg.distance for leg in legs):g} tiles over "
                 f"{len(legs)} map(s)"]
        for leg in legs:
            place = next(p for p in self._all_pathing[leg.map_index].places if p.id == leg.end)
            lines.append(f"  {names.get(leg.map_index)}: from {leg.start} walk {leg.distance:g} tiles to {place.label}")
        self.route_text.setPlainText("\n".join(lines) if legs else f"You are already on {names.get(end)}.")

    @Slot(object)
    def _all_pathing_ready(self, pathing):
        self._all_pathing = pathing
        self._plan_route()

    def _export_zones(self):
        zones = {}
        for row in self.map_rows.values():
            label = self.labels.get((row["id"], "area_name"))
            if label:
                zones[label["value"]] = zones.get(label["value"], 0) + 1
        try:
            current = self.labels.get((self._selected_map()["id"], "area_name"))
        except ValueError:
            current = None
        dialog = ZoneExportDialog(zones, current["value"] if current else None, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        chosen = dialog.zones()
        if not chosen:
            QMessageBox.information(self, "Export zones", "Tick at least one zone."); return
        name = chosen[0] if len(chosen) == 1 else f"bundle of {len(chosen)} zones"
        path, _ = QFileDialog.getSaveFileName(self, "Save zone package", f"{zone_slug(name)}{ZONE_SUFFIX}",
                                              f"Zone package (*{ZONE_SUFFIX})")
        if not path:
            return
        destination = Path(path).with_suffix(ZONE_SUFFIX)
        repository = Path(__file__).resolve().parents[2]
        if repository in destination.resolve().parents:
            QMessageBox.warning(self, "Export zones", "Packages contain game data; save them outside the source tree.")
            return
        export = {"destination": destination, "zones": chosen, "map_names": self._map_names(),
                  "character_names": self._character_names(), "meta": self.catalogue.meta(),
                  "all_palettes": dialog.palettes.isChecked()}
        try:
            self._start_zone_worker("export", f"Exporting {name}", export, self._zones_exported)
        except Exception as exc:
            QMessageBox.critical(self, "Zone export failed", str(exc))

    @Slot(object)
    def _zones_exported(self, manifest):
        QMessageBox.information(self, "Zone package written",
                                f"{manifest['kind'].capitalize()} with {len(manifest['zones'])} zone(s) and "
                                f"{len(manifest['maps'])} map(s), {len(manifest['files'])} files. It contains game "
                                "data from your disc: keep it private.")

    @Slot(int, str)
    def _zone_progress_changed(self, value, message):
        if self.zone_progress:
            self.zone_progress.setValue(value); self.zone_progress.setLabelText(message)

    @Slot(str)
    def _zone_failed(self, message):
        if not self._closing:
            QMessageBox.critical(self, "Zone task failed", message)

    @Slot(str)
    def _zone_cancelled(self, message):
        pass

    @Slot()
    def _zone_finished(self):
        if self.zone_progress:
            self.zone_progress.close(); self.zone_progress.deleteLater()
        self.zone_progress = None; self.zone_worker = None; self.zone_thread = None
        self.background_finished.emit()

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
            scene = parse_map(self._map_file(entry.index, "MAP", scene_row, MAX_MAP_FILE), entry)
            tfs = parse_tfs(self._map_file(entry.index, "TFS", tfs_row, MAX_TFS_FILE))
            self._map_scene, self._map_tfs = scene, tfs
            self._update_pathing(entry, scene)
            self.map_palette.blockSignals(True)
            self.map_palette.addItems([f"Palette {index}" for index in range(len(tfs.palettes))])
            self.map_palette.blockSignals(False)
            self._render_map_background()
            seen = self.play_panel.spawns.spawns(entry.index) if hasattr(self, "play_panel") else []
            self.map_floor.setPixmap(QPixmap.fromImage(ImageQt(floor_grid_with_digimon(
                scene, items=[spawn.tile for spawn in seen]))))
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
                f"files: {entry.map_path}, {entry.tfs_path}"
                + (f" — IMPORTED {', '.join(self.overrides.records()[entry.index]['files'])} from "
                   f"{self.overrides.records()[entry.index]['package']}" if entry.index in self.overrides.records() else ""),
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
                f"Digimon placements ({len(scene.digimon)}; candidates — the map script chooses which appear, "
                "and sets whether touching them starts their script, e.g. a battle):",
                *(f"  {self._character_name(d.character_id)} at {d.position} (tile "
                  f"{','.join(map(str, model_tile(d.position[0], d.position[2])))}); "
                  f"{follow_description(d.ai_type, d.tracking_range)}; HP {d.stats['hp']}/{d.stats['max_hp']}, "
                  f"MP {d.stats['mp']}/{d.stats['max_mp']}, off {d.stats['offense']}, def {d.stats['defense']}, "
                  f"spd {d.stats['speed']}, brn {d.stats['brains']}, bits {d.bits}, script {d.script_id}, "
                  f"{len(d.waypoints)} waypoint(s)" for d in scene.digimon),
                *self._inspection_lines(entry.index),
                *self._spawn_lines(entry.index),
                f"parser: {row['decoder']} {row['decoder_version']}; MAP {scene_row['decoder_version']}; "
                f"TFS {tfs_row['decoder_version']}",
                f"evidence: {row['mapping_evidence']}",
            ]
            self.map_details.setPlainText("\n".join(lines))
        except Exception as exc:
            self.map_background.setPixmap(QPixmap()); self.map_floor.setPixmap(QPixmap())
            self._map_pathing = None; self.path_image.setPixmap(QPixmap()); self.path_result.setText(str(exc))
            self.map_background.setText(f"Preview unavailable:\n{exc}")
            self.map_details.setPlainText(str(exc))

    def _inspection_lines(self, index):
        panel = getattr(self, "play_panel", None)
        records = panel.inspections.list(map_index=index) if panel is not None else []
        if not records:
            return ["inspections while playing: none (press F9 in the game window)"]
        return [f"inspections while playing ({len(records)}; observations, not decoded data):",
                *(f"  {record.created} {record.category} at tile {record.tile}: {record.note or '(no note)'}"
                  for record in records)]

    def _spawn_lines(self, index):
        panel = getattr(self, "play_panel", None)
        spawns = panel.spawns.spawns(index) if panel is not None else []
        if not spawns:
            return ["items seen on the ground while playing: none yet (map scripts place them; scripts are not decoded)"]
        return [f"items seen on the ground while playing ({len(spawns)}; placed by the map script):",
                *(f"  {spawn.item_name} at tile {spawn.tile[0]},{spawn.tile[1]} — seen on {spawn.times_seen} visit(s), "
                  f"last {spawn.last_seen}" for spawn in spawns)]

    def _render_map_background(self, *_):
        if self._map_scene is None or self._map_tfs is None or self.map_palette.currentIndex() < 0:
            return
        image = render_background(self._map_tfs, self._map_scene.setup, self.map_palette.currentIndex())
        self.map_background.setPixmap(QPixmap.fromImage(ImageQt(image)))

    def _follow_warp(self, item):
        self._select_map(item.data(Qt.ItemDataRole.UserRole))

    def _map_row_by_index(self, index):
        return next((row for row in self.map_rows.values()
                     if int(row["original_path"].rsplit("/", 2)[1]) == index), None) if index is not None else None

    def _select_map(self, index) -> bool:
        row = self._map_row_by_index(index)
        if row is None or row["id"] not in self._map_items:
            return False
        item = self._map_items[row["id"]]
        if item.isHidden() or item.parent().isHidden():
            self.map_search.clear()
        self.map_tree.setCurrentItem(item)
        return True

    @Slot(int)
    def show_map(self, index):
        """Open a map (by table index) in the Maps and Zones browser, on its floor grid."""
        if self._select_map(index):
            self.tabs.setCurrentWidget(self.maps_page); self.maps_tabs.setCurrentIndex(0)
            self.map_preview_tabs.setCurrentIndex(2)

    def _item_names(self) -> list[str]:
        executable = next((row for row in self.rows if row["offset"] is None
                           and row["original_path"].endswith(self.catalogue.meta().get("executable", "SLUS_010.32"))), None)
        if executable is None or not source_state(self.catalogue)[0]:
            return []
        try:
            return parse_item_names(self._read_row(executable, PREVIEW_LIMIT))
        except Exception:
            return []  # other releases: item IDs are shown as numbers

    def _character_names(self) -> dict[int, str]:
        names = {}
        for row in self.rows:
            if "#character/" not in row["original_path"]:
                continue
            character_id = int(row["original_path"].split("#character/", 1)[1].split("/", 1)[0])
            label = self.labels.get((row["id"], "display_name"))
            if label and label["value"]:
                names.setdefault(character_id, label["value"])
        return names

    def _map_file(self, index, kind, row, limit):
        """A map's MAP or TFS bytes: the imported override when there is one, else the disc's file."""
        return self.overrides.file(index, kind) or self._read_row(row, limit)

    def _override_files(self) -> dict[int, dict[str, bytes]]:
        return {index: {kind: self.overrides.file(index, kind) for kind in record["files"]}
                for index, record in self.overrides.records().items()}

    def _import_originals(self):
        """Catalogue hash and size of each map's MAP/TFS on the disc, and the bytes its sectors hold."""
        originals, room = {}, {}
        for row in self.map_rows.values():
            index = int(row["original_path"].rsplit("/", 2)[1])
            for kind, relation in (("MAP", "scene"), ("TFS", "background")):
                related = self._character_related(row, relation)
                if related is not None:
                    originals.setdefault(index, {})[kind] = {"content_hash": related["content_hash"],
                                                              "size": related["size"]}
                    room.setdefault(index, {})[kind] = (related["size"] + 2047) // 2048 * 2048
        return originals, room

    def _import_zones(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import zone package", "", f"Zone package (*{ZONE_SUFFIX})")
        if not path:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self._map_entry(next(iter(self.map_rows.values())))
            report = check_package(Path(path), self.catalogue.meta(), self._map_entries, *self._import_originals())
        except Exception as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Import failed", str(exc)); return
        QApplication.restoreOverrideCursor()
        dialog = ZoneImportDialog(report, self.overrides.records(), self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            changed = self.overrides.apply(report)
            self._overrides_changed()
            skipped = [item.label for item in report.maps if item.skipped]
            QMessageBox.information(self, "Zone package imported",
                                    f"{len(changed)} map(s) now use imported files: "
                                    f"{', '.join(item.label for item in changed) or 'none (all identical to the disc)'}. "
                                    + (f"Left as they were, because their pictures are damaged: {', '.join(skipped)}. " if skipped else "")
                                    + "Tick “Play with imported resources” in the Play tab to play them.")

    def _manage_overrides(self):
        ImportedZonesDialog(self.overrides, self).exec()
        self._overrides_changed()

    def _overrides_changed(self):
        count = len(self.overrides.records())
        self.imported_button.setText(f"Imported zones ({count})…")
        self._all_pathing = None
        self.play_panel.refresh_imports()
        self._preview_map()

    def _imported_forms(self) -> dict[int, dict]:
        """Digimon imported as a new form over a native one (their record describes the form)."""
        return {resource_id: record["form"] for resource_id, record in self.digimon_overrides.records().items()
                if record.get("form")}

    def _build_patched_disc(self, cancelled, progress) -> Path:
        """Runs on the Play tab's worker thread: a patched copy of the disc with every imported file."""
        executable = next(row for row in self.rows if row["detected_format"] == "PS-X EXE" and row["offset"] is None)
        with layout_from_catalogue(self.catalogue).open() as reader:
            entry = self._entry(executable)
            entries = parse_map_table(reader.read_extent_range(entry.extent, entry.extent_size, entry.offset, entry.size))
            alltim_row = next(row for row in self.rows if row['original_path'] == 'CHDAT/ALLTIM.TIM'
                              and row['offset'] is None)
            alltim_entry = self._entry(alltim_row)
            alltim = reader.read_extent_range(alltim_entry.extent, alltim_entry.extent_size, alltim_entry.offset,
                                              alltim_entry.size, cancelled=cancelled)
            if hashlib.sha256(alltim).hexdigest() != alltim_row['content_hash']:
                raise ValueError('ALLTIM source hash changed; re-import the disc.')
        models = {int(row['original_path'].split('/')[-2]): dict(model)
                  for row in self.character_rows if (model := self._character_related(row, 'model_container')) is not None}
        replacements = self.overrides.replacements(entries)
        replacements.update(self.digimon_overrides.replacements(alltim, models))
        replacements.update(self.title_store.replacements())
        return build_patched_image(layout_from_catalogue(self.catalogue), replacements,
                                   self.overrides.directory / "patched", cancelled, progress)

    def _map_names(self) -> dict[int, str]:
        names = {}
        for row in self.map_rows.values():
            _, index, name = row["original_path"].rsplit("/", 2)
            zone = self.labels.get((row["id"], "area_name"))
            names[int(index)] = f"{zone['value']} · {name}" if zone else name
        return names

    def _decoded_grid(self, index) -> bytes | None:
        """The floor grid decoded from a map's MAP file, for comparison with the running game."""
        row = self._map_row_by_index(index)
        scene_row = self._character_related(row, "scene") if row is not None else None
        if scene_row is None:
            return None
        entry = self._map_entry(row)
        return parse_map(self._map_file(entry.index, "MAP", scene_row, MAX_MAP_FILE), entry).floor_grid

    def _set_hd_picture(self):
        try:
            row = self._selected_map()
            if self._map_scene is None:
                raise ValueError("This map has no decoded background.")
            entry, setup = self._map_entry(row), self._map_scene.setup
            path, _ = QFileDialog.getOpenFileName(self, f"HD picture for {entry.name}", "",
                                                  "Images (*.png *.jpg *.jpeg *.webp)")
            if not path:
                return
            data = Path(path).read_bytes()
            with Image.open(io.BytesIO(data)) as picture:
                picture.verify()
                size = picture.size
            shape, map_shape = size[0] / size[1], setup.grid_width / setup.grid_height
            if abs(shape / map_shape - 1) > 0.02 and QMessageBox.question(
                    self, "Different shape", f"The picture is {size[0]}×{size[1]} but the map is {setup.grid_width}×"
                    f"{setup.grid_height} tiles, so it will be stretched and may not line up. Use it anyway?"
                    ) != QMessageBox.StandardButton.Yes:
                return
            self.hd_pictures.store(entry.index, entry.name, data)
            QMessageBox.information(self, "HD picture set", f"{entry.name} now shows {Path(path).name} ({size[0]}×"
                                    f"{size[1]}) in the player. It applies the next time the map loads.")
        except Exception as exc:
            QMessageBox.critical(self, "Cannot set the HD picture", str(exc))

    def _remove_hd_picture(self):
        try:
            self.hd_pictures.remove(self._map_entry(self._selected_map()).index)
        except Exception as exc:
            QMessageBox.critical(self, "Cannot remove the HD picture", str(exc))

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

    def _character_label(self, character):
        label = self.labels.get((character["id"], "display_name"))
        name = character["original_path"].split("/")[-1]
        return f"{label['value']} ({name})" if label else name

    def _load_character_model(self, character=None, panel=None, show_dialog=True):
        try:
            # Busy until _model_finished has run: the thread stops running slightly before that slot is
            # delivered, and starting a second load in that gap would orphan the first thread.
            if self.model_thread is not None:
                if show_dialog:
                    raise ValueError("A model preview is already loading.")
                # Browsing: drop the running load and start the newest selection once it has stopped.
                self._pending_model = (character, panel)
                if self.model_worker is not None:
                    self.model_worker.request_cancel()
                return
            character = character or self._selected_character()
            panel = panel or self.character_panel
            model = self._character_related(character, "model_container")
            texture = self._character_related(character, "texture")
            executable = next((row for row in self.rows
                               if row["original_path"] == character["parent_path"] and row["offset"] is None), None)
            if model is None or texture is None or executable is None:
                raise ValueError("This resource lacks a verified model, texture, or executable association.")
            resource_id = int(character["original_path"].split("/")[-2])
            label = self._character_label(character)
            dialog = None
            if show_dialog:
                dialog = QProgressDialog("Loading model and animations", "Cancel", 0, 100, self)
                dialog.setWindowModality(Qt.WindowModality.WindowModal); dialog.setAutoClose(False); dialog.show()
            else:
                panel.show_message(f"Loading {label}…")
            thread = QThread(self)
            worker = ModelWorker(self.catalogue, self._entry(executable), self._entry(model),
                                 self._entry(texture), resource_id, label)
            worker.moveToThread(thread); thread.started.connect(worker.run)
            # Worker signals must reach bound slots of GUI-thread objects: a lambda has no
            # receiver, so PySide would run it, and its widget repaints, in the worker thread.
            worker.progress.connect(self._model_progress_changed)
            if dialog is not None:
                dialog.canceled.connect(lambda: worker.request_cancel())
            worker.succeeded.connect(panel.show_load)
            worker.failed.connect(self._model_failed)
            worker.cancelled.connect(panel.show_message)
            worker.finished.connect(thread.quit); worker.finished.connect(worker.deleteLater)
            thread.finished.connect(thread.deleteLater); thread.finished.connect(self._model_finished)
            self.model_thread, self.model_worker, self.model_progress = thread, worker, dialog
            self._model_panel = panel
            thread.start()
        except Exception as exc:
            if show_dialog:
                QMessageBox.critical(self, "Model preview failed", str(exc))
            else:
                (panel or self.character_panel).show_message(f"Model preview unavailable: {exc}")

    @Slot(int, str)
    def _model_progress_changed(self, value, message):
        if self.model_progress:
            self.model_progress.setValue(value); self.model_progress.setLabelText(message)

    @Slot(str)
    def _model_failed(self, message):
        self._model_panel.show_message(f"Model preview unsupported: {message}")

    @Slot()
    def _model_finished(self):
        if self.model_progress:
            self.model_progress.close(); self.model_progress.deleteLater()
        self.model_progress = None; self.model_worker = None; self.model_thread = None
        pending, self._pending_model = self._pending_model, None
        if pending is not None and not self._closing:
            self._load_character_model(*pending, show_dialog=False)
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
        self._closing, self._pending_model = True, None
        self.play_panel.close_player()
        if self.zone_worker:
            self.zone_worker.request_cancel()
        if self.export_worker:
            self.export_worker.request_cancel()
        if self.model_worker:
            self.model_worker.request_cancel()
        if self.relation_worker:
            self.relation_worker.request_cancel()
        if self.digimon_worker:
            self.digimon_worker.request_cancel()

    def has_background_work(self):
        return bool(self.digimon_thread is not None or (self.export_thread and self.export_thread.isRunning()) or
                    (self.model_thread and self.model_thread.isRunning()) or
                    (self.relation_thread and self.relation_thread.isRunning()) or
                    (self.zone_thread and self.zone_thread.isRunning()))

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
    def __init__(self, settings: QSettings | None = None):
        """`settings`: where the opened game is remembered; None remembers nothing."""
        super().__init__(); self.setWindowTitle(APP_NAME); self.resize(1100, 720)
        self.settings = settings
        self.stack = QStackedWidget(); self.setCentralWidget(self.stack)
        game = self.menuBar().addMenu("Game")
        game.addAction("Open another project…", self.open_project)
        game.addAction("Import game image…", self.start_import)
        self.shortcuts_action = game.addAction("Create desktop shortcuts", self.create_desktop_shortcuts)
        self.shortcuts_action.setEnabled(False)
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

    def open_last_project(self) -> bool:
        """Reopen the game opened last time; when that isn't possible the welcome page stays, with the reason."""
        path = self.settings.value(LAST_PROJECT, "") if self.settings is not None else ""
        if not path:
            return False
        try:
            self.show_workspace(open_catalogue(Path(path)))
        except Exception as exc:
            self.statusBar().showMessage(f"Could not reopen your game at {path}: {exc}")
            return False
        return True

    @Slot(object)
    def show_workspace(self, catalogue):
        workspace = Workspace(catalogue, self)
        workspace.background_finished.connect(self._maybe_finish_close)
        self.stack.addWidget(workspace); self.stack.setCurrentWidget(workspace)
        self._disc = Path(catalogue.meta().get("source_path", ""))
        self.shortcuts_action.setEnabled(sys.platform == "win32")
        if self.settings is not None:
            self.settings.setValue(LAST_PROJECT, str(catalogue.project_dir))
            apply_icon(QApplication.instance(), self._disc)

    def create_desktop_shortcuts(self):
        try:
            create_shortcuts(self._disc)
        except (OSError, subprocess.SubprocessError) as exc:
            QMessageBox.warning(self, "Desktop shortcuts", f"The shortcuts could not be created: {exc}")
        else:
            self.statusBar().showMessage("DW1-settings and DW1-play are on your desktop")

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
        for workspace in self._active_workspaces():
            workspace.play_panel.close_player()  # writes the resume slot and memory card first
        if self._has_active_work():
            self._close_pending = True
            self._request_import_cancel()
            for workspace in self._active_workspaces():
                workspace.cancel_background()
            event.ignore()
            return
        event.accept()
