"""Turning an edited background picture back into a TFS file the game can load.

The PS1 draws backgrounds as 128×128 tiles of 8-bit indices into 256-colour palettes of 15-bit colours, and a
map may carry several palettes (other times of day) over the same indices. An edited picture is scaled to the
map's native size, fitted to a new 256-colour palette at 5-bit precision (one colour word reserved for
transparency where the original was transparent), and written into the existing tiles, so the TFS keeps its
size. Each other palette is re-tinted by the per-channel linear map that best turns the original first palette
into it (least squares over the colours the background uses). Detail beyond the native resolution and 256
colours cannot be shown by the game.

Maps also have a foreground layer drawn in front of the characters (bushes, branches, water): sprite objects cut
from 4- and 8-bit images embedded in the MAP file (dw_decomp src/main/overworld.c buildMapOverlayPrim and
loadMapImage1/2). Objects whose clut is -1 use the background's palette, so a new palette would recolour them and
turn their transparent texels opaque; the others use their own palette (a 4-bit CLUT row at y 486, or an 8-bit
CLUT at y clut + 468). Static pieces drawn at one place are repainted from the edited picture where the game
draws them, keeping their transparency (background-palette pieces into the new palette, the others into their
own palette, which stays as it is). Animated pieces, snow and mist particles, and pieces drawn at several places
keep their art; leftover background-palette texels keep their nearest colour. The MAP keeps its size.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

from PIL import Image

from .maps import EMPTY_TILE, TFS_PALETTE_SIZE, TFS_TILE, MapSetup, TfsFile, background_color, render_background

TRANSPARENT = 0
OPAQUE_BLACK = 0x8000  # a non-zero word with STP set: drawn black, not transparent


@dataclass(frozen=True)
class BackgroundResult:
    tfs: bytes
    notes: tuple[str, ...]
    map: bytes | None = None  # the MAP with its foreground images repainted, when it has any


BACKGROUND_CLUT = 0xFFFF  # SpriteObject.clut -1: drawn with the background palette (VRAM row 480)
PAGE_X = 384  # VRAM x (16-bit units) of foreground texture page 0
FOUR_BIT_CLUT_Y = 486  # loadMapImage2: the first 4-bit image's CLUT rows go to (16·row, 486)
OWN_CLUT_Y = 468  # 8-bit pieces with clut >= 16 use the CLUT row at y = clut + 468
# loadMapObjects: on these maps the first 0x23 instances are snow or mist particles at random positions.
PARTICLE_MAPS = set(range(0x58, 0x61)) | set(range(0x84, 0x88))
PARTICLE_INSTANCES = 0x23
OPAQUE = 4  # SpriteObject.transparency 4: drawn opaque; 0-3 are blend modes (renderMapOverlays SetSemiTrans)
# A piece counts as a cut-out of the background when this share of its texels is within CUT_OUT_TOLERANCE (mean
# per-channel difference, 0-255) of the original background where it is drawn. Opaque cut-outs measured 79% on
# Native Forest MAYO00 and 100% on Dragon Eye Lake MAYO06; separate art (Toy Mansion OMOC08) measured 0%.
CUT_OUT_SHARE = 0.5
CUT_OUT_TOLERANCE = 12


@dataclass
class _Tim:
    depth: int  # 0 = 4-bit, 1 = 8-bit
    x: int; y: int; width: int; height: int  # image rectangle in VRAM (16-bit units)
    data: int  # offset of the pixel data in the MAP
    clut: tuple[int, int, int, int, int] | None  # (x, y, width, height, data offset)


def _tims(map_bytes: bytes, offsets) -> list[_Tim]:
    images = []
    for offset in offsets:
        _, flags = struct.unpack_from("<II", map_bytes, offset)
        position, clut = offset + 8, None
        if flags & 8:
            length, cx, cy, cw, ch = struct.unpack_from("<IHHHH", map_bytes, position)
            clut, position = (cx, cy, cw, ch, position + 12), position + length
        _, x, y, width, height = struct.unpack_from("<IHHHH", map_bytes, position)
        images.append(_Tim(flags & 7, x, y, width, height, position + 12, clut))
    return images


class _Vram:
    """Which MAP bytes the game's foreground textures read, after loading the images in the game's order."""

    def __init__(self, map_bytes: bytes, scene):
        self.eight = _tims(map_bytes, scene.image_offsets)
        self.four = _tims(map_bytes, scene.object_image_offsets)
        self.loaded = self.eight + self.four  # loadMapImage1 for each 8-bit image, then loadMapImage2

    def byte(self, x: int, y: int) -> tuple[int, int] | None:
        """(MAP offset, image index) of the VRAM byte at 16-bit unit x, row y; the last image loaded wins."""
        for index in range(len(self.loaded) - 1, -1, -1):
            tim = self.loaded[index]
            if tim.x <= x // 2 < tim.x + tim.width and tim.y <= y < tim.y + tim.height:
                return tim.data + (y - tim.y) * tim.width * 2 + (x - tim.x * 2), index
        return None

    def pixel(self, item, u: int, v: int) -> tuple[int, int, int] | None:
        """(MAP offset, nibble shift or -1 for a whole byte, image index) of texel (u, v) of a sprite object."""
        page, u = item.uv[0] // 256, item.uv[0] % 256 + u
        if item.clut < 16:  # 4-bit page: 4 texels per 16-bit unit
            found = self.byte((PAGE_X + page * 64) * 2 + u // 2, item.uv[1] + v)
            return None if found is None else (found[0], 4 * (u % 2), found[1])
        found = self.byte((PAGE_X + page * 128) * 2 + u, item.uv[1] + v)
        return None if found is None else (found[0], -1, found[1])

    def own_palette(self, map_bytes: bytes, item) -> list[int] | None:
        """The CLUT words a piece with its own palette is drawn with, or None if the MAP does not hold it."""
        if item.clut < 16:
            first = self.four[0].clut if self.four else None
            if first is None or item.clut >= first[3]:
                return None
            return list(struct.unpack_from(f"<{first[2]}H", map_bytes, first[4] + item.clut * first[2] * 2))[:16]
        rows = [tim.clut for tim in self.eight if tim.clut and tim.clut[1] == item.clut + OWN_CLUT_Y]
        if not rows:
            return None
        cx, cy, width, height, data = rows[-1]
        return list(struct.unpack_from(f"<{width}H", map_bytes, data))


def _read(data, where) -> int:
    offset, shift, _ = where
    return data[offset] if shift < 0 else (data[offset] >> shift) & 15


def _write(data, where, value: int):
    offset, shift, _ = where
    if shift < 0:
        data[offset] = value
    else:
        data[offset] = (data[offset] & ~(15 << shift) & 0xFF) | (value << shift)


_NEAREST: dict[tuple, int] = {}


def _nearest(words: list[int], rgb: tuple[int, int, int]) -> int:
    """The non-transparent palette index closest to `rgb` at 5-bit precision (cached per palette and colour)."""
    target = tuple(min(31, round(channel * 31 / 255)) for channel in rgb)
    key = (tuple(words), target)
    if key not in _NEAREST:
        if len(_NEAREST) > 200_000:
            _NEAREST.clear()
        _NEAREST[key] = min((index for index, word in enumerate(words) if word != TRANSPARENT),
                            key=lambda index: sum((a - b) ** 2 for a, b in zip(_channels(words[index]), target)))
    return _NEAREST[key]


def _repaint_foreground(map_bytes: bytes, scene, map_index: int | None, old_words, new_words, index_at, rgb_at,
                        original_at):
    """Repaint the foreground layer from the picture. Opaque, static pieces drawn once that are cut-outs of the
    original background (`original_at`) are repainted (background-palette pieces into the new palette, others into
    their own fixed palette); semi-transparent effects, separate art, animated pieces, particles and pieces drawn
    at several places keep their art, and background-palette texels nobody repaints keep their nearest colour."""
    data = bytearray(map_bytes)
    vram = _Vram(map_bytes, scene)
    transparent = new_words.index(TRANSPARENT) if TRANSPARENT in new_words else None
    placements: dict[int, set] = {}
    keep: set[int] = set()  # sprite objects that must not be repainted
    reasons = {"animated": 0, "particles": 0, "repeated": 0, "semi-transparent effect": 0, "separate art": 0,
               "outside the MAP": 0}
    for number, instance in enumerate(scene.instances):
        sprites = [sprite for sprite, _ in instance.frames if 0 <= sprite < len(scene.objects)]
        particle = instance.flag & 0x80 or (map_index in PARTICLE_MAPS and number < PARTICLE_INSTANCES)
        for sprite in sprites:
            placements.setdefault(sprite, set()).add(instance.position)
            if len(set(sprites)) > 1:
                keep.add(sprite); reasons["animated"] += 1
            if particle:
                keep.add(sprite); reasons["particles"] += 1
    for sprite, places in placements.items():
        if sprite in keep:
            continue
        item = scene.objects[sprite]
        if len(places) > 1:
            keep.add(sprite); reasons["repeated"] += 1
        elif item.transparency != OPAQUE:
            keep.add(sprite); reasons["semi-transparent effect"] += 1
        else:
            palette = old_words if item.clut == BACKGROUND_CLUT else vram.own_palette(map_bytes, item)
            if palette is None:
                continue  # counted as outside the MAP below
            (x, y), = places
            close = total = 0
            for v in range(item.size[1]):
                for u in range(item.size[0]):
                    where = vram.pixel(item, u, v)
                    if where is None or palette[_read(map_bytes, where)] == TRANSPARENT:
                        continue
                    colour, under = background_color(palette[_read(map_bytes, where)])[:3], original_at(x + u, y + v)
                    total += 1
                    close += sum(abs(a - b) for a, b in zip(colour, under)) <= 3 * CUT_OUT_TOLERANCE
            if total and close < CUT_OUT_SHARE * total:
                keep.add(sprite); reasons["separate art"] += 1
    # Texels read through a piece's own palette must not be rewritten for the background palette, and vice versa.
    own_texels = set()
    for item in scene.objects:
        if item.clut != BACKGROUND_CLUT:
            for v in range(item.size[1]):
                for u in range(item.size[0]):
                    where = vram.pixel(item, u, v)
                    if where is not None:
                        own_texels.add(where[:2])
    kept_texels = set()
    for sprite in keep:
        item = scene.objects[sprite]
        for v in range(item.size[1]):
            for u in range(item.size[0]):
                where = vram.pixel(item, u, v)
                if where is not None:
                    kept_texels.add(where[:2])
    painted, counts = set(), {"background palette": 0, "own palette": 0}
    for sprite, places in placements.items():
        if sprite in keep:
            continue
        item = scene.objects[sprite]
        own = None if item.clut == BACKGROUND_CLUT else vram.own_palette(map_bytes, item)
        if item.clut != BACKGROUND_CLUT and own is None:
            reasons["outside the MAP"] += 1; continue
        (x, y), = places
        for v in range(item.size[1]):
            for u in range(item.size[0]):
                where = vram.pixel(item, u, v)
                if where is None or where[:2] in kept_texels or where[:2] in painted:
                    continue
                if item.clut == BACKGROUND_CLUT and where[:2] in own_texels:
                    continue
                old = _read(map_bytes, where)
                if item.clut == BACKGROUND_CLUT:
                    value = transparent if old_words[old] == TRANSPARENT else index_at(x + u, y + v)
                    counts["background palette"] += 1
                else:
                    value = old if own[old] == TRANSPARENT else _nearest(own, rgb_at(x + u, y + v))
                    counts["own palette"] += 1
                _write(data, where, value)
                painted.add(where[:2])
    # Background-palette texels left over (kept, animated or unused) keep their colour as closely as possible.
    nearest: dict[int, int] = {}
    remapped = 0
    background_images = {vram.pixel(item, 0, 0)[2] for item in scene.objects
                         if item.clut == BACKGROUND_CLUT and vram.pixel(item, 0, 0) is not None}
    for index in background_images:
        tim = vram.loaded[index]
        if tim.depth != 1:
            continue
        for at in range(tim.data, tim.data + tim.width * 2 * tim.height):
            if (at, -1) in painted or (at, -1) in own_texels:
                continue
            old = map_bytes[at]
            if old not in nearest:
                nearest[old] = transparent if old_words[old] == TRANSPARENT else _nearest(
                    new_words, background_color(old_words[old])[:3])
            data[at] = nearest[old]; remapped += 1
    return bytes(data), counts, reasons, remapped


def _word(rgb: tuple[int, int, int]) -> int:
    r, g, b = (min(31, max(0, round(channel * 31 / 255))) for channel in rgb)
    return (r | g << 5 | b << 10) or OPAQUE_BLACK


def _fit_channel(source: list[int], target: list[int]) -> tuple[float, float]:
    count = len(source)
    mean_s, mean_t = sum(source) / count, sum(target) / count
    variance = sum((value - mean_s) ** 2 for value in source)
    gain = sum((s - mean_s) * (t - mean_t) for s, t in zip(source, target)) / variance if variance else 1.0
    return gain, mean_t - gain * mean_s


def _channels(word: int) -> tuple[int, int, int]:
    return tuple((word >> shift) & 31 for shift in (0, 5, 10))


def encode_background(tfs: TfsFile, setup: MapSetup, edited: Image.Image, map_bytes: bytes | None = None,
                      scene=None, map_index: int | None = None) -> BackgroundResult:
    """A new TFS (same size) whose first palette draws `edited`; other palettes are re-tinted to match. With the
    MAP bytes and scene, its foreground images that use the background palette are repainted too."""
    notes = []
    original = render_background(tfs, setup, 0)
    width, height = original.size
    picture = edited.convert("RGBA")
    if picture.size != (width, height):
        if abs(picture.width / picture.height - width / height) > 0.01:
            notes.append(f"the picture's shape ({picture.width}×{picture.height}) differs from the map's "
                         f"({width}×{height}); it was stretched to fit")
        notes.append(f"scaled from {picture.width}×{picture.height} to the game's {width}×{height}")
        picture = picture.resize((width, height), Image.Resampling.LANCZOS)
    # Keep the original's transparent pixels (they are not part of the background) and honour new ones.
    alpha = [255 if a_new >= 128 and a_old else 0
             for a_new, a_old in zip(picture.getchannel("A").tobytes(), original.getchannel("A").tobytes())]
    opaque = Image.new("RGB", (width, height))
    opaque.paste(picture.convert("RGB"))
    overlays = scene is not None and map_bytes is not None and bool(scene.objects) and any(
        item.clut == BACKGROUND_CLUT for item in scene.objects)
    has_transparency = 0 in alpha or bool(overlays)  # the foreground needs a transparent colour word
    colours = 255 if has_transparency else 256
    quantized = opaque.quantize(colors=colours, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    raw_palette = quantized.getpalette()[:colours * 3]
    colours = len(raw_palette) // 3  # a simple picture may need fewer colours than were allowed
    first = [_word(tuple(raw_palette[i * 3:i * 3 + 3])) for i in range(colours)]
    # Map pixels to the palette as the PS1 will show it (5-bit colour), with error diffusion for smooth grass.
    shown = Image.new("P", (1, 1))
    shown_colours = [background_color(word)[:3] for word in first]
    shown_colours += [shown_colours[0]] * (256 - len(shown_colours))  # unused entries repeat a real colour
    shown.putpalette([channel for colour in shown_colours for channel in colour])
    indices = opaque.quantize(palette=shown, dither=Image.Dither.FLOYDSTEINBERG)
    offset = 1 if has_transparency else 0
    pixels = [min(index, colours - 1) + offset if visible else TRANSPARENT
              for index, visible in zip(indices.tobytes(), alpha)]
    palette_words = ([TRANSPARENT] if has_transparency else []) + first
    palette_words += [OPAQUE_BLACK] * (256 - len(palette_words))

    palettes = [palette_words]
    used = sorted(set(tfs.data[offset_:offset_ + TFS_TILE * TFS_TILE][i] for offset_ in tfs.tile_offsets
                      for i in range(0, TFS_TILE * TFS_TILE, 7)))
    base = [_channels(tfs.palettes[0][index]) for index in used if tfs.palettes[0][index]]
    for number, other in enumerate(tfs.palettes[1:], start=1):
        target = [_channels(other[index]) for index in used if tfs.palettes[0][index]]
        fits = [_fit_channel([colour[c] for colour in base], [colour[c] for colour in target]) for c in range(3)]
        error = sum(abs(gain * s[c] + bias - t[c]) for s, t in zip(base, target) for c, (gain, bias) in enumerate(fits))
        tinted = []
        for word in palette_words:
            if word == TRANSPARENT:
                tinted.append(TRANSPARENT); continue
            r, g, b = (min(31, max(0, round(gain * value + bias))) for value, (gain, bias) in zip(_channels(word), fits))
            tinted.append((r | g << 5 | b << 10) or OPAQUE_BLACK)
        palettes.append(tinted)
        notes.append(f"palette {number} re-tinted from the new palette (original tint matched within "
                     f"{error / max(1, len(base) * 3):.2f} of 31 per channel on average)")

    data = bytearray(tfs.data)
    for number, words in enumerate(palettes):
        struct.pack_into("<256H", data, 8 + number * TFS_PALETTE_SIZE, *words)
    for cell, tile in enumerate(setup.tiles):
        if tile == EMPTY_TILE:
            continue
        left, top = (cell % setup.grid_width) * TFS_TILE, (cell // setup.grid_width) * TFS_TILE
        start = tfs.tile_offsets[tile]
        for row in range(TFS_TILE):
            line = pixels[(top + row) * width + left:(top + row) * width + left + TFS_TILE]
            data[start + row * TFS_TILE:start + (row + 1) * TFS_TILE] = bytes(line)
    notes.insert(0, f"background repainted with {colours} colours")
    new_map = None
    if scene is not None and map_bytes is not None and scene.objects:
        full = indices.tobytes()
        clamp = lambda x, y: min(height - 1, max(0, y)) * width + min(width - 1, max(0, x))
        index_at = lambda x, y: min(full[clamp(x, y)], colours - 1) + offset
        rgb = opaque.tobytes()
        rgb_at = lambda x, y: tuple(rgb[clamp(x, y) * 3:clamp(x, y) * 3 + 3])
        before = original.convert("RGB").tobytes()
        original_at = lambda x, y: tuple(before[clamp(x, y) * 3:clamp(x, y) * 3 + 3])
        try:
            new_map, counts, reasons, remapped = _repaint_foreground(
                map_bytes, scene, map_index, tfs.palettes[0], palette_words if overlays else list(tfs.palettes[0]),
                index_at, rgb_at, original_at)
        except (struct.error, IndexError):
            if overlays:
                raise ValueError("the MAP's foreground images could not be read, so the new palette cannot be used")
            notes.append("the MAP's foreground images could not be read; the foreground layer was left unchanged")
            return BackgroundResult(bytes(data), tuple(notes), None)
        repainted = ", ".join(f"{count:,} {kind}" for kind, count in counts.items() if count)
        kept = ", ".join(f"{count} {kind}" for kind, count in reasons.items() if count)
        notes.append(f"foreground layer repainted from the picture ({repainted or 'nothing to repaint'} texels)"
                     + (f"; original art kept for {kept} piece(s)" if kept else "")
                     + (f"; {remapped:,} other texels kept to their nearest colour" if remapped else ""))
        if new_map == map_bytes:
            new_map = None
    return BackgroundResult(bytes(data), tuple(notes), new_map)
