from __future__ import annotations

from dataclasses import dataclass

import pytest

from dw1_workbench import split_screen as S
from dw1_workbench.evolution import EvolutionController, bit


@dataclass
class Info:
    slot: int
    created: str = "2026-11-04T09:30:00+00:00"


def test_player_ones_save_is_marked_taken():
    rows = S.choosable_saves([Info(0), Info(1), Info(3)], 3)
    assert [(row["slot"], row["taken"]) for row in rows] == [(0, False), (1, False), (3, True)]
    assert rows[0]["label"].startswith("Resume") and "Player 1" in rows[2]["label"]
    assert not any(row["taken"] for row in S.choosable_saves([Info(1)], None))


def test_copy_keeps_player_twos_save_apart(tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    for name in ("slot2.state", "slot2.json", "slot2.png", "slot2.progress.json", "slot3.state", "memory_card_1.srm"):
        (store / name).write_bytes(name.encode())
    S.copy_save(store, 2, store / "player2")
    copied = sorted(path.name for path in (store / "player2").iterdir())
    assert copied == ["memory_card_1.srm", "slot2.json", "slot2.png", "slot2.progress.json", "slot2.state"]
    # A card player 2 has already played on is kept.
    (store / "player2" / "memory_card_1.srm").write_bytes(b"theirs")
    S.copy_save(store, 2, store / "player2")
    assert (store / "player2" / "memory_card_1.srm").read_bytes() == b"theirs"


@pytest.fixture
def controller():
    menu = EvolutionController.__new__(EvolutionController)
    menu.menu = None
    menu.notices = []
    menu.split_request = None
    menu.split_running = lambda: False
    menu._split_open = True
    menu._split_index = 0
    menu._handoff = False
    menu._handoff_button = 0
    menu.split_saves = lambda: S.choosable_saves([Info(1), Info(3)], 1)
    return menu


def test_chooser_refuses_player_ones_save_and_starts_on_another(controller):
    assert controller._split_frame(0) is None and controller.menu.rows[0].endswith("Player 1's save")
    assert controller._split_frame(bit("B")) is None
    assert controller.split_request is None and "can't use" in controller.notices[0] and controller._split_open
    controller._split_frame(bit("Down"))
    close = controller._split_frame(bit("B"))
    assert controller.split_request == 3 and not controller._split_open and close == bit("X")


def test_chooser_with_no_other_save_says_so(controller):
    controller.split_saves = lambda: S.choosable_saves([Info(1)], 1)
    controller._split_frame(bit("B"))
    assert controller.split_request is None and "No other save" in controller.menu.rows[0]


def test_back_and_stop(controller):
    controller._split_frame(bit("X"))
    assert not controller._split_open
    controller._split_open = True
    controller.split_running = lambda: True
    controller._split_frame(bit("B"))
    assert controller.split_request == "stop"


def test_shared_memory_frame_round_trip():
    memory = S.shared_memory.SharedMemory(create=True, size=S.SIZE)
    try:
        game = S.SplitGame.__new__(S.SplitGame)
        game.memory = memory
        game._last = 0
        S.HEADER.pack_into(memory.buf, 0, 0, 0, 0, 0, 0)
        assert game.newest_frame() is None
        S.struct.pack_into("<III", memory.buf, 0, 3, 2, 1)  # odd: still being written
        assert game.newest_frame() is None
        memory.buf[S.HEADER.size:S.HEADER.size + 8] = bytes(range(8))
        S.struct.pack_into("<I", memory.buf, 0, 4)
        assert game.newest_frame() == (2, 1, bytes(range(8)))
        assert game.newest_frame() is None  # nothing new
        game.set_buttons(5)
        assert S.struct.unpack_from("<I", memory.buf, 12)[0] == 5
    finally:
        memory.close()
        memory.unlink()


def test_stopping_copes_with_the_memory_already_gone():
    class Exited:
        def wait(self, timeout):
            return 0

    game = S.SplitGame.__new__(S.SplitGame)
    game.memory = S.shared_memory.SharedMemory(create=True, size=S.HEADER.size)
    game.process = Exited()
    game.link = S.WorldLink(game.memory, 0)
    other = S._attach(game.memory.name)
    other.close()
    other.unlink()
    game.stop()
