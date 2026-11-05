"""Play window: runs the user's game through a libretro core on a worker thread.

The core runs on its own QThread at the core's frame rate. Frames, audio and messages reach the GUI
through queued signals only; the GUI talks back through thread-safe attributes and a command queue.
"""
from __future__ import annotations

import hashlib
import os
import queue
import struct
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL.ImageQt import ImageQt
from PySide6.QtCore import (QTimer, QBuffer, QCoreApplication, QPointF, QByteArray, QIODevice, QObject, QRectF, Qt,
                            QThread, Signal, Slot)
from PySide6.QtGui import QAction, QColor, QFont, QImage, QKeySequence, QPainter, QPen
from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDockWidget, QFormLayout, QLabel,
                               QLineEdit, QMainWindow, QMessageBox, QScrollArea, QSizePolicy, QSlider, QToolBar,
                               QVBoxLayout, QWidget)

from .camera import CameraZoom
from .fixed_framing import FrameController
from .status_panel import LevelBoxes, draw_level_boxes, read_digits, status_screen_open
from .controls import load_controls, system_pads
from .graphics import SCREENS, screen_rects
from .inspection import CATEGORIES, InspectionError, InspectionStore, SpawnLog
from .libretro import (BUTTON, PIXEL_0RGB1555, PIXEL_RGB565, PIXEL_XRGB8888, Frame, LibretroCore,
                       LibretroError)
from .live import CURRENT_SCREEN, LiveNpc, LiveState, RAM_SIZE, grid_differences, ram_offset, read_live_state, set_ram_size
from .maps import follow_kind, has_tracking_box, trigger_description, trigger_grid_image, tracking_box
from .coop import CONFIRM, LEFT, MONEY, RIGHT, SQUARE, CoopBattle, LinkedPlayer, PlayerTwo
from .hd_background import BackgroundLayer, Compositor, HdPictures, GAME_STATE, SCRIPT_MAP_CHANGE_STATE
from .shared_data import app_settings
from .telemetry import Telemetry
from .title_hd import TitleLayer
from .title_screens import TitleStore
from .split_screen import SplitGame, choosable_saves, copy_save
from .tamer_skin import Character, TamerSkin, disc_tamer
from .extra_memory import extend_heap
from .visitor import PARTNER, TAMER, Visitor, match_world, on_the_field, read_partner_stats, read_record, read_world
from .hud_view import HudPainter, HudState, command_icons, draw_ally_panel, read_hud
from .progression import BattleResult, GameRules, Progress
from .avatars import load_avatars_isolated
from .evolution import EvolutionController, EvolutionHistory
from .game_menu_view import evolution_menu_image, native_icons, native_portraits
from .menu_audio import MenuAudio, tone_pcm
from .digidex import DigiDex, load_placements
from .digidex_view import DigiDexDialog
from .errors import Cancelled, DiscFormatError
from .quicksave import RESUME_SLOT, SLOT_COUNT, QuickSaveError, QuickSaveStore, _atomic_write

# World zoom (camera.py) is switched off: in the user's desktop play it crashed the game and misplaced the
# player and NPCs (2026-11-03). The code stays for later work; nothing patches the game while this is False.
WORLD_ZOOM_ENABLED = False
FAST_FORWARD = 4  # speed while fast-forward is held
MOST_REPEATS = 15  # a still picture is still redrawn this often, for the map and bars over it
EXTEND_HEAP_FRAMES = 30
EXTENDED_RAM = 8 * 1024 * 1024
MEMORY_CARD_FLUSH_FRAMES = 600  # write a changed memory card about every 10 s
PAD_POLL_MS = 8  # controllers, read on the GUI thread
VIEW_SIZE_TRIES, VIEW_SIZE_RETRY_MS = 20, 50  # the 640 × 480 view button keeps trying for about a second
LIVE_FRAMES = 10  # read the live map, positions and grid about six times a second
IMAGE_FORMATS = {PIXEL_XRGB8888: QImage.Format.Format_RGB32, PIXEL_RGB565: QImage.Format.Format_RGB16,
                 PIXEL_0RGB1555: QImage.Format.Format_RGB555}

# Keyboard defaults. On the PlayStation core B is Cross, A Circle, Y Square and X Triangle.
DEFAULT_KEYS = {
    Qt.Key.Key_Up: "Up", Qt.Key.Key_Down: "Down", Qt.Key.Key_Left: "Left", Qt.Key.Key_Right: "Right",
    Qt.Key.Key_Z: "B", Qt.Key.Key_X: "A", Qt.Key.Key_A: "Y", Qt.Key.Key_S: "X",
    Qt.Key.Key_Q: "L", Qt.Key.Key_W: "R", Qt.Key.Key_E: "L2", Qt.Key.Key_R: "R2",
    Qt.Key.Key_Return: "Start", Qt.Key.Key_Enter: "Start", Qt.Key.Key_Backspace: "Select",
}
# Player 2's battle controls in the co-op test, the same as player 1's: Left, Right, confirm and finisher.
ALLY_BUTTONS = {"Left": LEFT, "Right": RIGHT, "B": CONFIRM, "Y": SQUARE}  # what the guest's buttons do in a shared fight
COOP_KEYS = {Qt.Key.Key_J: LEFT, Qt.Key.Key_L: RIGHT, Qt.Key.Key_K: CONFIRM, Qt.Key.Key_U: SQUARE}


# Player 2's whole controller while split screen is on.
SPLIT_KEYS = {Qt.Key.Key_I: "Up", Qt.Key.Key_K: "Down", Qt.Key.Key_J: "Left", Qt.Key.Key_L: "Right", Qt.Key.Key_U: "B",
              Qt.Key.Key_O: "A", Qt.Key.Key_Y: "Y", Qt.Key.Key_H: "X", Qt.Key.Key_N: "Start", Qt.Key.Key_M: "Select",
              Qt.Key.Key_7: "L", Qt.Key.Key_8: "R"}
KEY_HELP = ("Arrows: D-pad  •  Z Cross  X Circle  A Square  S Triangle  •  Q/W L1/R1  E/R L2/R2  •  "
            "Enter Start  Backspace Select  •  F4 Digi-Dex  •  F5 quick save  F8 quick load  F6/F7 slot  •  "
            "Co-op player 2: J/L command, K confirm, U finisher  •  Split screen player 2: I/J/K/L D-pad, U Cross, O Circle, Y Square, H Triangle, N Start  •  F9 inspect here  F10 live map  •  hold Tab fast-forward  •  P pause  Ctrl+R reset  •  F11 full screen")


@dataclass(frozen=True)
class PlayConfig:
    core_path: Path
    bios_dir: Path
    disc_path: Path
    store_dir: Path
    launch_slot: int | None = None
    launch_inspection: str | None = None  # start from an inspection's save state instead of a slot
    resume_on_close: bool = True
    live_ram: bool = False  # read map/position/grid from RAM; the addresses are only valid for USA SLUS_010.32
    hd_pictures: Path | None = None  # folder of full-size backgrounds (hd_background.py); needs live_ram
    coop_species: int | None = None  # player 2's Digimon for the co-op battle prototype (coop.py); needs live_ram
    coop_level: int = 20  # player 2's starting level
    game_rules: bool = False  # P6 rules (progression.py): levels/EXP, no aging or death; needs live_ram
    options: dict[str, str] = field(default_factory=dict)
    forms: dict[int, dict] = field(default_factory=dict)  # on the patched disc: imported Digimon, see EvolutionController
    offer_forms: bool = False  # the bank's egg list offers them
    double_frame: bool = False  # experimental: the camera shows twice the world while walking; 8 MB core, live_ram
    character: Path | None = None  # the folder of who to play as (tamer_skin.py)
    keep_tamer: bool = True  # False: put the disc's own Tamer back if a save was made as someone else
    framerate: float = 0.0  # frames per second to run at; 0 is the core's own. The game's speed follows it.


def stretched_audio(audio: bytes, speed: float) -> bytes:
    """Stereo 16-bit audio for a game running at `speed` times its own rate: the same sound, faster, so it keeps up with the
    device instead of the device dropping or starving. Its pitch follows, like any game run at another speed."""
    samples = np.frombuffer(audio, dtype="<i2").reshape(-1, 2)
    count = round(len(samples) / speed)
    if not count or count == len(samples):
        return audio
    where = np.arange(count) * (len(samples) / count)
    source = np.arange(len(samples))
    mixed = np.column_stack([np.interp(where, source, samples[:, side]) for side in (0, 1)])
    return mixed.astype("<i2").tobytes()


def frame_image(frame: Frame) -> QImage:
    return QImage(frame.data, frame.width, frame.height, frame.pitch, IMAGE_FORMATS[frame.pixel_format]).copy()


def battle_result_image(image: QImage, result: BattleResult | None) -> QImage:
    """Replace only the USA post-battle stats panel in the presented frame.

    Native rendering also applies rewards, so it must continue running underneath.
    Coordinates follow createPostBattleStatsBox: centred (-88,-78), 176x96.
    """
    if result is None:
        return image
    image = image.convertToFormat(QImage.Format.Format_RGB32)
    painter = QPainter(image)
    painter.scale(image.width() / 320, image.height() / 240)
    painter.fillRect(70, 40, 180, 100, QColor("#111c30"))
    painter.setPen(QColor("#fad990"))
    painter.drawRect(71, 41, 178, 98)
    font = QFont("Arial")
    font.setPixelSize(10)
    painter.setFont(font)
    for index, line in enumerate(result.lines):
        painter.setPen(QColor("#fad990" if index == 0 else "#ffffff"))
        painter.drawText(82, 57 + index * 17, line)
    painter.end()
    return image


def png_bytes(image: QImage | None) -> bytes | None:
    if image is None or image.isNull():
        return None
    data = QByteArray(); buffer = QBuffer(data); buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(data.data())


class EmulatorWorker(QObject):
    frame_ready = Signal()
    audio_ready = Signal(bytes)
    message = Signal(str)
    started_ok = Signal(object)  # dict with core identity and timing
    failed = Signal(str)
    slot_saved = Signal(object)  # SlotInfo
    slot_loaded = Signal(int)
    command_failed = Signal(str)
    live_changed = Signal(object)  # LiveState
    inspection_captured = Signal(object)  # dict: state, screenshot, live
    camera_zoom_changed = Signal(int, int)  # requested/effective fixed-point scale
    dex_changed = Signal(object)  # immutable snapshot from the core thread
    progress_changed = Signal(str)  # level/EXP summary while the game rules are on
    split_requested = Signal(object)  # a save slot for player 2, or "stop"
    finished = Signal()

    def __init__(self, config: PlayConfig):
        super().__init__()
        self._telemetry_dir = os.environ.get("DW1_COOP_TELEMETRY")
        self.telemetry = Telemetry(bool(self._telemetry_dir))
        self.config = config
        self.store = QuickSaveStore(config.store_dir)
        self.hd_enabled = True
        self.hud_enabled = True
        self._hud: HudState | None = None
        self._hud_painter: HudPainter | None = None
        self.save_slot: int | None = config.launch_slot  # the save player 1 is playing, which player 2 may not take
        self.split_slot: int | None = None  # the save player 2 started from
        self.link = None  # split_screen.WorldLink while split screen shares the partners' positions
        self.visitors: list[Visitor] = []
        self.player_label = "Player 1"  # this game's player; player 2's game says otherwise
        self._solo_coop: CoopBattle | None = None  # the Co-op battle test, put away during split screen
        self._linked: LinkedPlayer | None = None
        self._awards_seen = (0, 0)
        self._dex_seen = 0
        self._dex_sent: set[int] = set()
        self.coop: CoopBattle | None = None
        self._coop_icons = None
        self._coop_icon_tries = 0
        self._coop_frames = 0
        self.hd_layer: BackgroundLayer | None = None
        self._compositor: Compositor | None = None
        self.title_layer: TitleLayer | None = None
        self._previous_picture = None
        self._repeats = 0
        self._title_compositor: Compositor | None = None
        self.buttons = 0
        self.paused = False
        self.fast_forward = False
        self._stop = threading.Event()
        self._commands: queue.SimpleQueue = queue.SimpleQueue()
        self._image_lock = threading.Lock()
        self._image: QImage | None = None  # newest frame
        self._image_pending = False
        self._last_image: QImage | None = None
        self._card_hash = None
        self._live_key = None
        self.live: LiveState | None = None
        self.core: LibretroCore | None = None
        self._camera: CameraZoom | None = None
        self._camera_scale = 256
        self._frame: FrameController | None = None
        self._level_boxes: LevelBoxes | None = None
        self._status_digits = None
        self._camera_notice_sent = False
        self._reported_zoom = None
        self.rules: GameRules | None = None
        self.tamer_skin: TamerSkin | None = None
        self.evolution: EvolutionController | None = None
        self.dex: DigiDex | None = None
        self.dex_map_names = {}
        self._dex_write_error = False
        self._has_run_frame = False
        self._menu_icons = ()
        self._icons_attempted = False
        self._portraits = {}
        self._portraits_attempted = False
        self.history: EvolutionHistory | None = None
        self.avatars = {}  # replaced (never mutated) by the avatar thread, so the core thread reads it safely
        self._avatar_thread: threading.Thread | None = None
        self._menu_audio = None
        self._progress_path = config.store_dir / "progression.json"

    # -- called from the GUI thread ------------------------------------------------------------------------

    def request_stop(self):
        self._stop.set()

    def quick_save(self, slot: int, note: str = ""):
        self._commands.put(("save", slot, note))

    def quick_load(self, slot: int):
        self._commands.put(("load", slot))

    def reset(self):
        self._commands.put(("reset",))

    def set_camera_scale(self, scale: int):
        """Set world zoom only; the HUD and native frame remain unscaled by this control."""
        self._commands.put(("camera_scale", max(64, min(256, int(scale)))))

    def inspect(self):
        self._commands.put(("inspect",))

    def take_frame(self) -> QImage | None:
        with self._image_lock:
            self._image_pending = False
            return self._image

    # -- worker thread -------------------------------------------------------------------------------------

    @Slot()
    def run(self):
        config = self.config
        try:
            config.store_dir.mkdir(parents=True, exist_ok=True)
            self.core = LibretroCore(config.core_path, config.bios_dir, config.store_dir, config.options,
                                     log=self.message.emit, telemetry=self.telemetry)
            self.core.start(config.disc_path)
        except (LibretroError, OSError) as exc:
            self.failed.emit(str(exc)); self.finished.emit()
            return
        core = self.core
        self._menu_audio = MenuAudio(core.sample_rate)
        try:
            card = self.store.read_memory_card()
            if card is not None:
                core.load_save_ram(card); self._card_hash = hashlib.sha256(card).digest()
                self.message.emit(f"Memory card loaded from {self.store.memory_card_path}")
            if config.live_ram:
                try:
                    self.dex = DigiDex(config.store_dir / "digidex.json")
                except (OSError, ValueError) as exc:
                    self.message.emit(f"Digi-Dex unavailable: {exc}; existing file preserved")
                    self.dex_changed.emit({"error": str(exc)})
                if self.dex is not None:
                    try:
                        self.dex.map_names = self.dex_map_names
                        self.dex.placements = load_placements(config.disc_path, self._stop.is_set, self.dex_map_names)
                    except Cancelled:
                        return
                    except (OSError, ValueError, DiscFormatError, KeyError) as exc:
                        self.message.emit(f"Digi-Dex placement index unavailable; sightings still recorded: {exc}")
                    self.dex_changed.emit(self.dex.snapshot())
                    self._start_avatars()
                try:
                    self.history = EvolutionHistory(config.store_dir / "evolution_history.json")
                except (OSError, ValueError) as exc:
                    self.message.emit(f"Evolution history unavailable ({exc}); the chart shows every linked form")
            if config.live_ram and config.hd_pictures is not None:
                self.hd_layer = BackgroundLayer(core.read_memory, core.serialize, HdPictures(config.hd_pictures).path,
                                                on_error=self.message.emit, telemetry=self.telemetry)
                self._compositor = Compositor(self.hd_layer, self._store_image, self._hd_failed, self.telemetry,
                                              paint=self._paint_composed)
                self._prepare_titles(core)
            set_ram_size(core.ram_size)
            if config.live_ram and core.ram_size > RAM_SIZE:
                self.message.emit(f"Extended core: the game has {core.ram_size // (1024 * 1024)} MB of RAM")
            if config.double_frame:
                if config.live_ram and core.ram_size == EXTENDED_RAM:
                    self._frame = FrameController(core, self.message.emit)
                else:
                    self.message.emit("The ×2 camera frame needs the 8 MB core and USA game: using the normal camera")
            if config.coop_species is not None and config.live_ram:
                path = config.store_dir / "coop_player2.json"
                self.coop = CoopBattle(core.read_memory, core.write_memory,
                                       PlayerTwo.load(path, config.coop_species, config.coop_level), path)
                self.message.emit(f"Co-op test: player 2 starts at {self.coop.player.summary()}")
            if config.game_rules and config.live_ram:
                self.rules = GameRules(core.read_memory, core.write_memory, Progress.load(self._progress_path))
                self.evolution = EvolutionController(core, self.rules.progress, self.dex, self.dex_map_names,
                                                     self.history, forms=config.forms, offer_forms=config.offer_forms)
                self.evolution.split_saves = lambda: choosable_saves(self.store.slots(), self.save_slot)
                self.evolution.split_running = lambda: self.split_slot is not None
            try:
                if config.launch_slot is not None:
                    self._load(config.launch_slot)
                elif config.launch_inspection is not None:
                    self._load_inspection(config.launch_inspection)
            except (LibretroError, QuickSaveError, InspectionError, OSError) as exc:
                self.command_failed.emit(f"Started from power-on instead: {exc}")
            if self.rules is not None:
                self.progress_changed.emit(self.rules.progress.summary())
            self.started_ok.emit({"core": core.info.name, "version": core.info.version, "fps": core.fps,
                                  "sample_rate": core.sample_rate, "aspect": core.aspect_ratio})
            self._prepare_character(core)
            self._loop(core)
        except Exception as exc:
            self.failed.emit(f"The game stopped unexpectedly: {exc}")
        finally:
            self._shutdown(core)
            self.finished.emit()

    def _prepare_character(self, core: LibretroCore):
        """Who the Tamer is. Trouble here leaves the game's own Tamer: a picture must not stop the game."""
        config = self.config
        if config.character is None and config.keep_tamer:
            return
        try:
            parts = (Character("", config.character).data() if config.character is not None
                     else disc_tamer(config.disc_path))
            self.tamer_skin = TamerSkin(core, *parts)
        except (OSError, ValueError, KeyError, DiscFormatError, struct.error) as exc:
            self.message.emit(f"Character not applied: {exc}")

    def _loop(self, core: LibretroCore):
        frames = 0
        deadline = time.perf_counter()
        while not self._stop.is_set() and not core.shutdown_requested:
            frame_started = time.monotonic_ns()
            self._run_commands(core)
            if self.paused:
                self._stop.wait(0.02); deadline = time.perf_counter()
                continue
            if self.link is not None and self.link.is_guest():  # the other game is running the fight: wait for it
                self._stop.wait(0.005); deadline = time.perf_counter()
                continue
            core.buttons = self.buttons
            if self.evolution is not None:
                try:
                    self.evolution.progress = self.rules.progress
                    core.buttons = self.evolution.before_frame(self.buttons)
                    for event in self.evolution.sound_events:
                        self._menu_audio.trigger(event)
                    self._bank_events()
                except ValueError as exc:
                    self.evolution.restore()
                    self.evolution = None
                    self.message.emit(f"Manual evolution turned off: {exc}")
            frame, audio = core.run_frame()
            if self.telemetry.enabled and self.config.live_ram:
                self.telemetry.observe_scene(core.read_memory(ram_offset(CURRENT_SCREEN), 1)[0],
                                             core.read_memory(ram_offset(GAME_STATE), 1)[0],
                                             struct.unpack("<h", core.read_memory(ram_offset(SCRIPT_MAP_CHANGE_STATE), 2))[0])
            hd_layer = self.hd_layer  # one read: the compositor thread clears it when HD fails
            if hd_layer is not None:
                hd_layer.observe_scene()
            presentation = (self.hd_enabled, self.hud_enabled, hd_layer.generation if hd_layer is not None else None)
            if presentation != getattr(self, "_presentation_key", None):
                self._invalidate_compositors()
                self._previous_picture = None
                self._presentation_key = presentation
            self._has_run_frame = True
            if self.tamer_skin is not None:
                self.tamer_skin.tick()
            audio = self._menu_audio.mix(audio)
            if self.coop is not None:
                self._apply_coop()
            if self.link is not None or self.visitors:
                self._apply_visitors(core)
            if self.rules is not None:
                self._apply_rules()
            if self._frame is not None:
                self._frame.tick()
                hd_layer = self.hd_layer
                if hd_layer is not None:
                    hd_layer.wide = self._frame.active
            self._ensure_camera()
            if self._camera is not None:
                self._camera.tick()
            zoom_state = (self._camera_scale, self._camera.scale if self._camera else 256)
            if zoom_state != self._reported_zoom:
                self._reported_zoom = zoom_state
                self.camera_zoom_changed.emit(*zoom_state)
            frames += 1
            if self.config.live_ram and core.ram_size > RAM_SIZE and frames % EXTEND_HEAP_FRAMES == 0:
                extend_heap(core.read_memory, core.write_memory, core.ram_size)
            if frame is not None and self._is_repeat(frame):
                self.telemetry.count("frame.repeated")
                frame = None  # the game draws about 20 pictures a second: the same picture needs no work again
            if frame is not None:
                qt_started = time.monotonic_ns()
                image = battle_result_image(frame_image(frame), self.rules.battle_result if self.rules else None)
                menu = self.evolution.menu if self.evolution else None
                if menu is not None and menu.layout == "grid" and not self._icons_attempted:
                    self._menu_icons = native_icons(core)
                    self._icons_attempted = True
                if menu is not None and menu.layout == "relation" and not self._portraits_attempted:
                    self._portraits = native_portraits(core)
                    self._portraits_attempted = True
                image = evolution_menu_image(image, menu, self._menu_icons, self._portraits, self.avatars)
                self._read_hud(core, menu)
                self._read_level_boxes(core)
                if not self._submit_hd(image):
                    self._publish(image)
                self._last_image = image
                self.telemetry.duration("qt.frame_preparation", time.monotonic_ns() - qt_started)
            fast = self.fast_forward
            own_rate = core.fps
            rate = self.config.framerate or own_rate
            if audio and not fast:
                self.audio_ready.emit(stretched_audio(audio, rate / own_rate) if rate != own_rate else audio)
            if frames % MEMORY_CARD_FLUSH_FRAMES == 0:
                self._flush_memory_card(core)
            if self.config.live_ram and frames % LIVE_FRAMES == 0:
                self._read_live(core)
            if frames % 120 == 0:
                self.telemetry.memory()
            elapsed = time.monotonic_ns() - frame_started
            self.telemetry.duration("frame.work", elapsed)
            if elapsed > 1e9 / rate:
                self.telemetry.count("frame.over_budget")
            # core.fps is read every frame: cores may change timing (Beetle PSX settles on 59.826 fps after boot).
            deadline += 1.0 / (rate * (FAST_FORWARD if fast else 1))
            delay = deadline - time.perf_counter()
            if delay > 0:
                # time.sleep uses a high-resolution timer on Windows (Python 3.11+); Event.wait rounds up to the
                # 15.6 ms system tick, which alternated 31 ms and 2 ms frames. A stop request is seen next frame.
                time.sleep(delay)
            elif delay < -0.25:  # far behind (a slow frame or a breakpoint): do not try to catch up
                deadline = time.perf_counter()

    def _is_repeat(self, frame: Frame) -> bool:
        """The same picture as last time, with nothing of ours drawn over it that could have changed."""
        repeat = (frame.data == self._previous_picture and self._repeats < MOST_REPEATS
                  and not (self.evolution is not None and self.evolution.menu is not None) and not self.visitors)
        self._repeats = self._repeats + 1 if repeat else 0
        self._previous_picture = frame.data
        return repeat

    def _apply_visitors(self, core):
        try:
            link = self.link
            if link is None:  # split screen ended: take them off the map
                for visitor in self.visitors:
                    visitor.hide()
                self.visitors = []
                self.coop, self._solo_coop, self._linked = self._solo_coop, None, None
                return
            if not self.visitors:
                self.visitors = [Visitor(core.read_memory, core.write_memory, actor) for actor in (PARTNER, TAMER)]
                self._start_linked_battles(core, link)
            self._run_shared_battle(core, link)
            for visitor in self.visitors:
                link.publish(read_record(core.read_memory, visitor.actor), visitor.actor)
                busy = any(placing._step is not None for placing in self.visitors if placing is not visitor)
                for text in visitor.tick(link.other(visitor.actor), busy):
                    self.message.emit(text)
        except Exception as exc:  # the game must keep running
            traceback.print_exc()
            self.message.emit(f"Shared world turned off: {exc}")
            self.link = None
            self.visitors = []

    def _start_linked_battles(self, core, link):
        self._solo_coop = self.coop
        other = "Player 1" if self.player_label == "Player 2" else "Player 2"
        self._linked = LinkedPlayer(0, 0, label=other)
        self.coop = CoopBattle(core.read_memory, core.write_memory, self._linked)
        self.coop.owner = "P1" if other == "Player 1" else "P2"
        self.coop.can_join = lambda: self._other_can_join(core, link)
        self._awards_seen = (0, 0)
        self._dex_seen = 0
        self._dex_sent = set()

    def _other_can_join(self, core, link) -> bool:
        """When this game meets an enemy: the other player's partner, seen here on this map, joins."""
        other, partner = link.other(PARTNER), link.other_partner()
        here = read_record(core.read_memory, PARTNER)
        if (other is None or partner is None or here is None or not other.visible or other.map != here.map
                or self.visitors[PARTNER]._slot is None):
            return False
        self._linked.species, self._linked.fixed_level, self._linked.fixed_stats = other.species, partner[0], partner[1]
        if self.coop.species != other.species:
            self.coop.species, self.coop._slot = other.species, None
            self.coop.names = {}
        return True

    def _run_shared_battle(self, core, link):
        coop = self.coop
        link.set_hosting(coop.engaged)
        for code in link.take_presses():
            coop.press(code)
        stats = read_partner_stats(core.read_memory)
        if stats is not None:
            link.publish_partner(self.rules.progress.level if self.rules is not None else 1, stats)
        link.award_other(self._linked.exp, self._linked.bits)
        if coop.foes and self.dex is not None and coop.foes != sorted(self._dex_sent):
            entries = {str(kind): self.dex.entries[str(kind)] for kind in coop.foes if str(kind) in self.dex.entries}
            if entries:
                link.send_dex(entries)
                self._dex_sent = set(coop.foes)
        self._take_rewards(core, link)
        self._share_world(core, link)

    def _share_world(self, core, link):
        """Only on the field: a battle or script takes Digimon off the map for a while, which isn't them being beaten."""
        if not on_the_field(core.read_memory):
            return
        skip = {visitor._slot for visitor in self.visitors} | {self.coop._slot}
        link.publish_world(read_world(core.read_memory, skip))
        theirs = link.other_world()
        if theirs is not None:
            match_world(core.read_memory, core.write_memory, theirs, self.player_label == "Player 1", skip)

    def _take_rewards(self, core, link):
        """What the other game's fight earned this player."""
        exp_total, bits_total = link.my_awards()
        exp, bits = exp_total - self._awards_seen[0], bits_total - self._awards_seen[1]
        self._awards_seen = (exp_total, bits_total)
        if exp > 0 and self.rules is not None:
            for text in self.rules.gain(exp, "Co-op battle"):
                self.message.emit(text)
            self._write_progress()
            self.progress_changed.emit(self.rules.progress.summary())
        if bits > 0:
            money = struct.unpack("<i", core.read_memory(ram_offset(MONEY), 4))[0]
            core.write_memory(ram_offset(MONEY), struct.pack("<i", money + bits))
            self.message.emit(f"Co-op battle: +{bits} Bits")
        self._dex_seen, entries = link.take_dex(self._dex_seen)
        if entries and self.dex is not None:
            new = {key: entry for key, entry in entries.items() if key not in self.dex.entries and DigiDex._valid_entry(entry)}
            if new:
                self.dex.entries.update(new)
                self.dex.dirty = True
                self.dex.save()
                self.dex_changed.emit(self.dex.snapshot())

    def _apply_coop(self):
        try:
            for text in self.coop.tick():
                self.message.emit(text)
        except Exception as exc:  # whatever went wrong in this prototype, the game must keep running
            traceback.print_exc()
            self.message.emit(f"Co-op test turned off: {exc}")
            self.coop = None
            if self.rules is not None:
                self.rules.excluded_slot = None
            if self.dex is not None:
                self.dex.excluded_slot = None
            return
        for event in self.coop.sound_events:
            self._menu_audio.trigger(event)
        self.coop.sound_events = []
        if self.coop.panel is not None:
            if self._coop_icons is None and self._coop_icon_tries < 3:
                self._coop_icon_tries += 1
                self._coop_icons = command_icons(self.core.serialize())
            if self._hud_painter is None:
                self._hud_painter = HudPainter(self.core.serialize)
            self._hud_painter.prepare()
        if self.rules is not None:
            self.rules.excluded_slot = self.coop.excluded_slot
        if self.dex is not None:
            self.dex.excluded_slot = self.coop.excluded_slot

    def _apply_rules(self):
        before = self.rules.progress.exp
        try:
            messages = self.rules.tick()
        except LibretroError as exc:
            self.message.emit(f"Game rules turned off: {exc}")
            if self.evolution is not None:
                self.evolution.restore()
                self.evolution = None
            self.rules = None
            return
        for text in messages:
            self.message.emit(text)
        if self.rules.progress.exp != before and self.coop is not None:
            gained = self.rules.progress.exp - before
            if gained > 0:
                for text in self.coop.gain_exp(gained):
                    self.message.emit(text)
        if self.rules.progress.exp != before:
            self._write_progress()
            self.progress_changed.emit(self.rules.progress.summary())

    def _bank_events(self):
        """Digi-Bank changes (a hatch, a swap starting or finishing): keep the record, and let the rules re-read
        the partner so its own age and battle count are not mistaken for the previous partner's."""
        for text in self.evolution.notices:
            self.message.emit(text)
        self.evolution.notices.clear()
        if self.evolution.split_request is not None:
            self.split_requested.emit(self.evolution.split_request)
            self.evolution.split_request = None
        if self.evolution.events:
            self.evolution.events.clear()
            self.rules.reset()
            self._write_progress()
            self.progress_changed.emit(self.rules.progress.summary())

    def _write_progress(self):
        try:
            _atomic_write(self._progress_path, self.rules.progress.to_json())
        except OSError as exc:
            self.message.emit(f"Level/EXP could not be written: {exc}")

    def _prepare_titles(self, core):
        """The title pictures at full size. Trouble here leaves the game's own: a picture must not stop the game."""
        try:
            self.title_layer = TitleLayer.from_store(TitleStore(self.config.hd_pictures.parent))
        except (OSError, ValueError, DiscFormatError) as exc:
            self.message.emit(f"HD title screens not available: {exc}")
        if self.title_layer is not None:
            self._title_compositor = Compositor(self.title_layer, self._store_image, self._titles_failed, self.telemetry,
                                                paint=self._paint_composed)

    def _titles_failed(self, text: str):
        self.title_layer = None
        self.message.emit(text.replace("backgrounds", "title screens"))

    def _submit_hd(self, image: QImage) -> bool:
        """Send the frame to the HD compositor when it has a picture; False to show it as it is."""
        if not self.hd_enabled or (self.hd_layer is None and self.title_layer is None):
            self._invalidate_compositors()
            return False
        if image.format() != QImage.Format.Format_RGB32:
            image = image.convertToFormat(QImage.Format.Format_RGB32)
        pixels = np.frombuffer(image.constBits(), np.uint32).reshape(image.height(), image.bytesPerLine() // 4)
        pixels = pixels[:, :image.width()] & 0xFFFFFF
        if self.title_layer is not None:
            title = self.title_layer.prepare(pixels)
            if title is not None:
                if self._compositor is not None:
                    self._compositor.invalidate()
                self._title_compositor.submit(title)
                return True
        hd_layer = self.hd_layer
        if hd_layer is None or self._compositor is None:
            self._invalidate_compositors()
            return False
        try:
            job = hd_layer.prepare(pixels)
        except (LibretroError, OSError, ValueError) as exc:
            self.message.emit(f"HD backgrounds turned off: {exc}")
            self._invalidate_compositors()
            self.hd_layer = None
            return False
        if job is None:
            self._invalidate_compositors()
            return False
        if self._title_compositor is not None:
            self._title_compositor.invalidate()
        self._compositor.submit(job)
        return True

    def _invalidate_compositors(self):
        for compositor in (self._compositor, self._title_compositor):
            if compositor is not None:
                compositor.invalidate()

    def _hd_failed(self, text: str):
        """From the compositor thread: show plain frames from now on rather than a frozen picture."""
        self.hd_layer = None
        self.message.emit(text)

    def _paint_composed(self, composed) -> QImage:
        pixels = composed.pixels
        image = QImage(pixels.data, pixels.shape[1], pixels.shape[0], pixels.shape[1] * 4, QImage.Format.Format_RGB32)
        image.pixels = pixels  # the QImage does not own the array's memory
        return self._paint_overlays(image)

    def _forget_hd_ram(self):
        self.telemetry.discontinuity("load/reset")
        self._invalidate_compositors()
        hd_layer = self.hd_layer
        if hd_layer is not None:
            hd_layer.reset()
        if self.coop is not None:
            self.coop.reset()
        for visitor in self.visitors:
            visitor.reset()

    def _read_level_boxes(self, core: LibretroCore):
        if self.rules is None or not self.config.live_ram or not status_screen_open(core.read_memory):
            self._level_boxes = None
            return
        if self._status_digits is None:
            self._status_digits = read_digits(core.serialize())
        done, need = self.rules.progress.into_level
        self._level_boxes = None if self._status_digits is None else LevelBoxes(self.rules.progress.level, need - done if need else None)

    def _read_hud(self, core: LibretroCore, menu):
        """What the on-screen map and bars show for this frame; nothing over the workbench's own game menus.
        The compositor's thread draws them too, so `_hud` changes in one step, never empty on the way."""
        if not self.hud_enabled or not self.config.live_ram or menu is not None:
            self._hud = None
            return
        try:
            hud = read_hud(core.read_memory, self.live)
            if hud is not None:
                if self._hud_painter is None:
                    self._hud_painter = HudPainter(core.serialize)
                self._hud_painter.prepare()
            self._hud = hud
        except LibretroError as exc:
            self.message.emit(f"On-screen map and bars turned off: {exc}")
            self.hud_enabled = False
            self._hud = None

    def _publish(self, image: QImage):
        self._store_image(self._paint_overlays(image))

    def _paint_overlays(self, image: QImage) -> QImage:
        hud = self._hud
        if hud is not None and self._hud_painter is not None:
            # Quick saves and inspections keep the plain frame: draw on a copy unless this is a composed picture.
            if not hasattr(image, "pixels"):
                image = image.convertToFormat(QImage.Format.Format_RGB32).copy()
            self._hud_painter.draw(image, hud)
        panel = self.coop.panel if self.coop is not None else None
        if panel is not None:
            if not hasattr(image, "pixels") and hud is None:
                image = image.convertToFormat(QImage.Format.Format_RGB32).copy()
            self._coop_frames += 1
            draw_ally_panel(image, panel, self._coop_icons, self._hud_painter, self._coop_frames % 16 < 8)
        boxes = self._level_boxes
        if boxes is not None:
            if not hasattr(image, "pixels") and hud is None and panel is None:
                image = image.convertToFormat(QImage.Format.Format_RGB32).copy()
            draw_level_boxes(image, boxes, self._status_digits)
        return image

    def _store_image(self, image: QImage):
        self.telemetry.count("qt.frames_published")
        with self._image_lock:
            if self._image_pending:
                self.telemetry.count("qt.pending_replaced")
            self._published_at = time.monotonic_ns()
            self._image, notify = image, not self._image_pending
            self._image_pending = True
        if notify:  # at most one queued notification; the GUI always takes the newest frame
            self.frame_ready.emit()

    def _run_commands(self, core: LibretroCore):
        while True:
            try:
                command = self._commands.get_nowait()
            except queue.Empty:
                return
            try:
                if command[0] == "save":
                    self.slot_saved.emit(self._save(command[1], command[2]))
                elif command[0] == "load":
                    self._load(command[1]); self.slot_loaded.emit(command[1])
                elif command[0] == "reset":
                    self._restore_camera()
                    core.reset(); self.message.emit("Console reset")
                    if self.dex is not None:
                        self.dex.reset_runtime()
                    if self.evolution is not None:
                        self.evolution.reset()
                    if self.rules is not None:
                        self.rules.reset()
                    self._forget_hd_ram()
                elif command[0] == "camera_scale":
                    self._camera_scale = command[1]
                    if self._camera_scale == 256:
                        self._restore_camera()
                    else:
                        self._ensure_camera()
                        if self._camera is not None:
                            self._camera.set_scale(self._camera_scale)
                elif command[0] == "inspect":
                    self._restore_camera()  # Inspections must also reopen with native camera RAM.
                    if self.config.live_ram:
                        self._read_live(core)
                    self.inspection_captured.emit({"state": self._serialize_native(), "screenshot": png_bytes(self._last_image),
                                                   "live": self.live, "core": core.info.name,
                                                   "core_version": core.info.version})
            except (LibretroError, QuickSaveError, InspectionError, OSError) as exc:
                self.command_failed.emit(str(exc))

    def _serialize_native(self):
        # No live evolution-selector patch may escape into a slot or inspection.
        if self.evolution is not None:
            self.evolution.restore()
        for visitor in self.visitors:
            visitor.hide()
        return self.core.serialize()

    def _save(self, slot: int, note: str = ""):
        if slot != RESUME_SLOT:
            self.save_slot = slot
        core = self.core
        self._restore_camera()  # Save native game RAM; reapply the selected zoom on the next frame.
        info = self.store.save(slot, self._serialize_native(), png_bytes(self._last_image), core.info.name,
                               core.info.version, note,
                               self.rules.progress.to_json() if self.rules is not None else None)
        self.message.emit(f"{info.label} saved ({info.size:,} bytes){f': {info.note}' if info.note else ''}")
        return info

    def _load(self, slot: int):
        if slot == self.split_slot:
            raise QuickSaveError("Player 2 is playing from that save; choose another one.")
        self.save_slot = slot
        core = self.core
        info = self.store.info(slot)
        core.unserialize(self.store.load(slot, core=core.info.name))
        self._camera = None
        if self._frame is not None:
            self._frame.forget()
        if self.dex is not None:
            self.dex.reset_runtime()
        if self.evolution is not None:
            self.evolution.reset()
        self._forget_hd_ram()
        if self.rules is not None:
            self.rules.reset()
            saved = self.store.progress(slot)
            if saved is not None:
                try:
                    self.rules.progress = Progress.from_json(saved)
                except ValueError:
                    pass
                else:
                    self._write_progress()
            self.progress_changed.emit(self.rules.progress.summary())
        if info and info.core_version != core.info.version:
            self.message.emit(f"{info.label} was made by core version {info.core_version}; "
                              f"running {core.info.version}")
        self.message.emit(f"{info.label if info else f'Slot {slot}'} loaded")

    def _load_inspection(self, inspection_id: str):
        store = InspectionStore(self.config.store_dir / "inspections")
        record = store.get(inspection_id)
        if record is not None and record.core != self.core.info.name:
            raise InspectionError(f"That inspection was saved by {record.core}, not {self.core.info.name}.")
        self.core.unserialize(store.state(inspection_id))
        self._camera = None
        if self._frame is not None:
            self._frame.forget()
        if self.dex is not None:
            self.dex.reset_runtime()
        if self.evolution is not None:
            self.evolution.reset()
        self._forget_hd_ram()
        if self.rules is not None:
            self.rules.reset()
        self.message.emit(f"Started from the inspection at {record.map_name if record else inspection_id}")

    def _ensure_camera(self):
        if (not WORLD_ZOOM_ENABLED or self._camera is not None or self.core is None or self._camera_scale == 256
                or not self.config.live_ram):
            return
        try:
            self._camera = CameraZoom(self.core)
            self._camera.install(self._camera_scale)
            self.message.emit(f"World zoom {round(self._camera_scale * 100 / 256)}%: the background is drawn by "
                              "the zoom tile engine; saving restores the native camera")
        except (ValueError, LibretroError) as exc:
            self._camera = None
            if not self._camera_notice_sent:
                self.message.emit(f"World zoom unavailable in this game state: {exc}")
                self._camera_notice_sent = True

    def _restore_camera(self):
        if self._frame is not None:
            self._frame.suspend(for_save=True)
        if self._camera is not None:
            self._camera.uninstall()
            self._camera = None

    def _read_live(self, core: LibretroCore):
        try:
            live = read_live_state(core.read_memory)
        except LibretroError as exc:
            self.message.emit(f"Live map reading turned off: {exc}")
            self.config = PlayConfig(**{**self.config.__dict__, "live_ram": False})
            return
        key = (live.map_index, live.tamer_tile, live.partner_tile, hash(live.grid),
               tuple((npc.slot, npc.character_id, npc.tile) for npc in live.npcs))
        self.live = live
        if self.dex is not None:
            try:
                unlocked = self.dex.observe(core.read_memory, live)
                if unlocked:
                    self.message.emit("Digi-Dex unlocked: " + ", ".join(unlocked))
                if self.dex.dirty:
                    self.dex_changed.emit(self.dex.snapshot())
                    self.dex.save()
                    self._dex_write_error = False
            except (LibretroError, OSError) as exc:
                if not self._dex_write_error:
                    self.message.emit(f"Digi-Dex update failed: {exc}")
                    self._dex_write_error = True
        if self.history is not None:
            try:
                if self.history.observe(core.read_memory):
                    self.history.save()
            except (LibretroError, OSError) as exc:
                self.message.emit(f"Evolution history not recorded: {exc}")
                self.history = None
        if key != self._live_key:
            self._live_key = key
            self.live_changed.emit(live)

    def _flush_memory_card(self, core: LibretroCore):
        card = core.save_ram()
        if card is None:
            return
        digest = hashlib.sha256(card).digest()
        if digest != self._card_hash:
            self.store.write_memory_card(card); self._card_hash = digest
            self.message.emit("Memory card written")

    def _shutdown(self, core: LibretroCore):
        if self._compositor is not None:
            self._compositor.stop()
        if self._title_compositor is not None:
            self._title_compositor.stop()
        try:
            if self.config.resume_on_close and core.av is not None and self._has_run_frame:
                self.slot_saved.emit(self._save(RESUME_SLOT, "Saved when the game was closed"))
        except (LibretroError, QuickSaveError, OSError) as exc:
            self.message.emit(f"Resume save failed: {exc}")
        try:
            if core.av is not None:
                self._flush_memory_card(core)
        except OSError as exc:
            self.message.emit(f"Memory card could not be written: {exc}")
        self._stop.set()  # also ends avatar rendering when the game itself quit
        if self._avatar_thread is not None:
            self._avatar_thread.join(5)
        core.stop()
        if self._telemetry_dir:
            try:
                self.telemetry.memory()
                path = self.telemetry.write(self._telemetry_dir)
                self.message.emit(f"Runtime telemetry: {path}")
            except OSError as exc:
                self.message.emit(f"Runtime telemetry could not be saved: {exc}")
        self.message.emit("Game closed")

    def _start_avatars(self):
        """Supervise an isolated renderer; discovered Digimon first. No GIL contention with rendering."""
        discovered = [int(kind) for kind in self.dex.entries]
        order = list(dict.fromkeys(discovered + list(range(1, 180))))

        def publish(kind, image):
            self.avatars = {**self.avatars, kind: image}  # GUI (F4) and core threads read the reference

        def run():
            try:
                self.telemetry.event("avatars.started")
                with self.telemetry.measure("avatars.batch"):
                    load_avatars_isolated(self.config.disc_path, self.config.store_dir / "avatars", order, publish,
                                          self._stop.is_set, telemetry=self.telemetry)
            except (OSError, KeyError, DiscFormatError) as exc:
                self.message.emit(f"Digi-Dex avatars unavailable: {exc}")
            except Exception as exc:  # never take the game down for a picture
                self.message.emit(f"Digi-Dex avatars stopped: {exc}")
            finally:
                self.telemetry.event("avatars.stopped")

        self._avatar_thread = threading.Thread(target=run, name="dex-avatars", daemon=True)
        self._avatar_thread.start()


class AudioOutput:
    """Pushes the core's 16-bit stereo audio to the default output; drops audio rather than lagging."""

    def __init__(self, sample_rate: int, log):
        self.sample_rate = sample_rate
        audio_format = QAudioFormat()
        audio_format.setSampleRate(sample_rate); audio_format.setChannelCount(2)
        audio_format.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        device = QMediaDevices.defaultAudioOutput()
        self.sink = self.device = None
        if device.isNull():
            log("No audio output device; playing without sound")
            return
        if not device.isFormatSupported(audio_format):
            log(f"The audio device does not accept {sample_rate} Hz stereo 16-bit; playing without sound")
            return
        self.sink = QAudioSink(device, audio_format)
        self.sink.setBufferSize(sample_rate * 4 // 6)  # about 170 ms
        self.device = self.sink.start()

    def write(self, data: bytes):
        if self.device is not None and self.sink.bytesFree() >= len(data):
            self.device.write(data)

    def stop(self):
        if self.sink is not None:
            self.sink.stop()


class FrameView(QWidget):
    """Draws the newest frame at the core's aspect ratio, or in a 16:9 screen (graphics.SCREENS); forwards keys."""

    def __init__(self, key_handler, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(320, 240)
        self.image: QImage | None = None
        self.second: QImage | None = None  # player 2's game, in split screen
        self.shared = None  # in a shared fight, the player whose game is running it: both halves show that game
        self.aspect = 4 / 3
        self.smooth = False
        self.screen = SCREENS[0]
        self.telemetry = Telemetry()
        self.text = "Starting…"
        self._keys = key_handler

    def target(self) -> QRectF:
        return QRectF(*screen_rects(self.width(), self.height(), self.aspect, self.screen)[1])

    def paintEvent(self, _event):
        with self.telemetry.measure("qt.paint"):
            self._paint(_event)

    def _paint(self, _event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("black"))
        if self.second is not None and self.image is not None:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, self.smooth)
            half = self.width() / 2
            pictures = {None: (self.image, self.second), 0: (self.image, self.image), 1: (self.second, self.second)}[self.shared]
            for left, picture in ((0, pictures[0]), (half, pictures[1])):
                x, y, w, h = screen_rects(half - 1, self.height(), self.aspect, SCREENS[0])[1]
                painter.drawImage(QRectF(left + x, y, w, h), picture)
            painter.fillRect(QRectF(half - 1, 0, 2, self.height()), QColor("#3080c0"))
        elif self.image is not None:
            area, game = (QRectF(*rect) for rect in screen_rects(self.width(), self.height(), self.aspect, self.screen))
            if area != game:  # 16:9 fill: a blurred, darkened copy of the picture behind it, so the sides match
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
                painter.drawImage(area, self.image.scaled(40, 22, Qt.AspectRatioMode.IgnoreAspectRatio,
                                                          Qt.TransformationMode.SmoothTransformation))
                painter.fillRect(area, QColor(0, 0, 0, 120))
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, self.smooth)
            painter.drawImage(game, self.image)
        if self.text:
            painter.setPen(QColor("#e0e0e0"))
            painter.drawText(self.rect().adjusted(12, 12, -12, -12),
                             Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft, self.text)

    def keyPressEvent(self, event):
        if not self._keys(event, True):
            super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if not self._keys(event, False):
            super().keyReleaseEvent(event)

    def focusOutEvent(self, event):
        self._keys(None, False)  # release every held button so none sticks
        super().focusOutEvent(event)


NPC_COLOURS = {"chases you": QColor("#e8413c"), "watches you": QColor("#f2c94c"), "ignores you": QColor("#ff9a1f")}


def is_hostile(npc: LiveNpc) -> bool:
    """Chases the Tamer when it comes near, and starts its script (a battle) on contact."""
    return follow_kind(npc.follow_mode) == "chases you" and npc.autotalk


class LiveMapView(QWidget):
    """The running game's tile-trigger grid with the player (white), partner (cyan) and NPCs marked.

    NPCs are red when they chase the Tamer, yellow when they only turn to watch, orange otherwise; the box
    around each chaser or watcher is where the Tamer sets it off, drawn solid while it is reacting.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(300, 300)
        self.grid: QImage | None = None
        self._grid_bytes = None
        self.tamer = self.partner = None
        self.npcs: list[tuple[LiveNpc, str]] = []
        self.items: list[tuple[tuple[int, int], str]] = []
        self.chests: list[tuple[tuple[int, int], str, bool]] = []

    def show_items(self, items: list[tuple[tuple[int, int], str]], chests: list[tuple[tuple[int, int], str, bool]]):
        self.items, self.chests = items, chests
        self.update()

    def show_live(self, live: LiveState, npc_names: list[str] | None = None):
        if live.grid != self._grid_bytes:
            self._grid_bytes = live.grid
            self.grid = QImage(ImageQt(trigger_grid_image(live.grid))).copy()
        self.tamer, self.partner = live.tamer_tile, live.partner_tile
        names = npc_names or [f"ID {npc.character_id}" for npc in live.npcs]
        self.npcs = list(zip(live.npcs, names))
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#20242b"))
        if self.grid is None:
            painter.setPen(QColor("#d0d4da"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Waiting for the game")
            return
        side = min(self.width(), self.height())
        cell = side / 100
        left, top = (self.width() - side) / 2, (self.height() - side) / 2
        painter.drawImage(QRectF(left, top, side, side), self.grid)
        radius = max(3.0, cell * 1.5)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for npc, _ in self.npcs:
            kind = follow_kind(npc.follow_mode)
            if not has_tracking_box(npc.follow_mode) or npc.tracking_range <= 0:
                continue
            (x0, y0), (x1, y1) = tracking_box(npc.post, npc.tracking_range)
            pen = QPen(NPC_COLOURS[kind], 2 if npc.reacting else 1,
                       Qt.PenStyle.SolidLine if npc.reacting else Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawRect(QRectF(left + x0 * cell, top + y0 * cell, (x1 - x0 + 1) * cell, (y1 - y0 + 1) * cell))
        half = max(2.5, cell)
        for tile, name, taken in self.chests:
            x, y = left + (tile[0] + 0.5) * cell, top + (tile[1] + 0.5) * cell
            painter.setPen(QPen(QColor("black"), 1)); painter.setBrush(QColor("#7d7d7d" if taken else "#d4a017"))
            painter.drawRect(QRectF(x - half * 1.4, y - half, half * 2.8, half * 2))
            painter.setPen(QColor("#f3e2a9")); painter.drawText(QPointF(x + half * 1.6, y + 4), name)
        for tile, name in self.items:
            x, y = left + (tile[0] + 0.5) * cell, top + (tile[1] + 0.5) * cell
            painter.setPen(QPen(QColor("white"), 1)); painter.setBrush(QColor("#2fbf71"))
            painter.drawPolygon([QPointF(x, y - half * 1.5), QPointF(x + half * 1.5, y), QPointF(x, y + half * 1.5),
                                 QPointF(x - half * 1.5, y)])
            painter.setPen(QColor("#bff5d6")); painter.drawText(QPointF(x + half * 1.7, y + 4), name)
        markers = [(npc.tile, NPC_COLOURS[follow_kind(npc.follow_mode)], name + (" !" if npc.reacting else ""))
                   for npc, name in self.npcs]
        markers += [(self.partner, QColor("cyan"), None), (self.tamer, QColor("white"), None)]
        for tile, colour, name in markers:
            if tile is None:
                continue
            x, y = left + (tile[0] + 0.5) * cell, top + (tile[1] + 0.5) * cell
            # NPCs get a white ring so they stand apart from the coloured exit tiles.
            painter.setPen(QPen(QColor("white" if name else "black"), 1.5 if name else 1)); painter.setBrush(colour)
            painter.drawEllipse(QRectF(x - radius, y - radius, radius * 2, radius * 2))
            if name:
                painter.setPen(QColor("#ffd9a8")); painter.drawText(QPointF(x + radius + 2, y + 4), name)


class InspectDialog(QDialog):
    def __init__(self, summary: str, note: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Inspect here")
        form = QFormLayout(self)
        where = QLabel(summary); where.setWordWrap(True); form.addRow("Where", where)
        self.category = QComboBox(); self.category.addItems(CATEGORIES); form.addRow("What", self.category)
        self.note = QLineEdit(note)
        self.note.setPlaceholderText("What you see, e.g. gateway south to Native Forest, toilet behind the gym")
        form.addRow("Note", self.note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); form.addRow(buttons)
        self.resize(520, self.sizeHint().height())


class PlayWindow(QMainWindow):
    """One game session. Closing the window stops the core, writes the resume slot and the memory card."""

    slots_changed = Signal()
    inspections_changed = Signal()
    quick_save_changed = Signal()  # the slot or note, in the settings the Play tab shows them from
    zoom_reported = Signal(str)
    log = Signal(str)
    closed = Signal()

    def __init__(self, config: PlayConfig, title: str = "Digimon World", map_names: dict[int, str] | None = None,
                 decoded_grid=None, character_names: dict[int, str] | None = None,
                 item_names: list[str] | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{title} — DW1-coop player")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(960, 760)
        self.config = config
        self.keys = dict(DEFAULT_KEYS)
        self._held = 0
        self.audio: AudioOutput | None = None
        self.view = FrameView(self._handle_key)
        self.setCentralWidget(self.view)
        self._build_toolbar()
        self.status = QLabel("Starting the core…"); self.statusBar().addWidget(self.status, 1)
        self.map_names = map_names or {}
        self.character_names = character_names or {}
        self.item_names = item_names or []
        self.spawns = SpawnLog(config.store_dir / "item_spawns.json")
        self._decoded_grid = decoded_grid or (lambda index: None)
        self._decoded_cache: dict[int, bytes | None] = {}
        self.inspections = InspectionStore(config.store_dir / "inspections")
        self.live_label = QLabel("Live map: off (USA release only)" if not config.live_ram else "Live map: waiting")
        self.statusBar().addPermanentWidget(self.live_label)
        # Long map names and messages clip instead of widening the window. A fixed width keeps the map text off its neighbours.
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.live_label.setFixedWidth(320)
        self.level_label = QLabel()
        self.level_label.setVisible(config.game_rules and config.live_ram)
        self.level_label.setToolTip("P6 game rules: level and EXP of your partner. Aging and death are off.")
        self.statusBar().addPermanentWidget(self.level_label)
        self.statusBar().addPermanentWidget(QLabel("F1: controls"))
        self.live_map = LiveMapView()
        self.npc_list = QLabel("NPCs on this map: —"); self.npc_list.setWordWrap(True)
        self.npc_list.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        # Fixed width, scrolling list: the panel must not resize (and shrink the game view) as maps/NPCs change.
        npc_scroll = QScrollArea(); npc_scroll.setWidget(self.npc_list); npc_scroll.setWidgetResizable(True)
        npc_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        npc_scroll.setFixedHeight(180); npc_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        live_panel = QWidget(); live_panel.setFixedWidth(320); live_layout = QVBoxLayout(live_panel)
        live_layout.addWidget(self.live_map, 1); live_layout.addWidget(npc_scroll)
        self.live_dock = QDockWidget("Live map", self); self.live_dock.setWidget(live_panel)
        self.live_dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetClosable)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.live_dock)
        self.live_dock.setVisible(False)  # the map is part of the game screen; F10 still opens this detailed one
        self._live: LiveState | None = None
        self.thread = QThread(self)
        self._dex_snapshot = {"entries": {}, "catalog": []}
        self.worker = EmulatorWorker(config)
        self.view.telemetry = self.worker.telemetry
        self.worker.hd_enabled = self._hd_on
        self.worker.hud_enabled = self._hud_on
        self.worker.dex_map_names = dict(self.map_names)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.frame_ready.connect(self._show_frame)
        self.worker.audio_ready.connect(self._play_audio)
        self.worker.message.connect(self._message)
        self.worker.started_ok.connect(self._started)
        self.worker.failed.connect(self._failed)
        self.worker.slot_saved.connect(self._slot_saved)
        self.worker.slot_loaded.connect(self._slot_loaded)
        self.worker.command_failed.connect(self._command_failed)
        self.worker.live_changed.connect(self._live_changed)
        self.worker.inspection_captured.connect(self._inspection_captured)
        self.worker.camera_zoom_changed.connect(self._camera_zoom_changed)
        self.worker.dex_changed.connect(self._dex_changed)
        self.worker.progress_changed.connect(self._progress_changed)
        self.worker.split_requested.connect(self._split_requested)
        self._split: SplitGame | None = None
        self._split_held = 0
        self._split_timer = QTimer(self)
        self._split_timer.timeout.connect(self._show_split_frame)
        self._pads = system_pads()
        self._controls = load_controls(app_settings())
        self._pad_buttons = [set(), set()]  # each player's controller buttons held
        self._pad_held = [0, 0]  # the bits of those that reach a game
        self._pad_timer = QTimer(self)
        self._pad_timer.timeout.connect(self._poll_pads)
        # Direct: stop() blocks the GUI thread in wait(), so a queued quit would never be delivered.
        self.worker.finished.connect(self.thread.quit, Qt.ConnectionType.DirectConnection)
        self._finished = False
        self.thread.finished.connect(self._thread_finished)

    def start(self):
        self.show(); self.view.setFocus()
        self.thread.start()
        self._pad_timer.start(PAD_POLL_MS)

    def _send_buttons(self):
        self.worker.buttons = self._held | self._pad_held[0]
        if self._split is not None:
            self._split.set_buttons(self._split_held | self._pad_held[1])

    def _poll_pads(self):
        """Controllers count only while a game window is in front, as the keyboard does."""
        active = self.isActiveWindow()
        for player, controls in enumerate(self._controls):
            inputs = self._pads.read(controls.pad) if active and controls.pad is not None else set()
            held = controls.held(inputs)
            for button in held - self._pad_buttons[player]:
                self._pad_button(player, button, True)
            for button in self._pad_buttons[player] - held:
                self._pad_button(player, button, False)
            self._pad_buttons[player] = held

    def _pad_button(self, player: int, button: str, pressed: bool):
        """The same rules as the keys: who a button reaches depends on the shared fight and split screen."""
        bit = 1 << BUTTON[button]
        if not pressed:
            self._pad_held[player] &= ~bit
        else:
            host = self._split.link.host() if self._split is not None else None
            if player == 0 and host == 1:
                if button in ALLY_BUTTONS:
                    self._split.link.send_press(1, ALLY_BUTTONS[button])
                return
            if player == 1 and (self._split is None or host == 0):
                if self.worker.coop is not None and button in ALLY_BUTTONS:
                    self.worker.coop.press(ALLY_BUTTONS[button])
                return
            self._pad_held[player] |= bit
        self._send_buttons()

    @Slot(object)
    def _dex_changed(self, snapshot):
        self._dex_snapshot = snapshot

    def _show_digidex(self):
        if self._dex_snapshot.get("error"):
            QMessageBox.warning(self, "Digi-Dex unavailable", self._dex_snapshot["error"])
            return
        if self.audio is not None:
            self.audio.write(tone_pcm("open",self.audio.sample_rate))
        paused = self.worker.paused
        self.worker.paused = True
        try:
            DigiDexDialog(self._dex_snapshot, self.map_names, self, self.worker.avatars).exec()
        finally:
            self.worker.paused = paused
            self.view.setFocus()

    def _build_toolbar(self):
        """The game screen keeps Save, Load and Full screen. The rest is in the Play tab's Settings; keys still work here."""
        bar = QToolBar("Quick saves"); bar.setMovable(False); self.addToolBar(bar)

        def action(text, shortcut, handler, checkable=False, on_bar=True):
            item = QAction(text, self); item.setShortcut(QKeySequence(shortcut))
            item.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
            item.setCheckable(checkable); item.triggered.connect(handler)
            bar.addAction(item) if on_bar else self.addAction(item)
            return item

        settings = app_settings()
        self._slot = min(max(settings.value("play/slot", 1, type=int), 1), SLOT_COUNT)
        action("Save", "F5", lambda: self.worker.quick_save(self.current_slot, self.quick_save_note))
        action("Load", "F8", lambda: self.worker.quick_load(self.current_slot))
        previous = QAction(self); previous.setShortcut(QKeySequence("F6")); previous.triggered.connect(lambda: self._step_slot(-1))
        following = QAction(self); following.setShortcut(QKeySequence("F7")); following.triggered.connect(lambda: self._step_slot(1))
        self.addAction(previous); self.addAction(following)
        self.full = action("Full screen", "F11", self._toggle_full_screen, checkable=True)
        self.pause = action("Pause", "P", self._toggle_pause, checkable=True, on_bar=False)
        action("Reset", "Ctrl+R", self._reset, on_bar=False)
        self.dex_action = action("Digi-Dex", "F4", self._show_digidex, on_bar=False)
        self.dex_action.setEnabled(self.config.live_ram)
        self.view.smooth = settings.value("play/smooth", True, type=bool)
        self.view.screen = settings.value("play/screen", SCREENS[0])
        self._hd_on = (self.config.live_ram and self.config.hd_pictures is not None
                       and settings.value("play/hd_backgrounds", True, type=bool))
        self._hud_on = self.config.live_ram and settings.value("play/hud", True, type=bool)
        inspect = QAction(self); inspect.setShortcut(QKeySequence("F9")); inspect.triggered.connect(self.worker_inspect)
        live_toggle = QAction(self); live_toggle.setShortcut(QKeySequence("F10"))
        live_toggle.triggered.connect(lambda: self.live_dock.setVisible(not self.live_dock.isVisible()))
        self.addAction(inspect); self.addAction(live_toggle)
        help_action = QAction(self); help_action.setShortcut(QKeySequence("F1")); help_action.triggered.connect(self._show_help)
        self.addAction(help_action)

    @property
    def current_slot(self) -> int:
        return self._slot

    @property
    def quick_save_note(self) -> str:
        return app_settings().value("play/save_note", "") or ""

    def _clear_quick_save_note(self):
        app_settings().remove("play/save_note")
        self.quick_save_changed.emit()

    def set_slot(self, slot: int):
        """From the Settings tab, while a game is open."""
        self._slot = slot

    def _step_slot(self, step: int):
        self._slot = (self._slot - 1 + step) % SLOT_COUNT + 1
        app_settings().setValue("play/slot", self._slot)
        self.quick_save_changed.emit()
        info = self.worker.store.info(self.current_slot)
        self.status.setText(f"Slot {self.current_slot}: " + (f"{info.created}{f' — {info.note}' if info.note else ''}"
                                                             if info else "empty"))

    def _handle_key(self, event, pressed: bool) -> bool:
        if event is None:
            self._held = 0; self._send_buttons(); self.worker.fast_forward = False
            return True
        if event.isAutoRepeat():
            return event.key() in self.keys or event.key() == Qt.Key.Key_Tab
        if event.key() == Qt.Key.Key_Tab:
            self.worker.fast_forward = pressed
            return True
        if self._split is not None and self._split.link.host() is not None:
            return self._shared_fight_key(event, pressed)
        if self._split is not None and event.key() in SPLIT_KEYS:
            bit = 1 << BUTTON[SPLIT_KEYS[event.key()]]
            self._split_held = self._split_held | bit if pressed else self._split_held & ~bit
            self._send_buttons()
            return True
        if self.worker.coop is not None and event.key() in COOP_KEYS:
            if pressed:
                self.worker.coop.press(COOP_KEYS[event.key()])
            return True
        button = self.keys.get(event.key())
        if button is None:
            return False
        bit = 1 << BUTTON[button]
        self._held = self._held | bit if pressed else self._held & ~bit
        self._send_buttons()
        return True

    def focusNextPrevChild(self, _next):
        return False  # Tab is the fast-forward key, not focus navigation

    def worker_inspect(self):
        self.worker.inspect()

    def map_label(self, index: int) -> str:
        return self.map_names.get(index, f"map {index}")

    def character_label(self, character_id: int) -> str:
        return self.character_names.get(character_id, f"ID {character_id}")

    def item_label(self, item_id: int) -> str:
        name = self.item_names[item_id] if 0 <= item_id < len(self.item_names) else ""
        return name or f"item {item_id}"

    def item_summary(self, live: LiveState) -> str:
        if live.tamer is None:
            return ""
        lines = [f"• {self.item_label(item.item_id)} on the ground at {item.tile[0]},{item.tile[1]}"
                 + (" — in reach (walking within one tile picks it up)" if item.in_reach(live.tamer_tile) else "") for item in live.items]
        lines += [f"• chest with {self.item_label(chest.item_id)} at {chest.tile[0]},{chest.tile[1]}"
                  + (" (taken)" if chest.taken else "") for chest in live.chests]
        return "\n".join(lines)

    def npc_description(self, npc: LiveNpc) -> str:
        kind = follow_kind(npc.follow_mode)
        parts = [f"{self.character_label(npc.character_id)} at {npc.tile[0]},{npc.tile[1]}"]
        if has_tracking_box(npc.follow_mode):
            parts.append(f"{kind} within ±{npc.tracking_range / 100:g} tiles of its post")
        elif kind != "ignores you":
            parts.append(f"{kind} (the game's radius test, range {npc.tracking_range})")
        if npc.autotalk:
            parts.append("starts its script (a battle for enemies) on contact")
        parts.append(f"HP {npc.hp}/{npc.max_hp}")
        if npc.reacting:
            parts.append("REACTING NOW")
        return ", ".join(parts)

    def npc_summary(self, live: LiveState) -> str:
        return ", ".join(f"{self.character_label(npc.character_id)} at {npc.tile[0]},{npc.tile[1]}"
                         for npc in live.npcs) or "none"

    def threat_summary(self, live: LiveState) -> str:
        chasing = [self.character_label(npc.character_id) for npc in live.npcs if npc.reacting and is_hostile(npc)]
        hostile = sum(is_hostile(npc) for npc in live.npcs)
        if chasing:
            return f"⚠ {', '.join(chasing)} {'is' if len(chasing) == 1 else 'are'} after you"
        return f"{hostile} hostile Digimon on this map" if hostile else ""

    def decoded_grid(self, index: int) -> bytes | None:
        if index not in self._decoded_cache:
            try:
                self._decoded_cache[index] = self._decoded_grid(index)
            except Exception:
                self._decoded_cache[index] = None
        return self._decoded_cache[index]

    def grid_difference_count(self, live: LiveState) -> int | None:
        decoded = self.decoded_grid(live.map_index)
        return None if decoded is None else len(grid_differences(live.grid, decoded))

    def live_summary(self, live: LiveState) -> str:
        parts = [f"{self.map_label(live.map_index)} (map {live.map_index})"]
        if live.tamer_tile is not None:
            trigger = live.trigger_at(live.tamer_tile)
            parts.append(f"tile {live.tamer_tile[0]},{live.tamer_tile[1]}"
                         + (f" — {trigger_description(trigger)}" if trigger is not None else ""))
        differences = self.grid_difference_count(live)
        parts.append("grid: no decoded MAP" if differences is None else
                     "grid matches the decoded MAP" if differences == 0 else
                     f"grid: {differences} tiles differ from the decoded MAP")
        return " · ".join(parts)

    @Slot(object)
    def _live_changed(self, live: LiveState):
        self._live = live
        threat = self.threat_summary(live)
        in_game = live.tamer is not None  # item slots hold leftovers before the game sets them up
        reachable = [self.item_label(item.item_id) for item in live.items if in_game and item.in_reach(live.tamer_tile)]
        extra = [part for part in (threat, f"{', '.join(reachable)} in reach" if reachable else "") if part]
        self.live_label.setText(" · ".join([self.live_summary(live), *extra]))
        self.live_label.setToolTip(self.live_label.text())  # the label may be clipped
        if in_game:
            self.live_map.show_items([(item.tile, self.item_label(item.item_id)) for item in live.items],
                                     [(chest.tile, self.item_label(chest.item_id), chest.taken) for chest in live.chests])
            new = self.spawns.record(live.map_index, self.map_label(live.map_index),
                                     [(item.item_id, self.item_label(item.item_id), item.tile) for item in live.items])
            for item_name, tile in new:
                self._message(f"New item spawn seen: {item_name} at {tile[0]},{tile[1]} on {self.map_label(live.map_index)}")
        self.live_map.show_live(live, [self.character_label(npc.character_id) for npc in live.npcs])
        items = self.item_summary(live)
        self.npc_list.setText("Digimon on this map:\n" + ("\n".join(f"• {self.npc_description(npc)}" for npc in live.npcs)
                                                         or "none")
                              + (f"\nItems and chests:\n{items}" if items else ""))

    @Slot(object)
    def _inspection_captured(self, capture: dict):
        live: LiveState | None = capture["live"]
        was_paused = self.worker.paused
        self.worker.paused = True
        summary = (f"{self.live_summary(live)}\n" + "\n".join(self.npc_description(npc) for npc in live.npcs)
                   + (f"\n{self.item_summary(live)}" if self.item_summary(live) else "")
                   if live is not None
                   else "Position unknown (live map is off for this disc)")
        dialog = InspectDialog(summary, self.quick_save_note, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                record = self.inspections.add(
                    map_index=live.map_index if live else -1, map_name=self.map_label(live.map_index) if live else "unknown",
                    position=(live.tamer.x, live.tamer.y, live.tamer.z) if live and live.tamer else None,
                    tile=live.tamer_tile if live else None,
                    trigger=live.trigger_at(live.tamer_tile) if live else None,
                    partner_tile=live.partner_tile if live else None,
                    npcs=[(npc.character_id, self.character_label(npc.character_id), npc.tile,
                           self.npc_description(npc)) for npc in live.npcs] if live else [],
                    items=[(item.item_id, self.item_label(item.item_id), item.tile, "ground")
                           for item in live.items] + [(chest.item_id, self.item_label(chest.item_id), chest.tile,
                                                       "chest, taken" if chest.taken else "chest")
                                                      for chest in live.chests] if live and live.tamer else [],
                    grid_differences=self.grid_difference_count(live) if live else None,
                    category=dialog.category.currentText(), note=dialog.note.text(), core=capture["core"],
                    core_version=capture["core_version"], screenshot=capture["screenshot"], state=capture["state"],
                    live_grid=live.grid if live else None)
                self._message(f"Inspection saved: {record.category} at {record.map_name}"
                              + (f" — {record.note}" if record.note else ""))
                self._clear_quick_save_note()
                self.inspections_changed.emit()
            except (InspectionError, OSError) as exc:
                QMessageBox.warning(self, "Inspection", f"The inspection could not be saved: {exc}")
        self.worker.paused = was_paused
        self.view.setFocus()

    def _toggle_pause(self, paused: bool):
        self.worker.paused = paused
        self.view.text = "Paused" if paused else ""; self.view.update()

    def _reset(self):
        if QMessageBox.question(self, "Reset console", "Reset the console? Unsaved progress is lost.") \
                == QMessageBox.StandardButton.Yes:
            self.worker.reset()
        self.view.setFocus()

    def _toggle_full_screen(self, full: bool):
        self.showFullScreen() if full else self.showNormal()

    def _set_smooth(self, smooth: bool):
        app_settings().setValue("play/smooth", smooth)
        self.view.smooth = smooth; self.view.update(); self.view.setFocus()

    def _set_hud(self, enabled: bool):
        app_settings().setValue("play/hud", enabled)
        if getattr(self, "worker", None) is not None:
            self.worker.hud_enabled = enabled
        self.view.setFocus()

    def _set_hd(self, enabled: bool):
        app_settings().setValue("play/hd_backgrounds", enabled)
        if getattr(self, "worker", None) is not None:
            self.worker.hd_enabled = enabled
        self.view.setFocus()

    def _set_screen(self, screen: str):
        app_settings().setValue("play/screen", screen)
        self.view.screen = screen; self.view.update(); self.view.setFocus()

    def set_game_view_size(self, width=640, height=480):
        """Resize the presentation area only; never alter projection, world coordinates or emulated RAM."""
        if self.view.second is not None:
            self._message("Stop split screen before choosing a single-game view size.")
            return False
        if self.isFullScreen() or self.isMaximized():
            self.full.setChecked(False)
            self.showNormal()
        self._set_screen("4:3")
        self._fit_view(width, height, VIEW_SIZE_TRIES)
        return True

    def _fit_view(self, width: int, height: int, tries: int):
        """Leaving full screen finishes later on some window systems, and can restore the old size over ours."""
        leaving = self.isFullScreen() or self.isMaximized()
        if not leaving:
            self.layout().activate()
            self.resize(self.width() + width - self.view.width(), self.height() + height - self.view.height())
        if tries and (leaving or (self.view.width(), self.view.height()) != (width, height)):
            QTimer.singleShot(VIEW_SIZE_RETRY_MS, self, lambda: self._fit_view(width, height, tries - 1))

    def _show_help(self):
        QMessageBox.information(self, "Controls", KEY_HELP.replace("  •  ", "\n"))

    @Slot()
    def _show_frame(self):
        image = self.worker.take_frame()
        if image is not None:
            published = getattr(self.worker, "_published_at", None)
            if published is not None:
                self.worker.telemetry.duration("qt.delivery_latency", time.monotonic_ns() - published)
            self.view.image = image; self.view.update()

    @Slot(bytes)
    def _play_audio(self, data: bytes):
        if self.audio is not None:
            self.audio.write(data)

    def _shared_fight_key(self, event, pressed: bool) -> bool:
        """In a shared fight the host's buttons reach its game as usual; the guest's order the ally."""
        host = self._split.link.host()
        split_button, own_button = SPLIT_KEYS.get(event.key()), self.keys.get(event.key())
        if split_button is None and own_button is None:
            return False
        if host == 0:
            if split_button is not None:  # player 2's keys
                if pressed and split_button in ALLY_BUTTONS:
                    self.worker.coop.press(ALLY_BUTTONS[split_button])
                return True
            bit = 1 << BUTTON[own_button]
            self._held = self._held | bit if pressed else self._held & ~bit
            self._send_buttons()
            return True
        if split_button is not None:  # player 2 hosts: their game takes their keys
            bit = 1 << BUTTON[split_button]
            self._split_held = self._split_held | bit if pressed else self._split_held & ~bit
            self._send_buttons()
            return True
        if pressed and own_button in ALLY_BUTTONS:
            self._split.link.send_press(1, ALLY_BUTTONS[own_button])
        return True

    @Slot(object)
    def _split_requested(self, request):
        if request == "stop":
            self._stop_split()
            return
        if self._split is not None or request == self.worker.save_slot:
            return
        target = self.config.store_dir / "player2"
        try:
            copy_save(self.config.store_dir, request, target)
            self._split = SplitGame({
                "core": str(self.config.core_path), "bios": str(self.config.bios_dir), "disc": str(self.config.disc_path),
                "store": str(target), "slot": request, "live_ram": self.config.live_ram, "game_rules": self.config.game_rules,
                "hd": str(self.config.hd_pictures) if self.config.hd_pictures else None, "options": self.config.options,
                "character": str(self.config.character) if self.config.character else None,
                "keep_tamer": self.config.keep_tamer,
                "framerate": self.config.framerate})
        except (OSError, ValueError) as exc:
            self._message(f"Split screen could not start: {exc}")
            return
        self.worker.split_slot = request
        if self.config.live_ram:
            self.worker.link = self._split.link
        self._split_timer.start(16)
        self.live_dock.setVisible(False)
        if not self.isFullScreen():
            self.resize(max(self.width(), 1500), self.height())
        self._message(f"Split screen: player 2 is starting from slot {request}")

    @Slot()
    def _show_split_frame(self):
        if self._split is None:
            return
        if not self._split.running():
            self._message("Player 2's game has stopped")
            self._stop_split()
            return
        shared = self._split.link.host()
        if shared != self.view.shared:
            self.view.shared = shared
            self.view.update()
        frame = self._split.newest_frame()
        if frame is not None:
            width, height, data = frame
            self.view.second = QImage(data, width, height, width * 4, QImage.Format.Format_RGB32).copy()
            self.view.update()

    def _stop_split(self):
        self._split_timer.stop()
        self.worker.link = None
        if self._split is not None:
            split, self._split = self._split, None
            self._split_held = 0
            try:
                split.stop()
            except (OSError, BufferError, ValueError) as exc:
                self._message(f"Split screen did not stop cleanly: {exc}")
        self.worker.split_slot = None
        self.view.second = None
        self.view.shared = None
        self.view.update()

    @Slot(str)
    def _progress_changed(self, text: str):
        self.level_label.setText(text)

    @Slot(str)
    def _message(self, text: str):
        self.status.setText(text); self.log.emit(text)

    @Slot(int, int)
    def _camera_zoom_changed(self, requested: int, effective: int):
        self.zoom_reported.emit(f"{round(effective * 100 / 256)}%")

    @Slot(object)
    def _started(self, info):
        self.view.aspect, self.view.text = info["aspect"], ""
        self.audio = AudioOutput(info["sample_rate"], self._message)
        self._message(f"Running {info['core']} {info['version']} at {info['fps']:.3f} fps — F1 shows the controls")

    @Slot(str)
    def _failed(self, message: str):
        self.log.emit(f"Failed: {message}")
        self.view.text = message; self.view.update()
        QMessageBox.critical(self, "The game could not run", message)

    @Slot(object)
    def _slot_saved(self, info):
        self.slots_changed.emit()
        if info.slot != RESUME_SLOT:
            self._clear_quick_save_note()

    @Slot(int)
    def _slot_loaded(self, _slot):
        self.view.setFocus()

    @Slot(str)
    def _command_failed(self, message: str):
        self._message(message)
        QMessageBox.warning(self, "Quick save", message)
        self.view.setFocus()

    @Slot()
    def _thread_finished(self):
        self._finished = True
        if self.audio is not None:
            self.audio.stop()
        self.slots_changed.emit()

    def stop(self, wait_ms: int = 10000) -> bool:
        """Stop the core and wait for its shutdown saves; True once the worker thread has ended."""
        if not self.thread.isRunning():
            return True
        self.worker.request_stop()
        return self.thread.wait(wait_ms)

    def closeEvent(self, event):
        self._pad_timer.stop()
        self._stop_split()
        if not self.stop():
            self._message("The core is still shutting down…")
            event.ignore()
            return
        # Deliver the worker's last queued messages (resume save, memory card) before the window is deleted.
        QCoreApplication.sendPostedEvents(self)
        self.closed.emit()
        event.accept()
