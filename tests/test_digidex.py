import json
from dataclasses import replace

import pytest

from dw1_workbench import digidex as D
from dw1_workbench import progression as P
from dw1_workbench.live import ENTITY_TABLE, EntityPosition, LiveNpc, LiveState, ram_offset
from test_progression import Ram


@pytest.fixture
def encounter(tmp_path):
    ram = Ram()
    pointer = 0x80100500
    ram.put(P.PARTNER_ENTITY, "<i", 3)
    ram.put(P.PARTNER_STATS + 16, "<h", 2000)
    ram.put(D.HOUR, "<h", 10)
    ram.put(P.GAME_STATE, "<h", 3)
    ram.put(P.ENEMY_COUNT, "<h", 1)
    ram.put(P.COMBAT_DATA_PTR, "<I", 0x8013D640)
    ram.put(0x8013D640 + P.COMBAT_ENTITY_IDS, "4B", 1, 5, 0, 0)
    ram.put(ENTITY_TABLE + 5 * 4, "<I", pointer)
    ram.put(pointer, "<i", 83)
    ram.put(pointer + 52, "2B", 1, 1)
    ram.put(pointer + 56, "4h", 120, 90, 80, 70)
    ram.put(pointer + 72, "2h", 450, 300)
    meta = P.DIGIMON_DATA + 83 * 52
    ram.write(ram_offset(meta), b"Encountermon\0")
    ram.put(meta + 29, "<B", 3)
    ram.put(meta + 33, "2B", 44, 25)
    ram.write(ram_offset(meta + 35), bytes([1, 2] + [255] * 14))
    ram.put(pointer + 68, "4B", 46, 47, 255, 255)
    for move, name in [(1, b"Fireball"), (2, b"Ice Needle")]:
        address = 0x80110000 + move * 0x100
        ram.put(0x80126054 + move * 4, "<I", address)
        ram.write(ram_offset(address), name + b"\0")
    ram.write(ram_offset(D.ITEM_PARA + 44 * 32), b"Digimushrm\0")
    pos = EntityPosition(0, 0, 0)
    live = LiveState(110, pos, pos, bytes(10000), (LiveNpc(5, 83, pos),))
    dex = D.DigiDex(tmp_path / "digidex.json")
    dex.placements = {"83": [{"map": 111, "name": "Other Forest", "stats": dict(zip(D.STATS, (40,30,20,10,100,200)))}]}
    return ram, dex, live, pointer


def test_battle_unlocks_only_opponents_and_persists_information(encounter):
    ram, dex, live, pointer = encounter
    ram.put(P.GAME_STATE, "<h", 0)  # visible overworld NPC is not enough
    assert dex.observe(ram.read, live) == []
    assert dex.entries == {}
    ram.put(P.GAME_STATE, "<h", 3)
    ram.put(0x8013D640 + P.COMBAT_ENTITY_IDS + 1, "<B", 6)  # bystander slot 5
    assert dex.observe(ram.read, live) == []
    ram.put(0x8013D640 + P.COMBAT_ENTITY_IDS + 1, "<B", 5)
    ram.put(pointer + 53, "<B", 0)  # an actual combatant need not be on screen
    assert dex.observe(ram.read, live) == ["Encountermon"]
    entry = dex.entries["83"]
    assert entry["stage"] == 3
    assert entry["drop"] == {"name": "Digimushrm", "chance": 25, "item": 44}
    assert entry["profiles"][0]["stats"]["max_hp"] == 450
    assert entry["skills"] == [{"id": 1, "name": "Fireball"}, {"id": 2, "name": "Ice Needle"}]
    assert entry["profiles"][0]["skills"] == entry["skills"]
    dex.save()
    assert not dex.dirty
    saved = {key: value for key, value in dex.snapshot().items() if key != "moves"}  # moves come from RAM
    assert D.DigiDex(dex.path).snapshot() == {**saved, "moves": {}}
    assert dex.observe(ram.read, live) == []
    assert not dex.dirty  # no per-frame rewrite for the same sighting
    assert dex.entries["83"]["placements"][0]["name"] == "Other Forest"


def test_sightings_track_hours_and_variant_stats_without_claiming_schedule(encounter):
    ram, dex, live, pointer = encounter
    dex.observe(ram.read, live)
    ram.put(D.HOUR, "<h", 21)
    dex.observe(ram.read, live)
    assert dex.entries["83"]["profiles"][0]["hours"] == [10, 21]
    ram.put(pointer + 72, "<h", 600)
    dex.observe(ram.read, live)
    assert len(dex.entries["83"]["profiles"]) == 2
    pages = D.entry_pages(dex.entries["83"], {110: "Native Forest"})
    assert any("When: schedule unknown" in page for page in pages)
    assert any("Seen: Day/Night" in page for page in pages)
    assert any("Native Forest" in page for page in pages)
    assert [D.period(h) for h in [0, 5, 6, 15, 16, 19, 20, 23]] == ["Night","Night","Day","Day","Evening","Evening","Night","Night"]


def test_title_stale_pointer_human_and_invalid_clock_do_not_unlock(encounter):
    ram, dex, live, pointer = encounter
    assert dex.observe(ram.read, replace(live, tamer=None)) == []
    ram.put(ENTITY_TABLE + 5 * 4, "<I", 0x801ffff0)
    assert dex.observe(ram.read, live) == []
    ram.put(ENTITY_TABLE + 5 * 4, "<I", pointer)
    ram.put(pointer, "<i", 84)  # replaced slot has no valid Digimon metadata
    assert dex.observe(ram.read, live) == []
    ram.put(pointer, "<i", 83)
    ram.put(D.HOUR, "<h", -1)
    assert dex.observe(ram.read, live) == []
    ram.put(D.HOUR, "<h", 10)
    ram.put(P.DIGIMON_DATA + 83 * 52 + 29, "<B", 0)
    assert dex.observe(ram.read, live) == []
    assert dex.entries == {}


def test_failed_save_and_bad_record_preserve_existing_file(encounter, monkeypatch):
    ram, dex, live, _ = encounter
    dex.observe(ram.read, live)
    dex.save()
    before = dex.path.read_bytes()
    ram.put(D.HOUR, "<h", 22)
    dex.observe(ram.read, live)
    def fail(*args): raise OSError("disk full")
    monkeypatch.setattr(D, "_atomic_write", fail)
    with pytest.raises(OSError): dex.save()
    assert dex.path.read_bytes() == before and dex.dirty
    dex.path.write_bytes(b'{"format": 99, "entries": {}}')
    invalid = dex.path.read_bytes()
    with pytest.raises(ValueError): D.DigiDex(dex.path)
    assert dex.path.read_bytes() == invalid


def test_snapshot_cannot_mutate_discovery(encounter):
    ram, dex, live, _ = encounter
    dex.observe(ram.read, live)
    snapshot = dex.snapshot()
    snapshot["entries"]["83"]["name"] = "changed"
    assert dex.entries["83"]["name"] == "Encountermon"


def test_in_game_dex_gates_unknown_details(encounter, controller):
    from test_evolution import press
    ram, dex, live, _ = encounter
    dex.observe(ram.read, live)
    dex.catalog = [3, 83]
    controller.dex = dex
    controller.before_frame(0)
    assert controller.menu.rows[7] == "Digi-Dex"
    for _ in range(7): press(controller, "Right")
    press(controller, "B")
    assert controller.menu.rows[0] == "??? (not battled)"
    press(controller, "B")
    assert not controller._dex_detail
    press(controller, "Down")
    press(controller, "B")
    assert controller.menu.title == "Encountermon"
    assert "Rank: Rookie" in controller.menu.rows
    press(controller, "Right")
    assert "Linked skills (species)" in controller.menu.rows
    assert "Fireball" in controller.menu.rows
    press(controller, "Right")
    assert "When: schedule unknown" in controller.menu.rows
    press(controller, "A")
    assert controller.menu.title.startswith("Digi-Dex")
    press(controller, "A")
    assert controller.menu.title == "Menu"


from test_evolution import controller


def test_battle_initialization_cannot_reuse_stale_opponent_slots(encounter):
    ram, dex, live, _ = encounter
    ram.put(P.GAME_STATE, "<h", 0)
    dex.observe(ram.read, live)
    ram.put(P.GAME_STATE, "<h", 1)  # startBattle has not rebuilt opponents yet
    assert dex.observe(ram.read, live) == []
    assert not dex.entries
    ram.put(P.GAME_STATE, "<h", 3)
    assert dex.observe(ram.read, live) == ["Encountermon"]
    dex.reset_runtime()  # loading a valid in-progress battle can begin in state 1
    ram.put(P.GAME_STATE, "<h", 1)
    dex.observe(ram.read, live)
    assert dex._battle_ready


def test_enemy_variant_records_its_equipped_skills(encounter):
    ram, dex, live, pointer = encounter
    dex.observe(ram.read, live)
    ram.put(pointer + 68, "4B", 47, 255, 255, 255)
    dex.observe(ram.read, live)
    profiles = dex.entries["83"]["profiles"]
    assert len(profiles) == 2
    assert profiles[1]["skills"] == [{"id": 2, "name": "Ice Needle"}]
    assert len(dex.entries["83"]["skills"]) == 2


def test_windows_dex_hides_locked_details_and_shows_skills(encounter):
    from PySide6.QtWidgets import QApplication
    from dw1_workbench.digidex_view import DigiDexDialog
    ram, dex, live, _ = encounter
    dex.observe(ram.read, live)
    dex.catalog = [3, 83]
    app = QApplication.instance() or QApplication([])
    dialog = DigiDexDialog(dex.snapshot())
    assert "locked" in dialog.details.toPlainText()
    assert "Encountermon" not in dialog.details.toPlainText()
    dialog.list.setCurrentRow(1)
    assert "Fireball" in dialog.details.toPlainText()
    assert "Equipped in encountered battle" in dialog.details.toPlainText()
    dialog.search.setText("Encountermon")
    assert dialog.list.count() == 1
    dialog.search.setText("Agumon")
    assert dialog.list.count() == 0  # an unbattled name cannot be searched
    dialog.close()
