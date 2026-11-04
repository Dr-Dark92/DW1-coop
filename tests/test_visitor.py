from __future__ import annotations

import struct

from dw1_workbench import split_screen as S
from dw1_workbench import visitor as V
from dw1_workbench.coop import ENTITY_TABLE, FIRST_NPC, GAME_STATE, IS_ON_MAP, IS_SCRIPT_PAUSED, MAP_DIGIMON_SIZE, MAP_DIGIMON_TABLE, POSITION
from dw1_workbench.live import CURRENT_SCREEN, ram_offset


class Ram:
    def __init__(self):
        self.data = bytearray(0x200000)
        self.put32(IS_SCRIPT_PAUSED, 1)
        self.put(CURRENT_SCREEN, bytes([7]))
        self.partner = 0x80150000
        self.put32(ENTITY_TABLE + 4, self.partner)
        self.put32(self.partner, 3)
        self.put32(self.partner + POSITION, 0x80151000)
        self.put(V.LOADED_DIGIMON_MODELS, struct.pack("<8i", *[-1] * 8))
        for slot in range(8):
            self.put(MAP_DIGIMON_TABLE + slot * MAP_DIGIMON_SIZE, struct.pack("<h", -1))
        for index in range(128):
            self.put(V.WORLD_OBJECTS + index * 12, struct.pack("<h", -1))

    def read(self, offset, size):
        return bytes(self.data[offset:offset + size])

    def write(self, offset, data):
        self.data[offset:offset + len(data)] = data

    def put(self, address, data):
        self.write(ram_offset(address), data)

    def put32(self, address, value):
        self.put(address, struct.pack("<I", value))

    def visitor_entity(self, slot, species):
        entity = 0x80160000 + slot * 0x100
        self.put32(ENTITY_TABLE + slot * 4, entity)
        self.put32(entity, species)
        self.put32(entity + POSITION, 0x80170000 + slot * 0x100)
        self.put(MAP_DIGIMON_TABLE + (slot - FIRST_NPC) * MAP_DIGIMON_SIZE, struct.pack("<hH", species, V.VISITOR_MARK))
        return entity


def record(map_id=7, species=3, visible=True):
    return V.WorldRecord(map_id, 100, 0, -200, 512, species, 5, visible)


def test_shown_on_the_same_map_and_moved():
    ram = Ram()
    entity = ram.visitor_entity(9, 3)
    visitor = V.Visitor(ram.read, ram.write)
    visitor.tick(record())
    assert ram.read(ram_offset(entity + IS_ON_MAP), 1) == b"\1"
    position = 0x80170000 + 9 * 0x100
    assert struct.unpack("<3i", ram.read(ram_offset(position + V.LOCATION), 12)) == (100, 0, -200)
    assert struct.unpack("<h", ram.read(ram_offset(position + V.ROTATION + 2), 2))[0] == 512


def test_hidden_on_another_map_in_battle_and_in_scripts():
    for change in ("map", "battle", "script", "gone"):
        ram = Ram()
        entity = ram.visitor_entity(9, 3)
        visitor = V.Visitor(ram.read, ram.write)
        visitor.tick(record())
        if change == "battle":
            ram.put(GAME_STATE, struct.pack("<h", 1))
        elif change == "script":
            ram.put32(IS_SCRIPT_PAUSED, 0)
        visitor.tick(None if change == "gone" else record(8 if change == "map" else 7))
        assert ram.read(ram_offset(entity + IS_ON_MAP), 1) == b"\0", change


def test_memory_is_checked_before_the_model_loads():
    ram = Ram()
    visitor = V.Visitor(ram.read, ram.write)
    ram.put32(V.DIGIMON_FILE_NAMES + 3 * 4, 0x80140000)
    ram.put(0x80140000, b"AGUM\0")
    ram.put(V.MODEL_FOLDER, b"CHDAT\\MMD0\\\0")
    ram.put(V.MODEL_SUFFIX, b".MMD\0")
    calls = lambda: struct.unpack("<I", ram.read(ram_offset(V.call_data()), 4))[0]
    finish = lambda result: ram.put32(V.call_data() + 16, result)
    finish(0)
    for _ in range(V.SETTLE_TICKS + 1):
        visitor.tick(record())
    assert calls() == V.LOOKUP_FILE_SIZE
    assert ram.read(ram_offset(V.path_buffer()), 20).split(b"\0")[0] == b"CHDAT\\MMD0\\AGUM.MMD"
    finish(72012)
    visitor.tick(record())
    assert calls() == V.MALLOC
    assert struct.unpack("<I", ram.read(ram_offset(V.call_data() + 4), 4))[0] == 0x12000 + V.HEADROOM
    finish(0)  # no room
    messages = visitor.tick(record())
    assert calls() == V.MALLOC and "not enough game memory" in messages[0]
    for _ in range(V.SETTLE_TICKS):
        assert visitor.tick(record()) == []  # says so once per map, and never loads
    assert calls() == V.MALLOC


def test_a_borrowed_row_goes_back_when_hidden():
    ram = Ram()
    row = MAP_DIGIMON_TABLE + 7 * MAP_DIGIMON_SIZE
    entity = ram.visitor_entity(9, 3)
    ram.put(row, struct.pack("<hH", 150, 0))  # the map's script places species 150 here later
    visitor = V.Visitor(ram.read, ram.write)
    visitor.tick(record())
    assert struct.unpack("<hH", ram.read(ram_offset(row), 4)) == (3, V.VISITOR_MARK)
    assert ram.read(ram_offset(row + V.STOP_ANIM), 1) == b"\1"
    assert struct.unpack("<h", ram.read(ram_offset(entity + V.CURRENT_HP), 2))[0] == 0
    visitor.hide()
    assert struct.unpack("<hH", ram.read(ram_offset(row), 4)) == (150, 0)
    visitor.tick(record())  # found again, and borrows it again
    assert struct.unpack("<hH", ram.read(ram_offset(row), 4)) == (3, V.VISITOR_MARK)


def test_a_map_with_every_row_listed_still_has_room():
    ram = Ram()
    for slot in range(8):
        ram.put(MAP_DIGIMON_TABLE + slot * MAP_DIGIMON_SIZE, struct.pack("<h", 150))
    ram.put32(ENTITY_TABLE + 9 * 4, 0x80160000)  # the last row's Digimon is on the map
    assert V.Visitor(ram.read, ram.write)._free_slot() == 8


def test_a_call_lost_to_a_map_change_counts_as_finished():
    ram = Ram()
    visitor = V.Visitor(ram.read, ram.write)
    ram.put32(V.call_data() + 16, V.NOT_DONE)
    assert visitor._call_done()
    ram.put(V.WORLD_OBJECTS, struct.pack("<h", V.TASK_ID))
    assert not visitor._call_done()


def test_the_tamer_is_read_and_placed_apart_from_the_partner():
    ram = Ram()
    tamer = 0x80152000
    ram.put32(ENTITY_TABLE, tamer)
    ram.put32(tamer, 0)
    ram.put32(tamer + POSITION, 0x80153000)
    assert V.read_record(ram.read, V.TAMER).species == 0
    ram.put32(ENTITY_TABLE + 4, tamer)  # type 0 isn't a partner
    assert V.read_record(ram.read, V.PARTNER) is None
    assert V.call_data(V.TAMER) != V.call_data(V.PARTNER)
    visitor = V.Visitor(ram.read, ram.write, V.TAMER)
    for _ in range(V.SETTLE_TICKS + 1):
        visitor.tick(record(species=0), busy=True)
    assert visitor._step is None  # waits for the other to finish


def test_link_carries_each_players_record():
    memory = S.shared_memory.SharedMemory(create=True, size=S.SIZE)
    try:
        one, two = S.WorldLink(memory, 0), S.WorldLink(memory, 1)
        assert one.other() is None
        two.publish(record())
        assert one.other() == record()
        one.publish(record(9, 5, False))
        assert two.other() == record(9, 5, False)
        assert one.host() is None and not one.is_guest()
        two.set_hosting(True)
        assert one.host() == 1 and one.is_guest() and not two.is_guest()
        one.set_hosting(True)  # both met an enemy: player 1's game runs first
        assert two.is_guest() and not one.is_guest()
        one.set_hosting(False)
        two.set_hosting(False)
        stats = {"offense": 1, "defense": 2, "speed": 3, "brains": 4, "hp": 5, "mp": 6}
        one.publish_partner(20, stats)
        assert two.other_partner() == (20, stats) and one.other_partner() is None
        one.award_other(30, 100)
        assert two.my_awards() == (30, 100) and one.my_awards() == (0, 0)
        one.send_press(1, "left")
        one.send_press(1, "finisher")
        assert two.take_presses() == ["left", "finisher"] and two.take_presses() == []
        one.send_dex({"83": {"name": "ModokiBetamon"}})
        assert two.take_dex(0) == (1, {"83": {"name": "ModokiBetamon"}}) and two.take_dex(1) == (1, {})
        one.publish(record(9, 0), V.TAMER)
        assert two.other(V.TAMER) == record(9, 0) and two.other() == record(9, 5, False)
        one.close()
        one.publish(record())
        assert one.other() is None
    finally:
        memory.close()
        memory.unlink()


def test_an_enemy_beaten_in_one_game_is_gone_in_both():
    ram = Ram()
    enemy = ram.visitor_entity(5, 83)
    ram.put(enemy + IS_ON_MAP, bytes([1, 1]))
    ram.put(V.NPC_ENTITIES + 3 * V.NPC_SIZE + V.AUTOTALK, bytes([1]))
    gone = (V.NpcState(),) * 3 + (V.NpcState(83, False),) + (V.NpcState(),) * 4
    V.match_world(ram.read, ram.write, V.WorldState(8, 1, 2, 3, 4, 5, gone), True)  # another map: left alone
    assert ram.read(ram_offset(enemy + IS_ON_MAP), 1) == b"\1"
    V.match_world(ram.read, ram.write, V.WorldState(7, 1, 2, 3, 4, 5, gone), True)
    assert ram.read(ram_offset(enemy + IS_ON_MAP), 1) == b"\0"


def test_a_script_digimon_stays_and_the_follower_takes_place_and_clock():
    ram = Ram()
    npc = ram.visitor_entity(5, 83)
    ram.put(npc + IS_ON_MAP, bytes([1, 1]))
    gone = (V.NpcState(),) * 3 + (V.NpcState(83, False),) + (V.NpcState(),) * 4
    V.match_world(ram.read, ram.write, V.WorldState(7, 1, 2, 3, 4, 5, gone), True)
    assert ram.read(ram_offset(npc + IS_ON_MAP), 1) == b"\1"  # autotalk 0: a script's, not an enemy
    there = (V.NpcState(),) * 3 + (V.NpcState(83, True, 11, 22, 33, 44),) + (V.NpcState(),) * 4
    V.match_world(ram.read, ram.write, V.WorldState(7, 9, 30, 1, 2, 11400, there), True)
    assert struct.unpack("<h", ram.read(ram_offset(V.HOUR), 2))[0] == 0  # the leader keeps its own
    V.match_world(ram.read, ram.write, V.WorldState(7, 9, 30, 1, 2, 11400, there), False)
    assert struct.unpack("<2h", ram.read(ram_offset(V.HOUR), 4)) == (9, 30)
    position = 0x80170000 + 5 * 0x100
    assert struct.unpack("<3i", ram.read(ram_offset(position + V.LOCATION), 12)) == (11, 22, 33)


def test_world_crosses_the_link():
    memory = S.shared_memory.SharedMemory(create=True, size=S.SIZE)
    try:
        one, two = S.WorldLink(memory, 0), S.WorldLink(memory, 1)
        assert two.other_world() is None
        world = V.WorldState(7, 9, 30, 1, 2, 11400, (V.NpcState(83, True, 1, 2, 3, 4),) * 8)
        one.publish_world(world)
        assert two.other_world() == world
    finally:
        memory.close()
        memory.unlink()
