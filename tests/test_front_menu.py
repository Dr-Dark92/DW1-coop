from types import SimpleNamespace
import struct

import pytest

from dw1_workbench.front_menu import FrontMenu, SettingsMenu, TIMES, STARTER_WORDS, WORLD_OBJECTS, CURRENT_MENU, TARGET_MENU, SCRIPT_STATE, SCRIPT_POINTER
from dw1_workbench.gameplay_options import RoamingOptions
from dw1_workbench.evolution import bit
from dw1_workbench.live import ram_offset


class Core:
    info = SimpleNamespace(name="Beetle PSX", version="test")
    def __init__(self): self.data = bytearray(0x200000)
    def read_memory(self, offset, size): return bytes(self.data[offset:offset + size])
    def write_memory(self, offset, data): self.data[offset:offset + len(data)] = data
    def put(self, address, fmt, *values): struct.pack_into(fmt, self.data, ram_offset(address), *values)
    def serialize(self): return bytes(self.data)
    def unserialize(self, data): self.data[:] = data


def controller():
    core = Core()
    state = [RoamingOptions(), True, False]
    settings = SettingsMenu(lambda: tuple(state), lambda *values: state.__setitem__(slice(None), values))
    return core, FrontMenu(core, settings), state


def test_title_settings_toggle_consumes_input_and_returns_to_title():
    core, front, state = controller()
    core.put(WORLD_OBJECTS, "<hhII", 5000, 0, 0x8010FF1C, 0x80113458)
    front.selection = 4
    assert front.before_frame(bit("B")) == 0
    assert front.menu.title == "Settings"
    front.before_frame(0); front.before_frame(bit("B"))
    assert not state[0].hp_mp
    front.before_frame(0); front.before_frame(bit("A"))
    assert front.menu.rows[-1] == "Settings"
    assert front.before_frame(0) == 0
    core.put(TARGET_MENU, "<i", 10)
    assert front.before_frame(bit("B")) == bit("B")  # native card menu is delegated unchanged


@pytest.mark.parametrize("index,kind", list(enumerate((17, 31, 46, 3, 45, 57, 32))))
@pytest.mark.parametrize("stage", (6, 10))
def test_hidden_time_mapping_patches_only_verified_init_arguments(index, kind, stage):
    core, front, state = controller()
    core.put(WORLD_OBJECTS, "<hhII", 117, 2, 0x800F1498, 0x800F0000)
    core.put(SCRIPT_STATE, "<h", stage)
    core.put(SCRIPT_POINTER, "<I", 0x80100000)
    for address, word in STARTER_WORDS.items(): core.put(address, "<I", word)
    front.before_frame(0)
    assert front.menu.title == "Jijimon"
    assert not any(name in " ".join(front.menu.rows) for name in ("Agumon", "Gabumon", "Kunemon", "Patamon", "Palmon", "Penguinmon", "Biyomon"))
    front.time_index = index
    front.before_frame(bit("B"))
    assert front.patched == kind and front.number(SCRIPT_STATE, "<h") == 0x11
    assert all(front.number(a, "<I") == 0x24100000 | kind for a in STARTER_WORDS)
    front.restore()
    assert all(front.number(a, "<I") == word for a, word in STARTER_WORDS.items())


def test_shared_script_state_alone_cannot_trigger_starter_or_patch_unknown_code():
    core, front, state = controller()
    core.put(SCRIPT_STATE, "<h", 6)
    assert front.before_frame(bit("B")) == bit("B")
    assert front.menu is None
    before = bytes(core.data)
    with pytest.raises(ValueError, match="differs"):
        front._patch_starter(31)
    assert bytes(core.data) == before
    with pytest.raises(ValueError, match="Unsupported native starter"):
        front._patch_starter(111)
    assert bytes(core.data) == before


def test_classic_menu_does_not_install_evolution_patches():
    from dw1_workbench.evolution import EvolutionController
    from dw1_workbench.progression import Progress
    core, front, state = controller()
    before = bytes(core.data)
    classic = EvolutionController(core, Progress(), manual_evolution=False)
    assert classic.install()
    classic.restore()
    assert bytes(core.data) == before


def test_new_native_partner_gets_source_linked_attack_and_paired_learned_bits():
    from dw1_workbench.progression import PARTNER_ENTITY, PARTNER_STATS, LIFETIME, DIGIMON_DATA
    core, front, state = controller()
    core.put(WORLD_OBJECTS, "<hhII", 32, 1, 0x800A0000, 0x800B0000)
    core.put(PARTNER_ENTITY, "<i", 32)
    core.put(PARTNER_STATS + 16, "<h", 700)
    core.put(LIFETIME, "<h", 360)
    core.put(DIGIMON_DATA + 32 * 52 + 35, "16B", 255, 44, *([255] * 14))
    core.put(PARTNER_STATS + 12, "4B", 255, 255, 255, 63)
    core.put(PARTNER_ENTITY + 88, "<Q", 1 << 2)
    front.starting = 32
    front.before_frame(0)
    assert front.starting is None
    assert core.read_memory(ram_offset(PARTNER_STATS + 12), 4) == bytes([0x2f, 255, 255, 63])
    assert front.number(PARTNER_ENTITY + 88, "<Q") == (1 << 2) | (1 << 44) | (1 << 48)


def test_cancelling_question_restores_native_wait_stage_without_a_patch():
    core, front, state = controller()
    core.put(WORLD_OBJECTS, "<hhII", 117, 2, 0x800F1498, 0x800F0000)
    core.put(SCRIPT_STATE, "<h", 10)
    front.before_frame(0)
    assert front.question and front.number(SCRIPT_STATE, "<h") == 1
    front.reset()
    assert not front.question and front.number(SCRIPT_STATE, "<h") == 10


def test_style_applies_at_title_but_is_only_staged_during_play(tmp_path):
    from dw1_workbench.player import EmulatorWorker, PlayConfig
    from dw1_workbench.evolution import EvolutionController
    from dw1_workbench.progression import Progress
    core, front, state = controller()
    worker = EmulatorWorker(PlayConfig(tmp_path / "core", tmp_path / "bios", tmp_path / "disc", tmp_path))
    worker.core = core
    worker.front_menu = SimpleNamespace(at_title=False)
    worker.evolution = EvolutionController(core, Progress(), manual_evolution=False)
    worker._change_gameplay(RoamingOptions(), True, True)
    assert worker.next_exp and worker.rules is None and not worker.evolution.manual_evolution
    worker.front_menu.at_title = True
    worker._change_gameplay(RoamingOptions(), True, True)
    assert worker.rules is not None and worker.evolution.manual_evolution
    assert not worker.rules.legacy_immortality
    worker._change_gameplay(RoamingOptions(), True, False)
    assert worker.rules is None and not worker.evolution.manual_evolution
