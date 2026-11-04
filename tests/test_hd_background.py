from __future__ import annotations

import struct

import numpy as np
import pytest
from PIL import Image

from dw1_workbench import hd_background as H
from dw1_workbench.hd_background import BackgroundLayer, Compositor, HdPictures, MapBackground, compose, rgb5
from dw1_workbench.live import CURRENT_SCREEN, ram_offset

WIDTH, HEIGHT, TILE_RAM = 5, 3, 0x80020000


def palette_bytes(shift=0):
    return b"".join(struct.pack("<H", min(i // 8 + shift, 31) | min(i // 8, 31) << 5 | min(31 - i // 8, 31) << 10)
                    for i in range(256))


class Game:
    """Enough RAM and VRAM for a 5 × 3 tile map; tile (column, row) has a recognisable pattern."""

    def __init__(self):
        self.ram = bytearray(0x200000)
        self.vram = bytearray(1024 * 512 * 2)
        self.set(H.MAP_SIZE, "<bb", WIDTH, HEIGHT)
        self.set(CURRENT_SCREEN, "<B", 7)
        self.set(H.CAMERA, "<hh", 100, 60)
        self.set(H.MAP_CLUTS, "<I", 0x80010000)
        self.ram[ram_offset(0x80010000):ram_offset(0x80010000) + 512] = palette_bytes()
        self.vram[480 * 1024 * 2:480 * 1024 * 2 + 512] = palette_bytes()
        self.tiles = {}
        for row in range(HEIGHT):
            for column in range(WIDTH):
                pointer = TILE_RAM + (row * WIDTH + column) * 16384
                u, v = 768 + (column % 4) * 64, (row % 3) * 128
                self.set(H.MAP_TILE_DATA + (row * WIDTH + column) * 20, "<IhhhhhHH", pointer, row * WIDTH + column,
                         column * 128, row * 128, u, v, 0, 0x7800)
                tile = (np.indices((128, 128)).sum(axis=0) * 3 + column * 40 + row * 80).astype(np.uint8)
                self.tiles[column, row] = tile
                self.ram[ram_offset(pointer):ram_offset(pointer) + 16384] = tile.tobytes()
                for line in range(128 if column < 4 else 0):  # the ring holds the first four columns
                    at = ((v + line) * 1024 + u) * 2
                    self.vram[at:at + 128] = tile[line].tobytes()

    def set(self, address, fmt, *values):
        struct.pack_into(fmt, self.ram, ram_offset(address), *values)

    def read(self, offset, size):
        return bytes(self.ram[offset:offset + size])

    def serialize(self):
        return b"junk" + H.GPURAM_FIELD + b"\0\0\0\0" + bytes(self.vram)

    def frame(self, camera=(100, 60), palette=None):
        """What the game draws, including renderMap's 0x7F V span on the middle ring row."""
        palette = rgb5(palette_bytes()) if palette is None else palette
        background = MapBackground(self.read)
        assert background.refresh()
        indices, _ = background.window(*camera)
        levels = palette[indices].astype(np.uint32)
        return levels[..., 0] << 19 | levels[..., 1] << 11 | levels[..., 2] << 3


@pytest.fixture
def game():
    return Game()


def picture_file(tmp_path, color=(200, 100, 50)):
    path = tmp_path / "hd.png"
    Image.new("RGB", (WIDTH * 128 * 2, HEIGHT * 128 * 2), color).save(path)
    return path


def rgb(pixel):
    return int(pixel) >> 16 & 255, int(pixel) >> 8 & 255, int(pixel) & 255


def test_tile_rows_follow_render_maps_uv_rule(game):
    background = MapBackground(game.read)
    assert background.refresh()
    indices, _ = background.window(0, 0)
    assert np.array_equal(indices[:128, :128], game.tiles[0, 0])  # v = 0: as stored
    indices, _ = background.window(0, 128)
    source = game.tiles[0, 1]
    assert np.array_equal(indices[:65, :128], source[:65])  # v = 128: 127 texels over 128 lines
    assert np.array_equal(indices[65:128, :128], source[64:127])
    double, _ = background.window(0, 128, 2)  # at 2× the same rounding falls on single lines
    assert double.shape == (480, 640)
    expected = [(line * 254 + 256) // 512 for line in range(256)]
    assert all(np.array_equal(double[line, ::2][:128], source[expected[line]]) for line in (0, 130, 131, 255))
    assert np.array_equal(background.alternate[131, ::2][:128], source[expected[131] - 1])


def test_background_comes_from_the_picture_and_the_rest_stays(game, tmp_path):
    path = picture_file(tmp_path)
    layer = BackgroundLayer(game.read, game.serialize, lambda index: path if index == 7 else None)
    frame = game.frame()
    frame[100:110, 50:60] = 0xFF00FF  # a character
    assert layer.apply(frame) is None  # the picture loads in the background; the frame is shown as it is
    layer._load_picture(layer._picture_key, path)
    result = layer.apply(frame)
    assert result.pixels.shape == (960, 1280)
    assert rgb(result.pixels[0, 0]) == (200, 100, 50)
    assert rgb(result.pixels[105 * 4, 55 * 4]) == (255, 0, 255)
    assert result.coverage == pytest.approx(1 - 100 / (320 * 240))


def test_upscaled_frames_and_uniform_shadow_and_fade(game, tmp_path):
    path = picture_file(tmp_path)
    layer = BackgroundLayer(game.read, game.serialize, lambda index: path)
    layer.apply(game.frame()); layer._load_picture(layer._picture_key, path)
    frame = game.frame()
    double = np.repeat(np.repeat(frame, 2, axis=0), 2, axis=1)
    assert rgb(layer.apply(double).pixels[0, 0]) == (200, 100, 50)

    shadow = frame.copy()  # darken every channel by 4 levels in a patch
    bright = (frame >> 19 & 31) >= 4
    bright &= (frame >> 11 & 31) >= 4
    bright &= (frame >> 3 & 31) >= 4
    patch = np.zeros_like(bright); patch[20:40, 20:40] = True
    shadow[patch & bright] -= 0x202020
    result = layer.apply(shadow)
    assert result.coverage > 0.99
    y, x = np.argwhere(patch & bright)[0]
    assert rgb(result.pixels[y * 4, x * 4]) == (200 - 32, 100 - 32, 50 - 32)

    # A fade over the whole screen is measured and applied to the picture, clamped at black like the game.
    palette = np.clip(rgb5(palette_bytes()) - 4, 0, 31)
    faded = game.frame(palette=palette)
    result = layer.apply(faded)
    assert result.coverage > 0.99
    assert rgb(result.pixels[0, 0]) == (200 - 32, 100 - 32, 50 - 32)


def test_time_of_day_tints_the_picture_and_recent_cameras_are_tried(game, tmp_path):
    path = picture_file(tmp_path)
    layer = BackgroundLayer(game.read, game.serialize, lambda index: path)
    layer.apply(game.frame()); layer._load_picture(layer._picture_key, path)
    moved = game.frame(camera=(100, 60))
    assert layer.apply(moved).coverage > 0.99  # the camera is remembered
    game.set(H.CAMERA, "<hh", 103, 61)  # RAM moves on before the frame is shown
    result = layer.apply(moved)
    assert result.coverage > 0.99

    game.set(H.CAMERA, "<hh", 100, 60)
    night = palette_bytes(shift=8)
    game.vram[480 * 1024 * 2:480 * 1024 * 2 + 512] = night
    layer._palette = None
    frame = game.frame(palette=rgb5(night))
    result = layer.apply(frame)
    red, green, blue = rgb(result.pixels[0, 0])
    assert red > 200 and green == 100 and blue == 50  # red channel raised by the fitted table
    assert result.coverage > 0.99


def test_frames_that_are_not_the_map_pass_through(game, tmp_path):
    path = picture_file(tmp_path)
    layer = BackgroundLayer(game.read, game.serialize, lambda index: path)
    layer.apply(game.frame()); layer._load_picture(layer._picture_key, path)
    assert layer.apply(np.zeros((224, 368), np.uint32)) is None  # another video mode
    menu = np.full((240, 320), 0x102030, np.uint32)
    result = layer.apply(menu)
    assert result.coverage < H.MIN_COVERAGE and result.pixels.shape == (240, 320)
    assert int(result.pixels[0, 0]) == 0xFF102030
    game.set(H.MAP_SIZE, "<bb", 0, 0)  # no map at all
    assert layer.apply(menu) is None


def test_battle_keeps_cached_tiles_and_a_state_loaded_in_battle_uses_vram(game, tmp_path):
    path = picture_file(tmp_path)
    frame = game.frame()
    layer = BackgroundLayer(game.read, game.serialize, lambda index: path)
    layer.apply(frame); layer._load_picture(layer._picture_key, path)
    game.set(H.GAME_STATE, "<h", 1)
    for row in range(HEIGHT):  # the battle code loads over the tiles in RAM
        for column in range(WIDTH):
            at = ram_offset(TILE_RAM + (row * WIDTH + column) * 16384)
            game.ram[at:at + 16384] = bytes(16384)
    assert layer.apply(frame).coverage > 0.99  # cached tiles still match

    layer.reset()  # a state loaded during the battle: nothing cached, RAM is useless
    game.set(H.MAP_TILE_X, "<b", 0); game.set(H.MAP_TILE_Y, "<b", 0)
    assert layer.apply(frame).coverage > 0.99
    assert layer.background.from_vram

    # After the battle the game has reloaded the tiles; they are read from RAM again.
    game.set(H.GAME_STATE, "<h", 0)
    for (column, row), tile in game.tiles.items():
        at = ram_offset(TILE_RAM + (row * WIDTH + column) * 16384)
        game.ram[at:at + 16384] = tile.tobytes()
    assert layer.apply(frame).coverage > 0.99
    assert not layer.background.from_vram


def test_map_smaller_than_the_screen_is_padded(game, tmp_path):
    background = MapBackground(game.read)
    background.refresh()
    indices, valid = background.window(-50, -10)
    assert not valid[0, 0] and valid[10, 50] and indices[10, 50] == background.indices[0, 0]
    window = H.picture_window(np.full((512, 768), 7, np.uint32), -50, -10, 1)
    assert window.shape == (240, 320) and window[10, 50] == 7 and window[0, 0] == 0xFF000000


def test_compositor_delivers_the_newest_frame_and_reports_errors(game, tmp_path):
    import threading
    path = picture_file(tmp_path)
    layer = BackgroundLayer(game.read, game.serialize, lambda index: path)
    layer.apply(game.frame()); layer._load_picture(layer._picture_key, path)
    done = threading.Event(); results = []
    compositor = Compositor(layer, lambda composed: (results.append(composed), done.set()), results.append)
    compositor.submit(layer.prepare(game.frame()))
    assert done.wait(5)
    compositor.stop()
    assert results[0].pixels.shape == (960, 1280)


def test_pictures_store_replaces_and_converts(tmp_path):
    from io import BytesIO
    store = HdPictures(tmp_path / "hd")
    assert store.path(5) is None and store.maps() == []
    png = BytesIO(); Image.new("RGB", (8, 8)).save(png, "PNG")
    first = store.store(5, "MAYO 00", png.getvalue())
    assert first.name == "005-MAYO_00.png" and first.read_bytes() == png.getvalue() and store.maps() == [5]
    jpeg = BytesIO(); Image.new("RGB", (8, 8)).save(jpeg, "JPEG")
    second = store.store(5, "MAYO00", jpeg.getvalue())
    assert store.path(5) == second and not first.exists()
    assert Image.open(second).format == "PNG"
    with pytest.raises(Exception):
        store.store(6, "BAD", b"not a picture")
    assert store.path(6) is None
    store.remove(5)
    assert store.path(5) is None
