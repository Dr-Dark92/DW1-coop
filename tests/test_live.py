from __future__ import annotations

import struct

from dw1_workbench.live import (CURRENT_SCREEN, ENTITY_TABLE, MAP_COLLISION_DATA, PARTNER_ENTITY, TAMER_ENTITY, grid_differences,
                                model_tile, ram_offset, read_live_state)


def ram_with(**writes):
    ram = bytearray(0x200000)
    for address, data in writes.values():
        ram[ram_offset(address):ram_offset(address) + len(data)] = data
    return lambda offset, size: bytes(ram[offset:offset + size])


def test_model_tile_matches_get_model_tile_rounding():
    assert model_tile(0, 0) == (50, 50)
    assert model_tile(99, -99) == (50, 50)
    assert model_tile(-1, 1) == (49, 49)  # negative X and positive Z step one tile further
    assert model_tile(-100, 100) == (48, 48)
    assert model_tile(250, -350) == (52, 53)


def test_reads_map_positions_and_grid():
    read = ram_with(screen=(CURRENT_SCREEN, b"\x17"),
                    tamer=(TAMER_ENTITY + 4, struct.pack("<I", 0x80100000)),
                    location=(0x80100000 + 120, struct.pack("<3i", 250, -40, -350)),
                    partner=(PARTNER_ENTITY + 4, b"\x00\x00\x00\x00"),
                    grid=(MAP_COLLISION_DATA + 52 + 53 * 100, b"\x72"))
    state = read_live_state(read)
    assert state.map_index == 0x17
    assert (state.tamer.x, state.tamer.y, state.tamer.z, state.tamer_tile) == (250, -40, -350, (52, 53))
    assert state.partner is None and state.partner_tile is None
    assert state.trigger_at(state.tamer_tile) == 0x72
    assert state.trigger_at((100, 0)) is None


def test_grid_differences():
    assert grid_differences(b"\x00\x80\x00", b"\x00\x00\x00") == [(1, 0, 0, 128)]


def test_reads_on_map_npcs_only():
    table = struct.pack("<10I", 0x8015576C, 0x801557A8, 0x80110000, 0, 0x80111000, 0x12345678, 0, 0, 0, 0)
    read = ram_with(table=(ENTITY_TABLE, table),
                    npc=(0x80110000, struct.pack("<iI", 43, 0x80120000)), on_map=(0x80110000 + 52, b"\x01"),
                    npc_location=(0x80120000 + 120, struct.pack("<3i", -162, 0, -2352)),
                    off_map=(0x80111000, struct.pack("<iI", 44, 0x80120000)))  # isOnMap stays 0
    npcs = read_live_state(read).npcs
    assert [(npc.slot, npc.character_id, npc.tile) for npc in npcs] == [(2, 43, (48, 73))]


def test_reads_npc_behaviour():
    from dw1_workbench.live import MAP_DIGIMON_TABLE
    table = struct.pack("<10I", 0, 0, 0x80110000, 0, 0, 0, 0, 0, 0, 0)
    npc = bytearray(104)
    npc[0:8] = struct.pack("<iI", 83, 0x80120000)
    npc[52] = 1
    npc[72:74] = npc[76:78] = struct.pack("<h", 300)
    npc[101], npc[102] = 7, 1
    entry = bytearray(196)
    entry[168:174] = struct.pack("<3h", 594, 0, 2347)
    entry[180:182] = struct.pack("<h", 1000)
    entry[188], entry[192] = 12, 2
    read = ram_with(table=(ENTITY_TABLE, table), npc=(0x80110000, bytes(npc)), entry=(MAP_DIGIMON_TABLE, bytes(entry)),
                    location=(0x80120000 + 120, struct.pack("<3i", 594, 0, 2347)))
    (npc_state,) = read_live_state(read).npcs
    assert (npc_state.follow_mode, npc_state.tracking_range, npc_state.post, npc_state.reacting, npc_state.autotalk,
            npc_state.script_id, npc_state.hp, npc_state.max_hp) == (12, 1000, (594, 0, 2347), True, True, 7, 300, 300)
