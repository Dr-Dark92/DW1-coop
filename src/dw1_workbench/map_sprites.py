"""A map's sprite pieces: the pictures the game draws over its TFS background (signs, doors, the Whamon dock, a
waterfall's frames), from the TIMs embedded in its MAP file.

The game loads them as overworld.c does (USA, dw_decomp src/main/overworld.c and map.c): each 8-bit TIM's picture
where the TIM says and its palette there too unless that is row 480; each 4-bit TIM's picture likewise, and the first
4-bit TIM's palette rows as 16-colour palettes along row 486. The TFS palettes go to rows 481 onwards and row 480
holds the one for the time of day. A piece (SpriteObject) then reads texture page (u // 256) of x 384 onwards:
palette -1 is 8-bit through row 480, 0-15 is 4-bit through row 486 at x = palette * 16, anything else is 8-bit
through row palette + 468 (buildMapOverlayPrim).

renderMapOverlays draws an instance's current frame where it stands on the background (tiles are at x * 128,
y * 128, so instance positions are background pixels) unless its flag is 1: those wait for a script to show them
(the Whamon dock, a recruit's building). Snowflakes (flag 0x80) move about and are left out. A piece whose
transparency isn't 4 is semi-transparent: its texels with the top bit set blend with what is under them in the
PlayStation's mode for that number, the rest are solid.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

from PIL import Image

from .maps import MapScene, SpriteObject, TfsFile

TIM_MAGIC = 0x10
HIDDEN = 1       # the instance waits for a script
SNOWFLAKE = 0x80
OPAQUE = 4       # transparency value that draws without blending


@dataclass
class Vram:
    """The 1024 x 512 halfwords the pieces read from."""
    words: list[int]

    def __getitem__(self, xy: tuple[int, int]) -> int:
        x, y = xy
        return self.words[(y & 511) * 1024 + (x & 1023)]

    def put(self, x: int, y: int, value: int) -> None:
        self.words[(y & 511) * 1024 + (x & 1023)] = value


def _load_tim(vram: Vram, data: bytes, offset: int, four_bit_index: int | None) -> None:
    if offset + 8 > len(data) or struct.unpack_from("<I", data, offset)[0] != TIM_MAGIC:
        raise ValueError(f"No TIM at 0x{offset:x}.")
    flags = struct.unpack_from("<I", data, offset + 4)[0]
    at = offset + 8
    if flags & 8:
        length = struct.unpack_from("<I", data, at)[0]
        x, y, w, h = struct.unpack_from("<4H", data, at + 4)
        words = struct.unpack_from(f"<{w * h}H", data, at + 12)
        if four_bit_index is None:
            if y != 480:  # loadMapImage1 leaves the time-of-day row alone
                for row in range(h):
                    for column in range(w):
                        vram.put(x + column, y + row, words[row * w + column])
        elif four_bit_index == 0:  # loadMapImage2: each palette row becomes 16 colours along row 486
            for row in range(h):
                for column in range(min(16, w)):
                    vram.put(row * 16 + column, 486, words[row * w + column])
        at += length
    length = struct.unpack_from("<I", data, at)[0]
    x, y, w, h = struct.unpack_from("<4H", data, at + 4)
    words = struct.unpack_from(f"<{w * h}H", data, at + 12)
    for row in range(h):
        for column in range(w):
            vram.put(x + column, y + row, words[row * w + column])


def map_vram(scene: MapScene, map_bytes: bytes, tfs: TfsFile | None, palette: int = 0) -> Vram:
    """Video memory as the game has it once the map is loaded, with TFS palette `palette` as the time of day."""
    vram = Vram([0] * (1024 * 512))
    if tfs is not None:
        for index, colours in enumerate(tfs.palettes):
            for column, colour in enumerate(colours):
                vram.put(column, 481 + index, colour)
        if tfs.palettes:
            for column, colour in enumerate(tfs.palettes[min(palette, len(tfs.palettes) - 1)]):
                vram.put(column, 480, colour)
    for offset in scene.image_offsets:
        _load_tim(vram, map_bytes, offset, None)
    for index, offset in enumerate(scene.object_image_offsets):
        _load_tim(vram, map_bytes, offset, index)
    return vram


def _signed(value: int) -> int:
    return value - 0x10000 if value >= 0x8000 else value


def piece_words(vram: Vram, piece: SpriteObject) -> list[list[int]]:
    """A piece's colours as the GPU reads them (0: see-through), row by row."""
    (u0, v0), (width, height) = piece.uv, piece.size
    clut, page = _signed(piece.clut), u0 // 256
    rows = []
    for dy in range(height):
        y, row = (v0 % 256) + dy, []
        for dx in range(width):
            u = (u0 % 256) + dx
            if 0 <= clut < 16:
                word = vram[(page << 6) + 384 + (u >> 2), y]
                row.append(vram[(clut << 4) + ((word >> ((u & 3) * 4)) & 15), 486])
            else:
                word = vram[(page << 7) + 384 + (u >> 1), y]
                row.append(vram[(word >> ((u & 1) * 8)) & 255, 480 if clut == -1 else clut + 468])
        rows.append(row)
    return rows


def _rgb(word: int) -> tuple[int, int, int]:
    r, g, b = word & 31, (word >> 5) & 31, (word >> 10) & 31
    return (r << 3 | r >> 2, g << 3 | g >> 2, b << 3 | b >> 2)


def piece_image(vram: Vram, piece: SpriteObject) -> Image.Image:
    """A piece on its own: its colours, see-through where the GPU draws nothing."""
    rows = piece_words(vram, piece)
    image = Image.new("RGBA", piece.size)
    image.putdata([(*_rgb(word), 255) if word else (0, 0, 0, 0) for row in rows for word in row])
    return image


def _blend(under: tuple[int, int, int], over: tuple[int, int, int], mode: int) -> tuple[int, int, int]:
    if mode == 0:
        return tuple((a + b) // 2 for a, b in zip(under, over))
    if mode == 1:
        return tuple(min(255, a + b) for a, b in zip(under, over))
    if mode == 2:
        return tuple(max(0, a - b) for a, b in zip(under, over))
    return tuple(min(255, a + b // 4) for a, b in zip(under, over))


def compose(background: Image.Image, vram: Vram, scene: MapScene, with_hidden: bool,
            flags: dict[int, int] | None = None) -> Image.Image:
    """The background with each instance's first frame drawn on it, in instance order, as renderMapOverlays does;
    with_hidden also draws the instances that wait for a script. `flags` replaces instances' flags as the map's
    scripts set them (map_scripts.simulate)."""
    image = background.convert("RGB")
    pixels = image.load()
    for number, instance in enumerate(scene.instances):
        frame = instance.frames[0][0] if instance.frames else -1
        if frame < 0 or frame >= len(scene.objects) or instance.flag & SNOWFLAKE:
            continue
        flag = (flags or {}).get(number, instance.flag)
        if flag == HIDDEN and not with_hidden:
            continue
        piece = scene.objects[frame]
        blended = piece.transparency != OPAQUE
        x0, y0 = instance.position
        for dy, row in enumerate(piece_words(vram, piece)):
            for dx, word in enumerate(row):
                x, y = x0 + dx, y0 + dy
                if not word or not (0 <= x < image.width and 0 <= y < image.height):
                    continue
                colour = _rgb(word)
                pixels[x, y] = _blend(pixels[x, y], colour, piece.transparency) if blended and word & 0x8000 else colour
    return image
