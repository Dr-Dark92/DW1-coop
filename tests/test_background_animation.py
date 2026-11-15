from io import BytesIO
import struct
import threading
import time

import numpy as np
import pytest
from PIL import Image, features

from dw1_workbench import background_animation as A
from dw1_workbench import hd_background as H
from dw1_workbench.errors import Cancelled
from dw1_workbench.telemetry import Telemetry
from test_hd_background import Game

pytestmark = pytest.mark.skipif(not features.check("webp"), reason="Pillow requires WebP codec support")


def clip(loop=0, durations=(40, 80, 120)):
    images = [Image.new("RGB", (32, 32), color) for color in ("red", "blue", "lime")]
    output = BytesIO()
    images[0].save(output, "WEBP", save_all=True, append_images=images[1:], lossless=True,
                   duration=list(durations), loop=loop)
    return output.getvalue()


def until(function):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = function()
        if result: return result
        time.sleep(0.005)
    raise AssertionError("Background worker did not finish")


def test_webp_store_preserves_frames_timing_and_bytes(tmp_path):
    data = clip(durations=(16, 17, 0))
    store = H.HdPictures(tmp_path)
    path = store.store(204, "TWNA01", data)
    assert path.suffix == ".webp" and path.read_bytes() == data
    animation = A.webp_animation(data)
    assert animation.durations == (16, 17, 10) and animation.loops == 0
    with Image.open(path) as image: assert image.n_frames == 3


def test_timeline_loops_finite_hold_and_exact_boundaries():
    animation = A.AnimationInfo((40, 80, 120), 0)
    assert [animation.frame_at(t) for t in (-1, 0, 39, 40, 119, 120, 239, 240, 280)] == [0, 0, 0, 1, 1, 2, 2, 0, 1]
    finite = A.AnimationInfo((40, 80, 120), 2)
    assert finite.frame_at(480) == finite.frame_at(10000) == 2
    with pytest.raises(ValueError): A.AnimationInfo((), 0)


def test_malformed_chunk_and_frame_limit_rejected(tmp_path, monkeypatch):
    data = bytearray(clip())
    data[4:8] = struct.pack("<I", len(data))
    with pytest.raises(ValueError, match="RIFF"): A.webp_animation(bytes(data))
    data = bytearray(clip()); data[16:20] = struct.pack("<I", len(data))
    with pytest.raises(ValueError, match="exceeds"): A.webp_animation(bytes(data))
    monkeypatch.setattr(A, "MAX_FRAMES", 2)
    with pytest.raises(ValueError, match="600 frames"): A.webp_animation(clip())


def test_known_pixels_random_seek_and_visible_window(tmp_path):
    path = tmp_path / "test.webp"; path.write_bytes(clip())
    decoder = A.BackgroundDecoder()
    try:
        for index, color in ((0, (255, 0, 0, 255)), (2, (0, 255, 0, 255)), (1, (0, 0, 255, 255)), (0, (255, 0, 0, 255))):
            frame = decoder.load(path, 1, 1, 4, index)
            assert frame.image.getpixel((0, 0)) == color
            window = frame.window(0, 0, 1)
            assert window.shape == (240, 320)
            assert int(window[10, 10]) == color[3] << 24 | color[0] << 16 | color[1] << 8 | color[2]
            assert frame.window(600, 600, 1)[-1, -1] == 0  # transparent outside the source map
            assert frame.nbytes == 32 * 32 * 4  # source canvas, NOT resampled whole-map size
    finally: decoder.close()


def test_alpha_and_disposal_match_composited_expected_canvas(tmp_path):
    frames = [Image.new("RGBA", (32, 32), (0, 0, 0, 0)) for _ in range(3)]
    frames[0].paste((255, 0, 0, 255), (0, 0, 8, 8))
    frames[1].paste((0, 0, 255, 128), (10, 10, 20, 20))
    frames[2].paste((0, 255, 0, 255), (20, 20, 30, 30))
    path = tmp_path / "alpha.webp"
    frames[0].save(path, save_all=True, append_images=frames[1:], lossless=True, duration=40, loop=0)
    decoder = A.BackgroundDecoder()
    try:
        for index in (0, 2, 1):
            frame = decoder.load(path, 1, 1, 1, index)
            assert np.array_equal(np.asarray(frame.image), np.asarray(frames[index]))
    finally: decoder.close()


def test_async_frames_follow_clock_reset_and_cache_bounds(tmp_path):
    path = tmp_path / "clip.webp"; path.write_bytes(clip())
    clock = [0.0]
    layer = H.BackgroundLayer(lambda *a: b"", None, lambda index: path, scale=1, clock=lambda: clock[0])
    layer.background.width = layer.background.height = 1
    caller = threading.get_ident(); threads = []
    real = layer.picture_cache.loader
    layer.picture_cache.loader = lambda *args: (threads.append(threading.get_ident()), real(*args))[1]
    try:
        until(lambda: layer._ready_picture(204))
        assert layer._picture.index == 0
        clock[0] = 0.05
        until(lambda: layer._ready_picture(204) and layer._picture.index == 1)
        for _ in range(5): layer._ready_picture(204)
        assert layer._picture.index == 1  # frozen injected clock, independent of wall time
        clock[0] = 0.13
        until(lambda: layer._ready_picture(204) and layer._picture.index == 2)
        assert layer.picture_cache.size_bytes <= layer.picture_cache.limit
        assert all(thread != caller for thread in threads)
        until(lambda: layer._ready_picture(205))
        assert layer._picture.index == 0  # new map, same filename: phase restarts
        layer.reset(); layer.background.width = layer.background.height = 1
        until(lambda: layer._ready_picture(205))
        assert layer._picture.index == 0
    finally:
        layer.close()
    assert not layer.picture_cache._thread.is_alive()


def test_alpha_composition_preserves_character_and_native_underlay(tmp_path):
    game = Game()
    frame = game.frame(); frame[80:100, 100:120] = 0xFF00FF
    layer = H.BackgroundLayer(game.read, game.serialize, lambda index: None, scale=1)
    try:
        # Use the existing proven map matcher; only its replacement canvas has alpha.
        assert layer.background.refresh()
        layer._palette = H.rgb5(bytes(game.vram[480 * 2048:480 * 2048 + 512]))
        view = layer.background.view(100, 60, 1)
        pixels = np.zeros((240, 320), np.uint32)  # completely transparent
        native = H.compose(frame, layer._palette, H.MapLayers(layer.background.indices, layer.background.valid, None), view, lambda shift: pixels)
        assert np.array_equal(native.pixels, frame | 0xFF000000)
        pixels[:] = 0x800000FF
        result = H.compose(frame, layer._palette, H.MapLayers(layer.background.indices, layer.background.valid, None), view, lambda shift: pixels)
        assert result.pixels[90, 110] == 0xFFFF00FF  # character is never replaced
        assert not np.array_equal(result.pixels, native.pixels)
    finally: layer.close()


def test_import_cancel_and_failed_write_preserve_existing_override(tmp_path, monkeypatch):
    store = H.HdPictures(tmp_path)
    old = store.store(204, "old", clip())
    original = old.read_bytes()
    with pytest.raises(Cancelled): store.store(204, "new", clip(), lambda: True)
    assert old.read_bytes() == original
    def fail(*args): raise OSError("simulated write failure")
    monkeypatch.setattr(H, "_atomic_write", fail)
    with pytest.raises(OSError): store.store(204, "new", clip())
    assert old.read_bytes() == original and store.path(204) == old


def test_decoder_limits_and_cancellation(tmp_path, monkeypatch):
    path = tmp_path / "clip.webp"; path.write_bytes(clip())
    decoder = A.BackgroundDecoder(lambda: True)
    try:
        with pytest.raises(ValueError, match="cancelled"): decoder.load(path, 1, 1, 1)
    finally: decoder.close()
    monkeypatch.setattr(A, "MAX_ANIMATION_PIXELS", 100)
    decoder = A.BackgroundDecoder()
    try:
        with pytest.raises(ValueError, match="dimensions"): decoder.load(path, 1, 1, 1)
    finally: decoder.close()


def test_frame_lru_is_byte_bounded_not_whole_clip_decoding(tmp_path):
    path = tmp_path / "clip.webp"; path.write_bytes(clip())
    cache = H.PictureCache(Telemetry(), pytest.fail, limit=2 * 32 * 32 * 4)
    key = (204, 1, 1, 1, str(path))
    try:
        first = until(lambda: cache.request(key, path, 1, 1, 1))
        assert isinstance(first, A.AnimatedFrame) and first.index == 0
        until(lambda: cache.request((key, 1), path, 1, 1, 1, 1))
        until(lambda: cache.request((key, 2), path, 1, 1, 1, 2))
        assert cache.size_bytes == cache.limit and len(cache._items) == 2
        assert cache.latest(key).index == 2
    finally: cache.close()


def test_later_decode_failure_invalidates_clip_instead_of_freezing_old_frame(tmp_path):
    path = tmp_path / "clip.webp"; path.write_bytes(clip())
    clock, errors = [0.0], []
    layer = H.BackgroundLayer(lambda *args: b"", None, lambda index: path, scale=1,
                              clock=lambda: clock[0], on_error=errors.append)
    layer.background.width = layer.background.height = 1
    try:
        until(lambda: layer._ready_picture(204))
        real = layer.picture_cache.loader
        def broken(*args):
            if layer.picture_cache._decode_index == 1: raise ValueError("corrupt second frame")
            return real(*args)
        layer.picture_cache.loader = broken
        clock[0] = 0.05
        until(lambda: (layer._ready_picture(204), errors)[1])
        assert not layer._ready_picture(204) and layer._picture is None
        assert "corrupt second frame" in errors[0]
    finally: layer.close()


def test_background_import_worker_cancellation_preserves_prior_image(tmp_path):
    from dw1_workbench.gui import HdPictureWorker
    store = H.HdPictures(tmp_path / "hd")
    old = store.store(204, "old", clip())
    source = tmp_path / "new.webp"; source.write_bytes(clip())
    worker = HdPictureWorker(store, 204, "new", source)
    results = []
    worker.cancelled.connect(results.append)
    worker.request_cancel(); worker.run()
    assert results and store.path(204) == old and old.exists()


def test_cache_completion_is_not_lru_access_or_previous_loop(tmp_path):
    path = tmp_path / "clip.webp"; path.write_bytes(clip())
    cache = H.PictureCache(Telemetry(), pytest.fail)
    key = (204, 1, 1, 1, str(path))
    try:
        until(lambda: cache.request(key, path, 1, 1, 1))
        until(lambda: cache.request((key, 1), path, 1, 1, 1, 1, 0))
        until(lambda: cache.request((key, 2), path, 1, 1, 1, 2, 0))
        cache.request((key, 1), path, 1, 1, 1, 1, 0)  # access old cached frame
        assert cache.latest(key, 0).index == 2
        assert cache.latest(key, 1) is None  # old loop's late result is not a new loop frame
    finally: cache.close()


def test_opaque_full_canvas_vp8_shortcut_matches_sequential_decoder(tmp_path):
    frames = [Image.new("RGB", (32, 32), c) for c in ("red", "blue", "lime")]
    path = tmp_path / "opaque.webp"
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=50, loop=0, lossless=False)
    decoder = A.BackgroundDecoder()
    try:
        with Image.open(path) as reference:
            for index in (0, 2, 1):
                actual = decoder.load(path, 1, 1, 1, index)
                reference.seek(index); reference.load()
                assert np.array_equal(np.asarray(actual.image), np.asarray(reference.convert("RGBA")))
        assert decoder.independent is not None
    finally: decoder.close()
