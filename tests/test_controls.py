import json
import os
import struct
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from dw1_workbench import controls as C
from dw1_workbench.controls_view import ControlsPanel
from dw1_workbench.libretro import BUTTON


@pytest.fixture(autouse=True)
def application():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


@pytest.fixture
def settings(tmp_path):
    return QSettings(str(tmp_path / "qt.ini"), QSettings.Format.IniFormat)


class Pads:
    def __init__(self, count=2):
        self.inputs = [set() for _ in range(count)]

    def names(self):
        return [f"Pad {i + 1}" for i in range(len(self.inputs))]

    def read(self, pad):
        return set(self.inputs[pad]) if 0 <= pad < len(self.inputs) else set()


def test_held_buttons_and_every_default_is_a_game_button():
    player = C.PlayerControls(0)
    assert player.held({"b0", "a1-"}) == {"B", "Up"}
    assert player.held({"h0up"}) == {"Up"} and player.held(set()) == set()
    assert set(C.DEFAULT_MAPPING) == set(C.BUTTON_LABELS) and all(button in BUTTON for button in C.BUTTON_LABELS)


def test_controls_are_saved_and_bad_records_fall_back(settings):
    players = C.default_controls()
    assert [p.pad for p in players] == [0, 1]
    players[1].pad = None
    players[1].mapping["B"] = ["b9"]
    C.save_controls(settings, players)
    loaded = C.load_controls(settings)
    assert loaded[1].pad is None and loaded[1].mapping["B"] == ["b9"] and loaded[0].mapping["B"] == ["b0"]
    settings.setValue(C.SETTINGS_KEY, "not json")
    assert C.load_controls(settings)[1].pad == 1
    settings.setValue(C.SETTINGS_KEY, json.dumps([{"pad": -3, "mapping": {"B": "b1"}}, 5]))
    loaded = C.load_controls(settings)
    assert loaded[0].pad == 0 and loaded[0].mapping["B"] == ["b0"] and loaded[1].pad == 1


def test_inputs_are_described_for_people():
    assert C.describe_input("b0") == "Button 1" and C.describe_input("a1-") == "Axis 2 −" and C.describe_input("h0up") == "Hat up"
    assert C.describe_inputs([]) == "not set" and C.describe_inputs(["b0", "b1"]) == "Button 1 (+1)"


def panel_with(settings, pads):
    panel = ControlsPanel(settings, [{}, {}], "help", pads)
    panel.poll()
    return panel


def test_set_takes_the_next_new_input_and_saves(settings):
    pads = Pads()
    panel = panel_with(settings, pads)
    pads.inputs[1] = {"a2+"}  # a trigger already held doesn't count
    panel.capture(panel.boxes[1], "B")
    assert panel.boxes[1].values["B"].text() == "Press an input…"
    panel.poll()
    assert panel._capturing is not None
    pads.inputs[1] = {"a2+", "b5"}
    panel.poll()
    assert panel._capturing is None and panel.players[1].mapping["B"] == ["b5"]
    assert panel.boxes[1].values["B"].text() == "Button 6"
    assert C.load_controls(settings)[1].mapping["B"] == ["b5"]
    assert C.load_controls(settings)[0].mapping["B"] == ["b0"]
    panel.boxes[1]._reset()
    assert C.load_controls(settings)[1].mapping["B"] == ["b0"]


def test_set_gives_up_and_needs_a_controller(settings):
    pads = Pads()
    panel = panel_with(settings, pads)
    panel.capture(panel.boxes[0], "A")
    panel._capturing[3] = 1
    panel.poll()
    assert panel._capturing is None and panel.players[0].mapping["A"] == ["b1"] and "unchanged" in panel.status.text()
    panel.boxes[0].controls.pad = None
    panel.capture(panel.boxes[0], "A")
    assert panel._capturing is None and "Choose a controller" in panel.status.text()


def test_controller_list_follows_what_is_connected(settings):
    pads = Pads(1)
    panel = panel_with(settings, pads)
    assert panel.boxes[0].pad.currentText() == "Pad 1"
    assert panel.boxes[1].pad.currentText() == "Controller 2 (not connected)"
    pads.inputs = []
    panel.poll()
    assert "No controller" in panel.status.text()
    pads.inputs = [set(), set()]
    panel.poll()
    assert panel.boxes[1].pad.currentText() == "Pad 2" and "2 controllers" in panel.status.text()
    panel.boxes[1].pad.setCurrentIndex(0)
    panel.boxes[1]._pad_chosen(0)
    assert C.load_controls(settings)[1].pad is None


def test_live_line_shows_the_game_buttons(settings):
    pads = Pads()
    panel = panel_with(settings, pads)
    pads.inputs[0] = {"b0", "h0left"}
    panel.poll()
    assert panel.boxes[0].live.text() == "Pressed: D-pad left, Cross" and panel.boxes[1].live.text() == "Pressed: —"


def test_linux_event_decoding(monkeypatch, tmp_path):
    pads = C.LinuxPads(str(tmp_path))
    events = C.LinuxPads.EVENT.pack(1, 1, C.LinuxPads.BUTTON, 3) + C.LinuxPads.EVENT.pack(2, -30000, C.LinuxPads.AXIS | C.LinuxPads.INIT, 1)
    device = tmp_path / "js0"; device.write_bytes(events)
    if sys.platform == "win32":
        monkeypatch.setattr(C.os, "O_NONBLOCK", 0, raising=False)
    pads._scan()
    assert pads.names() == ["Controller 1 (js0)"]
    assert pads.read(0) == {"b3", "a1-"} and pads.read(4) == set()
    os.close(next(iter(pads._devices.values()))["fd"])


class Fake:
    def __init__(self):
        self.pressed, self.sent = [], []


def window_for_buttons(held=None):
    from dw1_workbench.player import PlayWindow
    window = PlayWindow.__new__(PlayWindow)
    from types import SimpleNamespace
    window._held, window._split_held = 0, 0
    window._pad_held, window._pad_buttons = [0, 0], [set(), set()]
    window.worker = SimpleNamespace(buttons=0, coop=None)
    window.config = SimpleNamespace(comparison_disc=None)
    window._split = None
    return window


class Split:
    def __init__(self, host):
        from types import SimpleNamespace
        self.buttons, self.sent = None, []
        self.link = SimpleNamespace(host=lambda: host, send_press=lambda player, press: self.sent.append((player, press)))

    def set_buttons(self, value):
        self.buttons = value


def test_controller_buttons_reach_the_right_game():
    bit = lambda name: 1 << BUTTON[name]
    window = window_for_buttons()
    window._pad_button(0, "B", True)
    assert window.worker.buttons == bit("B")
    window._pad_button(0, "B", False)
    assert window.worker.buttons == 0
    window._pad_button(1, "B", True)  # no second game yet
    assert window.worker.buttons == 0
    window._split = Split(None)
    window._pad_button(1, "B", False); window._pad_button(1, "Up", True)
    assert window._split.buttons == bit("Up") and window.worker.buttons == 0
    window._held, window._split_held = bit("Left"), bit("Right")
    window._send_buttons()  # keyboard and controller together
    assert window.worker.buttons == bit("Left") and window._split.buttons == bit("Up") | bit("Right")


def test_controller_in_a_shared_fight_orders_the_ally():
    from types import SimpleNamespace
    from dw1_workbench.player import ALLY_BUTTONS
    window = window_for_buttons()
    window._split = Split(1)  # player 2 runs the fight: player 1 orders the ally
    window._pad_button(0, "B", True)
    assert window._split.sent == [(1, ALLY_BUTTONS["B"])] and window.worker.buttons == 0
    pressed = []
    window._split = Split(0)  # player 1 runs it: player 2 orders the ally through the local fight
    window.worker.coop = SimpleNamespace(press=pressed.append)
    window._pad_button(1, "Left", True)
    assert pressed == [ALLY_BUTTONS["Left"]] and window._split.buttons is None
