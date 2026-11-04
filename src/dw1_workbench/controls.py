"""Controllers for both players: reading them with what the system already has, and which input is which button.

Windows: winmm's joystick API, which every controller driver feeds, XInput ones included (the triggers share one axis).
Linux: the /dev/input/js* devices. An input is named "b0" (button), "a1-" or "a1+" (axis past halfway), "h0up" (POV hat).
"""
from __future__ import annotations

import ctypes
import json
import os
import struct
import sys
import time
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path

PLAYERS = 2
AXIS_THRESHOLD = 16000
RESCAN_SECONDS = 2.0
SETTINGS_KEY = "controls/players"
# The game's buttons, by libretro name as elsewhere in the player, with what the PlayStation pad calls them.
BUTTON_LABELS = {"Up": "D-pad up", "Down": "D-pad down", "Left": "D-pad left", "Right": "D-pad right",
                 "B": "Cross", "A": "Circle", "Y": "Square", "X": "Triangle",
                 "L": "L1", "R": "R1", "L2": "L2", "R2": "R2", "Start": "Start", "Select": "Select"}
# An Xbox-style layout. D-pad: the hat, the left stick, and on Linux the axes that report a D-pad.
DEFAULT_MAPPING = {"Up": ["h0up", "a1-", "a7-"], "Down": ["h0down", "a1+", "a7+"],
                   "Left": ["h0left", "a0-", "a6-"], "Right": ["h0right", "a0+", "a6+"],
                   "B": ["b0"], "A": ["b1"], "Y": ["b2"], "X": ["b3"], "L": ["b4"], "R": ["b5"],
                   "L2": ["a2+"], "R2": ["a2-"] if sys.platform == "win32" else ["a5+"], "Select": ["b6"], "Start": ["b7"]}


def describe_input(name: str) -> str:
    if name.startswith("b"):
        return f"Button {int(name[1:]) + 1}"
    if name.startswith("a"):
        return f"Axis {int(name[1:-1]) + 1} {'+' if name[-1] == '+' else '−'}"
    if name.startswith("h"):
        return f"Hat {name[2:]}"
    return name


def describe_inputs(names: list[str]) -> str:
    """The first input, with a count of the others that also work."""
    if not names:
        return "not set"
    return describe_input(names[0]) + (f" (+{len(names) - 1})" if len(names) > 1 else "")


@dataclass
class PlayerControls:
    pad: int | None  # position in the list of connected controllers; None: no controller
    mapping: dict[str, list[str]] = field(default_factory=lambda: {k: list(v) for k, v in DEFAULT_MAPPING.items()})

    def held(self, inputs: set[str]) -> set[str]:
        """The game's buttons these inputs hold."""
        return {button for button, names in self.mapping.items() if inputs.intersection(names)}


def default_controls() -> list[PlayerControls]:
    return [PlayerControls(player) for player in range(PLAYERS)]


def load_controls(settings) -> list[PlayerControls]:
    """Saved controls; anything unreadable falls back to the defaults for that player."""
    players = default_controls()
    try:
        saved = json.loads(settings.value(SETTINGS_KEY, "") or "[]")
    except (TypeError, ValueError):
        return players
    if not isinstance(saved, list):
        return players
    for player, record in zip(players, saved):
        if not isinstance(record, dict):
            continue
        pad = record.get("pad")
        if pad is None or type(pad) is int and pad >= 0:
            player.pad = pad
        mapping = record.get("mapping")
        if isinstance(mapping, dict):
            player.mapping = {button: [str(name) for name in mapping[button]] if isinstance(mapping.get(button), list)
                              else list(DEFAULT_MAPPING[button]) for button in BUTTON_LABELS}
    return players


def save_controls(settings, players: list[PlayerControls]):
    settings.setValue(SETTINGS_KEY, json.dumps([{"pad": p.pad, "mapping": p.mapping} for p in players]))


class _JoyInfoEx(ctypes.Structure):
    _fields_ = [(name, wintypes.DWORD) for name in ("dwSize", "dwFlags", "dwXpos", "dwYpos", "dwZpos", "dwRpos", "dwUpos",
                                                    "dwVpos", "dwButtons", "dwButtonNumber", "dwPOV", "dwReserved1",
                                                    "dwReserved2")]


class _JoyCaps(ctypes.Structure):
    _fields_ = [("wMid", wintypes.WORD), ("wPid", wintypes.WORD), ("szPname", wintypes.WCHAR * 32),
                *[(name, wintypes.UINT) for name in ("wXmin", "wXmax", "wYmin", "wYmax", "wZmin", "wZmax", "wNumButtons",
                                                     "wPeriodMin", "wPeriodMax", "wRmin", "wRmax", "wUmin", "wUmax",
                                                     "wVmin", "wVmax", "wCaps", "wMaxAxes", "wNumAxes", "wMaxButtons")],
                ("szRegKey", wintypes.WCHAR * 32), ("szOEMVxD", wintypes.WCHAR * 260)]


class WindowsPads:
    JOY_RETURNALL, POV_CENTERED = 0xFF, 0xFFFF

    def __init__(self):
        self._winmm = ctypes.WinDLL("winmm")
        self._ids: list[int] = []
        self._axes: dict[int, int] = {}
        self._names: list[str] = []
        self._scanned = 0.0

    def _state(self, joystick: int) -> _JoyInfoEx | None:
        info = _JoyInfoEx(dwSize=ctypes.sizeof(_JoyInfoEx), dwFlags=self.JOY_RETURNALL)
        return info if self._winmm.joyGetPosEx(joystick, ctypes.byref(info)) == 0 else None

    def _scan(self):
        now = time.monotonic()
        if now - self._scanned < RESCAN_SECONDS:
            return
        self._scanned = now
        self._ids, self._names = [], []
        for joystick in range(self._winmm.joyGetNumDevs()):
            if self._state(joystick) is None:
                continue
            caps = _JoyCaps()
            axes = 2
            if self._winmm.joyGetDevCapsW(joystick, ctypes.byref(caps), ctypes.sizeof(_JoyCaps)) == 0:
                axes = caps.wNumAxes
                buttons = caps.wNumButtons
            else:
                buttons = 0
            self._ids.append(joystick); self._axes[joystick] = axes
            self._names.append(f"Controller {len(self._ids)} ({axes} axes, {buttons} buttons)")

    def names(self) -> list[str]:
        self._scan()
        return list(self._names)

    def read(self, pad: int) -> set[str]:
        self._scan()
        if not 0 <= pad < len(self._ids):
            return set()
        joystick = self._ids[pad]
        info = self._state(joystick)
        if info is None:
            return set()
        inputs = {f"b{bit}" for bit in range(32) if info.dwButtons >> bit & 1}
        values = (info.dwXpos, info.dwYpos, info.dwZpos, info.dwRpos, info.dwUpos, info.dwVpos)
        for axis in range(min(self._axes.get(joystick, 2), 6)):
            value = values[axis] - 32767
            if abs(value) > AXIS_THRESHOLD:
                inputs.add(f"a{axis}{'+' if value > 0 else '-'}")
        pov = info.dwPOV
        if pov != self.POV_CENTERED:
            if pov > 27000 or pov < 9000:
                inputs.add("h0up")
            if 0 < pov < 18000:
                inputs.add("h0right")
            if 9000 < pov < 27000:
                inputs.add("h0down")
            if pov > 18000:
                inputs.add("h0left")
        return inputs


class LinuxPads:
    EVENT = struct.Struct("<IhBB")  # time, value, type, number
    BUTTON, AXIS, INIT = 1, 2, 0x80

    def __init__(self, folder: str = "/dev/input"):
        self._folder = Path(folder)
        self._devices: dict[str, dict] = {}
        self._scanned = 0.0

    def _scan(self):
        now = time.monotonic()
        if now - self._scanned < RESCAN_SECONDS:
            return
        self._scanned = now
        found = {str(path) for path in sorted(self._folder.glob("js*"))}
        for path in list(self._devices):
            if path not in found:
                os.close(self._devices.pop(path)["fd"])
        for path in found - set(self._devices):
            try:
                self._devices[path] = {"fd": os.open(path, os.O_RDONLY | os.O_NONBLOCK), "buttons": {}, "axes": {}}
            except OSError:
                pass  # no permission, or it has just gone

    def names(self) -> list[str]:
        self._scan()
        return [f"Controller {index + 1} ({Path(path).name})" for index, path in enumerate(sorted(self._devices))]

    def read(self, pad: int) -> set[str]:
        self._scan()
        paths = sorted(self._devices)
        if not 0 <= pad < len(paths):
            return set()
        device = self._devices[paths[pad]]
        try:
            while data := os.read(device["fd"], self.EVENT.size * 32):
                for offset in range(0, len(data) - self.EVENT.size + 1, self.EVENT.size):
                    _, value, kind, number = self.EVENT.unpack_from(data, offset)
                    kind &= ~self.INIT
                    if kind == self.BUTTON:
                        device["buttons"][number] = value
                    elif kind == self.AXIS:
                        device["axes"][number] = value
        except BlockingIOError:
            pass
        except OSError:
            os.close(device["fd"]); del self._devices[paths[pad]]
            return set()
        inputs = {f"b{number}" for number, value in device["buttons"].items() if value}
        for number, value in device["axes"].items():
            if abs(value) > AXIS_THRESHOLD:
                inputs.add(f"a{number}{'+' if value > 0 else '-'}")
        return inputs


class NoPads:
    def names(self) -> list[str]:
        return []

    def read(self, pad: int) -> set[str]:
        return set()


def system_pads():
    try:
        return WindowsPads() if sys.platform == "win32" else LinuxPads() if sys.platform.startswith("linux") else NoPads()
    except OSError:
        return NoPads()
