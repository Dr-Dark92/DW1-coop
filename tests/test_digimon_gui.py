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


def test_gui_stage_bundle_export_review_and_apply(application, workspace, tmp_path, monkeypatch):
    from test_digimon_package import BundleCatalogue, character_source
    import dw1_workbench.digimon_package as package
    sources = BundleCatalogue([source(), character_source(5, 'BETA')])
    monkeypatch.setattr(package, 'stage_characters', lambda cat, stages: sources.resources())
    monkeypatch.setattr(package, 'load_source', lambda cat, row_id, cancelled=None: sources.sources[row_id])
    monkeypatch.setattr(gui, 'export_digimon_bundle', lambda path, cat, *args, **kwargs:
                        package.export_bundle(path, sources, *args, **kwargs))
    monkeypatch.setattr(gui, 'prepare_digimon_bundle', lambda path, cat, **kwargs:
                        package.prepare_bundle_import(path, sources, **kwargs))
    messages, reviewed = [], []
    monkeypatch.setattr(gui.QMessageBox, 'information', staticmethod(lambda *args: messages.append(args[1])))
    monkeypatch.setattr(gui.QMessageBox, 'critical', staticmethod(lambda *args: pytest.fail(str(args))))
    monkeypatch.setattr(QDialog, 'exec', lambda self: reviewed.append(self.windowTitle()) or QDialog.DialogCode.Accepted)
    monkeypatch.setattr(workspace, '_digimon_changed', lambda: messages.append('changed'))
    monkeypatch.setattr(workspace, '_selected_character', lambda: pytest.fail('bundles need no selected character'))
    destination = tmp_path / 'rookie.dw1digimons'
    workspace._start_digimon('export-bundle', destination, stages=('Rookie',))
    wait(application, lambda: workspace.digimon_thread is None)
    assert destination.is_file() and 'Digimon bundle exported' in messages
    workspace._start_digimon('validate-bundle', destination)
    wait(application, lambda: workspace.digimon_thread is None and 'Digimon bundle imported' in messages)
    assert 'Review Digimon bundle import' in reviewed and 'changed' in messages
    workspace.cancel_background(); workspace.close()


def test_class_filters_and_class_bundle_export(application, workspace, tmp_path, monkeypatch):
    rows = [{'id': f'c{i}'} for i in range(4)]
    stages = {'c0': 'Fresh', 'c1': 'Rookie', 'c2': 'Rookie', 'c3': None}
    workspace.character_rows = rows
    workspace.character_table = gui.QTableWidget(4, 1)
    workspace.character_stage = workspace._class_filter(workspace._filter_characters)
    monkeypatch.setattr(workspace, '_character_stages', lambda: stages)
    titles = [workspace.character_stage.itemText(i) for i in range(workspace.character_stage.count())]
    assert titles == ['All classes', 'Newborn (Fresh)', 'In-Training', 'Rookie', 'Champion', 'Ultimate', 'No class']
    shown = lambda: [i for i in range(4) if not workspace.character_table.isRowHidden(i)]
    for title, expected in (('Rookie', [1, 2]), ('Newborn (Fresh)', [0]), ('No class', [3]), ('All classes', [0, 1, 2, 3])):
        workspace.character_stage.setCurrentText(title)
        assert shown() == expected, title
    started = []
    monkeypatch.setattr(workspace, '_start_digimon', lambda task, path, stages=(): started.append((task, path.name, stages)))
    monkeypatch.setattr(gui.QFileDialog, 'getSaveFileName', staticmethod(lambda *a: (str(tmp_path / a[2]), '')))
    workspace._export_digimon_bundle('Fresh')
    workspace._export_digimon_bundle(workspace.ALL_CLASSES)
    assert started == [('export-bundle', 'newborn.dw1digimons', ('Fresh',)),
                       ('export-bundle', 'all-classes.dw1digimons', tuple(gui.STAGES.values()))]
