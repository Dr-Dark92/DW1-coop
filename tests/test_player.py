from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from dw1_workbench.libretro import BUTTON
from dw1_workbench.player import PlayConfig, PlayWindow
from dw1_workbench.quicksave import RESUME_SLOT, QuickSaveStore


@pytest.fixture(scope="module")
def application():
    yield QApplication.instance() or QApplication([])


def wait(application, predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        application.processEvents(); time.sleep(0.005)
    assert predicate(), "timed out"


def config(fake_core, tmp_path, **extra):
    game = tmp_path / "game.cue"
    if not game.exists():
        game.write_bytes(b"G")
    return PlayConfig(fake_core, tmp_path / "bios", game, tmp_path / "saves", **extra)


def key(window, qt_key, pressed):
    event = QKeyEvent(QEvent.Type.KeyPress if pressed else QEvent.Type.KeyRelease, qt_key, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(window.view, event)


def test_plays_saves_loads_and_resumes(application, fake_core, tmp_path, monkeypatch):
    monkeypatch.setattr("dw1_workbench.player.QMessageBox.warning", staticmethod(lambda *args: None))
    window = PlayWindow(config(fake_core, tmp_path))
    window.start()
    wait(application, lambda: window.view.image is not None)
    assert window.view.aspect == 2.0
    key(window, Qt.Key.Key_Return, True)
    assert window.worker.buttons == 1 << BUTTON["Start"]
    key(window, Qt.Key.Key_Return, False)
    assert window.worker.buttons == 0
    saved = []
    window.slots_changed.connect(lambda: saved.append(True))
    window.note.setText("Start screen")
    window.worker.quick_save(2, window.note.text())
    wait(application, lambda: saved)
    store = QuickSaveStore(tmp_path / "saves")
    assert store.info(2).note == "Start screen"
    assert store.screenshot_path(2) is not None
    window.worker.quick_load(2)
    window.worker.quick_load(5)  # empty: reported, the game keeps running
    wait(application, lambda: "empty" in window.status.text())
    window.close()
    assert store.info(RESUME_SLOT) is not None
    assert store.read_memory_card() is not None


def test_launch_into_slot_and_failed_boot(application, fake_core, tmp_path, monkeypatch):
    errors = []
    monkeypatch.setattr("dw1_workbench.player.QMessageBox.critical", staticmethod(lambda *args: errors.append(args[2])))
    store = QuickSaveStore(tmp_path / "saves")
    store.save(1, b"FAKE" + (40).to_bytes(4, "little"), None, "Fake Core", "1.0")
    window = PlayWindow(config(fake_core, tmp_path, launch_slot=1, resume_on_close=False))
    window.start()
    wait(application, lambda: window.view.image is not None)
    assert window.view.image.pixel(0, 0) & 0xFF >= 40  # the counter continued from the slot
    window.close()
    assert store.info(RESUME_SLOT) is None

    bad = tmp_path / "bad"; bad.mkdir(); (bad / "game.cue").write_bytes(b"X")
    failing = PlayWindow(config(fake_core, bad))
    failing.start()
    wait(application, lambda: errors)
    assert "could not load the game" in errors[0]
    failing.close()


def test_play_panel_checks_launches_and_lists_slots(application, fake_core, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    import dw1_workbench.play_panel as play_panel
    monkeypatch.setattr(play_panel, "saves_root", lambda: tmp_path / "root")
    monkeypatch.setattr(play_panel, "find_installed_core", lambda: None)  # ignore a core installed on this machine
    monkeypatch.setattr(play_panel, "QSettings", lambda *args: QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    game = tmp_path / "game.cue"; game.write_bytes(b"G")
    bios = tmp_path / "bios"; bios.mkdir(); (bios / "scph1001.bin").write_bytes(b"")
    panel = play_panel.PlayPanel({"source_path": str(game), "source_sha256": "ab" * 32}, True, "ok")
    assert not panel.play.isEnabled()
    panel.core.setText(str(fake_core)); panel.bios.setText(str(bios)); panel._check()
    assert panel.core_status.text() == "Fake Core 1.0"
    assert "scph5501.bin is missing (scph1001.bin found)" in panel.bios_status.text()
    assert panel.play.isEnabled() and panel.store.directory == tmp_path / "root" / ("ab" * 8)
    panel.launch(None)
    assert not panel.play.isEnabled()
    wait(application, lambda: panel.window.view.image is not None)
    panel.close_player()
    wait(application, lambda: panel.window is None)
    assert panel.slots.rowCount() == 1 and panel.slots.item(0, 0).text() == "Resume"
    assert "Game closed" in panel.log.toPlainText()
    assert (panel.store.directory / "launch.log").is_file()
