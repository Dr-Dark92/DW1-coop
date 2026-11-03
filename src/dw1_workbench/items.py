"""Item names from the USA executable, and ground items and chests read live from console RAM.

ITEM_PARA, DROPPED_ITEMS and CHEST_ARRAY are from jype0/dw_decomp aaf69a4 (config/us/symbols.txt,
include/dw/item.h, include/dw/map_object.h, src/main/item.c, src/main/map_object.c).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

from .characters import LOAD_ADDRESS
from .errors import DiscFormatError
from .live import ram_offset

# Item (32 bytes, USA): name[20], i32 value, u16 meritValue, i16 sortingValue, u8 itemColor, u8 droppable, u16.
ITEM_PARA_OFFSET = 0x801269DC - LOAD_ADDRESS
ITEM_COUNT = 128
ITEM_SIZE = 32
# DroppedItem (16 bytes): WorldItem {SVECTOR spriteLocation; i32 type (0xff = empty)}, i16 tileX, i16 tileY.
# Map scripts place ground items with SCRIPT_OP_SPAWN_ITEM -> spawnItem(item, tileX, tileY); battle drops use
# the same 11 slots. The Tamer picks one up within one tile in each direction (checkItemPickup).
DROPPED_ITEMS = 0x8013D4E0
DROPPED_ITEM_SLOTS = 11
DROPPED_ITEM_SIZE = 16
PICKUP_REACH = 1
# Chest (56 bytes): location, trayLocation, rotation, trayRotation, u16 trigger, u8 item, u8 isTaken,
# u8 tileX, u8 tileY. Map scripts place them with SCRIPT_OP_SPAWN_CHEST; the trigger records an opened chest.
CHEST_ARRAY = 0x80150F00
CHEST_SLOTS = 8
CHEST_SIZE = 56
CHEST_FIELDS_OFFSET = 48
EMPTY = 0xFF


def parse_item_names(executable: bytes) -> list[str]:
    """The 128 item names from the USA executable's ITEM_PARA table (blank names are kept as "")."""
    end = ITEM_PARA_OFFSET + ITEM_COUNT * ITEM_SIZE
    if len(executable) < end or not executable.startswith(b"PS-X EXE"):
        raise DiscFormatError("USA executable is truncated or lacks its PS-X EXE header.")
    names = []
    for index in range(ITEM_COUNT):
        raw = executable[ITEM_PARA_OFFSET + index * ITEM_SIZE:][:20].split(b"\0", 1)[0]
        try:
            name = raw.decode("ascii")
        except UnicodeDecodeError as exc:
            raise DiscFormatError(f"Item name {index} is not ASCII.") from exc
        if not name.isprintable():
            raise DiscFormatError(f"Item name {index} is not printable.")
        names.append(name)
    return names


@dataclass(frozen=True)
class GroundItem:
    slot: int
    item_id: int
    tile: tuple[int, int]

    def in_reach(self, tile: tuple[int, int] | None) -> bool:
        return tile is not None and all(abs(a - b) <= PICKUP_REACH for a, b in zip(tile, self.tile))


@dataclass(frozen=True)
class Chest:
    slot: int
    item_id: int
    tile: tuple[int, int]
    taken: bool
    trigger: int


def read_ground_items(read) -> tuple[GroundItem, ...]:
    data = read(ram_offset(DROPPED_ITEMS), DROPPED_ITEM_SLOTS * DROPPED_ITEM_SIZE)
    items = []
    for slot in range(DROPPED_ITEM_SLOTS):
        item_id, tile_x, tile_y = struct.unpack_from("<ihh", data, slot * DROPPED_ITEM_SIZE + 8)
        if item_id != EMPTY:
            items.append(GroundItem(slot, item_id, (tile_x, tile_y)))
    return tuple(items)


def read_chests(read) -> tuple[Chest, ...]:
    data = read(ram_offset(CHEST_ARRAY), CHEST_SLOTS * CHEST_SIZE)
    chests = []
    for slot in range(CHEST_SLOTS):
        trigger, item_id, taken, tile_x, tile_y = struct.unpack_from("<HBBBB", data, slot * CHEST_SIZE + CHEST_FIELDS_OFFSET)
        if item_id != EMPTY:
            chests.append(Chest(slot, item_id, (tile_x, tile_y), bool(taken), trigger))
    return tuple(chests)
