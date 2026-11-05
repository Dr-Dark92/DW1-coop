"""The menu selects rendering resolution, never a hidden camera patch."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QSlider

from dw1_workbench.play_panel import PlayPanel
from dw1_workbench.player import WORLD_ZOOM_ENABLED
from dw1_workbench.shared_data import app_settings


def panel():
    QApplication.instance() or QApplication([])
    return PlayPanel({"source_path": "/missing/game.cue", "source_sha256": "ab" * 32}, False, "test")


def core_resolution(play_panel):
    return play_panel._picture_options()["beetle_psx_internal_resolution"]


def test_menu_offers_4x_persists_and_selects_exact_core_option():
    first = panel()
    assert core_resolution(first) == "2x"
    assert not hasattr(first, "camera_zoom") and not first.findChildren(QSlider)
    assert WORLD_ZOOM_ENABLED is False
    first.resolution.setCurrentText("4x (may stutter)")
    assert core_resolution(first) == "4x"
    assert core_resolution(panel()) == "4x"
    first.resolution.setCurrentText("Native (1x)")
    assert core_resolution(first) == "1x(native)"
    assert core_resolution(panel()) == "1x(native)"
    assert "original camera" in first.resolution.toolTip()


@pytest.mark.parametrize("ticked, saved, expected", [
    (False, "4x (may stutter)", "1x(native)"),  # unticked meant native
    (True, "Native (1x)", "2x"),  # ticked over an old native setting
    (True, "4x (may stutter)", "4x"),  # the checkbox had no 4x: the saved choice comes back
    (True, None, "2x"),
])
def test_the_checkbox_setting_is_carried_over_once(ticked, saved, expected):
    settings = app_settings()
    settings.setValue("play/fixed_x2", ticked)
    if saved is not None:
        settings.setValue("play/resolution", saved)
    assert core_resolution(panel()) == expected
    assert not app_settings().contains("play/fixed_x2")
    assert core_resolution(panel()) == expected
