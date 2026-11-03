from __future__ import annotations

import os
import threading
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QDialog

import dw1_workbench.gui as gui
import dw1_workbench.play_panel as play_panel
from dw1_workbench.catalog import Catalogue
from dw1_workbench.errors import Cancelled
from test_digimon_package import edited_png, rewrite, source


@pytest.fixture(scope='module')
def application():
    yield QApplication.instance() or QApplication([])


def wait(app, condition):
    end = time.monotonic() + 4
    while not condition() and time.monotonic() < end:
        app.processEvents(); time.sleep(0.005)
    assert condition(), 'Digimon GUI operation did not finish'


@pytest.fixture
def workspace(application, tmp_path, monkeypatch):
    monkeypatch.setattr(play_panel, 'find_installed_core', lambda: None)
    monkeypatch.setattr(play_panel, 'QSettings', lambda *args: QSettings(str(tmp_path / 'qt.ini'), QSettings.Format.IniFormat))
    catalogue = Catalogue(tmp_path / 'project'); catalogue.initialize()
    original = source()
    catalogue.set_meta(**original.meta)
    monkeypatch.setattr(gui, 'load_digimon_source', lambda *args, **kwargs: original)
    page = gui.Workspace(catalogue)
    monkeypatch.setattr(page, '_selected_character', lambda: {'id': 'character-3', 'original_path': 'characters/003/AGUM'})
    return page


def test_gui_native_export_review_and_import_use_background_workers(application, workspace, tmp_path, monkeypatch):
    messages, changes, progress_threads = [], [], []
    monkeypatch.setattr(gui.QMessageBox, 'information', staticmethod(lambda *args: messages.append(args[1])))
    monkeypatch.setattr(gui.QMessageBox, 'critical', staticmethod(lambda *args: pytest.fail(str(args))))
    monkeypatch.setattr(QDialog, 'exec', lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(workspace, '_digimon_changed', lambda: changes.append(threading.current_thread() is threading.main_thread()))
    # Record inside the bound receiver, preserving Qt's thread-aware connection.
    class RecordingDialog(gui.QProgressDialog):
        def setValue(self, value):
            progress_threads.append(threading.current_thread() is threading.main_thread())
            super().setValue(value)
    monkeypatch.setattr(gui, 'QProgressDialog', RecordingDialog)
    destination = tmp_path / 'native.dw1digimon'
    workspace._start_digimon('export', destination)
    wait(application, lambda: workspace.digimon_thread is None)
    assert destination.is_file()
    edited = rewrite(destination, tmp_path / 'edited.dw1digimon', {'texture.png': edited_png(source())})
    workspace._start_digimon('validate', edited)
    wait(application, lambda: workspace.digimon_thread is None)
    assert workspace.digimon_overrides.file(3, 'TIM') is not None
    assert changes == [True]
    assert progress_threads and all(progress_threads)
    assert 'Digimon exported' in messages and 'Digimon imported' in messages
    workspace.cancel_background(); workspace.close()


def test_window_close_delivers_cancellation_to_busy_digimon_worker(application, workspace, tmp_path, monkeypatch):
    started = threading.Event()
    def busy_source(catalogue, character_id, cancelled, progress):
        started.set()
        while not cancelled(): time.sleep(0.005)
        raise Cancelled('Digimon shutdown cancelled')
    monkeypatch.setattr(gui, 'load_digimon_source', busy_source)
    window = gui.MainWindow()
    window.stack.addWidget(workspace); window.stack.setCurrentWidget(workspace)
    workspace.background_finished.connect(window._maybe_finish_close)
    window.show()
    workspace._start_digimon('export', tmp_path / 'never.dw1digimon')
    wait(application, started.is_set)
    worker = workspace.digimon_worker
    window.close()
    assert worker.cancel_event.is_set() and window._close_pending
    wait(application, lambda: workspace.digimon_thread is None)
    wait(application, lambda: not window.isVisible())
    assert not (tmp_path / 'never.dw1digimon').exists()
