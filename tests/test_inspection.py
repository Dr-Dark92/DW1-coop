from __future__ import annotations

import pytest

from dw1_workbench.inspection import InspectionError, InspectionStore


def add(store, **overrides):
    values = dict(map_index=112, map_name="Green Gym · TRAI00", position=(475, 0, -3299), tile=(54, 82),
                  trigger=0, partner_tile=(54, 85), grid_differences=0, category="Toilet", note=" by the gym ",
                  core="Core", core_version="1", screenshot=b"png", state=b"state" * 100, live_grid=bytes(10000))
    values.update(overrides)
    return store.add(**values)


def test_add_get_list_and_state(tmp_path):
    store = InspectionStore(tmp_path / "inspections")
    record = add(store)
    assert record.note == "by the gym" and record.tile == (54, 82)
    assert store.get(record.id) == record
    assert store.list() == [record] and store.list(map_index=112) == [record] and store.list(map_index=1) == []
    assert store.state(record.id) == b"state" * 100
    assert store.screenshot_path(record.id).read_bytes() == b"png"
    assert store.live_grid(record.id) == bytes(10000)


def test_without_state_and_delete(tmp_path):
    store = InspectionStore(tmp_path)
    record = add(store, state=None, screenshot=None, live_grid=None, position=None, tile=None, trigger=None)
    assert not store.has_state(record.id) and store.screenshot_path(record.id) is None
    with pytest.raises(InspectionError):
        store.state(record.id)
    store.delete(record.id)
    assert store.list() == []
    with pytest.raises(InspectionError):
        store.get("../escape")
