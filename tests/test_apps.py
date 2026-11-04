import os
import subprocess
from pathlib import Path

import pytest
from PySide6.QtCore import QLockFile, QSettings
from PySide6.QtWidgets import QApplication

from dw1_workbench import app_icon as I
from dw1_workbench import gui, play_panel
from dw1_workbench.gui import APP_NAME, LAST_PROJECT, MainWindow
from dw1_workbench.quicksave import QuickSaveStore


@pytest.fixture(autouse=True)
def application():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


@pytest.fixture
def settings(tmp_path):
    return QSettings(str(tmp_path / "qt.ini"), QSettings.Format.IniFormat)


class Catalogue:
    project_dir = Path("D:/somewhere/project")

    def meta(self):
        return {"source_path": "D:/somewhere/game.cue"}


def opened_window(monkeypatch, settings, opener):
    shown = []
    monkeypatch.setattr(gui, "open_catalogue", opener)
    monkeypatch.setattr(gui, "Workspace", lambda catalogue, parent: Page(catalogue))
    monkeypatch.setattr(gui, "apply_icon", lambda application, disc: shown.append(disc))
    return MainWindow(settings), shown


class Page(gui.QWidget):
    def __init__(self, catalogue):
        super().__init__()
        self.background_finished = Signal()


class Signal:
    def connect(self, _):
        pass


def test_the_game_opened_last_time_is_reopened(monkeypatch, settings, tmp_path):
    window, icons = opened_window(monkeypatch, settings, lambda path: Catalogue())
    assert window.windowTitle() == APP_NAME
    assert not window.open_last_project()  # nothing remembered yet: the welcome page stays
    assert window.stack.count() == 1 and not window.shortcuts_action.isEnabled()
    window.show_workspace(Catalogue())
    assert settings.value(LAST_PROJECT) == str(Catalogue.project_dir)
    assert icons == [Path("D:/somewhere/game.cue")]
    again, _ = opened_window(monkeypatch, settings, lambda path: Catalogue())
    assert again.open_last_project() and again.stack.count() == 2


def test_a_missing_game_leaves_the_welcome_page_and_the_memory(monkeypatch, settings):
    settings.setValue(LAST_PROJECT, "D:/gone")

    def missing(path):
        raise FileNotFoundError(path)

    window, _ = opened_window(monkeypatch, settings, missing)
    assert not window.open_last_project()
    assert window.stack.count() == 1 and "D:/gone" in window.statusBar().currentMessage()
    assert settings.value(LAST_PROJECT) == "D:/gone"


def test_without_settings_nothing_is_remembered(monkeypatch):
    monkeypatch.setattr(gui, "open_catalogue", lambda path: pytest.fail("nothing should open"))
    assert not MainWindow().open_last_project()


def test_icon_is_made_once_from_the_disc(monkeypatch, tmp_path):
    from PySide6.QtGui import QImage
    calls = []

    def render(disc, kind, size):
        calls.append((disc, kind, size))
        image = QImage(size, size, QImage.Format.Format_ARGB32); image.fill(0xFF00FF00)
        return image

    monkeypatch.setattr(I, "render_character", render)
    png = I.ensure_icon(Path("game.cue"), tmp_path)
    assert png == tmp_path / "agumon.png" and png.with_suffix(".ico").is_file()
    assert calls == [(Path("game.cue"), I.AGUMON, I.ICON_SIZE)]
    assert I.ensure_icon(Path("game.cue"), tmp_path) == png and len(calls) == 1


def test_no_icon_when_the_disc_cannot_supply_it(monkeypatch, tmp_path):
    def unreadable(*_):
        raise FileNotFoundError("game.cue")

    monkeypatch.setattr(I, "render_character", unreadable)
    assert I.ensure_icon(Path("game.cue"), tmp_path) is None


def test_shortcut_script_names_both_apps_and_quotes_paths():
    script = I.shortcut_script(Path("C:/it's/agumon.ico"), Path("C:/venv/pythonw.exe"))
    assert "'DW1-settings.lnk'" in script and "'DW1-play.lnk'" in script
    assert "'-m dw1_workbench.app'" in script and "'-m dw1_workbench.play_app'" in script
    assert "it''s" in script


class Panel:
    """launch_directly's choice, without a whole Play panel."""
    launch_directly = play_panel.PlayPanel.launch_directly

    def __init__(self, store, starts=True):
        self.store, self.starts, self.launched = store, starts, []
        self.window = self._patch_thread = None

    def launch(self, slot):
        self.launched.append(slot)
        self.window = object() if self.starts else None


def test_direct_launch_powers_on_even_with_a_resume_slot(tmp_path):
    store = QuickSaveStore(tmp_path)
    panel = Panel(store)
    assert panel.launch_directly() and panel.launched == [None]
    store.save(0, b"state", None, "core", "1")
    panel = Panel(store)
    assert panel.launch_directly() and panel.launched == [None]
    panel = Panel(store, starts=False)
    assert not panel.launch_directly() and panel.launched == [None]