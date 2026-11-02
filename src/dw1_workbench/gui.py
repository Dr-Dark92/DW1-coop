from __future__ import annotations

import html
import threading
import traceback
from pathlib import Path

from PIL.ImageQt import ImageQt
from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QCloseEvent, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QMainWindow,
    QMessageBox, QPlainTextEdit, QProgressBar, QProgressDialog, QPushButton, QSplitter, QStackedWidget,
    QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from .catalog import Catalogue, open_catalogue
from .errors import Cancelled
from .iso9660 import IsoEntry, safe_export
from .mmd import parse_mmd_static, parse_usa_skeleton, validate_texture_materials
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
            self.progress.emit(100, "Static model ready")
            self.succeeded.emit((model, palettes, first.clut_y, used_palettes))
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
        self.tabs = QTabWidget()
        self.tabs.addTab(self._overview(), "Overview")
        self.tabs.addTab(self._disc_files(), "Disc Files")
        self.tabs.addTab(self._characters(), "Characters and Digimon")
        self.tabs.addTab(UnsupportedPage("Animations", "No DW1 animation decoder is verified in this milestone. Unsupported resources are retained in the disc catalogue."), "Animations")
        self.tabs.addTab(self._textures(), "Textures")
        self.tabs.addTab(UnsupportedPage("Maps and Zones", "TFS backgrounds, geometry, collision, exits, and scripts are not decoded. A file listing is not a reconstructed scene."), "Maps and Zones")
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
        info = QLabel("Verified USA executable resource IDs and internal names, associated by index with ALLTIM texture slots. MMD boundaries are identified; geometry and animations are not decoded.")
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
        model_load = QPushButton("Load verified static model")
        self.model_mode.currentIndexChanged.connect(self._update_model_modes)
        self.model_wireframe.toggled.connect(self._update_model_modes)
        model_reset.clicked.connect(self.model_view.reset_view)
        model_load.clicked.connect(self._load_character_model)
        model_controls.addWidget(self.model_mode); model_controls.addWidget(self.model_wireframe)
        model_controls.addWidget(model_reset); model_controls.addStretch(); model_controls.addWidget(model_load)
        self.model_status = QLabel("Model geometry is loaded on request in a cancellable background worker.")
        self.model_status.setWordWrap(True)
        model_layout.addLayout(model_controls); model_layout.addWidget(self.model_view); model_layout.addWidget(self.model_status)
        texture_page = QWidget(); texture_layout = QVBoxLayout(texture_page)
        texture_layout.addWidget(self.character_preview); texture_layout.addWidget(self.character_details)
        preview_tabs = QTabWidget(); preview_tabs.addTab(model_page, "Static model"); preview_tabs.addTab(texture_page, "Texture")
        right = QWidget(); right_layout = QVBoxLayout(right)
        right_layout.addWidget(preview_tabs)
        split = QSplitter(); split.addWidget(self.character_table); split.addWidget(right)
        layout.addWidget(split); layout.addWidget(export)
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
        model, images, clut_y, palettes = result
        qimages = tuple(ImageQt(image).copy() for image in images)
        self.model_view.set_model(model, qimages, clut_y)
        self.model_status.setText(
            f"Experimental static preview — {len(model.objects)} objects, {model.vertex_count} vertices, "
            f"{model.primitive_count} primitives / {model.triangle_count} triangles; verified palettes "
            f"{', '.join(map(str, palettes))}. {model.warnings[0]}")

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

    def has_background_work(self):
        return bool((self.export_thread and self.export_thread.isRunning()) or
                    (self.model_thread and self.model_thread.isRunning()))

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
