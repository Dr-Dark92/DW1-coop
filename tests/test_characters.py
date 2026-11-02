from __future__ import annotations

import struct

import pytest

from dw1_workbench.characters import (DISPLAY_ENTRY_SIZE, DISPLAY_TABLE_OFFSET, ENTRY_COUNT, ENTRY_SIZE, LEVEL_FIELD,
                                       NAME_TABLE_OFFSET, inspect_mmd, parse_character_table)
from dw1_workbench.errors import DiscFormatError


def character_executable() -> bytearray:
    data = bytearray(max(NAME_TABLE_OFFSET + ENTRY_COUNT * ENTRY_SIZE,
                         DISPLAY_TABLE_OFFSET + ENTRY_COUNT * DISPLAY_ENTRY_SIZE))
    data[:8] = b"PS-X EXE"
    for index in range(ENTRY_COUNT):
        name = f"C{index:03d}".encode("ascii")
        offset = NAME_TABLE_OFFSET + index * ENTRY_SIZE
        data[offset:offset + len(name)] = name
        display = f"Character {index}".encode("ascii")
        display_offset = DISPLAY_TABLE_OFFSET + index * DISPLAY_ENTRY_SIZE
        data[display_offset:display_offset + len(display)] = display
    return data


def test_character_table_has_stable_ids_paths_and_offsets():
    entries = parse_character_table(bytes(character_executable()))
    assert len(entries) == 180
    assert entries[73].resource_id == 73
    assert entries[73].internal_name == "C073"
    assert entries[73].executable_offset == NAME_TABLE_OFFSET + 73 * ENTRY_SIZE
    assert entries[73].model_path == "CHDAT/MMD2/C073.MMD"
    assert entries[73].texture_slot == 73
    assert entries[73].display_name == "Character 73"
    assert entries[73].display_name_offset == DISPLAY_TABLE_OFFSET + 73 * DISPLAY_ENTRY_SIZE


def test_character_table_preserves_an_empty_display_name_as_unavailable():
    data = character_executable()
    offset = DISPLAY_TABLE_OFFSET + 114 * DISPLAY_ENTRY_SIZE
    data[offset:offset + 20] = bytes(20)
    assert parse_character_table(bytes(data))[114].display_name is None


@pytest.mark.parametrize("mutation", ["truncated", "duplicate", "padding"])
def test_character_table_rejects_malformed_inputs(mutation):
    data = character_executable()
    if mutation == "truncated":
        data = data[:-1]
    elif mutation == "duplicate":
        first = data[NAME_TABLE_OFFSET:NAME_TABLE_OFFSET + ENTRY_SIZE]
        data[NAME_TABLE_OFFSET + ENTRY_SIZE:NAME_TABLE_OFFSET + 2 * ENTRY_SIZE] = first
    else:
        data[NAME_TABLE_OFFSET + 7] = 1
    with pytest.raises(DiscFormatError):
        parse_character_table(bytes(data))


def test_mmd_boundary_inspection_and_malformed_offsets():
    header = struct.pack("<IIIII", 8, 0x100, 0x41, 0, 3)
    result = inspect_mmd(header, total_size=0x200)
    assert (result.tmd_offset, result.animation_offset, result.object_count) == (8, 0x100, 3)
    with pytest.raises(DiscFormatError, match="animation offset"):
        inspect_mmd(struct.pack("<IIIII", 8, 0x300, 0x41, 0, 3), total_size=0x200)
    with pytest.raises(DiscFormatError, match="object table"):
        inspect_mmd(struct.pack("<IIIII", 8, 0x40, 0x41, 0, 3), total_size=0x200)


def test_character_stage_comes_from_the_digimonpara_level_byte():
    data = character_executable()
    for resource_id, level in ((1, 1), (2, 2), (3, 3), (5, 4), (12, 5), (70, 3)):
        data[DISPLAY_TABLE_OFFSET + resource_id * DISPLAY_ENTRY_SIZE + LEVEL_FIELD] = level
    entries = parse_character_table(bytes(data))
    assert [entries[i].stage for i in (0, 1, 2, 3, 5, 12)] == [None, "Fresh", "In-Training", "Rookie", "Champion", "Ultimate"]
    assert entries[3].in_partner_range and not entries[0].in_partner_range and not entries[70].in_partner_range


def test_character_table_rejects_an_unknown_level():
    data = character_executable()
    data[DISPLAY_TABLE_OFFSET + 9 * DISPLAY_ENTRY_SIZE + LEVEL_FIELD] = 6
    with pytest.raises(DiscFormatError, match="unknown level 6"):
        parse_character_table(bytes(data))
