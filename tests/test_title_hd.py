import struct

import numpy as np
from PIL import Image

from dw1_workbench import title_hd as H
from dw1_workbench import title_screens as T
from dw1_workbench.tim import decode_tim


def screen_picture():
    rows = np.linspace(40, 230, 240, dtype=np.uint8)[:, None]
    base = np.zeros((240, 320, 3), np.uint8)
    base[..., 0] = rows; base[..., 1] = 255 - rows
    base[..., 2] = np.linspace(20, 200, 320, dtype=np.uint8)[None, :]
    return base


def layer(tmp_path, picture=(250, 10, 10)):
    hd = Image.new("RGB", (1280, 960), picture)
    path = tmp_path / "hd.png"; hd.save(path)
    return H.TitleLayer([H.TitlePicture(screen_picture().astype(np.float32), path)])


def as_frame(rgb, factor=2, fade=1.0):
    scaled = (np.repeat(np.repeat(rgb, factor, axis=0), factor, axis=1) * fade).astype(np.uint32)
    return scaled[..., 0] << 16 | scaled[..., 1] << 8 | scaled[..., 2]


def test_a_title_frame_becomes_the_full_picture_and_what_is_on_top_stays(tmp_path):
    title = layer(tmp_path)
    rgb = screen_picture(); rgb[100:120, 100:200] = (0, 255, 0)
    job = title.prepare(as_frame(rgb))
    assert job is not None
    out = title.compose(job).pixels
    assert out.shape == (960, 1280)
    assert out[10, 10] == 0xFFFA0A0A  # the picture
    assert out[110 * 4, 150 * 4] == 0xFF00FF00  # the game's own


def test_a_fade_is_followed(tmp_path):
    title = layer(tmp_path)
    job = title.prepare(as_frame(screen_picture(), fade=0.5))
    assert job is not None and abs(job[2] - 0.5) < 0.03
    assert (title.compose(job).pixels[10, 10] >> 16) & 255 in range(115, 135)


def test_other_frames_and_sizes_are_left_alone(tmp_path):
    title = layer(tmp_path)
    assert title.prepare(as_frame(np.zeros((240, 320, 3), np.uint8))) is None
    rng = np.random.default_rng(1)
    assert title.prepare(as_frame(rng.integers(0, 255, (240, 320, 3), dtype=np.uint8))) is None
    assert title.prepare(np.zeros((480, 512), np.uint32)) is None


def test_lone_mismatches_are_rounding_but_blocks_are_not():
    match = np.ones((20, 20), bool)
    match[5, 5] = False
    match[10:14, 10:14] = False
    clean = H._without_specks(match)
    assert clean[5, 5] and not clean[11, 11]


def test_each_game_resolution_composes(tmp_path):
    title = layer(tmp_path)
    for factor in (1, 2, 4):
        job = title.prepare(as_frame(screen_picture(), factor))
        assert job is not None and title.compose(job).pixels.shape == (960, 1280)


def test_store_needs_the_full_picture(tmp_path):
    store = T.TitleStore(tmp_path)
    assert H.TitleLayer.from_store(store) is None
    width, height = 320, 240
    palette = [0x7FFF] * 256
    tim = (struct.pack("<2I", 0x10, 9) + struct.pack("<I4H", 524, 0, 480, 256, 1) + struct.pack("<256H", *palette)
           + struct.pack("<I4H", 12 + width * height, 768, 0, width // 2, height) + bytes(width * height))
    store.set(T.TITLE_SCREENS[1], tim)
    assert H.TitleLayer.from_store(store) is None
    store.set(T.TITLE_SCREENS[1], tim, Image.new("RGB", (640, 480)))
    loaded = H.TitleLayer.from_store(store)
    assert len(loaded.pictures) == 1 and loaded.pictures[0].expected.shape == (240, 320, 3)
    store.remove(T.TITLE_SCREENS[1])
    assert not store.picture_path(T.TITLE_SCREENS[1]).exists()


def original_256():
    pixels = bytes(320 * 256)
    return (struct.pack("<2I", 0x10, 9) + struct.pack("<I4H", 524, 0, 480, 256, 1) + struct.pack("<256H", *([0x7FFF] * 256))
            + struct.pack("<I4H", 12 + len(pixels), 768, 0, 160, 256) + pixels)


def test_an_upscaled_picture_keeps_every_row_where_it_was():
    """1402 x 1122 is 320 x 256 give or take rounding: it must not be cropped as some other shape."""
    picture = Image.new("RGB", (1402, 1122), (0, 0, 200))
    picture.paste((200, 0, 0), (0, 0, 1402, 44))  # the top 10 rows of 256
    picture.paste((0, 200, 0), (0, 1100, 1402, 1122))  # the last 5
    image = decode_tim(T.encode_title(original_256(), picture)).image.convert("RGB")
    assert image.getpixel((100, 2))[0] > 150 and image.getpixel((100, 254))[1] > 150
    shown = T.shown_part(picture, original_256())
    assert shown.size == (1402, 1052) and shown.getpixel((5, 5))[0] > 150


def test_the_shown_part_of_another_shape_is_the_middle():
    picture = Image.new("RGB", (800, 240), (0, 0, 200))
    assert T.shown_part(picture, original_256()).size == (320, 240)
