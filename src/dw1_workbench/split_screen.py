"""Split screen: player 2's game runs beside player 1's, from a different save.

The emulator core allows one copy per process, so player 2's game is a second process running the same EmulatorWorker,
without a window. A block of shared memory carries its newest frame one way and its buttons the other. Player 2 has their
own copy of the chosen save and memory card under <store>/player2, so neither game can overwrite the other's.

This is two separate games side by side: nothing is synchronised between them yet, and player 2's game is silent.
"""
from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import sys
import time
from multiprocessing import shared_memory
from pathlib import Path

HEADER = struct.Struct("<IIIII")  # frame sequence, width, height, buttons, quit
MAX_WIDTH, MAX_HEIGHT = 1280, 960
SIZE = HEADER.size + MAX_WIDTH * MAX_HEIGHT * 4
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


class SplitGame:
    """Player 1's window's handle on player 2's process."""

    def __init__(self, config: dict):
        self.memory = shared_memory.SharedMemory(create=True, size=SIZE)
        HEADER.pack_into(self.memory.buf, 0, 0, 0, 0, 0, 0)
        environment = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONPATH=os.pathsep.join(p for p in sys.path if p))
        self.process = subprocess.Popen([sys.executable, "-m", "dw1_workbench.split_screen", self.memory.name, json.dumps(config)],
                                        env=environment)
        self._last = 0

    def set_buttons(self, buttons: int):
        struct.pack_into("<I", self.memory.buf, 12, buttons)

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
    config = PlayConfig(Path(settings["core"]), Path(settings["bios"]), Path(settings["disc"]), Path(settings["store"]),
                        launch_slot=settings["slot"], resume_on_close=True, live_ram=settings["live_ram"],
                        game_rules=settings["game_rules"],
                        hd_pictures=Path(settings["hd"]) if settings.get("hd") else None, options=settings["options"])
    memory = _attach(memory_name)
    parent = os.getppid()
    worker = EmulatorWorker(config)
    worker._start_avatars = lambda: None  # rendering them would slow this game for a minute; player 1's menus have them
    thread = QThread()
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.message.connect(lambda text: print(f"[player 2] {text}", file=sys.stderr, flush=True))
    worker.failed.connect(lambda text: print(f"[player 2] {text}", file=sys.stderr, flush=True))
    sequence = [0]
    shown = [None]

    def poll():
        worker.buttons = struct.unpack_from("<I", memory.buf, 12)[0]
        if struct.unpack_from("<I", memory.buf, 16)[0] or os.getppid() != parent:
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
        if width > MAX_WIDTH or height > MAX_HEIGHT or image.bytesPerLine() != width * 4:
            return
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
    memory.close()
    return code


if __name__ == "__main__":
    sys.exit(_child(sys.argv[1], sys.argv[2]))
