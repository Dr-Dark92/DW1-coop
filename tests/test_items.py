from __future__ import annotations

import struct

import pytest

from dw1_workbench.errors import DiscFormatError
from dw1_workbench.inspection import SpawnLog
from dw1_workbench.items import (CHEST_ARRAY, DROPPED_ITEMS, ITEM_PARA_OFFSET, GroundItem, parse_item_names,
                                 read_chests, read_ground_items)
from dw1_workbench.live import ram_offset


def executable_with(names):
    data = bytearray(b"PS-X EXE" + bytes(ITEM_PARA_OFFSET + 128 * 32))
    for index, name in enumerate(names):
        data[ITEM_PARA_OFFSET + index * 32:ITEM_PARA_OFFSET + index * 32 + len(name)] = name
    return bytes(data)


def test_item_names():
    names = parse_item_names(executable_with([b"sm.recovery", b"med.recovery"] + [b""] * 42 + [b"Digimushrm"]))
    assert len(names) == 128 and names[0] == "sm.recovery" and names[44] == "Digimushrm" and names[2] == ""
    with pytest.raises(DiscFormatError):
        parse_item_names(b"PS-X EXE")
    with pytest.raises(DiscFormatError, match="not printable"):
        parse_item_names(executable_with([b"bad\x01"]))


def test_ground_items_chests_and_reach():
    ram = bytearray(0x200000)
    for slot in range(11):
        struct.pack_into("<i", ram, ram_offset(DROPPED_ITEMS) + slot * 16 + 8, 0xFF)
    struct.pack_into("<ihh", ram, ram_offset(DROPPED_ITEMS) + 3 * 16 + 8, 44, 61, 54)
    for slot in range(8):
        ram[ram_offset(CHEST_ARRAY) + slot * 56 + 50] = 0xFF
    struct.pack_into("<HBBBB", ram, ram_offset(CHEST_ARRAY) + 56 + 48, 300, 9, 1, 40, 41)
    read = lambda offset, size: bytes(ram[offset:offset + size])
    (item,) = read_ground_items(read)
    assert item == GroundItem(3, 44, (61, 54))
    assert item.in_reach((60, 55)) and not item.in_reach((59, 54)) and not item.in_reach(None)
    (chest,) = read_chests(read)
    assert (chest.slot, chest.item_id, chest.tile, chest.taken, chest.trigger) == (1, 9, (40, 41), True, 300)


def test_spawn_log_counts_visits(tmp_path):
    log = SpawnLog(tmp_path / "spawns.json")
    mushroom = [(44, "Digimushrm", (61, 54))]
    assert log.record(110, "Native Forest · MAYO01_2", mushroom) == [("Digimushrm", (61, 54))]
    assert log.record(110, "Native Forest · MAYO01_2", mushroom) == []  # same visit
    assert log.record(110, "Native Forest · MAYO01_2", []) == []  # picked up
    log.record(112, "Green Gym · TRAI00", [])
    assert log.record(110, "Native Forest · MAYO01_2", mushroom) == []  # back again: a known spawn
    (spawn,) = SpawnLog(tmp_path / "spawns.json").spawns(110)
    assert (spawn.item_id, spawn.tile, spawn.times_seen) == (44, (61, 54), 2)
    assert log.spawns(112) == []
