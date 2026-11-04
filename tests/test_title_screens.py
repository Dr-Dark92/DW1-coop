import struct

import pytest
from PIL import Image

from dw1_workbench import title_screens as T
from dw1_workbench.tim import decode_tim


def original(height=8, width=8, colours=4):
    """An 8-bit TIM; its lowest two rows are the text the game draws from them."""
    palette = [0x7FFF, 0x001F, 0x03E0, 0x8000] + [0x7C00] * 252
    pixels = bytearray(width * height)
    pixels[width * (height - 2):] = bytes([1]) * (width * 2)
    return (struct.pack("<2I", 0x10, 9) + struct.pack("<I4H", 12 + 512, 0, 480, 256, 1) + struct.pack("<256H", *palette)
            + struct.pack("<I4H", 12 + len(pixels), 768, 0, width // 2, height) + bytes(pixels))


def wide(width, height, colour=(0, 200, 0)):
    return Image.new("RGB", (width, height), colour)


def rows(tim):
    image = decode_tim(tim).image.convert("RGB")
    return image, tim[T.PIXELS_AT:]


def test_size_stays_and_only_the_palette_and_pixels_change():
    disc = original()
    tim = T.encode_title(disc, wide(8, 8))
    assert len(tim) == len(disc) and tim[:T.PALETTE_AT] == disc[:T.PALETTE_AT]
    assert T.tim_size(tim) == (8, 8)


def test_a_picture_of_the_whole_shape_replaces_every_row():
    image, _ = rows(T.encode_title(original(), wide(8, 8)))
    assert image.getpixel((0, 7)) == image.getpixel((0, 0)) and image.getpixel((0, 7))[1] > 150


def test_a_shorter_picture_keeps_the_hidden_rows():
    disc = original(height=256, width=320)
    image, _ = rows(T.encode_title(disc, wide(640, 480)))
    own = decode_tim(disc).image.convert("RGB")
    assert image.getpixel((0, 0))[1] > 150
    assert image.getpixel((5, 250)) == own.getpixel((5, 250))


def test_other_shapes_fill_and_crop_equally():
    source = wide(400, 240, (0, 0, 200))
    source.paste((200, 0, 0), (0, 0, 40, 240))  # beyond the 320 kept from the centre
    source.paste((200, 0, 0), (360, 0, 400, 240))
    image, _ = rows(T.encode_title(original(height=240, width=320), source))
    assert image.getpixel((0, 100))[2] > 150 and image.getpixel((319, 100))[2] > 150


def test_black_is_never_the_see_through_colour():
    tim = T.encode_title(original(), wide(8, 8, (0, 0, 0)))
    words = struct.unpack_from("<256H", tim, T.PALETTE_AT)
    assert 0 not in words and words[0] == T.OPAQUE_BLACK


def test_see_through_parts_of_a_picture_become_black():
    picture = Image.new("RGBA", (8, 8), (255, 0, 0, 0))
    image, _ = rows(T.encode_title(original(), picture))
    assert image.getpixel((3, 3))[:3] == (0, 0, 0)


def test_many_colours_are_reduced_to_the_palette():
    picture = Image.effect_noise((320, 240), 90).convert("RGB")
    tim = T.encode_title(original(height=240, width=320), picture)
    assert len(tim) == len(original(height=240, width=320))
    assert len(decode_tim(tim).image.convert("RGB").getcolors(100000)) <= 256


def test_a_picture_with_few_colours_comes_back_exact():
    disc = original(height=240, width=320)
    picture = wide(320, 240)
    picture.paste((248, 16, 0), (10, 10, 50, 50))
    image, _ = rows(T.encode_title(disc, picture))
    assert image.getpixel((20, 20))[0] >= 248 and image.getpixel((20, 20))[1] in (16, 17)


def test_store_holds_only_replaced_pictures(tmp_path):
    store = T.TitleStore(tmp_path)
    first, second = T.TITLE_SCREENS
    assert store.count() == 0 and store.replacements() == {} and store.get(first) is None
    store.set(first, original())
    assert store.count() == 1 and store.replacements() == {"ETCNA/TITLE256.TIM": original()}
    store.remove(first); store.remove(second)
    assert store.count() == 0


def test_anything_else_is_refused(tmp_path):
    with pytest.raises(ValueError):
        T.tim_size(b"\0" * 64)
    with pytest.raises(ValueError):
        T.TitleStore(tmp_path).set(T.TITLE_SCREENS[0], original()[:600])
