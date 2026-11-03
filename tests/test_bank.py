import json

import pytest

from dw1_workbench import bank as B
from dw1_workbench import evolution as E
from dw1_workbench import progression as P
from dw1_workbench.live import ram_offset
from test_evolution import controller, press  # noqa: F401  (fixture)


def name(core, kind, text):
    core.write(ram_offset(P.DIGIMON_DATA + kind * 52), text + bytes(20 - len(text)))


@pytest.fixture
def banked(controller):
    core = controller.core
    name(core, 3, b"Agumon")
    for kind, stage, text in [(1, 1, b"Botamon"), (15, 1, b"Punimon"), (29, 1, b"Poyomon"), (43, 1, b"Yuramon"),
                              (2, 2, b"Koromon")]:
        name(core, kind, text)
        core.put(P.DIGIMON_DATA + kind * 52 + 29, "<B", stage)
    core.put(P.PARTNER_STATS, "<4h", 300, 250, 220, 210)
    core.put(P.PARTNER_STATS + 16, "<4h", 2100, 2050, 1500, 1400)
    core.put(P.AGE, "<h", 7)
    core.put(B.RAISE_DATA + 1 * 28 + B.DEFAULT_WEIGHT, "<B", 5)
    return controller


def open_bank(controller):
    controller.before_frame(0)
    controller.selection = controller.menu.rows.index("Digi-Bank")
    press(controller, "B")
    assert controller.menu.title.startswith("Digi-Bank ")


def finish_sequence(controller, kind):
    core = controller.core
    controller.before_frame(0)  # partner state 13: the sequence has started
    core.put(P.PARTNER_ENTITY, "<i", kind)
    core.put(E.PARTNER_STATE, "<b", 1)
    controller.before_frame(0)


def test_progress_keeps_bank_and_reads_format_1():
    progress = P.Progress(P.total_exp(12), [B.egg(1)])
    assert progress.bank == [B.egg(1), None, None, None]
    restored = P.Progress.from_json(progress.to_json())
    assert restored == progress and json.loads(progress.to_json())["format"] == 2
    assert P.Progress.from_json(b'{"format": 1, "exp": 10}').bank == [None] * 4
    for bad in ([{"kind": 1, "exp": 0}], [B.egg(1)] * 5, [{**B.egg(1), "stats": "00"}], [{**B.egg(1), "kind": 90}]):
        with pytest.raises(ValueError):
            P.Progress.from_json(json.dumps({"format": 2, "exp": 0, "bank": bad}).encode())


def test_ninth_icon_opens_bank_with_four_slots(banked):
    banked.before_frame(0)
    assert banked.menu.rows == ("Items", "Digimon", "Player", "Praise", "Scold", "Sleep", "Evolution", "Digi-Bank")
    banked.core.put(E.HAS_FISHING_ROD, "<i", 1)
    banked.before_frame(0)
    assert banked.menu.rows[-2:] == ("Digi-Bank", "Fishing")
    open_bank(banked)
    assert banked.menu.rows == ("Empty slot",) * 4 + ("Select new egg",)
    assert banked.menu.icons == ("empty",) * 4 + ("egg",)
    press(banked, "A")
    assert banked.menu.title == "Menu"


def test_hatch_an_egg_then_make_it_active(banked):
    core = banked.core
    open_bank(banked)
    for _ in range(4):
        press(banked, "Down")
    press(banked, "B")
    assert banked.menu.title == "Choose an egg"
    assert banked.menu.rows == ("Botamon", "Punimon", "Poyomon", "Yuramon")
    press(banked, "B")
    assert banked.progress.bank[0] is None  # first Cross only asks
    press(banked, "B")
    assert banked.progress.bank[0] == B.egg(1) and banked.events == ["bank"]
    assert banked.menu.rows[0].startswith("Botamon") and banked.menu.selected == 0
    banked.events.clear()
    press(banked, "B")  # status page
    assert banked.menu.title == "Botamon" and banked.menu.avatar == 1
    assert "HP 90/90   MP 110/110" in banked.menu.rows
    exp = banked.progress.exp
    press(banked, "B")
    assert banked.number(E.PARTNER_STATE, "<b") == 1
    assert press(banked, "B") == 0
    # The native sequence takes the partner to Botamon; Agumon (and its EXP) is stored in its slot.
    assert banked.number(E.EVOLUTION_TARGET) == 1 and banked.number(E.PARTNER_STATE, "<b") == 13
    stored = banked.progress.bank[0]
    assert stored["kind"] == 3 and stored["exp"] == exp and banked.progress.exp == 0
    assert bytes.fromhex(stored["stats"])[:8] == bytes.fromhex("2c01fa00dc00d200")
    finish_sequence(banked, 1)
    assert core.get(P.PARTNER_STATS + 16, "<4h") == 90 and core.get(P.PARTNER_STATS, "<h") == 10
    assert core.get(P.AGE, "<h") == 0 and core.get(P.PARTNER_PARA + B.DISCIPLINE, "<h") == 50
    assert banked._swap is None


def test_stored_partner_returns_exactly(banked):
    core = banked.core
    agumon = B.capture(core.read, P.total_exp(25))
    banked.progress.bank[2] = agumon
    core.put(P.PARTNER_ENTITY, "<i", 2)
    core.put(P.PARTNER_STATS + 16, "<4h", 500, 400, 500, 400)
    core.put(P.AGE, "<h", 2)
    banked.progress.exp = P.total_exp(9)
    open_bank(banked)
    press(banked, "Down"); press(banked, "Down")
    press(banked, "B"); press(banked, "B"); press(banked, "B")
    assert banked.progress.bank[2]["kind"] == 2 and banked.progress.exp == P.total_exp(25)
    finish_sequence(banked, 3)
    assert B.capture(core.read, banked.progress.exp) == agumon


def test_swap_waits_for_the_sequence_and_a_load_cancels_it(banked):
    banked.progress.bank[0] = B.egg(15)
    banked.start_swap(0)
    banked.core.put(P.PARTNER_ENTITY, "<i", 15)
    banked.before_frame(0)  # still state 13
    assert banked._swap is not None
    banked.reset()  # loading a state
    banked.core.put(E.PARTNER_STATE, "<b", 1)
    banked.before_frame(0)
    assert banked.core.get(P.PARTNER_STATS + 16, "<h") == 2100


def test_full_bank_and_busy_partner(banked):
    banked.progress.bank = [B.egg(1)] * 4
    open_bank_full = banked.before_frame(0)
    banked.selection = banked.menu.rows.index("Digi-Bank")
    press(banked, "B")
    assert banked.menu.rows[-1] == "Bank full: no new egg"
    with pytest.raises(ValueError, match="full"):
        banked.hatch(29)
    press(banked, "B")
    banked.core.put(E.FADE_PROTECTION, "<b", 1)
    press(banked, "B")
    assert "Partner busy" in banked.menu.footer
    assert not banked._bank_confirm


def test_fresh_and_in_training_evolve_by_level(banked):
    core = banked.core
    core.put(P.PARTNER_ENTITY, "<i", 1)
    assert [c.kind for c in banked.choices()] == [2]
    banked.progress.exp = P.total_exp(5)
    with pytest.raises(ValueError, match="level 6"):
        banked.start_evolution(2)
    banked.progress.exp = P.total_exp(6)
    banked.start_evolution(2)
    assert banked.number(E.EVOLUTION_TARGET) == 2
    core.put(P.PARTNER_ENTITY, "<i", 2)
    core.put(E.EVOLUTION_PATHS + 11 + 5, "6B", 3, 255, 255, 255, 255, 255)
    assert banked.required_level() == 12 and [c.kind for c in banked.choices()] == [3]


def test_worker_saves_bank_changes(banked, tmp_path):
    from dw1_workbench.player import EmulatorWorker, PlayConfig

    worker = EmulatorWorker(PlayConfig(tmp_path / "core.dll", tmp_path, tmp_path / "game.cue", tmp_path))
    worker.core = banked.core
    worker.rules = P.GameRules(banked.core.read_memory, banked.core.write_memory, banked.progress)
    worker.evolution = banked
    banked.hatch(43)
    worker._bank_events()
    saved = P.Progress.from_json((tmp_path / "progression.json").read_bytes())
    assert saved.bank[0] == B.egg(43) and not banked.events and not banked.notices
