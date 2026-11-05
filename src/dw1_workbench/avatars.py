"""Digi-Dex avatars: each Digimon's textured model, drawn the way the Characters and Digimon tab shows it.

Rendered from the disc being played (a patched copy shows imported Digimon) and cached as PNGs in the player's
store, keyed by the model and texture bytes. No game art is bundled; the cache stays on this computer.
"""
from __future__ import annotations

import hashlib
import multiprocessing
import os
import time
from pathlib import Path

from PySide6.QtGui import QImage

from .alltim import SLOT_SIZE
from .characters import parse_character_table
from .disc import inspect_image
from .errors import DiscFormatError, ValidationError
from .iso9660 import read_inventory
from .mmd import parse_mmd_static, parse_usa_skeleton
from .model_view import ModelSnapshot
from .tim import decode_tim

AVATAR_SIZE = 64
RENDER_VERSION = b"dex-avatar-v1"
EXECUTABLE = "SLUS_010.32"
ALLTIM = "CHDAT/ALLTIM.TIM"


def _avatar_process(disc, cache_dir, order, sender, stop):
    """Spawn target: no emulator, save or settings objects cross the process boundary."""
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["OMP_NUM_THREADS"] = "1"
    from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QThreadPool
    from PySide6.QtGui import QGuiApplication
    application = QGuiApplication.instance() or QGuiApplication([])
    QThreadPool.globalInstance().setMaxThreadCount(1)
    try:
        if hasattr(os, "nice"):
            os.nice(10)
        elif os.name == "nt":
            import ctypes
            ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
    except OSError:
        pass

    def send(message):
        if stop.is_set():
            return
        try:
            sender.send(message)  # a full pipe holds the renderer back until the player has read the icons
        except OSError:  # the player is gone or stopped listening
            stop.set()

    def publish(kind, image):
        buffer = QByteArray()
        output = QBuffer(buffer)
        output.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(output, "PNG")
        send(("image", kind, bytes(buffer)))

    try:
        load_avatars(Path(disc), Path(cache_dir), order, publish, stop.is_set)
    except Exception as exc:
        send(("error", str(exc)))
    finally:
        send(("done",))
        sender.close()


def load_avatars_isolated(disc, cache_dir, order, publish, cancelled=lambda: False, telemetry=None):
    """One low-priority renderer process, a one-way pipe, cooperative cancellation then process cleanup."""
    context = multiprocessing.get_context("spawn")
    stop = context.Event()
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_avatar_process, args=(str(disc), str(cache_dir), tuple(order), sender, stop),
                              name="dw1-avatar-renderer", daemon=True)
    process.start()
    sender.close()  # only the renderer holds the write end now, so its death ends the pipe, even mid-message
    last_memory = 0.0
    try:
        while not cancelled():
            if telemetry is not None and time.monotonic() - last_memory >= 1:
                telemetry.memory(process.pid)
                last_memory = time.monotonic()
            try:
                if not receiver.poll(0.1):
                    continue
                message = receiver.recv()
            except (EOFError, OSError):
                process.join(1)
                if process.exitcode:
                    raise RuntimeError(f"Avatar renderer exited with code {process.exitcode}") from None
                return
            if message[0] == "done":
                return
            if message[0] == "error":
                raise RuntimeError(message[1])
            image = QImage.fromData(message[2], "PNG")
            if not image.isNull():
                publish(message[1], image)
                if telemetry is not None:
                    telemetry.count("avatars.published")
    finally:
        stop.set()
        receiver.close()  # a renderer blocked on a full pipe wakes up and stops
        process.join(1)
        if process.is_alive():
            process.terminate()  # isolated disposable renderer only, never the emulator thread/process
            process.join(2)
        if process.is_alive():
            process.kill()
            process.join(2)
        process.close()


def _qimage(image) -> QImage:
    rgba = image.convert("RGBA")
    return QImage(rgba.tobytes("raw", "RGBA"), rgba.width, rgba.height, QImage.Format.Format_RGBA8888).copy()


def render_avatar(model_data: bytes, texture_data: bytes, skeleton, size: int = AVATAR_SIZE) -> QImage:
    """Static pose, preview camera angle, transparent background."""
    model = parse_mmd_static(model_data, skeleton)
    first = decode_tim(texture_data)
    palettes = tuple(_qimage(decode_tim(texture_data, index).image) for index in range(first.palette_count))
    snapshot = ModelSnapshot(model, palettes, first.clut_y)
    snapshot.zoom = 1.45  # fill the square; the preview leaves room to orbit
    return snapshot.image(size)


def render_character(disc: Path, kind: int, size: int = AVATAR_SIZE) -> QImage | None:
    """One Digimon's avatar straight from the disc; None when it isn't on the disc."""
    with inspect_image(Path(disc)).open() as reader:
        inventory = {entry.path: entry for entry in read_inventory(reader)}
        exe = inventory[EXECUTABLE]
        executable = reader.read_extent(exe.extent, exe.size, 2 * 1024 * 1024)
        character = next((entry for entry in parse_character_table(executable) if entry.resource_id == kind), None)
        mmd = inventory.get(character.model_path) if character else None
        if mmd is None:
            return None
        alltim = inventory[ALLTIM]
        return render_avatar(reader.read_extent(mmd.extent, mmd.size, 4 * 1024 * 1024),
                             reader.read_extent_range(alltim.extent, alltim.size, character.texture_slot * SLOT_SIZE, SLOT_SIZE),
                             parse_usa_skeleton(executable, kind), size)


def load_avatars(disc: Path, cache_dir: Path, order, publish, cancelled=lambda: False):
    """Call publish(kind, QImage) for every Digimon in `order` whose model renders. Runs on any thread."""
    cache_dir = Path(cache_dir)
    with inspect_image(Path(disc)).open() as reader:
        inventory = {entry.path: entry for entry in read_inventory(reader, cancelled)}
        exe = inventory[EXECUTABLE]
        executable = reader.read_extent(exe.extent, exe.size, 2 * 1024 * 1024)
        characters = {entry.resource_id: entry for entry in parse_character_table(executable)}
        alltim = inventory[ALLTIM]
        for kind in order:
            if cancelled():
                return
            character = characters.get(kind)
            mmd = inventory.get(character.model_path) if character else None
            if mmd is None:
                continue
            model_data = reader.read_extent(mmd.extent, mmd.size, 4 * 1024 * 1024)
            texture_data = reader.read_extent_range(alltim.extent, alltim.size, character.texture_slot * SLOT_SIZE,
                                                    SLOT_SIZE, cancelled)
            digest = hashlib.sha256(RENDER_VERSION + model_data + texture_data).hexdigest()[:16]
            path = cache_dir / f"{kind:03d}-{digest}.png"
            image = QImage(str(path)) if path.exists() else QImage()
            if image.isNull():
                try:
                    image = render_avatar(model_data, texture_data, parse_usa_skeleton(executable, kind))
                except (DiscFormatError, ValidationError):
                    continue  # unsupported geometry: the Dex shows the name without an avatar
                try:
                    cache_dir.mkdir(parents=True, exist_ok=True)
                    for old in cache_dir.glob(f"{kind:03d}-*.png"):
                        old.unlink(missing_ok=True)
                    image.save(str(path), "PNG")
                except OSError:
                    pass  # still shown this session
            publish(kind, image)
