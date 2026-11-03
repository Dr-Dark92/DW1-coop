from __future__ import annotations

import struct

import pytest

from dw1_workbench import progression as P
from dw1_workbench.live import ram_offset
from dw1_workbench.quicksave import QuickSaveStore


def test_level_equation_and_battle_exp():
    assert [P.exp_to_next(level) for level in (0, 19, 20, 98)] == [10, 390, 420, 5841]
    assert P.total_exp(1) == 10 and P.total_exp(20) == 3430 and P.total_exp(99) == 211200
    assert all(P.total_exp(level + 1) - P.total_exp(level) == P.exp_to_next(level) for level in range(99))
    assert P.level_for(3429) == 19 and P.level_for(3430) == 20 and P.level_for(10 ** 9) == 99
    assert P.battle_exp([3]) == 10 and P.battle_exp([4]) == 30 and P.battle_exp([5]) == 90
    assert P.battle_exp([3, 3]) == 30 and P.battle_exp([3, 4, 5]) == 260 and P.battle_exp([4] * 4) == 360
    assert P.battle_exp([2]) == 0 and P.battle_exp([]) == 0
    assert P.stats_for(20) == {"hp": 2000, "mp": 2000, "offense": 200, "defense": 200, "speed": 200, "brains": 200}
    assert P.stats_for(99)["hp"] == 9900 and P.stats_for(99)["brains"] == 990
    progress = P.Progress()
    assert progress.level == 20 and progress.summary() == "Lv 20 · EXP 0/420"
    assert P.Progress.from_json(progress.to_json()) == progress
    with pytest.raises(ValueError):
        P.Progress.from_json(b'{"format": 1, "exp": -1}')


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


COMBAT = 0x8013D640


@pytest.fixture
def ram():
    ram = Ram()
    ram.put(P.PARTNER_ENTITY, "<i", 3)  # Agumon
    ram.put(P.PARTNER_STATS, "<4h", 81, 61, 71, 71)
    ram.put(P.PARTNER_STATS + 16, "<4h", 800, 601, 636, 420)
    ram.put(P.PARTNER_LIVES, "<B", 3)
    ram.put(P.LIFETIME, "<hh", 358, 4)
    ram.put(P.COMBAT_DATA_PTR, "<I", COMBAT)
    for slot, kind, level in ((5, 83, 3), (6, 20, 4), (7, 50, 5)):
        entity = 0x80100000 + slot * 0x100
        ram.put(P.ENTITY_TABLE + slot * 4, "<I", entity)
        ram.put(entity, "<i", kind)
        ram.put(P.DIGIMON_DATA + kind * 52 + 29, "<B", level)
    return ram


def battle(ram, rules, slots):
    ram.put(COMBAT + P.COMBAT_ENTITY_IDS, "4B", 1, *slots, *([0] * (3 - len(slots))))
    ram.put(P.ENEMY_COUNT, "<h", len(slots))
    ram.put(P.GAME_STATE, "<h", 3)
    assert rules.tick() == []
    ram.put(P.GAME_STATE, "<h", 0)
    ram.put(P.BATTLES_WON, "<h", ram.get(P.BATTLES_WON, "<h") + 1)
    return rules.tick()


def test_rules_raise_to_level_floor_hold_life_and_award_wins(ram):
    rules = P.GameRules(ram.read, ram.write)
    assert rules.tick() == []
    assert rules._stats() == P.stats_for(20)
    assert ram.get(P.PARTNER_STATS + 20, "<h") == 636 + 1200  # current HP raised with its maximum
    assert ram.get(P.LIFETIME, "<h") == P.FULL_LIFETIME
    ram.put(P.LIFETIME, "<h", 0); ram.put(P.PARTNER_LIVES, "<B", 1); ram.put(P.AGE, "<h", 9)
    rules.tick()
    assert (ram.get(P.LIFETIME, "<h"), ram.get(P.PARTNER_LIVES, "<B"), ram.get(P.AGE, "<h")) == (360, 3, 4)

    messages = battle(ram, rules, [5, 6, 7])  # Rookie + Champion + Ultimate: 130 × 2
    assert "+260 EXP (×2)" in messages[0] and rules.progress.exp == P.total_exp(20) + 260
    messages = battle(ram, rules, [6, 6])  # 60 × 1.5 = 90 → 350 + ... crosses into 21 after 420
    assert rules.progress.exp == P.total_exp(20) + 350 and len(messages) == 1
    messages = battle(ram, rules, [7])
    assert rules.progress.level == 21 and messages[1].startswith("Level up! Lv 21")
    assert rules._stats() == {name: value + gain for (name, value), gain in
                              zip(P.stats_for(20).items(), P.STAT_GAINS.values())}

    # A won-counter change without a battle seen (a loaded state) awards nothing.
    ram.put(P.BATTLES_WON, "<h", 50)
    assert rules.tick() == [] and rules.progress.level == 21


def test_rules_wait_for_a_partner_and_keep_trained_stats(ram):
    ram.put(P.PARTNER_STATS + 16, "<h", 0)  # title screen: no partner yet
    rules = P.GameRules(ram.read, ram.write)
    assert rules.tick() == [] and ram.get(P.LIFETIME, "<h") == 358
    ram.put(P.PARTNER_STATS, "<h", 450)  # Offense trained above the level's value stays
    ram.put(P.PARTNER_STATS + 16, "<h", 800)
    rules.tick()
    assert rules._stats()["offense"] == 450 and rules._stats()["defense"] == 200


def test_quick_save_keeps_progress(tmp_path):
    store = QuickSaveStore(tmp_path)
    progress = P.Progress(P.total_exp(30) + 7)
    store.save(2, b"state", None, "core", "1", progress=progress.to_json())
    assert P.Progress.from_json(store.progress(2)).level == 30
    store.save(2, b"state", None, "core", "1")
    assert store.progress(2) is None
    store.save(3, b"state", None, "core", "1", progress=progress.to_json())
    store.delete(3)
    assert store.progress(3) is None


@pytest.mark.parametrize("outcome", ["flee", "loss", "win"])
def test_completed_encounter_cannot_award_stale_enemies(ram, outcome):
    rules = P.GameRules(ram.read, ram.write)
    rules.tick()
    ram.put(COMBAT + P.COMBAT_ENTITY_IDS, "4B", 1, 7, 0, 0)
    ram.put(P.ENEMY_COUNT, "<h", 1)
    ram.put(P.GAME_STATE, "<h", 3)
    rules.tick()
    ram.put(P.GAME_STATE, "<h", 0)
    if outcome == "win":
        ram.put(P.BATTLES_WON, "<h", 1)
    elif outcome == "loss":
        ram.put(P.PARTNER_LIVES, "<B", 2)
    messages = rules.tick()
    expected = P.total_exp(20) + (90 if outcome == "win" else 0)
    assert rules.progress.exp == expected
    assert bool(messages) == (outcome == "win")
    assert ram.get(P.PARTNER_LIVES, "<B") == 3

    # The next encounter cannot be read; its win must not reuse the Ultimate.
    ram.put(P.GAME_STATE, "<h", 3)
    ram.put(P.COMBAT_DATA_PTR, "<I", 0)
    rules.tick()
    ram.put(P.GAME_STATE, "<h", 0)
    ram.put(P.BATTLES_WON, "<h", ram.get(P.BATTLES_WON, "<h") + 1)
    assert rules.tick() == []
    assert rules.progress.exp == expected
    assert rules.tick() == []
    assert rules.progress.exp == expected


def test_results_preview_before_reward_and_clear_on_exit(ram):
    rules = P.GameRules(ram.read, ram.write, P.Progress(P.total_exp(21) - 5))
    rules.tick()
    ram.put(COMBAT + P.COMBAT_ENTITY_IDS, "4B", 1, 5, 0, 0)
    ram.put(P.ENEMY_COUNT, "<h", 1)
    ram.put(P.GAME_STATE, "<h", 2)
    ram.put(P.UI_BOX_DATA + 18, "<h", 1)
    ram.put(P.UI_BOX_DATA + 32, "<I", P.POST_BATTLE_RENDER)
    before = rules.progress.exp
    for _ in range(3):
        assert rules.tick() == []
        assert rules.progress.exp == before
        assert rules.battle_result.gained == 10
        assert rules.battle_result.exp == P.total_exp(21) + 5
        assert rules.battle_result.lines[2] == "Level 21"
        assert rules.battle_result.lines[-1] == "Next level 446 EXP"
    ram.put(P.UI_BOX_DATA + 18, "<h", 0)
    rules.tick()
    assert rules.battle_result is None
    ram.put(P.GAME_STATE, "<h", 0)
    ram.put(P.BATTLES_WON, "<h", 1)
    rules.tick()
    assert rules.progress.exp == before + 10
    assert rules.battle_result is None
    rules.reset()
    assert rules.battle_result is None


def test_results_ignore_unrelated_boxes_and_max_level(ram):
    rules = P.GameRules(ram.read, ram.write)
    ram.put(P.GAME_STATE, "<h", 2)
    ram.put(P.UI_BOX_DATA + 18, "<h", 1)
    ram.put(P.UI_BOX_DATA + 32, "<I", 0x800BB610)
    rules.tick()
    assert rules.battle_result is None
    assert P.BattleResult(90, P.total_exp(99)).lines[-2:] == ("Maximum level", "EXP complete")


def test_confirmed_victory_survives_area_reload_before_win_counter(ram):
    rules = P.GameRules(ram.read, ram.write, P.Progress(P.total_exp(21) - 5))
    rules.tick()
    ram.put(COMBAT + P.COMBAT_ENTITY_IDS, "4B", 1, 5, 0, 0)
    ram.put(P.ENEMY_COUNT, "<h", 1)
    ram.put(P.GAME_STATE, "<h", 3)
    rules.tick()
    ram.put(P.UI_BOX_DATA + 18, "<h", 1)
    ram.put(P.UI_BOX_DATA + 32, "<I", P.POST_BATTLE_RENDER)
    rules.tick()
    assert rules.battle_result.gained == 10
    # The native return reloads map assets before incrementing the win counter.
    ram.put(P.GAME_STATE, "<h", 0)
    ram.put(P.PARTNER_STATS + 16, "<h", 0)
    rules.tick()
    ram.put(0x80134DA8, "<B", 112)
    ram.put(P.PARTNER_STATS + 16, "<h", 2000)
    assert any("+10 EXP" in text for text in rules.tick())
    expected = P.total_exp(21) + 5
    assert rules.progress.exp == expected
    assert rules.progress.level == 21
    assert rules._stats()["offense"] == 210
    ram.put(P.BATTLES_WON, "<h", 1)
    for _ in range(5): assert rules.tick() == []
    assert rules.progress.exp == expected
    restored = P.Progress.from_json(rules.progress.to_json())
    assert restored.exp == expected
    ram.put(0x80134DA8, "<B", 109)
    rules.tick()
    assert rules.progress.exp == expected


def test_regen_one_percent_per_second_out_of_battle_only(ram):
    rules = P.GameRules(ram.read, ram.write, P.Progress(P.total_exp(20)))
    rules.tick()  # floor: max HP/MP 2000, current 636+1200 / 420+1399
    ram.put(P.PARTNER_STATS + P.CURRENT_HP, "<hh", 1000, 1990)
    for _ in range(P.REGEN_FRAMES - 2):  # the floor tick above was the first frame of this second
        rules.tick()
    assert ram.get(P.PARTNER_STATS + P.CURRENT_HP, "<h") == 1000
    rules.tick()
    assert ram.get(P.PARTNER_STATS + P.CURRENT_HP, "<hh") == 1020
    assert ram.get(P.PARTNER_STATS + P.CURRENT_MP, "<h") == 2000  # capped at max
    ram.put(P.GAME_STATE, "<h", 1)
    for _ in range(P.REGEN_FRAMES * 3):
        rules.tick()
    assert ram.get(P.PARTNER_STATS + P.CURRENT_HP, "<h") == 1020
    ram.put(P.GAME_STATE, "<h", 0)
    ram.put(P.PARTNER_STATS + P.CURRENT_HP, "<h", 0)
    for _ in range(P.REGEN_FRAMES):
        rules.tick()
    assert ram.get(P.PARTNER_STATS + P.CURRENT_HP, "<h") == 0  # never revives


def test_finished_training_session_gives_exp(ram):
    rules = P.GameRules(ram.read, ram.write, P.Progress(P.total_exp(20) + 418))
    ram.put(P.TRN_CURRENT_HOUR, "<h", 23)
    ram.put(P.HOUR, "<h", 23)
    for state in (1, 3, 4, 5):
        ram.put(P.TRAINING_STATE, "<B", state)
        assert rules.tick() == []
    ram.put(P.HOUR, "<h", 0)  # TRN_advanceTrainingTime: one hour later
    ram.put(P.TRAINING_STATE, "<B", 0)
    messages = rules.tick()
    assert messages[0].startswith("Training: +5 EXP") and messages[1].startswith("Level up! Lv 21")
    assert rules.progress.exp == P.total_exp(21) + 3
    assert rules.tick() == []
    # A session whose clock did not advance (a state loaded mid-session, then left) gives nothing.
    ram.put(P.TRAINING_STATE, "<B", 2)
    rules.tick()
    ram.put(P.TRAINING_STATE, "<B", 0)
    ram.put(P.TRN_CURRENT_HOUR, "<h", 0)
    assert rules.tick() == []
