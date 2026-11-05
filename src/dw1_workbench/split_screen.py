"""Split screen: player 2's game runs beside player 1's, from a different save.

The emulator core allows one copy per process, so player 2's game is a second process running the same EmulatorWorker,
without a window. A block of shared memory carries its newest frame one way and its buttons the other. Player 2 has their
own copy of the chosen save and memory card under <store>/player2, so neither game can overwrite the other's.

Each game also publishes where its partner is, so the other can show it on the same map (visitor.py). A fight either player
starts on a shared map runs in that game with the other's Digimon as the ally: the other game pauses, and both halves of
the screen show the fight. Otherwise these are two separate games: progress isn't shared yet, and player 2's game is silent.
"""
from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
from multiprocessing import shared_memory
from pathlib import Path

from .visitor import NpcState, WorldRecord, WorldState

HEADER = struct.Struct("<IIIIII")  # frame sequence, width, height, buttons, quit, paused
MAX_WIDTH, MAX_HEIGHT = 1280, 960
RECORD = struct.Struct("<IiiihHBBB")  # sequence, x, y, z, rotation, species, map, animation, visible
RECORDS = HEADER.size + MAX_WIDTH * MAX_HEIGHT * 4  # after the frame: one per player for each actor
ACTORS = 2
CONTROL = RECORDS + RECORD.size * 2 * ACTORS
# Per player, after the records: wants to host, level, the partner's six stats, and what the host has earned for them
# (exp and bits in total, and a growing count of Digi-Dex messages), then presses waiting for this player's game to host.
PLAYER = struct.Struct("<IH6hIII")
PRESS_COUNT = 16
PRESSES = CONTROL + PLAYER.size * 2  # per player: a sequence and PRESS_COUNT button codes
PRESS_SIZE = 4 + PRESS_COUNT
DEX = PRESSES + PRESS_SIZE * 2  # per player: the sequence above is PLAYER's; length, then JSON
DEX_SIZE = 8192
WORLDS = DEX + DEX_SIZE * 2  # per player: a sequence, the map and clock, and eight map Digimon
WORLD_HEADER = struct.Struct("<IIhhhhi")
WORLD_NPC = struct.Struct("<hBxiiih")
WORLD_SIZE = WORLD_HEADER.size + WORLD_NPC.size * 8
SIZE = WORLDS + WORLD_SIZE * 2
PRESS_CODES = ("left", "right", "confirm", "finisher")
SAVE_SUFFIXES = (".state", ".json", ".png", ".progress.json")


def copy_save(store: Path, slot: int, target: Path, card_name: str = "memory_card_1.srm"):
    """Player 2's own copy of a quick save and of the memory card; the originals are never touched."""
    target.mkdir(parents=True, exist_ok=True)
    for suffix in SAVE_SUFFIXES:
        source = store / f"slot{slot}{suffix}"
        if source.exists():
            shutil.copy2(source, target / source.name)
    card = store / card_name
    if card.exists() and not (target / card_name).exists():
        shutil.copy2(card, target / card_name)


def choosable_saves(slots, player_one_slot) -> list[dict]:
    """The rows of player 2's save list: every save, with player 1's marked as not available."""
    rows = []
    for info in slots:
        taken = info.slot == player_one_slot
        when = info.created[5:16].replace("T", " ")
        name = "Resume" if info.slot == 0 else f"Slot {info.slot}"
        rows.append({"slot": info.slot, "label": f"{name} · " + ("Player 1's save" if taken else when), "taken": taken})
    return rows


class WorldLink:
    """One game's side of the two records saying where each partner is. Used from the core's thread."""

    def __init__(self, memory: shared_memory.SharedMemory, player: int):
        self._memory, self._player = memory, player
        self._sequence = [0] * ACTORS
        self._presses_seen = 0
        self._world_sequence = 0
        self._lock = threading.Lock()

    def publish(self, record: WorldRecord | None, actor: int = 0) -> None:
        offset = RECORDS + RECORD.size * (self._player * ACTORS + actor)
        visible = record is not None and record.visible
        fields = (record.x, record.y, record.z, record.rotation, record.species, record.map, record.animation) if record else (0,) * 7
        with self._lock:
            if self._memory is None:
                return
            self._sequence[actor] += 1
            struct.pack_into("<I", self._memory.buf, offset, self._sequence[actor] * 2 - 1)  # odd: being written
            RECORD.pack_into(self._memory.buf, offset, self._sequence[actor] * 2 - 1, *fields, visible)
            struct.pack_into("<I", self._memory.buf, offset, self._sequence[actor] * 2)

    def other(self, actor: int = 0) -> WorldRecord | None:
        offset = RECORDS + RECORD.size * ((1 - self._player) * ACTORS + actor)
        with self._lock:
            if self._memory is None:
                return None
            sequence, x, y, z, rotation, species, screen, animation, visible = RECORD.unpack_from(self._memory.buf, offset)
            if not sequence or sequence & 1 or struct.unpack_from("<I", self._memory.buf, offset)[0] != sequence:
                return None
        return WorldRecord(screen, x, y, z, rotation, species, animation, bool(visible))

    # -- the shared battle --------------------------------------------------------------------------------------
    def _control(self, player: int) -> tuple:
        return PLAYER.unpack_from(self._memory.buf, CONTROL + PLAYER.size * player)

    def _set_control(self, player: int, field: int, value) -> None:
        fields = list(self._control(player))
        fields[field] = value
        PLAYER.pack_into(self._memory.buf, CONTROL + PLAYER.size * player, *fields)

    def host(self) -> int | None:
        """The player whose game runs the fight both are in. Player 1's wins if both meet an enemy at once."""
        with self._lock:
            if self._memory is None:
                return None
            for player in (0, 1):
                if self._control(player)[0]:
                    return player
        return None

    def is_guest(self) -> bool:
        return self.host() == 1 - self._player

    def set_hosting(self, hosting: bool) -> None:
        with self._lock:
            if self._memory is not None:
                self._set_control(self._player, 0, int(hosting))

    def publish_partner(self, level: int, stats: dict) -> None:
        with self._lock:
            if self._memory is not None:
                fields = list(self._control(self._player))
                fields[1:8] = [level, stats["offense"], stats["defense"], stats["speed"], stats["brains"], stats["hp"], stats["mp"]]
                PLAYER.pack_into(self._memory.buf, CONTROL + PLAYER.size * self._player, *fields)

    def other_partner(self) -> tuple[int, dict] | None:
        with self._lock:
            if self._memory is None:
                return None
            fields = self._control(1 - self._player)
        if not fields[1]:
            return None
        return fields[1], dict(zip(("offense", "defense", "speed", "brains", "hp", "mp"), fields[2:8]))

    def award_other(self, exp_total: int, bits_total: int) -> None:
        """What this game has earned for the other player, in total since split screen started."""
        with self._lock:
            if self._memory is not None:
                self._set_control(1 - self._player, 8, exp_total)
                self._set_control(1 - self._player, 9, bits_total)

    def my_awards(self) -> tuple[int, int]:
        with self._lock:
            if self._memory is None:
                return 0, 0
            fields = self._control(self._player)
        return fields[8], fields[9]

    def send_dex(self, entries: dict) -> None:
        data = json.dumps(entries).encode()
        if len(data) > DEX_SIZE - 4:
            return
        offset = DEX + DEX_SIZE * (1 - self._player)
        with self._lock:
            if self._memory is not None:
                struct.pack_into("<I", self._memory.buf, offset, len(data))
                self._memory.buf[offset + 4:offset + 4 + len(data)] = data
                self._set_control(1 - self._player, 10, self._control(1 - self._player)[10] + 1)

    def take_dex(self, seen: int) -> tuple[int, dict]:
        """(count, entries) when there is a Digi-Dex message newer than seen."""
        offset = DEX + DEX_SIZE * self._player
        with self._lock:
            if self._memory is None:
                return seen, {}
            count = self._control(self._player)[10]
            if count == seen:
                return seen, {}
            length = struct.unpack_from("<I", self._memory.buf, offset)[0]
            data = bytes(self._memory.buf[offset + 4:offset + 4 + length])
        return count, json.loads(data)

    def send_press(self, player: int, code: str) -> None:
        """A battle button, for the game of the given player to act on."""
        offset = PRESSES + PRESS_SIZE * player
        with self._lock:
            if self._memory is not None:
                sequence = struct.unpack_from("<I", self._memory.buf, offset)[0]
                self._memory.buf[offset + 4 + sequence % PRESS_COUNT] = PRESS_CODES.index(code)
                struct.pack_into("<I", self._memory.buf, offset, sequence + 1)

    def take_presses(self) -> list[str]:
        offset = PRESSES + PRESS_SIZE * self._player
        with self._lock:
            if self._memory is None:
                return []
            sequence = struct.unpack_from("<I", self._memory.buf, offset)[0]
            start = max(self._presses_seen, sequence - PRESS_COUNT)
            codes = [PRESS_CODES[self._memory.buf[offset + 4 + index % PRESS_COUNT]] for index in range(start, sequence)]
            self._presses_seen = sequence
        return codes

    def publish_world(self, world: WorldState) -> None:
        offset = WORLDS + WORLD_SIZE * self._player
        with self._lock:
            if self._memory is None:
                return
            self._world_sequence += 1
            WORLD_HEADER.pack_into(self._memory.buf, offset, self._world_sequence * 2 - 1, world.map, world.hour, world.minute,
                                   world.year, world.day, world.frame)
            for index, npc in enumerate(world.npcs):
                WORLD_NPC.pack_into(self._memory.buf, offset + WORLD_HEADER.size + WORLD_NPC.size * index, npc.species,
                                    npc.on_map, npc.x, npc.y, npc.z, npc.rotation)
            struct.pack_into("<I", self._memory.buf, offset, self._world_sequence * 2)

    def other_world(self) -> WorldState | None:
        offset = WORLDS + WORLD_SIZE * (1 - self._player)
        with self._lock:
            if self._memory is None:
                return None
            sequence, screen, hour, minute, year, day, frame = WORLD_HEADER.unpack_from(self._memory.buf, offset)
            npcs = tuple(WORLD_NPC.unpack_from(self._memory.buf, offset + WORLD_HEADER.size + WORLD_NPC.size * index)
                         for index in range(8))
            if not sequence or sequence & 1 or struct.unpack_from("<I", self._memory.buf, offset)[0] != sequence:
                return None
        return WorldState(screen, hour, minute, year, day, frame,
                          tuple(NpcState(s, bool(on), x, y, z, r) for s, on, x, y, z, r in npcs))

    def close(self):
        with self._lock:
            self._memory = None


class SplitGame:
    """Player 1's window's handle on player 2's process."""

    def __init__(self, config: dict):
        self.memory = shared_memory.SharedMemory(create=True, size=SIZE)
        HEADER.pack_into(self.memory.buf, 0, 0, 0, 0, 0, 0, 0)
        environment = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONPATH=os.pathsep.join(p for p in sys.path if p))
        self.process = subprocess.Popen([sys.executable, "-m", "dw1_workbench.split_screen", self.memory.name,
                                         json.dumps({**config, "parent": os.getpid()})], env=environment)
        self._last = 0
        self.link = WorldLink(self.memory, 0)

    def set_buttons(self, buttons: int):
        struct.pack_into("<I", self.memory.buf, 12, buttons)

    def set_paused(self, paused: bool):
        struct.pack_into("<I", self.memory.buf, 20, paused)

    def newest_frame(self):
        """(width, height, bytes) when there is a new frame, else None."""
        sequence, width, height = struct.unpack_from("<III", self.memory.buf, 0)
        if sequence == self._last or sequence & 1 or not width or not height:
            return None
        data = bytes(self.memory.buf[HEADER.size:HEADER.size + width * height * 4])
        if struct.unpack_from("<I", self.memory.buf, 0)[0] != sequence:
            return None  # being overwritten: the next one will do
        self._last = sequence
        return width, height, data

    def running(self) -> bool:
        return self.process.poll() is None

    def stop(self):
        self.link.close()
        struct.pack_into("<I", self.memory.buf, 16, 1)
        try:
            self.process.wait(8)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        self.memory.close()
        try:
            self.memory.unlink()
        except FileNotFoundError:
            pass  # already gone: nothing is left to free


def parent_watch(pid: int):
    """A function telling whether player 1's process (`pid`) is still running, for player 2 to stop with it.

    On Windows os.getppid() keeps its value after the parent dies, and the parent may be the venv's python.exe
    launcher rather than player 1, so the process itself is watched through a handle (which also holds its id)."""
    if sys.platform != "win32":
        return lambda: os.getppid() == pid
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    synchronize, wait_timeout = 0x00100000, 0x102
    handle = kernel32.OpenProcess(synchronize, False, pid)
    if not handle:
        return lambda: False  # already gone
    return lambda: kernel32.WaitForSingleObject(handle, 0) == wait_timeout


def _attach(name: str) -> shared_memory.SharedMemory:
    """Open player 1's block without owning it: an owner's exit would delete the block while player 1 still uses it."""
    if sys.version_info >= (3, 13):
        return shared_memory.SharedMemory(name=name, track=False)
    memory = shared_memory.SharedMemory(name=name)
    if os.name == "posix":
        from multiprocessing import resource_tracker
        resource_tracker.unregister(memory._name, "shared_memory")
    return memory


def _child(memory_name: str, raw: str) -> int:
    from PySide6.QtCore import QThread, QTimer
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication

    from .player import EmulatorWorker, PlayConfig

    application = QApplication([])
    settings = json.loads(raw)
    comparison = settings.get("comparison", False)
    temporary = tempfile.TemporaryDirectory(prefix="dw1-original-") if comparison else None
    store = Path(temporary.name if temporary else settings["store"])
    config = PlayConfig(Path(settings["core"]), Path(settings["bios"]), Path(settings["disc"]), store,
                        launch_slot=settings["slot"], resume_on_close=not comparison, live_ram=settings["live_ram"],
                        game_rules=settings["game_rules"],
                        hd_pictures=Path(settings["hd"]) if settings.get("hd") else None, options=settings["options"],
                        character=Path(settings["character"]) if settings.get("character") else None,
                        keep_tamer=settings.get("keep_tamer", True),
                        framerate=settings.get("framerate", 0.0))
    memory = _attach(memory_name)
    parent_running = parent_watch(settings["parent"])
    worker = EmulatorWorker(config)
    worker._start_avatars = lambda: None  # rendering them would slow this game for a minute; player 1's menus have them
    worker.player_label = "Player 2"
    if settings["live_ram"]:
        worker.link = WorldLink(memory, 1)
    thread = QThread()
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.message.connect(lambda text: print(f"[player 2] {text}", file=sys.stderr, flush=True))
    worker.failed.connect(lambda text: print(f"[player 2] {text}", file=sys.stderr, flush=True))
    sequence = [0]
    shown = [None]

    def poll():
        worker.buttons = struct.unpack_from("<I", memory.buf, 12)[0]
        if comparison:
            worker.paused = bool(struct.unpack_from("<I", memory.buf, 20)[0])
        if struct.unpack_from("<I", memory.buf, 16)[0] or not parent_running():
            worker.request_stop()
            thread.quit()
            thread.wait(6000)
            application.quit()
            return
        image = worker.take_frame()
        if image is None or image is shown[0]:
            return
        shown[0] = image
        if image.format() != QImage.Format.Format_RGB32:
            image = image.convertToFormat(QImage.Format.Format_RGB32)
        width, height = image.width(), image.height()
        if width > MAX_WIDTH or height > MAX_HEIGHT:
            return
        if image.bytesPerLine() != width * 4:
            image = image.copy()  # The core can supply padded rows, but the shared frame is packed.
        sequence[0] += 1
        struct.pack_into("<I", memory.buf, 0, sequence[0] * 2 - 1)  # odd: being written
        struct.pack_into("<II", memory.buf, 4, width, height)
        memory.buf[HEADER.size:HEADER.size + width * height * 4] = bytes(image.constBits())[:width * height * 4]
        struct.pack_into("<I", memory.buf, 0, sequence[0] * 2)

    timer = QTimer()
    timer.timeout.connect(poll)
    timer.start(4)
    thread.start()
    code = application.exec()
    if worker.link is not None:
        worker.link.close()
    memory.close()
    return code


if __name__ == "__main__":
    sys.exit(_child(sys.argv[1], sys.argv[2]))
