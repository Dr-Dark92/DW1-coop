"""Digi-Dex avatars: each Digimon's textured model, drawn the way the Characters and Digimon tab shows it.

Rendered from the disc being played (a patched copy shows imported Digimon) and cached as PNGs in the player's
store, keyed by the model and texture bytes. No game art is bundled; the cache stays on this computer.
"""
from __future__ import annotations

import hashlib
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
