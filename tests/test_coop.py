from __future__ import annotations

import struct

import pytest

from dw1_workbench import coop as C
from dw1_workbench.live import ram_offset
from dw1_workbench.progression import stats_for, total_exp

SPECIES = 5
COMBAT = 0x8013D640


class Ram:
    def __init__(self):
        self.data = bytearray(0x200000)

    def read(self, offset, size):
        return bytes(self.data[offset:offset + size])

    def write(self, offset, data):
        self.data[offset:offset + len(data)] = data

    def put(self, address, fmt, *values):
        struct.pack_into(fmt, self.data, ram_offset(address), *values)

    def get(self, address, fmt):
        return struct.unpack_from(fmt, self.data, ram_offset(address))[0]


def entity(ram, slot, kind, hp=300):
    address = 0x80100000 + slot * 0x100
    ram.put(C.ENTITY_TABLE + slot * 4, "<I", address)
    ram.put(address, "<i", kind)
    ram.put(address + C.POSITION, "<I", address + 0x1000)
    ram.put(address + 0x1000 + C.LOCATION, "<3i", slot * 100, 0, 0)
    ram.put(address + C.HP, "<h", hp)
    return address


@pytest.fixture
def game():
    ram = Ram()
    for index in range(8):
        ram.put(C.MAP_DIGIMON_TABLE + index * C.MAP_DIGIMON_SIZE, "<h", -1)
    for index in range(C.OBJECT_COUNT):
        ram.put(C.WORLD_OBJECTS + index * 12, "<h", -1)
    entity(ram, 0, 0)
    entity(ram, 1, 3, 2000)
    entity(ram, 5, 83)
    ram.put(C.MAP_DIGIMON_TABLE + 3 * C.MAP_DIGIMON_SIZE, "<h", 83)
    ram.put(C.IS_SCRIPT_PAUSED, "<i", 1)
    ram.put(C.COMBAT_DATA_PTR, "<I", COMBAT)
    ram.put(C.MONEY, "<i", 100)
    ram.put(C.DIGIMON_DATA + SPECIES * C.DIGIMON_SIZE, "20s", b"Greymon")
    table = [0, 4, 7, 9] + [0xFF] * 12
    ram.write(ram_offset(C.DIGIMON_DATA + SPECIES * C.DIGIMON_SIZE + C.DIGIMON_MOVES), bytes(table))
    ram.put(C.MOVE_NAMES, "<I", 0x80110000)
    ram.put(0x80110000, "6s", b"Blast\0")
    return ram


def controller(ram, tmp_path=None, level=20):
    return C.CoopBattle(ram.read, ram.write, C.PlayerTwo(SPECIES, total_exp(level)), tmp_path)


def contact(ram, slot=5):
    ram.put(C.IS_SCRIPT_PAUSED, "<i", 0)
    ram.put(C.TALKED_TO_ENTITY, "<b", slot)
    ram.put(C.NPC_ENTITIES + (slot - 2) * C.NPC_SIZE + C.AUTOTALK, "<B", 1)


def finish_call(ram, result=1):
    ram.put(C.CALL_DATA + 16, "<I", result)


def spawn(ram, coop):
    """Run the load, the placement and the join as the game would."""
    contact(ram)
    coop.tick()
    assert ram.get(C.CALL_DATA, "<I") == C.LOAD_NPC_MODEL and ram.get(C.CALL_DATA + 4, "<I") == SPECIES
    coop.tick()  # still loading
    finish_call(ram, 0)
    coop.tick()
    assert ram.get(C.CALL_DATA, "<I") == C.SCRIPT_SET_DIGIMON
    assert ram.get(C.CALL_DATA + 8, "<I") == 9 - 2  # the highest free slot, so it fights last
    address = entity(ram, 9, SPECIES)
    finish_call(ram, 1)
    coop.tick()
    assert coop._slot == 9
    return address


def battle(ram, ids, count):
    ram.put(C.GAME_STATE, "<h", 3)
    ram.put(C.ENEMY_COUNT, "<h", count)
    ram.write(ram_offset(COMBAT + C.ENTITY_IDS), bytes(ids))


def test_routine_is_r3000_safe_and_removes_itself():
    words = struct.unpack(f"<{len(C.routine()) // 4}I", C.routine())
    jal = 0x0C000000 | (C.REMOVE_OBJECT >> 2) & 0x3FFFFFF
    assert jal in words and words[words.index(jal) + 1] == 0  # branch delay slot
    jalr = words.index(0x0320F809)
    assert words[jalr + 1] == 0
    assert words[jalr - 1] == 0  # load delay before jumping to the function just loaded
    assert words[-2:] == (0x03E00008, 0)


def test_joins_on_contact_with_level_stats_and_hides_when_the_script_ends(game):
    coop = controller(game)
    assert coop.tick() == [] and coop._step is None  # nothing until a battle-on-contact script runs
    ally = spawn(game, coop)
    stats = stats_for(20)
    assert game.get(ally + C.MAX_HP, "<h") == stats["hp"] and game.get(ally + C.OFFENSE, "<h") == stats["offense"]
    assert list(game.read(ram_offset(ally + C.MOVES), 4)) == [0x2E, 0x2F, 0x30, 0x31]
    coop.tick()
    assert game.read(ram_offset(ally + C.IS_ON_MAP), 2) == bytes([1, 1]) and coop._state == "joining"
    assert game.read(ram_offset(C.FLEE_CHANCES), 12) == bytes([100]) * 12
    game.put(C.IS_SCRIPT_PAUSED, "<i", 1)  # no fight after all
    coop.tick()
    assert game.read(ram_offset(ally + C.IS_ON_MAP), 2) == bytes(2) and coop._state == "field"
    assert game.read(ram_offset(C.FLEE_CHANCES), 12) == bytes(12)
    # A Digimon you merely talk to brings no one.
    game.put(C.IS_SCRIPT_PAUSED, "<i", 0)
    game.put(C.NPC_ENTITIES + 3 * C.NPC_SIZE + C.AUTOTALK, "<B", 0)
    coop.tick()
    assert coop._state == "field"


def test_no_room_is_reported_once_and_changes_nothing(game):
    for index in range(8):
        game.put(C.MAP_DIGIMON_TABLE + index * C.MAP_DIGIMON_SIZE, "<h", 83)
    coop = controller(game)
    contact(game)
    assert any("can't join" in text for text in coop.tick())
    assert coop.tick() == [] and coop._step is None
    assert game.get(C.WORLD_OBJECTS, "<h") == -1


def in_battle(game, coop, ally_slots=(1, 5, 9, 0), count=2):
    spawn(game, coop)
    coop.tick()
    battle(game, ally_slots, count)
    coop.tick()
    assert coop._state == "battle"


def test_commands_follow_the_partners_brains_rule():
    assert C.available_commands(50)[1:] == (C.RUN, C.YOUR_CALL)
    assert C.available_commands(200)[1:] == (C.RUN, C.MODERATE, C.ATTACK, C.YOUR_CALL)
    assert C.available_commands(450) == (C.FINISHER, C.RUN, C.CHANGE, C.DEFENSIVE, C.DISTANCE, C.MODERATE, C.ATTACK, C.YOUR_CALL)
    assert all(command < 8 or command == C.FINISHER for command in C.available_commands(999))  # no technique commands


def set_moves(game):
    for tech, (power, mp, reach) in {0: (50, 10, 1), 4: (150, 40, 2), 7: (90, 5, 3), 9: (120, 20, 1)}.items():
        game.put(C.MOVE_DATA + tech * C.MOVE_SIZE + C.MOVE_POWER, "<h", power)
        game.put(C.MOVE_DATA + tech * C.MOVE_SIZE + C.MOVE_MP, "<B", mp)
        game.put(C.MOVE_DATA + tech * C.MOVE_SIZE + C.MOVE_RANGE, "<B", reach)


def test_player_two_uses_player_ones_controls_and_the_ai_picks_skills(game):
    set_moves(game)
    coop = controller(game, level=45)  # Brains 450: the full menu
    entity(game, 6, 83)
    in_battle(game, coop, (1, 5, 6, 9), 3)
    ally = game.get(C.ENTITY_TABLE + 9 * 4, "<I")
    ally_fighter = COMBAT + 3 * C.FIGHTER_SIZE
    coop.tick()
    panel = coop.panel
    assert panel.commands[panel.hovered] == C.YOUR_CALL and panel.current == C.YOUR_CALL
    assert list(game.read(ram_offset(ally + C.MOVE_WEIGHTS), 4))[:3] == [25, 25, 25]  # the AI's own choice
    table = game.read(ram_offset(C.DIGIMON_DATA + SPECIES * C.DIGIMON_SIZE + C.DIGIMON_MOVES), 16)
    game.write(ram_offset(C.DIGIMON_DATA + SPECIES * C.DIGIMON_SIZE + C.DIGIMON_MOVES + 3), bytes([0x40]))
    coop.tick()
    assert game.read(ram_offset(ally + C.MOVE_WEIGHTS), 4)[3] == 0  # a finisher is never a normal pick
    game.write(ram_offset(C.DIGIMON_DATA + SPECIES * C.DIGIMON_SIZE + C.DIGIMON_MOVES), table)
    assert game.get(ally_fighter + C.TARGET, "<B") == 1 and game.get(ally_fighter + C.FLAGS, "<H") & C.FLAG_KEEP_TARGET
    # Right moves toward Attack, as Right does for player 1; confirm gives the order.
    coop.press(C.RIGHT); coop.press(C.CONFIRM)
    coop.tick()
    assert coop.panel.current == C.ATTACK
    assert list(game.read(ram_offset(ally + C.MOVE_WEIGHTS), 4))[:3] == [1, 100, 1]  # strongest
    coop.press(C.RIGHT); coop.press(C.CONFIRM)
    coop.tick()
    assert coop.panel.current == C.MODERATE
    assert list(game.read(ram_offset(ally + C.MOVE_WEIGHTS), 4))[:3] == [1, 1, 100]  # cheapest
    assert game.get(ally + C.CHARGE_MODE, "<B") == 2
    coop.press(C.RIGHT); coop.press(C.RIGHT); coop.press(C.CONFIRM)  # Distance, then Defensive
    coop.tick()
    assert coop.panel.current == C.DEFENSIVE
    assert game.get(ally_fighter + C.FLAGS, "<H") & C.FLAG_CHARGING
    coop.press(C.RIGHT); coop.press(C.CONFIRM)  # Change: next enemy, the command stays
    coop.tick()
    assert coop.panel.current == C.DEFENSIVE and game.get(ally_fighter + C.TARGET, "<B") == 2
    # The partner picking the ally is turned back to an enemy; enemies aim at the partner or the ally, never themselves.
    game.put(COMBAT + C.TARGET, "<B", 3)
    coop.tick()
    assert game.get(COMBAT + C.TARGET, "<B") == 1
    for foe in (1, 2):
        assert game.get(COMBAT + foe * C.FIGHTER_SIZE + C.TARGET, "<B") in (0, 3)
    assert coop.sound_events


def test_finisher_waits_for_player_twos_button(game):
    coop = controller(game)
    in_battle(game, coop)
    fighter = COMBAT + 2 * C.FIGHTER_SIZE
    game.put(fighter + C.FINISHER_GOAL, "<h", 100)
    game.put(fighter + C.FINISHER_PROGRESS, "<h", 100)
    coop.tick()
    assert game.get(fighter + C.FINISHER_PROGRESS, "<h") == 99 and coop.panel.finisher_ready
    coop.press(C.SQUARE)
    coop.tick()
    assert game.get(fighter + C.FINISHER_PROGRESS, "<h") == 100  # released: the AI fires it at a full bar
    assert coop._finisher_armed
    game.put(fighter + C.FINISHER_PROGRESS, "<h", 0)  # the AI took it
    coop.tick()
    assert not coop._finisher_armed and game.get(fighter + C.FINISHER_PROGRESS, "<h") == 0


def test_player_two_running_leaves_the_partner_fighting(game):
    coop = controller(game)
    in_battle(game, coop)
    coop.press(C.LEFT); coop.press(C.CONFIRM)  # from Your Call, Left wraps to the first entry: Run
    coop.tick()
    coop.tick()
    assert coop._state == "left" and game.get(C.ENEMY_COUNT, "<h") == 1 and game.get(C.GAME_STATE, "<h") == 3
    game.put(C.MONEY, "<i", 150)
    game.put(C.GAME_STATE, "<h", 0)
    messages = coop.tick()
    assert any("+50 Bits" in text for text in messages)  # still gains what the partner wins


@pytest.mark.parametrize("how", ["won", "lost", "fled"])
def test_the_ally_leaves_so_the_game_can_end_the_battle(game, how):
    coop = controller(game)
    in_battle(game, coop)
    if how == "won":
        game.put(game.get(C.ENTITY_TABLE + 5 * 4, "<I") + C.HP, "<h", 0)
    elif how == "lost":
        game.put(game.get(C.ENTITY_TABLE + 4, "<I") + C.HP, "<h", 0)
    else:
        game.put(COMBAT + 1440 + 176, "<B", 1)
    coop.tick()
    assert game.get(C.ENEMY_COUNT, "<h") == 1 and coop.panel is None
    ally = game.get(C.ENTITY_TABLE + 9 * 4, "<I")
    assert game.read(ram_offset(ally + C.IS_ON_MAP), 2) == bytes(2)


def test_gains_match_the_main_player_and_persist(game, tmp_path):
    path = tmp_path / "p2.json"
    coop = controller(game, path, level=20)
    in_battle(game, coop)
    game.put(C.MONEY, "<i", 180)  # the game paid out
    game.put(C.GAME_STATE, "<h", 0)
    messages = coop.tick()
    assert "Player 2: +80 Bits" in messages[0] and coop.player.bits == 80 and coop.excluded_slot is None
    text = coop.gain_exp(500)
    assert coop.player.level == 21 and any("level up" in line for line in text)
    ally = game.get(C.ENTITY_TABLE + 9 * 4, "<I")
    assert game.get(ally + C.MAX_HP, "<h") == stats_for(21)["hp"]
    again = C.PlayerTwo.load(path, SPECIES, 5)
    assert again.exp == coop.player.exp and again.bits == 80
    assert C.PlayerTwo.load(path, 9, 5).level == 5  # another species starts its own record


def test_the_script_finishing_a_fight_does_not_call_the_ally_back(game):
    coop = controller(game)
    in_battle(game, coop)
    game.put(C.GAME_STATE, "<h", 0)
    coop.tick()  # back on the map with the loss or win script still running
    coop.tick()
    assert coop._state == "field"
    ally = game.get(C.ENTITY_TABLE + 9 * 4, "<I")
    assert game.read(ram_offset(ally + C.IS_ON_MAP), 2) == bytes(2)
    game.put(C.IS_SCRIPT_PAUSED, "<i", 1)
    coop.tick()
    contact(game)  # a new fight later does bring it
    coop.tick()
    assert coop._state == "joining"


def test_a_loaded_state_finds_the_ally_again(game):
    coop = controller(game)
    spawn(game, coop)
    game.put(C.MAP_DIGIMON_TABLE + 7 * C.MAP_DIGIMON_SIZE + 2, "<H", C.ALLY_MARK)
    other = controller(game)
    assert other._slot == 9


def test_a_fight_the_ally_is_not_in_still_pays_player_two(game):
    coop = controller(game)
    battle(game, (1, 5, 0, 0), 1)  # e.g. a save made after the ally had left
    coop.tick()
    assert coop._state == "solo"
    game.put(C.MONEY, "<i", 170)
    game.put(C.GAME_STATE, "<h", 0)
    assert any("+70 Bits" in text for text in coop.tick())
