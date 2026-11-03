from types import SimpleNamespace
import struct

import pytest

from dw1_workbench import evolution as E
from dw1_workbench import progression as P
from dw1_workbench.live import ram_offset
from test_progression import Ram


class Core(Ram):
    info = SimpleNamespace(name="Beetle PSX", version="test")
    read_memory = Ram.read
    write_memory = Ram.write

    def serialize(self):
        return bytes(self.data)

    def unserialize(self, state):
        self.data[:] = state


@pytest.fixture
def controller():
    core = Core()
    for address, (original, _) in E.PATCHES.items():
        core.write(ram_offset(address), original)
    core.put(P.PARTNER_ENTITY, "<i", 3)
    core.put(P.PARTNER_STATS + 16, "<h", 2000)
    core.put(E.TAMER_STATE, "<b", 6)
    core.put(E.IS_IN_MENU, "<i", 1)
    core.put(E.PARTNER_STATE, "<b", 1)
    core.put(E.TRIANGLE_MENU_STATE, "<i", -1)
    core.put(P.UI_BOX_DATA + 18, "<h", 1)
    core.put(P.UI_BOX_DATA + 32, "<I", E.GAME_MENU_RENDER)
    core.put(E.MENU_OPTIONS + 4, "<h", 1)
    core.put(P.DIGIMON_DATA + 3 * 52 + 29, "<B", 3)
    core.put(E.EVOLUTION_PATHS + 2 * 11 + 5, "6B", 5, 9, 255, 66, 5, 10)
    for kind, stage, name in [(5, 4, b"Greymon"), (9, 4, b"Tyrannomon"), (10, 5, b"Ultimate")]:
        core.write(ram_offset(P.DIGIMON_DATA + kind * 52), name + bytes(20 - len(name)))
        core.put(P.DIGIMON_DATA + kind * 52 + 29, "<B", stage)
    return E.EvolutionController(core, P.Progress(P.total_exp(21)))


def press(controller, name):
    controller.before_frame(0)
    return controller.before_frame(E.bit(name))


def test_reversible_guard_preserves_other_ram_and_care(controller):
    core = controller.core
    before = bytes(core.data)
    assert controller.install()
    after = bytes(core.data)
    allowed = {ram_offset(a) + i for a in E.PATCHES for i in range(8)}
    assert all(a == b or i in allowed for i, (a, b) in enumerate(zip(before, after)))
    assert controller.install()
    controller.restore()
    assert bytes(core.data) == before
    assert controller.install()
    controller.restore()
    assert bytes(core.data) == before


def test_guard_rejects_unknown_code_without_partial_writes(controller):
    core = controller.core
    core.put(0x800E3234, "<I", 0xdeadbeef)
    before = bytes(core.data)
    with pytest.raises(ValueError, match="original USA"):
        controller.install()
    assert bytes(core.data) == before


def test_guard_waits_for_boot(controller):
    core = controller.core
    core.write(ram_offset(0x800E3234), bytes(8))
    before = bytes(core.data)
    assert not controller.install()
    assert bytes(core.data) == before


def test_only_linked_champions_and_level_boundary(controller):
    assert [(c.kind, c.name) for c in controller.choices()] == [(5, "Greymon"), (9, "Tyrannomon")]
    controller.progress.exp = P.total_exp(21) - 1
    with pytest.raises(ValueError, match="level 21"):
        controller.start_evolution(5)
    controller.progress.exp += 1
    with pytest.raises(ValueError):
        controller.start_evolution(10)
    with pytest.raises(ValueError):
        controller.start_evolution(36)
    controller.start_evolution(9)
    assert controller.number(E.EVOLUTION_TARGET) == 9
    assert controller.number(E.PARTNER_STATE, "<b") == 13
    assert controller.number(E.TAMER_STATE, "<b") == 6
    assert controller.number(E.PARTNER_STATE + 1, "<b") == 0
    assert controller.number(E.TAMER_STATE + 1, "<b") == 0
    assert controller.progress.level == 21


def test_seventh_menu_choice_confirm_cancel_and_native_handoff(controller):
    assert controller.before_frame(0) == 0
    assert controller.menu.rows[6] == "Evolution"
    for _ in range(6):
        press(controller, "Right")
    assert controller.menu.selected == 6
    press(controller, "B")
    assert controller.menu.title == "Evolution"
    assert controller.menu.rows == ("Greymon", "Tyrannomon")
    press(controller, "B")
    assert controller.confirming
    assert controller.number(E.PARTNER_STATE, "<b") == 1
    press(controller, "A")
    assert controller.menu.title == "Menu"
    press(controller, "B")
    press(controller, "Down")
    press(controller, "B")
    press(controller, "B")
    assert controller.number(E.EVOLUTION_TARGET) == 9
    assert controller.menu is None


def test_native_options_and_fishing_survive(controller):
    controller.before_frame(0)
    press(controller, "Right")
    assert press(controller, "B") == E.bit("B")
    assert controller.number(E.MENU_OPTIONS + 4) == 2
    controller.before_frame(0)  # a rejected/disabled native action returns control
    assert controller.menu is not None
    controller.core.put(E.HAS_FISHING_ROD, "<i", 1)
    controller.selection = 8  # after Evolution and the Digi-Bank
    assert press(controller, "B") == E.bit("B")
    assert controller.number(E.MENU_OPTIONS + 4) == 7


def test_battle_and_other_forms_cannot_evolve(controller):
    controller.core.put(P.GAME_STATE, "<h", 2)
    assert controller.before_frame(E.bit("Down")) == E.bit("Down")
    assert controller.menu is None
    with pytest.raises(ValueError):
        controller.start_evolution(5)
    controller.core.put(P.PARTNER_ENTITY, "<i", 5)
    assert controller.choices() == ()


def test_quick_save_is_native_and_load_restores_level_and_menu_state(controller, tmp_path):
    from dw1_workbench.player import EmulatorWorker, PlayConfig
    from dw1_workbench.quicksave import QuickSaveStore

    config = PlayConfig(tmp_path / "core.dll", tmp_path, tmp_path / "game.cue", tmp_path)
    worker = EmulatorWorker(config)
    worker.core = controller.core
    worker.rules = P.GameRules(controller.core.read_memory, controller.core.write_memory, controller.progress)
    worker.evolution = controller
    worker.evolution.install()
    worker._save(1)
    state = worker.store.load(1, core="Beetle PSX")
    for address, (original, _) in E.PATCHES.items():
        assert state[ram_offset(address):ram_offset(address) + 8] == original
    worker.evolution.install()
    worker.evolution.choosing = True
    worker.rules.progress = P.Progress(P.total_exp(30))
    worker._load(1)
    assert worker.rules.progress.level == 21
    assert not worker.evolution.choosing
    worker.evolution.progress = worker.rules.progress
    worker.evolution.before_frame(0)
    assert worker.evolution.menu.rows[6] == "Evolution"
    worker.evolution.restore()


def test_requires_supported_core_and_idle_partner(controller):
    controller.core.info = SimpleNamespace(name="Other PSX", version="1")
    before = bytes(controller.core.data)
    with pytest.raises(ValueError, match="Beetle PSX"):
        controller.install()
    assert bytes(controller.core.data) == before
    controller.core.info = SimpleNamespace(name="Beetle PSX", version="1")
    controller.core.put(E.PARTNER_STATE, "<b", 3)
    with pytest.raises(ValueError):
        controller.start_evolution(5)


@pytest.mark.parametrize("button", ["A", "X"])
def test_main_menu_cancel_forwards_usa_triangle(controller, button):
    controller.before_frame(0)
    assert controller.before_frame(E.bit(button)) == E.bit("X")
    assert controller.before_frame(E.bit(button)) == E.bit("X")


def test_relation_links_birth_stages_and_no_arbitrary_evolution(controller):
    core = controller.core
    for kind, stage in [(1, 1), (2, 2), (6, 5)]:
        core.put(P.DIGIMON_DATA + kind * 52 + 29, "<B", stage)
    core.put(E.EVOLUTION_PATHS + 11 + 5, "6B", 3, 255,255,255,255,255)
    core.put(E.EVOLUTION_PATHS + 4 * 11 + 5, "6B", 6,255,255,255,255,255)
    graph = controller.relation_graph()
    assert {(1,2),(2,3),(3,5),(5,6)} <= set(graph["edges"])
    controller.before_frame(0)
    controller.selection = 6
    press(controller, "B")
    assert controller.menu.layout == "relation"
    assert controller.menu.relation["selected"] == 5
    press(controller, "Left")
    assert controller.menu.relation["selected"] == 2
    press(controller, "B")
    assert not controller.confirming
    assert controller.number(E.PARTNER_STATE, "<b") == 1


def test_main_grid_navigation_preserves_columns(controller):
    controller.before_frame(0)
    press(controller, "Down")
    assert controller.menu.selected == 3
    press(controller, "Down")
    assert controller.menu.selected == 6
    press(controller, "Down")
    assert controller.menu.selected == 0


def test_overlay_sound_intents_for_open_navigation_and_choose(controller):
    controller.before_frame(0)
    assert controller.sound_events == ["open"]
    press(controller, "Right")
    assert controller.sound_events == ["navigate"]
    controller.selection = 6
    press(controller, "B")
    assert controller.sound_events == ["choose"]
    press(controller, "B")
    assert controller.sound_events == ["choose"]
    press(controller, "A")
    assert controller.sound_events == ["back"]
