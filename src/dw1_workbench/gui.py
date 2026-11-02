from __future__ import annotations

import html
import traceback
from pathlib import Path

from PIL.ImageQt import ImageQt
from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QMainWindow,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSplitter, QStackedWidget,
    QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from .catalog import Catalogue, open_catalogue
from .iso9660 import IsoEntry, safe_export
from .project import import_project, layout_from_catalogue, source_state
from .tim import decode_tim, export_png

HEX_LIMIT = 256 * 1024
PREVIEW_LIMIT = 64 * 1024 * 1024


class ImportWorker(QObject):
    progress = Signal(int, str)
    succeeded = Signal(object)
    failed = Signal(str, str)
    finished = Signal()

    def __init__(self, source: Path, project: Path):
        super().__init__()
        self.source, self.project, self.cancelled = source, project, False

    @Slot()
    def run(self):
        try:
            result = import_project(self.source, self.project, lambda: self.cancelled, self.progress.emit)
            self.succeeded.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc), traceback.format_exc())
        finally:
            self.finished.emit()

    @Slot()
    def cancel(self):
        self.cancelled = True


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
    def __init__(self, catalogue: Catalogue, parent=None):
        super().__init__(parent)
        self.catalogue = catalogue
        self.rows = catalogue.resources()
        self.tabs = QTabWidget()
        self.tabs.addTab(self._overview(), "Overview")
        self.tabs.addTab(self._disc_files(), "Disc Files")
        self.tabs.addTab(UnsupportedPage("Characters and Digimon", "No verified character/model/animation associations have been imported. MMD decoding is not implemented."), "Characters and Digimon")
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

    def _selected_row(self):
        index = self.file_table.currentRow()
        if index < 0:
            raise ValueError("Select a disc file first.")
        return self.rows[index]

    def _entry(self, row):
        return IsoEntry(row["original_path"], row["extent"], row["size"], False)

    def _read_row(self, row, limit=None):
        if limit is not None and row["size"] > limit:
            raise ValueError(f"File is {row['size']:,} bytes; preview limit is {limit:,} bytes.")
        with layout_from_catalogue(self.catalogue).open() as reader:
            return reader.read_extent(row["extent"], row["size"], limit)

    def _export_selected(self):
        try:
            row = self._selected_row()
            path, _ = QFileDialog.getSaveFileName(self, "Export disc file", Path(row["original_path"]).name)
            if not path: return
            with layout_from_catalogue(self.catalogue).open() as reader:
                safe_export(reader, self._entry(row), Path(path))
            QMessageBox.information(self, "Export complete", f"Exported to {path}")
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", str(exc))

    def _hex_selected(self):
        try:
            row = self._selected_row()
            data = self._read_row(row)[:HEX_LIMIT]
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
        textures = [r for r in self.rows if r["detected_format"] == "TIM"]
        info = QLabel("Standalone TIM files detected by header. Indexed palettes and PS1 STP are preserved in preview/export metadata." if textures else
                      "No standalone TIM files were identified. Embedded/container textures are not guessed or decoded.")
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
            export_png(tim, Path(path), row["id"], row["content_hash"])
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

    def start_import(self):
        source, _ = QFileDialog.getOpenFileName(self, "Choose your Digimon World image", "", "Disc images (*.iso *.bin *.cue);;All files (*)")
        if not source: return
        project = QFileDialog.getExistingDirectory(self, "Choose an empty analysis-project directory")
        if not project: return
        page, layout = QWidget(), QVBoxLayout(); page.setLayout(layout)
        label, bar, cancel = QLabel("Preparing import…"), QProgressBar(), QPushButton("Cancel")
        layout.addStretch(); layout.addWidget(label); layout.addWidget(bar); layout.addWidget(cancel); layout.addStretch()
        self.stack.addWidget(page); self.stack.setCurrentWidget(page)
        self.thread, self.worker = QThread(self), ImportWorker(Path(source), Path(project))
        self.worker.moveToThread(self.thread); self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(lambda p, m: (bar.setValue(p), label.setText(m)))
        cancel.clicked.connect(self.worker.cancel); self.worker.succeeded.connect(self.show_workspace)
        self.worker.failed.connect(self.import_failed); self.worker.finished.connect(self.thread.quit)
        self.thread.finished.connect(self.worker.deleteLater); self.thread.finished.connect(self.thread.deleteLater)
        self.thread.start()

    @Slot(str, str)
    def import_failed(self, message, detail):
        QMessageBox.critical(self, "Import failed", message + "\n\nThe incomplete project records this failure and can be inspected or removed.")
        self.stack.setCurrentIndex(0)

    def open_project(self):
        path = QFileDialog.getExistingDirectory(self, "Open analysis project")
        if not path: return
        try: self.show_workspace(open_catalogue(Path(path)))
        except Exception as exc: QMessageBox.critical(self, "Could not open project", str(exc))

    @Slot(object)
    def show_workspace(self, catalogue):
        workspace = Workspace(catalogue, self); self.stack.addWidget(workspace); self.stack.setCurrentWidget(workspace)
