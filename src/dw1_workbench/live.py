"""Live game state read from console RAM while the USA release runs in the player.

Addresses and layouts are from jype0/dw_decomp aaf69a4 (config/us/symbols.txt, include/dw/entity.h,
src/main/map.c, src/main/map_collision.c) and apply only to SLUS_010.32.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

RAM_BASE = 0x80000000
RAM_SIZE = 0x200000
RAM_ADDRESS_MASK = 0x7FFFFF  # up to the 8 MB of the extended core
CURRENT_SCREEN = 0x80134DA8  # u8 map-table index, set by the map loader after the floor grid is copied
TAMER_ENTITY = 0x8015576C  # TamerEntity; starts with Entity {i32 type; PositionData *posData; ...}
PARTNER_ENTITY = 0x801557A8  # PartnerEntity; starts with the same Entity header
MAP_COLLISION_DATA = 0x801AF398  # u8[100 * 100], the running map's tile-trigger grid
ENTITY_TABLE = 0x8012F344  # Entity *[10]: 0 Tamer, 1 partner, 2-9 NPC Digimon on the current map
ENTITY_SLOTS = 10
FIRST_NPC_SLOT = 2
TYPE_OFFSET = 0  # Entity.type: the character resource ID
IS_ON_MAP_OFFSET = 52  # Entity.isOnMap, after type, posData, animPtr and the 40-byte EntityAnim
# NPCEntity (104 bytes): Entity (56), BaseStats (20: max HP at +16), CurrentStats (12: HP at +0), then
# flee (8), bits, unk1, unk1_2, unk2, scriptId and autotalk at +102.
NPC_MAX_HP_OFFSET, NPC_HP_OFFSET, NPC_SCRIPT_OFFSET, NPC_AUTOTALK_OFFSET = 72, 76, 101, 102
# MAP_DIGIMON_TABLE[slot - 2] (MapDigimonEntity, 196 bytes) drives NPC slot `slot` (NPCEntityTickOverworld).
MAP_DIGIMON_TABLE = 0x8013CB50
MAP_DIGIMON_SIZE = 196
POST_OFFSET, TRACKING_RANGE_OFFSET = 168, 180  # post: posX, posY, posZ (3 x i16)
FOLLOW_MODE_OFFSET, LOOK_STATE_OFFSET = 188, 192  # lookAtTamerState: nonzero while it reacts to the Tamer
GRID = 100
POSITION_POINTER_OFFSET = 4
# PositionData: GsDOBJ2 (16) + GsCOORDINATE2 (80) + VECTOR scale (16) + SVECTOR rotation (8), then VECTOR location.
LOCATION_OFFSET = 120


_ram_size = RAM_SIZE  # the running core's: more than the console's RAM_SIZE on the extended core


def set_ram_size(size: int) -> None:
    global _ram_size
    _ram_size = size


def ram_end() -> int:
    """The first address past this core's main RAM, for checking pointers read from the game."""
    return RAM_BASE + _ram_size


def ram_offset(address: int) -> int:
    return (address - RAM_BASE) & RAM_ADDRESS_MASK


def _c_div(value: int, divisor: int) -> int:
    quotient = abs(value) // divisor
    return quotient if value >= 0 else -quotient


def model_tile(x: int, z: int) -> tuple[int, int]:
    """getModelTile: world X/Z to floor-grid column/row (row 0 is the far edge), with C truncating division."""
    tile_x, tile_y = _c_div(x, 100) + 50, 50 - _c_div(z, 100)
    return tile_x - (x < 0), tile_y - (z > 0)


@dataclass(frozen=True)
class EntityPosition:
    x: int
    y: int
    z: int

    @property
    def tile(self) -> tuple[int, int]:
        return model_tile(self.x, self.z)


@dataclass(frozen=True)
class LiveNpc:
    slot: int  # ENTITY_TABLE index, 2-9
    character_id: int
    position: EntityPosition
    follow_mode: int = 0
    tracking_range: int = 0
    post: tuple[int, int, int] = (0, 0, 0)  # where the map placed it; the centre of its tracking box
    reacting: bool = False  # lookAtTamerState != 0: turned towards, or chasing, the Tamer
    autotalk: bool = False  # touching the Tamer starts its script (set by the map script; battles use it)
    script_id: int = 0
    hp: int = 0
    max_hp: int = 0

    @property
    def tile(self) -> tuple[int, int]:
        return self.position.tile


@dataclass(frozen=True)
class LiveState:
    map_index: int
    tamer: EntityPosition | None
    partner: EntityPosition | None
    grid: bytes  # MAP_COLLISION_DATA as the game holds it now (scripts may have changed it)
    npcs: tuple[LiveNpc, ...] = ()
    items: tuple = ()  # items.GroundItem
    chests: tuple = ()  # items.Chest

    def trigger_at(self, tile: tuple[int, int] | None) -> int | None:
        if tile is None or not (0 <= tile[0] < GRID and 0 <= tile[1] < GRID):
            return None
        return self.grid[tile[0] + tile[1] * GRID]

    @property
    def tamer_tile(self) -> tuple[int, int] | None:
        return self.tamer.tile if self.tamer else None

    @property
    def partner_tile(self) -> tuple[int, int] | None:
        return self.partner.tile if self.partner else None


def _in_ram(pointer: int, size: int) -> bool:
    return RAM_BASE <= pointer and pointer + size <= ram_end()


def _position(read, entity_address: int) -> EntityPosition | None:
    pointer = struct.unpack("<I", read(ram_offset(entity_address + POSITION_POINTER_OFFSET), 4))[0]
    if not _in_ram(pointer, LOCATION_OFFSET + 12):
        return None  # not set up yet (title screen, loading)
    return EntityPosition(*struct.unpack("<3i", read(ram_offset(pointer + LOCATION_OFFSET), 12)))


def _npcs(read) -> tuple[LiveNpc, ...]:
    pointers = struct.unpack(f"<{ENTITY_SLOTS}I", read(ram_offset(ENTITY_TABLE), ENTITY_SLOTS * 4))
    npcs = []
    for slot in range(FIRST_NPC_SLOT, ENTITY_SLOTS):
        pointer = pointers[slot]
        if not _in_ram(pointer, IS_ON_MAP_OFFSET + 1) or not read(ram_offset(pointer + IS_ON_MAP_OFFSET), 1)[0]:
            continue  # empty slot, or an entity the game has taken off the map
        position = _position(read, pointer)
        if position is None:
            continue
        character_id = struct.unpack("<i", read(ram_offset(pointer + TYPE_OFFSET), 4))[0]
        max_hp, hp = struct.unpack("<h", read(ram_offset(pointer + NPC_MAX_HP_OFFSET), 2))[0], \
            struct.unpack("<h", read(ram_offset(pointer + NPC_HP_OFFSET), 2))[0]
        script_id, autotalk = read(ram_offset(pointer + NPC_SCRIPT_OFFSET), 2)
        table = MAP_DIGIMON_TABLE + (slot - FIRST_NPC_SLOT) * MAP_DIGIMON_SIZE
        post = struct.unpack("<3h", read(ram_offset(table + POST_OFFSET), 6))
        (tracking_range,) = struct.unpack("<h", read(ram_offset(table + TRACKING_RANGE_OFFSET), 2))
        follow_mode, _, _, _, look_state = struct.unpack("<5b", read(ram_offset(table + FOLLOW_MODE_OFFSET), 5))
        npcs.append(LiveNpc(slot, character_id, position, follow_mode, tracking_range, post,
                            look_state != 0, autotalk == 1, script_id, hp, max_hp))
    return tuple(npcs)


def read_live_state(read) -> LiveState:
    """`read(offset, size)` returns bytes of main RAM (2 MiB, offset from 0x80000000)."""
    from .items import read_chests, read_ground_items  # items builds on this module's address helpers
    return LiveState(read(ram_offset(CURRENT_SCREEN), 1)[0], _position(read, TAMER_ENTITY),
                     _position(read, PARTNER_ENTITY), read(ram_offset(MAP_COLLISION_DATA), GRID * GRID),
                     _npcs(read), read_ground_items(read), read_chests(read))


def grid_differences(live: bytes, decoded: bytes) -> list[tuple[int, int, int, int]]:
    """(column, row, decoded value, live value) for each tile the running game holds differently."""
    return [(index % GRID, index // GRID, before, after)
            for index, (before, after) in enumerate(zip(decoded, live)) if before != after]
