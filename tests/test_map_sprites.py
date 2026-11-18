import struct

from PIL import Image

from dw1_workbench.map_sprites import compose, map_vram, piece_image
from dw1_workbench.maps import MapScene, SpriteInstance, SpriteObject, TfsFile


def tim(flags, clut_rect, clut_words, image_rect, image_words):
    data = struct.pack("<II", 0x10, flags)
    if clut_rect:
        data += struct.pack("<I4H", 12 + 2 * len(clut_words), *clut_rect) + struct.pack(f"<{len(clut_words)}H", *clut_words)
    data += struct.pack("<I4H", 12 + 2 * len(image_words), *image_rect) + struct.pack(f"<{len(image_words)}H", *image_words)
    return data


def scene_with(objects, instances, image_offsets=(), object_image_offsets=()):
    return MapScene(None, tuple(image_offsets), tuple(object_image_offsets), tuple(objects), tuple(instances),
                    (), (), 0, (), b"")


RED, BLUE, GREEN = 0x001F, 0x7C00, 0x03E0


def four_bit_map():
    """A MAP holding one 4-bit TIM at 640,0 (texture page 4 of the pieces): texels 0, 1, 2, 3 in a 4 x 1 row, and
    two palette rows, which loadMapImage2 puts along row 486."""
    first_row = [0, RED, BLUE | 0x8000, GREEN] + [0] * 12
    second_row = [0, GREEN, GREEN, GREEN] + [0] * 12
    return b"\0" * 16 + tim(8 | 0, (0, 300, 16, 2), first_row + second_row, (640, 0, 1, 1), [0x3210])


def test_four_bit_piece_reads_the_first_tims_palettes_from_row_486():
    data = four_bit_map()
    scene = scene_with([SpriteObject((1024, 0), (4, 1), (0, 0, 0), 0, 4), SpriteObject((1024, 0), (4, 1), (0, 0, 0), 1, 4)],
                       [], object_image_offsets=[16])
    vram = map_vram(scene, data, None)
    first, second = (piece_image(vram, piece) for piece in scene.objects)
    assert first.getpixel((0, 0))[3] == 0  # colour 0 is see-through
    assert first.getpixel((1, 0)) == (255, 0, 0, 255)
    assert first.getpixel((2, 0)) == (0, 0, 255, 255)
    assert second.getpixel((3, 0)) == (0, 255, 0, 255)
    assert vram[0, 300] == 0  # the palette went to row 486, not where the TIM says


def test_eight_bit_piece_without_palette_uses_the_time_of_days_tfs_palette():
    data = b"\0" * 8 + tim(9, (0, 480, 4, 1), [1, 2, 3, 4], (384, 0, 1, 1), [0x0201])
    scene = scene_with([SpriteObject((0, 0), (2, 1), (0, 0, 0), 0xFFFF, 4)], [], image_offsets=[8])
    tfs = TfsFile(0, 0, ((0, RED, BLUE) + (0,) * 253, (0, GREEN, GREEN) + (0,) * 253), (), b"")
    day = piece_image(map_vram(scene, data, tfs, 0), scene.objects[0])
    night = piece_image(map_vram(scene, data, tfs, 1), scene.objects[0])
    assert day.getpixel((0, 0)) == (255, 0, 0, 255) and day.getpixel((1, 0)) == (0, 0, 255, 255)
    assert night.getpixel((0, 0)) == (0, 255, 0, 255)  # its row 480 palette was left alone, as the game does


def test_compose_shows_script_pieces_only_on_request_and_blends_top_bit_texels():
    data = four_bit_map()
    objects = [SpriteObject((1024, 0), (4, 1), (0, 0, 0), 0, 1)]  # additive where the texel's top bit is set
    instances = [SpriteInstance(((0, 1),) + ((-1, 0),) * 7, (0, 0), 0),
                 SpriteInstance(((0, 1),) + ((-1, 0),) * 7, (0, 1), 1)]  # waits for a script
    scene = scene_with(objects, instances, object_image_offsets=[16])
    vram = map_vram(scene, data, None)
    background = Image.new("RGB", (4, 2), (16, 16, 16))
    start, every = compose(background, vram, scene, False), compose(background, vram, scene, True)
    assert start.getpixel((0, 0)) == (16, 16, 16)
    assert start.getpixel((1, 0)) == (255, 0, 0)  # no top bit: solid
    assert start.getpixel((2, 0)) == (16, 16, 255)  # added to what is under it
    assert start.getpixel((1, 1)) == (16, 16, 16) and every.getpixel((1, 1)) == (255, 0, 0)
