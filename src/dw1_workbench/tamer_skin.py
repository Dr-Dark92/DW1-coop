"""Playing as another character: the Tamer's model and texture replaced in the running game, same skeleton and animations.

The game loads the Tamer only at a new game or a memory-card load, and a quick save holds its copy, so the choice can't be
made on the disc: it is made in RAM and VRAM, and checked again in case the game loads the Tamer from the disc later. A
character is a folder of skin.json, model.mmd (the same size as the original, with its animations where they were) and
texture.tim (the same fixed slot), and detail.bin when the model doesn't fit the slot: an object-table offset past the end
of model.mmd means detail.bin, as if appended. No character data is bundled; these live in the user's project.

The detail goes in the low end of the 64 KB the game reserves for its stack (_stack_addr 0x801FFF00, _stack_size 64). The heap
stops below that, and the stack stays far above it. The empty RAM below it is the heap's, for the next Digimon a map loads.
"""
from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from pathlib import Path

from .alltim import SLOT_SIZE
from .camera import GPURAM_FIELD
from .live import TAMER_ENTITY, ram_offset

CHECK_FRAMES = 30
OBJECT_TABLE = 20  # in an MMD: its 8-byte header and the TMD's id, flags and object count
OBJECT_SIZE = 28
VRAM_WIDTH, VRAM_HEIGHT = 1024, 512
ANIMATION_POINTER = 8  # Entity.animPtr
# The Tamer's ModelComponent (getEntityModelComponent): its texture is uploaded to this page whatever the TIM says.
TAMER_PIXEL_PAGE, TAMER_IMAGE_Y = 0x15, 256
DETAIL_ADDRESS, DETAIL_CAPACITY = 0x801EFF00, 48 * 1024


@dataclass(frozen=True)
class Character:
    name: str
    folder: Path

    @property
    def preview(self) -> Path:
        return self.folder / "preview.png"

    def data(self) -> tuple[bytes, bytes, bytes]:
        detail = self.folder / "detail.bin"
        return ((self.folder / "model.mmd").read_bytes(), (self.folder / "texture.tim").read_bytes(),
                detail.read_bytes() if detail.is_file() else b"")


def load_characters(directory: Path | None) -> list[Character]:
    """The folders that hold a whole character; anything else is left out."""
    found = []
    if directory is None or not Path(directory).is_dir():
        return found
    for folder in sorted(Path(directory).iterdir()):
        try:
            record = json.loads((folder / "skin.json").read_text(encoding="utf-8"))
            name = record["name"]
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if isinstance(name, str) and name and (folder / "model.mmd").is_file() and (folder / "texture.tim").is_file():
            found.append(Character(name, folder))
    return found


def tim_blocks(tim: bytes) -> list[tuple[int, int, int, int, bytes]]:
    """The VRAM rectangles of a 4- or 8-bit TIM with a palette: palette, then image, as (x, y, width, height, words)."""
    if len(tim) < 8 or struct.unpack_from("<I", tim)[0] != 0x10 or not struct.unpack_from("<I", tim, 4)[0] & 8:
        raise ValueError("Not a TIM with a palette")
    blocks, at = [], 8
    for _ in range(2):
        length, x, y, width, height = struct.unpack_from("<I4H", tim, at)
        data = tim[at + 12:at + 12 + width * height * 2]
        if len(data) != width * height * 2 or x + width > VRAM_WIDTH or y + height > VRAM_HEIGHT:
            raise ValueError("TIM block outside VRAM")
        blocks.append((x, y, width, height, data))
        at += length
    return blocks


def patch_vram(state: bytes, tim: bytes, image_y: int = TAMER_IMAGE_Y) -> bytes | None:
    """A save state with the TIM written into its VRAM, its image at `image_y`; None when the state has no VRAM this size."""
    marker = state.find(GPURAM_FIELD)
    if marker < 0:
        return None
    start = marker + len(GPURAM_FIELD) + 4
    if struct.unpack_from("<I", state, marker + len(GPURAM_FIELD))[0] != VRAM_WIDTH * VRAM_HEIGHT * 2:
        return None
    result = bytearray(state)
    palette, image = tim_blocks(tim)
    for x, y, width, height, data in (palette, (image[0], image_y, *image[2:])):
        for row in range(height):
            at = start + ((y + row) * VRAM_WIDTH + x) * 2
            result[at:at + width * 2] = data[row * width * 2:(row + 1) * width * 2]
    return bytes(result)


def as_loaded(model: bytes, base: int, object_count: int, pixel_page: int = TAMER_PIXEL_PAGE, detail: bytes = b"") -> tuple[bytes, bytes]:
    """The model and detail as the game has them in RAM, the model at `base`. GsMapModelingData makes the object table's three
    offsets absolute and gives the first polygon of each run of one kind that run's length, in place of its two size bytes:
    the renderer walks by it, so a wrong one hangs the game. updateTMDTextureData moves every polygon to the component's page."""
    result = bytearray(model + detail)
    for index in range(object_count):
        at = OBJECT_TABLE + index * OBJECT_SIZE
        _, _, _, _, primitives, primitive_count = struct.unpack_from("<6I", result, at)
        for field in (0, 8, 16):
            offset = OBJECT_TABLE + struct.unpack_from("<I", result, at + field)[0]
            struct.pack_into("<I", result, at + field, base + offset if offset < len(model) else DETAIL_ADDRESS + offset - len(model))
        cursor = OBJECT_TABLE + primitives
        run_start, run_kind, run_length = None, None, 0
        for _ in range(primitive_count):
            words = result[cursor + 1] + 1
            texture_word = struct.unpack_from("<I", result, cursor + 8)[0]
            struct.pack_into("<I", result, cursor + 8, (texture_word & 0xFFE0FFFF) | pixel_page << 16)
            kind = bytes(result[cursor + 2:cursor + 4])
            if kind != run_kind:
                if run_start is not None:
                    struct.pack_into("<H", result, run_start, run_length)
                run_start, run_kind, run_length = cursor, kind, 0
            run_length += 1
            cursor += words * 4
        if run_start is not None:
            struct.pack_into("<H", result, run_start, run_length)
    struct.pack_into("<I", result, 12, 1)  # TMD flags: mapped
    return bytes(result[:len(model)]), bytes(result[len(model):])


def vertex_ranges(whole: bytes, object_count: int) -> list[tuple[int, int]]:
    """Where the vertices are in model + detail. They tell which model is loaded; polygon headers don't, the running game
    writes to some."""
    ranges = []
    for index in range(object_count):
        top, count = struct.unpack_from("<2I", whole, OBJECT_TABLE + index * OBJECT_SIZE)
        ranges.append((OBJECT_TABLE + top, count * 8))
    return ranges


class TamerSkin:
    """Keeps the running game's Tamer as this model and texture. Other models, and loads in progress, are left alone."""

    def __init__(self, core, model: bytes, texture: bytes, detail: bytes = b"", entity: int = TAMER_ENTITY):
        self.core, self.entity = core, entity
        self.model, self.texture, self.detail = model, texture, detail
        self.animation_offset = struct.unpack_from("<I", model, 4)[0]
        self.object_count = struct.unpack_from("<I", model, 16)[0]
        if struct.unpack_from("<I", model, 0)[0] != 8 or struct.unpack_from("<I", model, 8)[0] != 0x41 or len(texture) != SLOT_SIZE:
            raise ValueError("Not a character model and texture")
        if len(detail) > DETAIL_CAPACITY:
            raise ValueError("Character model too large")
        tim_blocks(texture)
        self._whole = model + detail
        self._vertices = vertex_ranges(self._whole, self.object_count)
        self._frames = 0
        self.applied = 0

    def _read(self, address, size):
        return self.core.read_memory(ram_offset(address), size)

    def _base(self) -> int | None:
        """Where the Tamer's model is in RAM, if this is the file we expect there."""
        pointer = struct.unpack("<I", self._read(self.entity + ANIMATION_POINTER, 4))[0]
        base = pointer - self.animation_offset
        if not 0x80000000 <= base < 0x80200000 - len(self.model):
            return None
        animation_offset, tmd_id = struct.unpack("<I", self._read(base + 4, 4))[0], struct.unpack("<I", self._read(base + 8, 4))[0]
        count = struct.unpack("<I", self._read(base + 16, 4))[0]
        if animation_offset != self.animation_offset or tmd_id != 0x41 or count != self.object_count:
            return None
        return base

    def tick(self):
        self._frames += 1
        if self._frames % CHECK_FRAMES == 1:
            self.apply()

    def apply(self) -> bool:
        """Make RAM and VRAM this character, if they aren't. True when something changed."""
        base = self._base()
        if base is None:
            return False
        if all(self._read(base + at if at < len(self.model) else DETAIL_ADDRESS + at - len(self.model), size) == self._whole[at:at + size]
               for at, size in self._vertices):
            return False
        model, detail = as_loaded(self.model, base, self.object_count, detail=self.detail)
        self.core.write_memory(ram_offset(base), model)
        if detail:
            self.core.write_memory(ram_offset(DETAIL_ADDRESS), detail)
        state = patch_vram(self.core.serialize(), self.texture)
        if state is not None:
            self.core.unserialize(state)
        self.applied += 1
        return True


def disc_tamer(disc: Path) -> tuple[bytes, bytes]:
    """The disc's own Tamer, for putting back when a save was made as another character."""
    from .avatars import ALLTIM, read_inventory
    from .disc import inspect_image
    with inspect_image(Path(disc)).open() as reader:
        inventory = {entry.path: entry for entry in read_inventory(reader)}
        model = inventory["CHDAT/MMD0/BOYS.MMD"]
        alltim = inventory[ALLTIM]
        return (reader.read_extent(model.extent, model.size, 4 * 1024 * 1024),
                reader.read_extent_range(alltim.extent, alltim.size, 0, SLOT_SIZE))
