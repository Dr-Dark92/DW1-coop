from __future__ import annotations

import pytest

from dw1_workbench.quicksave import RESUME_SLOT, QuickSaveError, QuickSaveStore


def test_save_list_and_load(tmp_path):
    store = QuickSaveStore(tmp_path / "disc")
    info = store.save(3, b"state", b"png", "Core", "1.0", note="  Green Gym  ")
    assert (info.slot, info.note, info.size) == (3, "Green Gym", 5)
    assert [slot.slot for slot in store.slots()] == [3]
    assert store.load(3, core="Core") == b"state"
    assert store.screenshot_path(3).read_bytes() == b"png"
    assert not list((tmp_path / "disc").glob("*.partial"))


def test_empty_damaged_and_foreign_slots_are_refused(tmp_path):
    store = QuickSaveStore(tmp_path)
    with pytest.raises(QuickSaveError, match="empty"):
        store.load(1)
    store.save(1, b"state", None, "Core", "1.0")
    with pytest.raises(QuickSaveError, match="made by Core"):
        store.load(1, core="Other")
    (tmp_path / "slot1.state").write_bytes(b"other")
    with pytest.raises(QuickSaveError, match="damaged"):
        store.load(1)


def test_resume_slot_delete_and_bounds(tmp_path):
    store = QuickSaveStore(tmp_path)
    assert store.save(RESUME_SLOT, b"s", None, "Core", "1").label == "Resume"
    store.delete(RESUME_SLOT)
    assert store.slots() == []
    with pytest.raises(QuickSaveError):
        store.save(10, b"s", None, "Core", "1")


def test_memory_card_round_trip(tmp_path):
    store = QuickSaveStore(tmp_path / "disc")
    assert store.read_memory_card() is None
    store.write_memory_card(b"card")
    assert store.read_memory_card() == b"card"
