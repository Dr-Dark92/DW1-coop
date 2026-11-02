from __future__ import annotations

import os
import threading
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication, QPushButton

import dw1_workbench.gui as gui
from dw1_workbench.errors import Cancelled


@pytest.fixture(scope="module")
def application():
    app = QApplication.instance() or QApplication([])
    yield app


def process_until(application, predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        application.processEvents()
        time.sleep(0.005)
    assert predicate(), "Qt operation did not finish before timeout"


def test_button_cancels_busy_worker_without_worker_event_loop(application, monkeypatch, tmp_path):
    started = False

    def busy_import(source, project, cancelled, progress):
        nonlocal started
        started = True
        while not cancelled():
            time.sleep(0.005)
        raise Cancelled("cancel delivered")

    monkeypatch.setattr(gui, "import_project", busy_import)
    worker = gui.ImportWorker(Path("source.iso"), tmp_path / "project")
    thread = QThread()
    worker.moveToThread(thread)
    messages = []
    worker.cancelled.connect(messages.append)
    worker.finished.connect(thread.quit)
    thread.started.connect(worker.run)
    button = QPushButton("Cancel")
    button.clicked.connect(lambda: worker.request_cancel())
    thread.start()
    process_until(application, lambda: started)
    button.click()
    assert worker.cancel_event.is_set()
    process_until(application, lambda: not thread.isRunning())
    assert messages == ["cancel delivered"]
    worker.deleteLater(); thread.deleteLater(); application.processEvents()


def test_import_progress_updates_widgets_on_gui_thread(application, monkeypatch, tmp_path):
    on_gui_thread = []

    class RecordingBar(gui.QProgressBar):
        def setValue(self, value):
            on_gui_thread.append(threading.current_thread() is threading.main_thread())
            super().setValue(value)

    def reporting_import(source, project, cancelled, progress):
        progress(42, "halfway")
        raise Cancelled("stopped after progress")

    monkeypatch.setattr(gui, "QProgressBar", RecordingBar)
    monkeypatch.setattr(gui, "import_project", reporting_import)
    monkeypatch.setattr(gui.QFileDialog, "getOpenFileName", staticmethod(lambda *args: ("source.iso", "")))
    monkeypatch.setattr(gui.QFileDialog, "getExistingDirectory", staticmethod(lambda *args: str(tmp_path / "project")))
    monkeypatch.setattr(gui.QMessageBox, "information", staticmethod(lambda *args: None))
    window = gui.MainWindow()
    window.start_import()
    process_until(application, lambda: window.thread is None)
    assert on_gui_thread == [True]


def test_window_close_waits_for_active_import(application, monkeypatch, tmp_path):
    def busy_import(source, project, cancelled, progress):
        while not cancelled():
            time.sleep(0.005)
        raise Cancelled("shutdown cancellation")

    monkeypatch.setattr(gui, "import_project", busy_import)
    window = gui.MainWindow()
    worker = gui.ImportWorker(Path("source.iso"), tmp_path / "project")
    thread = QThread(window)
    worker.moveToThread(thread)
    window.worker, window.thread = worker, thread
    thread.started.connect(worker.run)
    worker.finished.connect(thread.quit)
    worker.finished.connect(worker.deleteLater)
    thread.finished.connect(thread.deleteLater)
    thread.finished.connect(window._import_thread_finished)
    thread.start()
    process_until(application, thread.isRunning)
    window.show()
    window.close()
    assert window._close_pending
    assert worker.cancel_event.is_set()
    process_until(application, lambda: window.thread is None)
    process_until(application, lambda: not window.isVisible())
