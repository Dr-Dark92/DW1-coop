"""No retail data. Barrier-based race regressions, cache bounds, diagnostics and isolated workers."""
import threading
import time

import numpy as np
import pytest

from dw1_workbench import hd_background as H
from dw1_workbench.telemetry import Telemetry
from test_hd_background import Game, picture_file


def wait_for(predicate):
    deadline = time.monotonic() + 5
    while not predicate():
        assert time.monotonic() < deadline
        time.sleep(0.005)


def test_telemetry_has_bounded_samples_events_and_known_percentiles(tmp_path):
    telemetry = Telemetry(True, capacity=100, clock=lambda: 1_000_000)
    for index in range(300):
        telemetry.duration("frame", index * 1_000_000)
        telemetry.event("tick", index=index)
    report = telemetry.snapshot()
    assert report["metrics"]["frame"] == dict(samples=100, p50_ms=249, p95_ms=294, p99_ms=298)
    assert report["counts"]["frame.total"] == 300
    assert len(report["events"]) == 256
    assert telemetry.write(tmp_path) != telemetry.write(tmp_path)
    disabled = Telemetry()
    disabled.count("x"); disabled.event("x"); disabled.duration("x", 100)
    assert disabled.snapshot()["metrics"] == {}


def test_battle_overwritten_dimensions_and_table_do_not_destroy_environment():
    game = Game()
    background = H.MapBackground(game.read)
    assert background.refresh()
    original, key = background.indices.copy(), background.key
    game.set(H.GAME_STATE, "<h", 1)
    game.set(H.MAP_SIZE, "<bb", -1, -1)
    game.set(H.MAP_TILE_DATA, "<I", 0)
    assert background.refresh(force=True)
    assert background.key == key
    np.testing.assert_array_equal(background.indices, original)
    # A different map must never inherit that environment, even during battle initialization.
    game.set(H.CURRENT_SCREEN, "<B", 8)
    assert not background.refresh()
    assert background.key == key  # retained but unusable for this frame


def test_game_state_is_byte_not_neighboring_halfword():
    game = Game()
    background = H.MapBackground(game.read)
    assert background.refresh()
    game.set(H.GAME_STATE, "<BB", 0, 0x7F)
    game.ram[0x20000:0x24000] = bytes(16384)
    assert background.refresh(force=True)
    assert not background.indices[:128, :128].any()  # exploration re-read, not a false cached battle


@pytest.mark.parametrize("invalidate", ["reset", "native_fallback", "scene", "stop"])
def test_in_flight_result_is_rejected_after_invalidating_scene(invalidate):
    started, release = threading.Event(), threading.Event()
    telemetry = Telemetry(True)
    delivered = []

    class Layer:
        generation = 1

        def compose(self, job):
            started.set()
            assert release.wait(5)
            return object()

    layer = Layer()
    compositor = H.Compositor(layer, delivered.append, delivered.append, telemetry)
    stopper = None
    try:
        compositor.submit(H.BackgroundJob(1, ()))
        assert started.wait(5)
        if invalidate in ("scene", "reset"):
            layer.generation += 1
        elif invalidate == "stop":
            stopper = threading.Thread(target=compositor.stop)
            stopper.start()
            wait_for(lambda: compositor._stop)
        else:
            compositor.invalidate()
        release.set()
        wait_for(lambda: telemetry.snapshot()["counts"].get("compositor.stale_rejected") == 1)
        assert delivered == []
    finally:
        release.set()
        compositor.stop()
        if stopper is not None:
            stopper.join(5)
            assert not stopper.is_alive()


def test_first_battle_prepare_serializes_once_not_twice(tmp_path):
    game, count = Game(), []
    frame = game.frame()
    game.set(H.GAME_STATE, "<h", 1)

    def serialize():
        count.append(1)
        return game.serialize()

    layer = H.BackgroundLayer(game.read, serialize, lambda index: picture_file(tmp_path))
    try:
        layer.prepare(frame)
        layer._load_picture(layer._picture_key, layer._picture_path)
        layer.reset()
        count.clear()
        # Reopen a battle with no tile snapshot; palette and tile VRAM use the same serialization.
        layer.prepare(frame)
        assert len(count) == 1
    finally:
        layer.close()


def test_truncated_vram_fails_actionably():
    layer = H.BackgroundLayer(lambda *args: b"", lambda: H.GPURAM_FIELD + bytes(20), lambda index: None)
    with pytest.raises(ValueError, match="truncated VRAM"):
        layer._read_vram()
    layer.close()


def test_picture_cache_lru_and_single_bounded_pending_load():
    calls = []
    started, release = threading.Event(), threading.Event()

    def load(path, width, height, scale):
        calls.append(path)
        if path == "A":
            started.set()
            assert release.wait(5)
        return np.full((128, 128), len(calls), np.uint32)

    telemetry = Telemetry(True)
    cache = H.PictureCache(telemetry, pytest.fail, limit=2 * 128 * 128 * 4, loader=load)
    try:
        assert cache.request("A", "A", 1, 1, 1) is None
        assert started.wait(5)
        cache.request("B", "B", 1, 1, 1)
        cache.request("C", "C", 1, 1, 1)  # replaces waiting B, no extra loader/thread
        release.set()
        wait_for(lambda: cache._active is None and cache._pending is None)
        assert calls == ["A", "C"]
        assert cache.request("A", "A", 1, 1, 1) is not None
        cache.request("D", "D", 1, 1, 1)
        wait_for(lambda: "D" in cache._items)
        assert list(cache._items) == ["A", "D"]
        assert cache.size_bytes <= cache.limit
    finally:
        release.set()
        cache.close()


def test_old_map_tint_cannot_be_reused_for_new_map():
    layer = H.BackgroundLayer(lambda *a: b"", None, lambda index: None)
    one = np.full((240, 320), 0xFF808080, np.uint32)
    other = np.full_like(one, 0xFF408040)
    shift = np.array([-1, -1, -1])
    layer._look(one, None, shift); layer._look(one, None, shift)
    assert layer._look(other, None, shift) is None
    result = layer._look(other, None, shift)
    assert int(result[0, 0]) == 0xFF387838
    layer.close()


def test_isolated_avatar_process_reports_bad_disc_and_cleans_up(synthetic_iso, tmp_path):
    import multiprocessing
    from dw1_workbench.avatars import load_avatars_isolated
    # Valid synthetic ISO but no DW1 character tables: must report failure, not leave a child alive.
    with pytest.raises(RuntimeError):
        load_avatars_isolated(synthetic_iso, tmp_path / "avatars", [3], lambda *args: pytest.fail("not retail data"))
    assert not [child for child in multiprocessing.active_children() if child.name == "dw1-avatar-renderer"]


def _synthetic_avatar_child(disc, cache, order, sender, stop):
    from io import BytesIO
    from PIL import Image
    buffer = BytesIO()
    Image.new("RGBA", (64, 64), (255, 0, 0, 255)).save(buffer, "PNG")
    sender.send(("image", 3, buffer.getvalue()))
    stop.wait(10)  # proves cancellation reaches an already-running process


def test_cancel_isolated_avatar_after_first_generated_icon(monkeypatch, tmp_path):
    import multiprocessing
    from dw1_workbench import avatars
    monkeypatch.setattr(avatars, "_avatar_process", _synthetic_avatar_child)
    cancelled, images = threading.Event(), []

    def publish(kind, image):
        images.append((kind, image))
        cancelled.set()

    avatars.load_avatars_isolated(tmp_path / "unused.iso", tmp_path / "cache", [3], publish, cancelled.is_set)
    assert len(images) == 1 and images[0][0] == 3 and images[0][1].width() == 64
    assert not [child for child in multiprocessing.active_children() if child.name == "dw1-avatar-renderer"]


def _avatar_child_dies_mid_message(disc, cache, order, sender, stop):
    import os, struct
    os.write(sender.fileno(), struct.pack("!i", 1 << 20) + b"part of a picture")
    os._exit(9)


def test_isolated_avatar_renderer_dying_mid_message_cannot_hang_the_player(monkeypatch, tmp_path):
    import multiprocessing
    from dw1_workbench import avatars
    monkeypatch.setattr(avatars, "_avatar_process", _avatar_child_dies_mid_message)
    outcome = []

    def run():
        try:
            avatars.load_avatars_isolated(tmp_path / "unused.iso", tmp_path / "cache", [3], lambda *args: None)
        except RuntimeError as exc:
            outcome.append(str(exc))

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(15)
    assert not thread.is_alive() and outcome and "code 9" in outcome[0]
    assert not [child for child in multiprocessing.active_children() if child.name == "dw1-avatar-renderer"]


def test_compositor_paints_without_blocking_the_emulator_and_drops_invalidated_frames():
    class Layer:
        def compose(self, job):
            return job

    painting, release, delivered, errors = threading.Event(), threading.Event(), [], []

    def paint(result):
        painting.set()
        assert release.wait(5)
        return result

    compositor = H.Compositor(Layer(), delivered.append, errors.append, paint=paint)
    try:
        compositor.submit("first")
        assert painting.wait(5)
        started = time.monotonic()
        compositor.invalidate()  # the emulator's calls must not wait for the HUD to be painted
        assert time.monotonic() - started < 1
        release.set()
        compositor.submit("second")
        deadline = time.monotonic() + 5
        while not delivered and time.monotonic() < deadline:
            time.sleep(0.005)
    finally:
        release.set()
        compositor.stop()
    assert delivered == ["second"] and not errors


def test_picture_loader_survives_an_unexpected_error():
    picture = np.zeros((1, 1), np.uint32)
    errors = []

    def load(path, width, height, scale):
        if path == "bad.png":
            raise RuntimeError("unexpected")
        return picture

    cache = H.PictureCache(Telemetry(), errors.append, loader=load)
    try:
        def request(key, path):
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                result = cache.request(key, path, 1, 1, 1)
                if result is not None or (errors and key == "bad"):
                    return result
                time.sleep(0.005)

        assert request("bad", "bad.png") is None and "unexpected" in errors[0]
        assert request("good", "good.png") is picture
    finally:
        cache.close()


def test_scene_timing_distinguishes_partial_battle_and_script_transition():
    now = [0]
    telemetry = Telemetry(True, clock=lambda: now[0])
    telemetry.observe_scene(7, 2, 0)  # starts mid-battle, do not invent a complete duration
    now[0] += 5_000_000
    telemetry.observe_scene(7, 0, 0)
    assert "battle.duration" not in telemetry.snapshot()["metrics"]
    telemetry.observe_scene(7, 1, 0)
    now[0] += 10_000_000
    telemetry.observe_scene(7, 3, 0)
    now[0] += 20_000_000
    telemetry.observe_scene(7, 0, 1)
    now[0] += 40_000_000
    telemetry.observe_scene(8, 0, 0)
    report = telemetry.snapshot()
    assert report["metrics"]["battle.duration"]["p50_ms"] == 30
    assert report["metrics"]["zone.script_transition"]["p50_ms"] == 40


def test_transition_and_unsupported_video_mode_fall_back_to_native(tmp_path):
    game = Game()
    layer = H.BackgroundLayer(game.read, game.serialize, lambda index: picture_file(tmp_path))
    frame = game.frame()
    try:
        layer.prepare(frame); layer._load_picture(layer._picture_key, layer._picture_path)
        assert layer.prepare(frame) is not None
        generation = layer.generation
        game.set(H.SCRIPT_MAP_CHANGE_STATE, "<h", 1)
        assert layer.prepare(frame) is None and layer.generation > generation
        game.set(H.SCRIPT_MAP_CHANGE_STATE, "<h", 0)
        assert layer.prepare(np.zeros((720, 960), np.uint32)) is None  # 3x cannot divide 4x output
        game.set(H.GAME_STATE, "<B", 4)  # non-battle scene, not an established environment
        assert layer.prepare(frame) is None
        from dw1_workbench.player import WORLD_ZOOM_ENABLED
        assert WORLD_ZOOM_ENABLED is False
    finally:
        layer.close()


def test_a_b_a_picture_revisit_preloads_only_known_successor_dimensions(tmp_path):
    game = Game()
    path = picture_file(tmp_path)
    layer = H.BackgroundLayer(game.read, game.serialize, lambda index: path)
    calls = []

    class Cache:
        def request(self, *args):
            calls.append(args)
            return np.zeros((240, 320), np.uint32)

        def close(self): pass

    layer.picture_cache = Cache()
    layer.background.width, layer.background.height = 5, 3
    assert layer._ready_picture(7)
    assert layer._ready_picture(8)
    assert layer._ready_picture(7)
    assert [call[0][0] for call in calls] == [7, 8, 7, 8]
    assert calls[-1][2:] == (5, 3, 4)  # B's previously observed geometry, never inferred from PNG size
    layer.close()


def test_same_map_new_table_invalidates_jobs_even_when_picture_is_not_ready(tmp_path):
    game = Game()
    layer = H.BackgroundLayer(game.read, game.serialize, lambda index: picture_file(tmp_path))
    frame = game.frame()
    try:
        layer.prepare(frame)
        generation = layer.generation
        game.set(H.MAP_TILE_DATA, "<I", 0x80060000)
        layer.prepare(frame)
        assert layer.generation > generation
    finally:
        layer.close()


def test_disappearing_picture_is_native_fallback_not_a_game_failure(tmp_path):
    game, errors = Game(), []
    layer = H.BackgroundLayer(game.read, game.serialize, lambda index: tmp_path / "deleted.png", on_error=errors.append)
    try:
        assert layer.prepare(game.frame()) is None
        assert len(errors) == 1 and "native" in errors[0]
    finally:
        layer.close()
