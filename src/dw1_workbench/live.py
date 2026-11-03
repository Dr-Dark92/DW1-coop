"""Live game state read from console RAM while the USA release runs in the player.

Addresses and layouts are from jype0/dw_decomp aaf69a4 (config/us/symbols.txt, include/dw/entity.h,
src/main/map.c, src/main/map_collision.c) and apply only to SLUS_010.32.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

RAM_BASE = 0x80000000
RAM_SIZE = 0x200000
CURRENT_SCREEN = 0x80134DA8  # u8 map-table index, set by the map loader after the floor grid is copied
TAMER_ENTITY = 0x8015576C  # TamerEntity; starts with Entity {i32 type; PositionData *posData; ...}
PARTNER_ENTITY = 0x801557A8  # PartnerEntity; starts with the same Entity header
MAP_COLLISION_DATA = 0x801AF398  # u8[100 * 100], the running map's tile-trigger grid
GRID = 100
POSITION_POINTER_OFFSET = 4
# PositionData: GsDOBJ2 (16) + GsCOORDINATE2 (80) + VECTOR scale (16) + SVECTOR rotation (8), then VECTOR location.
LOCATION_OFFSET = 120


def ram_offset(address: int) -> int:
    return (address - RAM_BASE) & (RAM_SIZE - 1)


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
class LiveState:
    map_index: int
    tamer: EntityPosition | None
    partner: EntityPosition | None
    grid: bytes  # MAP_COLLISION_DATA as the game holds it now (scripts may have changed it)

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


def _position(read, entity_address: int) -> EntityPosition | None:
    pointer = struct.unpack("<I", read(ram_offset(entity_address + POSITION_POINTER_OFFSET), 4))[0]
    if not RAM_BASE <= pointer < RAM_BASE + RAM_SIZE - LOCATION_OFFSET - 12:
        return None  # not set up yet (title screen, loading)
    return EntityPosition(*struct.unpack("<3i", read(ram_offset(pointer + LOCATION_OFFSET), 12)))


def read_live_state(read) -> LiveState:
    """`read(offset, size)` returns bytes of main RAM (2 MiB, offset from 0x80000000)."""
    return LiveState(read(ram_offset(CURRENT_SCREEN), 1)[0], _position(read, TAMER_ENTITY),
                     _position(read, PARTNER_ENTITY), read(ram_offset(MAP_COLLISION_DATA), GRID * GRID))


def grid_differences(live: bytes, decoded: bytes) -> list[tuple[int, int, int, int]]:
    """(column, row, decoded value, live value) for each tile the running game holds differently."""
    return [(index % GRID, index // GRID, before, after)
            for index, (before, after) in enumerate(zip(decoded, live)) if before != after]
