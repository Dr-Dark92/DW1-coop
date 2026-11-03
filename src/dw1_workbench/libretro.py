"""Minimal libretro frontend over ctypes.

The PS1 is emulated by a user-supplied libretro core (a .so on Linux, a .dll on Windows); this module
loads it, answers its environment queries, and turns its callbacks into Python values. It holds no Qt
objects so it can run on a worker thread. Only one core may be running per process because libretro
cores keep global state.
"""
from __future__ import annotations

import ctypes
import threading
from ctypes import (CFUNCTYPE, POINTER, Structure, c_bool, c_char_p, c_double, c_float, c_int16, c_size_t,
                    c_uint, c_uint64, c_void_p)
from dataclasses import dataclass
from pathlib import Path

API_VERSION = 1

# retro_pixel_format
PIXEL_0RGB1555, PIXEL_XRGB8888, PIXEL_RGB565 = 0, 1, 2
BYTES_PER_PIXEL = {PIXEL_0RGB1555: 2, PIXEL_XRGB8888: 4, PIXEL_RGB565: 2}

DEVICE_JOYPAD, DEVICE_ANALOG = 1, 5
JOYPAD_MASK = 256  # RETRO_DEVICE_ID_JOYPAD_MASK: ask for every button as one bitmask
MEMORY_SAVE_RAM = 0

# Joypad button IDs (RETRO_DEVICE_ID_JOYPAD_*). On a PlayStation core B is Cross, A Circle, Y Square, X Triangle.
BUTTONS = ("B", "Y", "Select", "Start", "Up", "Down", "Left", "Right", "A", "X", "L", "R", "L2", "R2", "L3", "R3")
BUTTON = {name: index for index, name in enumerate(BUTTONS)}

# Environment commands this frontend answers (RETRO_ENVIRONMENT_*).
EXPERIMENTAL = 0x10000
ENV_SET_ROTATION, ENV_GET_OVERSCAN, ENV_GET_CAN_DUPE = 1, 2, 3
ENV_SET_MESSAGE, ENV_SHUTDOWN, ENV_SET_PERFORMANCE_LEVEL = 6, 7, 8
ENV_GET_SYSTEM_DIRECTORY, ENV_SET_PIXEL_FORMAT, ENV_SET_INPUT_DESCRIPTORS = 9, 10, 11
ENV_SET_DISK_CONTROL_INTERFACE, ENV_GET_VARIABLE, ENV_SET_VARIABLES, ENV_GET_VARIABLE_UPDATE = 13, 15, 16, 17
ENV_GET_INPUT_DEVICE_CAPABILITIES, ENV_GET_SAVE_DIRECTORY, ENV_SET_SYSTEM_AV_INFO = 24, 31, 32
ENV_SET_SUBSYSTEM_INFO, ENV_SET_CONTROLLER_INFO, ENV_SET_MEMORY_MAPS, ENV_SET_GEOMETRY = 34, 35, 36, 37
ENV_GET_LANGUAGE, ENV_SET_SUPPORT_ACHIEVEMENTS, ENV_GET_INPUT_BITMASKS = 39, 42, 51
ENV_GET_CORE_OPTIONS_VERSION, ENV_GET_MESSAGE_INTERFACE_VERSION, ENV_SET_MESSAGE_EXT = 52, 59, 60
ACCEPTED_WITHOUT_DATA = {ENV_SET_ROTATION, ENV_SET_PERFORMANCE_LEVEL, ENV_SET_INPUT_DESCRIPTORS,
                         ENV_SET_DISK_CONTROL_INTERFACE, ENV_SET_SUBSYSTEM_INFO, ENV_SET_CONTROLLER_INFO,
                         ENV_SET_MEMORY_MAPS, ENV_SET_SUPPORT_ACHIEVEMENTS}


class LibretroError(RuntimeError):
    pass


class SystemInfo(Structure):
    _fields_ = [("library_name", c_char_p), ("library_version", c_char_p), ("valid_extensions", c_char_p),
                ("need_fullpath", c_bool), ("block_extract", c_bool)]


class GameGeometry(Structure):
    _fields_ = [("base_width", c_uint), ("base_height", c_uint), ("max_width", c_uint),
                ("max_height", c_uint), ("aspect_ratio", c_float)]


class SystemTiming(Structure):
    _fields_ = [("fps", c_double), ("sample_rate", c_double)]


class SystemAvInfo(Structure):
    _fields_ = [("geometry", GameGeometry), ("timing", SystemTiming)]


class GameInfo(Structure):
    _fields_ = [("path", c_char_p), ("data", c_void_p), ("size", c_size_t), ("meta", c_char_p)]


class Variable(Structure):
    _fields_ = [("key", c_char_p), ("value", c_void_p)]


class Message(Structure):
    _fields_ = [("msg", c_char_p), ("frames", c_uint)]


EnvironmentCallback = CFUNCTYPE(c_bool, c_uint, c_void_p)
VideoCallback = CFUNCTYPE(None, c_void_p, c_uint, c_uint, c_size_t)
AudioSampleCallback = CFUNCTYPE(None, c_int16, c_int16)
AudioBatchCallback = CFUNCTYPE(c_size_t, c_void_p, c_size_t)
InputPollCallback = CFUNCTYPE(None)
InputStateCallback = CFUNCTYPE(c_int16, c_uint, c_uint, c_uint, c_uint)

_SIGNATURES = {
    "retro_api_version": (c_uint, []),
    "retro_set_environment": (None, [EnvironmentCallback]),
    "retro_set_video_refresh": (None, [VideoCallback]),
    "retro_set_audio_sample": (None, [AudioSampleCallback]),
    "retro_set_audio_sample_batch": (None, [AudioBatchCallback]),
    "retro_set_input_poll": (None, [InputPollCallback]),
    "retro_set_input_state": (None, [InputStateCallback]),
    "retro_init": (None, []),
    "retro_deinit": (None, []),
    "retro_get_system_info": (None, [POINTER(SystemInfo)]),
    "retro_get_system_av_info": (None, [POINTER(SystemAvInfo)]),
    "retro_set_controller_port_device": (None, [c_uint, c_uint]),
    "retro_reset": (None, []),
    "retro_run": (None, []),
    "retro_serialize_size": (c_size_t, []),
    "retro_serialize": (c_bool, [c_void_p, c_size_t]),
    "retro_unserialize": (c_bool, [c_void_p, c_size_t]),
    "retro_load_game": (c_bool, [POINTER(GameInfo)]),
    "retro_unload_game": (None, []),
    "retro_get_memory_data": (c_void_p, [c_uint]),
    "retro_get_memory_size": (c_size_t, [c_uint]),
}

_libraries: dict[str, ctypes.CDLL] = {}
_active_lock = threading.Lock()


def _library(path: Path) -> ctypes.CDLL:
    # Cores are never unloaded: dlclose/FreeLibrary of a core with live threads or atexit hooks can crash.
    key = str(path.resolve())
    if key not in _libraries:
        try:
            _libraries[key] = ctypes.CDLL(key)
        except OSError as exc:
            raise LibretroError(f"Could not load the core {path}: {exc}") from exc
    return _libraries[key]


def _text(value: bytes | None) -> str:
    return value.decode("utf-8", "replace") if value else ""


@dataclass(frozen=True)
class CoreInfo:
    name: str
    version: str
    extensions: str
    need_fullpath: bool


@dataclass(frozen=True)
class Frame:
    """One video frame as the core produced it; pixel_format selects how the bytes are read."""
    data: bytes
    width: int
    height: int
    pitch: int
    pixel_format: int


def core_info(path: Path) -> CoreInfo:
    """Read a core's identity without starting it."""
    lib = _bind(_library(path), path)
    info = SystemInfo(); lib.retro_get_system_info(ctypes.byref(info))
    return CoreInfo(_text(info.library_name), _text(info.library_version), _text(info.valid_extensions),
                    bool(info.need_fullpath))


def _bind(lib: ctypes.CDLL, path: Path) -> ctypes.CDLL:
    missing = []
    for name, (restype, argtypes) in _SIGNATURES.items():
        try:
            function = getattr(lib, name)
        except AttributeError:
            missing.append(name); continue
        function.restype, function.argtypes = restype, argtypes
    if missing:
        raise LibretroError(f"{path.name} is not a libretro core (missing {', '.join(missing[:3])}).")
    version = lib.retro_api_version()
    if version != API_VERSION:
        raise LibretroError(f"{path.name} uses libretro API {version}; this frontend supports {API_VERSION}.")
    return lib


class LibretroCore:
    """One running core and game. Every method must be called from the thread that called start()."""

    def __init__(self, core_path: Path, system_dir: Path, save_dir: Path,
                 options: dict[str, str] | None = None, log=None):
        self.core_path, self.system_dir, self.save_dir = Path(core_path), Path(system_dir), Path(save_dir)
        self._log = log or (lambda message: None)
        self._overrides = dict(options or {})
        self.options: dict[str, str] = {}  # key -> value in use, after SET_VARIABLES defaults and overrides
        self.option_choices: dict[str, list[str]] = {}
        self._option_buffers: dict[str, ctypes.Array] = {}
        self._options_changed = False
        self._system_c = ctypes.create_string_buffer(str(self.system_dir).encode())
        self._save_c = ctypes.create_string_buffer(str(self.save_dir).encode())
        self.pixel_format = PIXEL_0RGB1555  # libretro's default until the core asks for another
        self.buttons = 0  # bitmask of BUTTON indices for port 0; written by the GUI thread, read here
        self.shutdown_requested = False
        self.info: CoreInfo | None = None
        self.av: SystemAvInfo | None = None
        self._frame: Frame | None = None
        self._audio = bytearray()
        self._game_data = None
        self._lib = None
        self._running = self._loaded = self._owns_lock = False
        self._callbacks = (EnvironmentCallback(self._environment), VideoCallback(self._video),
                           AudioSampleCallback(self._audio_sample), AudioBatchCallback(self._audio_batch),
                           InputPollCallback(lambda: None), InputStateCallback(self._input_state))

    # -- lifecycle -----------------------------------------------------------------------------------------

    def start(self, game_path: Path):
        if not _active_lock.acquire(blocking=False):
            raise LibretroError("Another game is already running; close it first.")
        self._owns_lock = True
        try:
            self._start(Path(game_path))
        except BaseException:
            self._teardown()
            raise

    def _start(self, game_path: Path):
        lib = self._lib = _bind(_library(self.core_path), self.core_path)
        environment, video, sample, batch, poll, state = self._callbacks
        lib.retro_set_environment(environment)
        lib.retro_init(); self._running = True
        lib.retro_set_video_refresh(video); lib.retro_set_audio_sample(sample)
        lib.retro_set_audio_sample_batch(batch); lib.retro_set_input_poll(poll); lib.retro_set_input_state(state)
        info = SystemInfo(); lib.retro_get_system_info(ctypes.byref(info))
        self.info = CoreInfo(_text(info.library_name), _text(info.library_version), _text(info.valid_extensions),
                             bool(info.need_fullpath))
        self._log(f"Core {self.info.name} {self.info.version}; game {game_path}")
        game = GameInfo(str(game_path).encode(), None, 0, None)
        if not self.info.need_fullpath:
            self._game_data = ctypes.create_string_buffer(game_path.read_bytes())
            game.data, game.size = ctypes.cast(self._game_data, c_void_p), len(self._game_data) - 1
        if not lib.retro_load_game(ctypes.byref(game)):
            raise LibretroError("The core could not load the game. Check the BIOS folder and the disc image; "
                                "the core's own messages are in the launch log or the terminal.")
        self._loaded = True
        self.av = SystemAvInfo(); lib.retro_get_system_av_info(ctypes.byref(self.av))
        lib.retro_set_controller_port_device(0, DEVICE_JOYPAD)
        timing, geometry = self.av.timing, self.av.geometry
        self._log(f"Video {geometry.base_width}×{geometry.base_height} at {timing.fps:.3f} fps; "
                  f"audio {timing.sample_rate:.0f} Hz")

    def stop(self):
        self._teardown()

    def _teardown(self):
        if self._lib is not None and self._running:
            if self._loaded:
                self._lib.retro_unload_game()
            self._lib.retro_deinit()
        self._running = self._loaded = False
        self._lib = self._game_data = None
        if self._owns_lock:
            self._owns_lock = False
            _active_lock.release()

    # -- per frame -----------------------------------------------------------------------------------------

    @property
    def fps(self) -> float:
        return self.av.timing.fps if self.av and self.av.timing.fps > 0 else 60.0

    @property
    def sample_rate(self) -> int:
        return round(self.av.timing.sample_rate) if self.av and self.av.timing.sample_rate > 0 else 44100

    @property
    def aspect_ratio(self) -> float:
        geometry = self.av.geometry if self.av else None
        if geometry and geometry.aspect_ratio > 0:
            return geometry.aspect_ratio
        if geometry and geometry.base_height:
            return geometry.base_width / geometry.base_height
        return 4 / 3

    def run_frame(self) -> tuple[Frame | None, bytes]:
        """Run one frame; returns the new frame (None when the core repeated the last one) and its audio."""
        self._frame, self._audio = None, bytearray()
        self._lib.retro_run()
        return self._frame, bytes(self._audio)

    def reset(self):
        self._lib.retro_reset()

    def serialize(self) -> bytes:
        size = self._lib.retro_serialize_size()
        if not size:
            raise LibretroError("This core does not support quick saves.")
        buffer = ctypes.create_string_buffer(size)
        if not self._lib.retro_serialize(buffer, size):
            raise LibretroError("The core refused to write a quick save at this moment.")
        return buffer.raw

    def unserialize(self, data: bytes):
        buffer = ctypes.create_string_buffer(data, len(data))
        if not self._lib.retro_unserialize(buffer, len(data)):
            raise LibretroError("The core rejected this quick save (made by another core or core version?).")

    def save_ram(self) -> bytes | None:
        pointer, size = self._lib.retro_get_memory_data(MEMORY_SAVE_RAM), self._lib.retro_get_memory_size(MEMORY_SAVE_RAM)
        return ctypes.string_at(pointer, size) if pointer and size else None

    def load_save_ram(self, data: bytes) -> bool:
        pointer, size = self._lib.retro_get_memory_data(MEMORY_SAVE_RAM), self._lib.retro_get_memory_size(MEMORY_SAVE_RAM)
        if not pointer or not size:
            return False
        ctypes.memmove(pointer, data, min(size, len(data)))
        return True

    # -- callbacks (run on the core's thread) --------------------------------------------------------------

    def _environment(self, command, data):
        try:
            return self._handle_environment(command & ~EXPERIMENTAL, data)
        except Exception as exc:  # an exception must never unwind into C
            self._log(f"Environment call {command} failed: {exc}")
            return False

    def _handle_environment(self, command, data):
        if command in ACCEPTED_WITHOUT_DATA:
            return True
        if not data and command not in (ENV_SHUTDOWN,):
            return False
        if command == ENV_GET_CAN_DUPE:
            ctypes.cast(data, POINTER(c_bool))[0] = True
        elif command == ENV_GET_OVERSCAN:
            ctypes.cast(data, POINTER(c_bool))[0] = False
        elif command == ENV_GET_SYSTEM_DIRECTORY:
            ctypes.cast(data, POINTER(c_void_p))[0] = ctypes.addressof(self._system_c)
        elif command == ENV_GET_SAVE_DIRECTORY:
            ctypes.cast(data, POINTER(c_void_p))[0] = ctypes.addressof(self._save_c)
        elif command == ENV_SET_PIXEL_FORMAT:
            pixel_format = ctypes.cast(data, POINTER(c_uint))[0]
            if pixel_format not in BYTES_PER_PIXEL:
                return False
            self.pixel_format = pixel_format
        elif command == ENV_SET_VARIABLES:
            self._set_variables(ctypes.cast(data, POINTER(Variable)))
        elif command == ENV_GET_VARIABLE:
            variable = ctypes.cast(data, POINTER(Variable)).contents
            key = _text(variable.key)
            if key not in self.options:
                return False
            variable.value = ctypes.addressof(self._option_buffers[key])
        elif command == ENV_GET_VARIABLE_UPDATE:
            ctypes.cast(data, POINTER(c_bool))[0] = self._options_changed
            self._options_changed = False
        elif command == ENV_GET_CORE_OPTIONS_VERSION:
            ctypes.cast(data, POINTER(c_uint))[0] = 0  # legacy SET_VARIABLES only
        elif command == ENV_GET_INPUT_BITMASKS:
            return True
        elif command == ENV_GET_INPUT_DEVICE_CAPABILITIES:
            ctypes.cast(data, POINTER(c_uint64))[0] = (1 << DEVICE_JOYPAD) | (1 << DEVICE_ANALOG)
        elif command == ENV_GET_LANGUAGE:
            ctypes.cast(data, POINTER(c_uint))[0] = 0  # English
        elif command == ENV_GET_MESSAGE_INTERFACE_VERSION:
            ctypes.cast(data, POINTER(c_uint))[0] = 1
        elif command in (ENV_SET_MESSAGE, ENV_SET_MESSAGE_EXT):
            # Both structures start with the message pointer.
            self._log(f"Core: {_text(ctypes.cast(data, POINTER(c_char_p))[0])}")
        elif command == ENV_SHUTDOWN:
            self.shutdown_requested = True
        elif command == ENV_SET_GEOMETRY:
            if self.av is not None:
                self.av.geometry = ctypes.cast(data, POINTER(GameGeometry))[0]
        elif command == ENV_SET_SYSTEM_AV_INFO:
            self.av = SystemAvInfo.from_buffer_copy(ctypes.string_at(data, ctypes.sizeof(SystemAvInfo)))
            self._log(f"Core changed timing to {self.av.timing.fps:.3f} fps, {self.av.timing.sample_rate:.0f} Hz")
        else:
            return False
        return True

    def _set_variables(self, variables):
        index = 0
        while variables[index].key:
            key = _text(variables[index].key)
            description = _text(ctypes.cast(variables[index].value, c_char_p).value)
            # "Description; default|other|..."; the first choice is the default.
            choices = description.split(";", 1)[-1].strip().split("|")
            self.option_choices[key] = choices
            value = self._overrides.get(key, choices[0])
            if value not in choices:
                self._log(f"Option {key}={value} is not offered by the core; using {choices[0]}")
                value = choices[0]
            self.options[key] = value
            self._option_buffers[key] = ctypes.create_string_buffer(value.encode())
            index += 1

    def _video(self, data, width, height, pitch):
        if data:  # NULL means "repeat the previous frame"
            self._frame = Frame(ctypes.string_at(data, pitch * height), width, height, pitch, self.pixel_format)

    def _audio_sample(self, left, right):
        self._audio += int(left & 0xFFFF).to_bytes(2, "little") + int(right & 0xFFFF).to_bytes(2, "little")

    def _audio_batch(self, data, frames):
        if data and frames:
            self._audio += ctypes.string_at(data, frames * 4)
        return frames

    def _input_state(self, port, device, index, button):
        if port != 0 or device != DEVICE_JOYPAD:
            return 0
        buttons = self.buttons
        value = buttons & 0xFFFF if button == JOYPAD_MASK else (buttons >> button) & 1
        return value - 0x10000 if value >= 0x8000 else value
