"""Full-size zone backgrounds in the player, for the USA release on a PC.

The game keeps backgrounds as 128×128 8-bit tiles, so a repainted picture imported into the disc is shrunk and
reduced to 256 colours. This layer leaves the game alone and works on each finished frame instead. It rebuilds what
the game's own background looks like at the current camera from RAM (the tiles stay there, identical to the VRAM
ring, dw_decomp src/main/map.c), compares it with the frame, and:

- a pixel equal to the game's background, or the same pixel uniformly darkened (shadows, fades), comes from the
  full-size picture, darkened by the same amount;
- a pixel equal to a foreground piece that is a cut-out of the background comes from the picture too, whichever
  palette the piece uses; where such a piece covers a character the character is hidden either way;
- anything else (characters, the HUD, effects, separate art, menus) stays the game's pixel.

A frame with too little background (cutscenes, menus, map changes) is passed through unchanged.

Two details of renderMap matter: screen position = map position - camera, and tiles whose texV is not a multiple
of 256 are drawn with a 127-texel V span over 128 lines, so their lower half shows the source row above.

The frame on screen was drawn with an earlier camera than the one in RAM, so recent camera values are tried. A
battle overwrites the tiles in RAM (startBattle re-reads the TFS after a win), so tiles read before it stay in use;
a state loaded during a battle takes the visible tiles from VRAM instead.
"""
from __future__ import annotations

import re
import struct
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from .live import CURRENT_SCREEN, ram_offset
from .quicksave import _atomic_write

MAP_TILE_DATA = 0x8014F084  # MapTileData tiles[35] (20 bytes each), then ...
CAMERA = MAP_TILE_DATA + 0x1BB4  # i16 cameraX, cameraY
MAP_SIZE = MAP_TILE_DATA + 0x1BB8  # i8 width, height (in tiles)
MAP_TILE_X, MAP_TILE_Y = 0x80134DC2, 0x80134DC4  # i8: the top-left tile of the 4 × 3 ring
GAME_STATE = 0x80134F0A  # nonzero during a battle, whose code loads over the tile data in RAM (see below)
MAP_INSTANCES = MAP_TILE_DATA + 35 * 20  # LocalMapObjectInstance[188]
MAP_OBJECT_COUNT = 0x80134D1C  # MAP_OBJECT_INSTANCE_COUNT
LOCAL_MAP_OBJECTS = 0x8013C060  # LocalMapObject[180]
MAX_INSTANCES, INSTANCE_SIZE, MAX_OBJECTS, OBJECT_SIZE = 188, 34, 180, 14
PAGE_X = 384  # VRAM x of foreground texture page 0
OPAQUE = 4  # LocalMapObject.transparency: drawn opaque
CUT_OUT_SHARE = 0.5
NO_FOREGROUND = 0xFFFF
MAP_CLUTS = 0x80150C70  # u16 *[3]: the TFS palettes (day, evening, night)
GPURAM_FIELD = b"\r&GPURAM[0][0]"  # Beetle PSX save-state field: 1024 × 512 16-bit VRAM
PALETTE_ROW = 480  # the background palette in VRAM
TILE, SCREEN_W, SCREEN_H = 128, 320, 240
OUTPUT_SCALE = 4  # composed at 1280×960; the picture is resampled to this once per map
CAMERA_HISTORY = 4  # camera values to try for a frame
RECHECK_FRAMES = 30  # re-read the tiles twice a second (not during a battle, which overwrites them)
PALETTE_FRAMES = 15  # read the live palette four times a second (it changes with the time of day)
MIN_COVERAGE = 0.25  # below this the frame is not showing the map
MAX_LOOKS = 4  # tinted whole-map pictures kept per map
CLEAN_SHARE = 0.5  # share of a 5×5 square that must be background for a darkened pixel to count as a shadow
FOREGROUND_READS = 4  # re-read the foreground pieces every this many palette reads (about once a second)
SPREAD, DARKEST = 2, 16  # 5-bit levels: channels must shift alike; how far a shadow may darken


@dataclass(frozen=True)
class Composed:
    pixels: np.ndarray  # OUTPUT_SCALE × screen, uint32 0xFFRRGGBB
    coverage: float


def rgb5(palette: bytes) -> np.ndarray:
    """256 × 3 (R, G, B in 0-31) from raw 16-bit PS1 colours."""
    words = np.frombuffer(palette, "<u2", 256)
    return np.stack((words & 31, (words >> 5) & 31, (words >> 10) & 31), axis=1).astype(np.int16)


def channel_tables(base: np.ndarray, target: np.ndarray) -> np.ndarray | None:
    """Per-channel 256-entry tables turning the first palette's colours into `target`'s (None when equal)."""
    if np.array_equal(base, target):
        return None
    tables = np.empty((3, 256), np.uint8)
    for channel in range(3):
        gain, bias = np.polyfit(base[:, channel].astype(float), target[:, channel].astype(float), 1)
        tables[channel] = np.clip(np.arange(256) * gain + bias * 8, 0, 255).astype(np.uint8)
    return tables


def load_picture(path: Path, width_tiles: int, height_tiles: int, scale: int = OUTPUT_SCALE) -> np.ndarray:
    """The whole map as uint32 0xFFRRGGBB at `scale` × native size."""
    with Image.open(path) as opened:
        picture = opened.convert("RGB").resize((width_tiles * TILE * scale, height_tiles * TILE * scale),
                                               Image.Resampling.LANCZOS)
    rgb = np.asarray(picture, np.uint32)
    return 0xFF000000 | rgb[..., 0] << 16 | rgb[..., 1] << 8 | rgb[..., 2]


def _packed(levels: np.ndarray) -> np.ndarray:
    return levels[..., 0] | levels[..., 1] << 5 | levels[..., 2] << 10


def frame_packed(frame: np.ndarray) -> np.ndarray:
    """Frame pixels (uint32 0x00RRGGBB) as 15-bit values, comparable with `_packed` palettes."""
    return ((frame >> 19) & 31) | ((frame >> 11) & 31) << 5 | ((frame >> 3) & 31) << 10


def _levels(pixels: np.ndarray) -> np.ndarray:
    return np.stack(((pixels >> 19) & 31, (pixels >> 11) & 31, (pixels >> 3) & 31), axis=-1).astype(np.int16)


def screen_shift(frame: np.ndarray, palette: np.ndarray, indices: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """A screen-wide shift (a fade or tint), as the commonest difference from the background in a sample."""
    keep = valid[::7, ::7]
    if not keep.any():
        return np.zeros(3, np.int16)
    native = palette[indices[::7, ::7]][keep]
    levels = _levels(frame[::7, ::7][keep])
    return np.array([np.bincount(levels[:, c] - native[:, c] + 31, minlength=63).argmax() - 31 for c in range(3)], np.int16)


@dataclass(frozen=True)
class ScreenView:
    """Which map texel each drawn line and column of the screen shows."""
    factor: int
    rows: np.ndarray  # source row of the background per drawn line (renderMap's V span applied)
    above: np.ndarray  # the same, one row up where upscaled rendering may round either way
    map_y: np.ndarray  # map row per drawn line, for foreground pieces (drawn 1:1)
    cols: np.ndarray  # map column per drawn column
    inside_y: np.ndarray
    inside_x: np.ndarray


@dataclass(frozen=True)
class MapLayers:
    indices: np.ndarray  # whole-map palette indices, not squeezed
    valid: np.ndarray
    foreground: np.ndarray | None  # whole-map 15-bit colours of cut-out pieces, NO_FOREGROUND elsewhere


def _matches_piece(levels, foreground, y, x, shift) -> np.ndarray:
    piece = foreground[y, x]
    colours = np.stack((piece & 31, (piece >> 5) & 31, (piece >> 10) & 31), axis=-1).astype(np.int16)
    return (piece != NO_FOREGROUND) & (np.abs(levels - np.clip(colours + shift, 0, 31)).max(axis=-1) <= 1)


def _test_pixels(pixels, rows, cols, map_y, valid, layers: MapLayers, above_rows, expected, expected_levels, shift):
    """Classify pixels (equal-length arrays): 0 not background, 1 background, 2 background darkened. Also returns
    each darkened pixel's difference, zero elsewhere. Most are exact, so only the others get the slower tests."""
    packed = frame_packed(pixels)
    state = np.zeros(pixels.shape, np.uint8)
    state[valid & (packed == expected[layers.indices[rows, cols]])] = 1
    relative = np.zeros((*pixels.shape, 3), np.int16)
    rest = np.nonzero(valid & (state == 0))[0]
    if not rest.size:
        return state, relative
    levels = _levels(pixels[rest])
    here_rows, here_cols = rows[rest], cols[rest]
    if above_rows is not None:
        matched = packed[rest] == expected[layers.indices[above_rows[rest], here_cols]]
    else:
        matched = np.zeros(rest.shape, bool)
    if layers.foreground is not None:
        here_y = map_y[rest]
        matched |= _matches_piece(levels, layers.foreground, here_y, here_cols, shift)
        if above_rows is not None:  # upscaled, a squeezed piece may round to the texel above or to the left
            up, left = np.maximum(here_y - 1, 0), np.maximum(here_cols - 1, 0)
            matched |= (_matches_piece(levels, layers.foreground, up, here_cols, shift)
                        | _matches_piece(levels, layers.foreground, here_y, left, shift)
                        | _matches_piece(levels, layers.foreground, up, left, shift))
    state[rest[matched]] = 1
    delta = levels - expected_levels[layers.indices[here_rows, here_cols]].astype(np.int16)
    high, low = delta.max(axis=-1), delta.min(axis=-1)
    darker = ~matched & (high - low <= SPREAD) & (high <= 1) & (low >= -DARKEST)
    state[rest[darker]] = 2
    relative[rest[darker]] = delta[darker]
    return state, relative


def _box_sum(mask: np.ndarray, radius: int) -> np.ndarray:
    """How many pixels are set in the (2r + 1)² square around each pixel."""
    table = np.zeros((mask.shape[0] + 1, mask.shape[1] + 1), np.int32)
    table[1:, 1:] = mask.cumsum(axis=0, dtype=np.int32).cumsum(axis=1)
    height, width = mask.shape
    top = np.clip(np.arange(height) - radius, 0, height)[:, None]
    bottom = np.clip(np.arange(height) + radius + 1, 0, height)[:, None]
    left = np.clip(np.arange(width) - radius, 0, width)[None, :]
    right = np.clip(np.arange(width) + radius + 1, 0, width)[None, :]
    return table[bottom, right] - table[top, right] - table[bottom, left] + table[top, left]


def _clean(state: np.ndarray) -> np.ndarray:
    """A real shadow is a smooth area; darkened pixels mostly surrounded by other things are chance matches
    (a piece's own colours, a character's). Single-texel holes in the background are noise too."""
    keep = state != 0
    chance = (state == 2) & (_box_sum(keep, 2) < CLEAN_SHARE * 25)
    state = state.copy()
    state[chance] = 0
    hole = (state == 0) & (_box_sum(state != 0, 1) >= 8)
    state[hole] = 1
    return state


def _edges(state: np.ndarray) -> np.ndarray:
    """Texels whose class differs from a neighbour's: the only ones an upscaled frame needs at full detail."""
    edges = np.zeros(state.shape, bool)
    across = state[:, 1:] != state[:, :-1]
    down = state[1:, :] != state[:-1, :]
    edges[:, 1:] |= across
    edges[:, :-1] |= across
    edges[1:, :] |= down
    edges[:-1, :] |= down
    return edges


def compose(frame: np.ndarray, palette: np.ndarray, layers: MapLayers, view: ScreenView, picture) -> Composed:
    """Merge one frame with the picture.

    `frame` is uint32 0x00RRGGBB at view.factor × the screen, `palette` 256 × 3 in 5-bit levels. `picture(shift)`
    returns the screen's picture, uint32 at a whole multiple of the screen, with the time of day and that screen-wide
    shift applied. Every game texel is tested once on a sample of the frame; only texels at the edge of what they
    belong to are tested at full detail, so the cost hardly grows with the internal resolution."""
    factor = view.factor
    sample = frame[::factor, ::factor]
    rows, cols = view.rows[::factor], view.cols[::factor]
    map_y = view.map_y[::factor]
    grid_rows, grid_cols = np.meshgrid(np.arange(rows.size), np.arange(cols.size), indexing="ij")
    valid = layers.valid[np.ix_(rows, cols)] & view.inside_y[::factor, None] & view.inside_x[None, ::factor]
    shift = screen_shift(sample, palette, layers.indices[np.ix_(rows, cols)], valid)
    expected_levels = np.clip(palette + shift, 0, 31).astype(np.uint16)  # the game's fades clamp at black
    expected = _packed(expected_levels)
    above = view.above[::factor] if factor > 1 else None
    grid_rows, grid_cols = grid_rows.ravel(), grid_cols.ravel()
    state, relative = _test_pixels(sample.ravel(), rows[grid_rows], cols[grid_cols], map_y[grid_rows], valid.ravel(),
                                   layers, None if above is None else above[grid_rows], expected, expected_levels, shift)
    state = _clean(state.reshape(sample.shape))
    relative = relative.reshape(*sample.shape, 3)
    coverage = float(np.count_nonzero(state)) / state.size
    if coverage < MIN_COVERAGE:
        return Composed(0xFF000000 | frame, coverage)
    layer = picture(shift)
    scale = layer.shape[0] // SCREEN_H
    per_frame = scale // factor
    mask = np.repeat(np.repeat(state != 0, factor, axis=0), factor, axis=1)
    edge = _edges(state) if factor > 1 else None
    plain = state == 2
    if edge is not None:
        plain &= ~edge
    layer = _shade(layer, *np.nonzero(plain), relative[plain], scale)
    if edge is not None and edge.any():
        block_y, block_x = np.nonzero(edge)
        step_y, step_x = np.meshgrid(np.arange(factor), np.arange(factor), indexing="ij")
        y = (block_y[:, None] * factor + step_y.ravel()).ravel()
        x = (block_x[:, None] * factor + step_x.ravel()).ravel()
        here = view.inside_y[y] & view.inside_x[x]
        here &= layers.valid[view.rows[y], view.cols[x]]
        fine, fine_relative = _test_pixels(frame[y, x], view.rows[y], view.cols[x], view.map_y[y], here, layers,
                                           view.above[y], expected, expected_levels, shift)
        mask[y, x] = fine != 0
        darker = fine == 2
        if darker.any():
            layer = _shade(layer, y[darker], x[darker], fine_relative[darker], per_frame)
    output = 0xFF000000 | frame
    if per_frame > 1:
        output = np.repeat(np.repeat(output, per_frame, axis=0), per_frame, axis=1)
        mask = np.repeat(np.repeat(mask, per_frame, axis=0), per_frame, axis=1)
    return Composed(np.where(mask, layer, output), coverage)


def _shifted(tables: np.ndarray | None, shift: np.ndarray) -> np.ndarray:
    base = np.tile(np.arange(256, dtype=np.int16), (3, 1)) if tables is None else tables.astype(np.int16)
    return np.clip(base + shift[:, None] * 8, 0, 255).astype(np.uint8)


def _tint(layer: np.ndarray, tables: np.ndarray) -> np.ndarray:
    return (0xFF000000 | tables[0][(layer >> 16) & 255].astype(np.uint32) << 16
            | tables[1][(layer >> 8) & 255].astype(np.uint32) << 8 | tables[2][layer & 255])


def _shade(layer: np.ndarray, rows, columns, relative, repeat: int) -> np.ndarray:
    """Darken the picture by the frame's own difference from the background, only at those pixels."""
    if not rows.size:
        return layer
    layer = layer.copy()
    add = relative.astype(np.int64) * 8
    if repeat > 1:
        steps = np.arange(repeat)
        add = np.repeat(add, repeat * repeat, axis=0)
        rows, columns = (np.repeat(rows, repeat * repeat) * repeat + np.tile(np.repeat(steps, repeat), rows.size),
                         np.repeat(columns, repeat * repeat) * repeat + np.tile(steps, columns.size * repeat))
    pixels = layer[rows, columns].astype(np.int64)
    red = np.clip(((pixels >> 16) & 255) + add[:, 0], 0, 255)
    green = np.clip(((pixels >> 8) & 255) + add[:, 1], 0, 255)
    blue = np.clip((pixels & 255) + add[:, 2], 0, 255)
    layer[rows, columns] = (0xFF000000 | red << 16 | green << 8 | blue).astype(np.uint32)
    return layer


class MapBackground:
    """One map's tile indices, read from RAM."""

    def __init__(self, read):
        self.read = read
        self.key = None
        self.indices = self.valid = None
        self.width = self.height = 0
        self.from_vram = False
        self.squeezed = None  # per tile row: drawn with a 127-texel V span
        self.alternate = None  # from the last window(): the row above on upscaled squeezed lines

    def _map_key(self):
        width, height = struct.unpack("<bb", self.read(ram_offset(MAP_SIZE), 2))
        if width <= 0 or height <= 0 or width * height > 35:
            return None
        table = self.read(ram_offset(MAP_TILE_DATA), 20 * width * height)
        return self.read(ram_offset(CURRENT_SCREEN), 1)[0], width, height, table

    def refresh(self, force: bool = False, vram: bytes | None = None) -> bool:
        """Keep the indices current; False when no usable map is in RAM. `vram` is a 1024 × 512 × 2 byte copy."""
        key = self._map_key()
        if key is None:
            self.key = None
            return False
        in_battle = struct.unpack("<h", self.read(ram_offset(GAME_STATE), 2))[0] != 0
        if key == self.key and not force and (in_battle or not self.from_vram):
            return True
        if in_battle and vram is None:
            return self.indices is not None and key == self.key
        _, self.width, self.height, table = self.key = key
        self.from_vram = in_battle
        if in_battle:
            self._load_from_vram(table, vram)
            return True
        self.indices = np.zeros((self.height * TILE, self.width * TILE), np.uint8)
        self.valid = np.zeros_like(self.indices, bool)
        self.squeezed = np.zeros(self.height, bool)
        for row in range(self.height):
            for column in range(self.width):
                pointer, tile_id, _, _, _, tex_v = struct.unpack_from("<Ihhhhh", table, (row * self.width + column) * 20)
                if tile_id == -1 or not 0x80000000 <= pointer < 0x80200000 - TILE * TILE:
                    continue
                tile = np.frombuffer(self.read(ram_offset(pointer), TILE * TILE), np.uint8).reshape(TILE, TILE)
                self.squeezed[row] = tex_v % 256 != 0
                top, left = row * TILE, column * TILE
                self.indices[top:top + TILE, left:left + TILE] = tile
                self.valid[top:top + TILE, left:left + TILE] = True
        return True

    def _load_from_vram(self, table: bytes, vram: bytes):
        self.indices = np.zeros((self.height * TILE, self.width * TILE), np.uint8)
        self.valid = np.zeros_like(self.indices, bool)
        self.squeezed = np.zeros(self.height, bool)
        first_x = struct.unpack("<b", self.read(ram_offset(MAP_TILE_X), 1))[0]
        first_y = struct.unpack("<b", self.read(ram_offset(MAP_TILE_Y), 1))[0]
        for row in range(max(first_y, 0), min(first_y + 3, self.height)):
            for column in range(max(first_x, 0), min(first_x + 4, self.width)):
                _, tile_id, _, _, tex_u, tex_v = struct.unpack_from("<Ihhhhh", table, (row * self.width + column) * 20)
                if tile_id == -1 or not 0 <= tex_u <= 1024 - TILE // 2 or not 0 <= tex_v <= 512 - TILE:
                    continue
                tile = np.stack([np.frombuffer(vram, np.uint8, TILE, ((tex_v + line) * 1024 + tex_u) * 2)
                                 for line in range(TILE)])
                self.squeezed[row] = tex_v % 256 != 0
                top, left = row * TILE, column * TILE
                self.indices[top:top + TILE, left:left + TILE] = tile
                self.valid[top:top + TILE, left:left + TILE] = True

    def view(self, camera_x: int, camera_y: int, factor: int = 1) -> ScreenView:
        """The map texels the screen shows at this camera, at `factor` lines per screen line. renderMap draws some
        tile rows with 127 texels over 128 lines, which the rasterizer rounds per drawn line: source row =
        floor(line × 127 / (128 × factor) + 1/2). That is exact at 1× (96% of those lines measured, the rest being
        characters); above it the rounding varies with the camera, so `above` gives the row above for those lines."""
        lines = np.arange(SCREEN_H * factor) + camera_y * factor
        columns = np.repeat(np.arange(SCREEN_W) + camera_x, factor)
        tile_rows = lines // (TILE * factor)
        inside_y = (lines >= 0) & (tile_rows < self.height)
        inside_x = (columns >= 0) & (columns < self.width * TILE)
        into_tile = lines % (TILE * factor)
        source = into_tile // factor
        squeezed = inside_y.copy()
        squeezed[inside_y] = self.squeezed[tile_rows[inside_y]]
        source[squeezed] = (into_tile[squeezed] * 2 * (TILE - 1) + TILE * factor) // (2 * TILE * factor)
        rows = np.where(inside_y, tile_rows * TILE + source, 0)
        above = np.where(squeezed, np.maximum(rows - 1, tile_rows * TILE), rows) if factor > 1 else rows
        map_y = np.where(inside_y, np.clip(lines // factor, 0, self.height * TILE - 1), 0)
        return ScreenView(factor, rows, above, map_y, np.where(inside_x, columns, 0), inside_y, inside_x)

    def window(self, camera_x: int, camera_y: int, factor: int = 1) -> tuple[np.ndarray, np.ndarray]:
        """Palette indices and validity of the whole screen at this camera; outside the map is invalid."""
        view = self.view(camera_x, camera_y, factor)
        grid = np.ix_(view.rows, view.cols)
        self.alternate = self.indices[np.ix_(view.above, view.cols)] if factor > 1 else None
        return self.indices[grid], self.valid[grid] & view.inside_y[:, None] & view.inside_x[None, :]


def read_foreground(read, vram: bytes, indices: np.ndarray, valid: np.ndarray, palette: np.ndarray, base: np.ndarray,
                    cut_outs: dict[int, bool]) -> np.ndarray | None:
    """Colours of the foreground pieces that are cut-outs of the background, painted onto a whole-map array.

    Such a piece shows part of the same picture, so where the frame shows it the full-size picture can show instead;
    where it covers a character the character is hidden either way. Animated pieces, effects, particles and separate
    art stay the game's. Whether a piece is a cut-out is decided once per map (`cut_outs`), against the first palette:
    at least half its texels within a mean 12/255 of the background under it. renderMapOverlays and
    buildMapOverlayPrim (dw_decomp src/main/overworld.c) give the layout and how a piece is drawn."""
    count = struct.unpack("<h", read(ram_offset(MAP_OBJECT_COUNT), 2))[0]
    if not 0 < count <= MAX_INSTANCES:
        return None
    instances = read(ram_offset(MAP_INSTANCES), MAX_INSTANCES * INSTANCE_SIZE)
    objects = read(ram_offset(LOCAL_MAP_OBJECTS), MAX_OBJECTS * OBJECT_SIZE)
    words = np.frombuffer(vram, "<u2").reshape(512, 1024)
    bytes8 = np.frombuffer(vram, np.uint8).reshape(512, 2048)
    height, width = indices.shape
    result = np.full((height, width), NO_FOREGROUND, np.uint16)
    live, first = _packed(palette.astype(np.uint16)), _packed(base.astype(np.uint16))
    base_u16 = base.astype(np.int16)
    for number in range(count):
        at = number * INSTANCE_SIZE
        order, x, y = struct.unpack_from("<3h", instances, at)
        sprites = struct.unpack_from("<8h", instances, at + 6)
        flag = struct.unpack_from("<b", instances, at + 33)[0]
        frames = []
        for sprite in sprites:
            if sprite == -1:
                break
            frames.append(sprite)
        if (len(set(frames)) != 1 or not 0 <= frames[0] < MAX_OBJECTS or flag == 1 or flag & 0x80 or order < 20):
            continue
        tex_x, tex_y, _, _, _, w, h, clut, transparency = struct.unpack_from("<5h2B2b", objects, frames[0] * OBJECT_SIZE)
        if transparency != OPAQUE or not w or not h or tex_x < 0 or tex_y < 0:
            continue
        page, u, v = tex_x // 256, tex_x % 256, tex_y % 256
        if v + h > 512:
            continue
        # buildMapOverlayPrim: a piece reaching the end of its 256 × 256 page spans one texel less in both directions.
        fits = u + w < 256 and v + h < 256
        columns = u + (np.arange(w) if fits else (np.arange(w) * 2 * (w - 1) + w) // (2 * w))
        lines = v + (np.arange(h) if fits else (np.arange(h) * 2 * (h - 1) + h) // (2 * h))
        if 0 <= clut < 16:  # 4-bit page, a 16-colour row of the CLUT at y = 486
            start = (PAGE_X + page * 64) * 2
            if start + (u + w) // 2 >= 2048:
                continue
            texels = (bytes8[np.ix_(lines, start + columns // 2)] >> (4 * (columns % 2))) & 15
            colours = words[486, clut * 16:clut * 16 + 16]
        else:
            start = (PAGE_X + page * 128) * 2
            if start + u + w > 2048:
                continue
            texels = bytes8[np.ix_(lines, start + columns)]
            colours = None if clut == -1 else words[clut + 468, :256]
        opaque_words = (first if colours is None else colours)[texels]
        shown = opaque_words != 0
        x0, y0, x1, y1 = max(x, 0), max(y, 0), min(x + w, width), min(y + h, height)
        if x0 >= x1 or y0 >= y1:
            continue
        crop = (slice(y0 - y, y1 - y), slice(x0 - x, x1 - x))
        if number not in cut_outs:
            piece = opaque_words[crop].astype(np.int32)
            levels = np.stack((piece & 31, (piece >> 5) & 31, (piece >> 10) & 31), axis=-1).astype(np.int16)
            under = base_u16[indices[y0:y1, x0:x1]]
            here = shown[crop] & valid[y0:y1, x0:x1]
            close = np.abs(levels - under).sum(axis=-1)[here] <= 4
            cut_outs[number] = bool(close.size) and close.mean() >= CUT_OUT_SHARE
        if not cut_outs[number]:
            continue
        painted = (live if colours is None else colours & 0x7FFF)[texels][crop]
        place = shown[crop]
        target = result[y0:y1, x0:x1]
        target[place] = painted[place]
    return result


def picture_window(picture: np.ndarray, camera_x: int, camera_y: int, scale: int = OUTPUT_SCALE) -> np.ndarray:
    """The screen's part of a whole-map picture; outside the picture is black (always kept out by `valid`)."""
    height, width = picture.shape
    x0, y0 = max(camera_x * scale, 0), max(camera_y * scale, 0)
    x1, y1 = min((camera_x + SCREEN_W) * scale, width), min((camera_y + SCREEN_H) * scale, height)
    if (x0, y0, x1 - x0, y1 - y0) == (camera_x * scale, camera_y * scale, SCREEN_W * scale, SCREEN_H * scale):
        return picture[y0:y1, x0:x1]
    result = np.full((SCREEN_H * scale, SCREEN_W * scale), 0xFF000000, np.uint32)
    if x0 < x1 and y0 < y1:
        result[y0 - camera_y * scale:y1 - camera_y * scale, x0 - camera_x * scale:x1 - camera_x * scale] = picture[y0:y1, x0:x1]
    return result


class BackgroundLayer:
    """Prepared on the emulator thread (it reads RAM), composed anywhere. `pictures(map_index)` gives a path or None."""

    def __init__(self, read, serialize, pictures, scale: int = OUTPUT_SCALE, on_error=None):
        self.read, self.serialize, self.pictures, self.scale = read, serialize, pictures, scale
        self.on_error = on_error or (lambda text: None)
        self.background = MapBackground(read)
        self._picture = None
        self._picture_key = None
        self._palette = None
        self._tables = None
        self._foreground = None
        self._foreground_key = None
        self._cut_outs: dict[int, bool] = {}
        self._palette_reads = 0
        self._tinted: dict[tuple, np.ndarray] = {}  # whole-map pictures for the looks in use (day, night, a fade)
        self._last_look = None
        self._frames = 0
        self._low_coverage = False
        self._cameras: list[tuple[int, int]] = []
        self.coverage = 0.0

    def reset(self):
        """Forget what was seen in RAM (after loading a state or resetting)."""
        self.background.key = None
        self.background.indices = None
        self._palette = None
        self._foreground = None
        self._foreground_key = None
        self._cameras = []

    def _ready_picture(self, index: int) -> bool:
        """True once this map's picture is loaded; resampling runs in the background so the game never waits."""
        key = (index, self.background.width, self.background.height)
        if key != self._picture_key:
            self._picture_key = key
            self._picture = None
            self._tinted = {}
            path = self.pictures(index)
            if path:
                threading.Thread(target=self._load_picture, args=(key, path), daemon=True).start()
        return self._picture is not None

    def _load_picture(self, key, path):
        try:
            picture = load_picture(path, key[1], key[2], self.scale)
        except (OSError, ValueError) as exc:
            self.on_error(f"The HD background {Path(path).name} could not be loaded: {exc}")
            return
        if key == self._picture_key:
            self._picture = picture

    def _read_vram(self) -> bytes:
        state = self.serialize()
        at = state.find(GPURAM_FIELD)
        if at < 0:
            raise ValueError("This core's save state has no VRAM field.")
        start = at + len(GPURAM_FIELD) + 4
        return state[start:start + 1024 * 512 * 2]

    def _set_palette(self, vram: bytes):
        start = PALETTE_ROW * 1024 * 2
        palette = rgb5(vram[start:start + 512])
        changed = self._palette is None or not np.array_equal(palette, self._palette)
        self._palette = palette
        pointer = struct.unpack("<I", self.read(ram_offset(MAP_CLUTS), 4))[0]
        base = rgb5(self.read(ram_offset(pointer), 512)) if 0x80000000 <= pointer < 0x80200000 - 512 else palette
        self._tables = channel_tables(base, palette)
        self._palette_reads += 1
        in_battle = struct.unpack("<h", self.read(ram_offset(GAME_STATE), 2))[0] != 0
        map_key = self.background.key
        if map_key != self._foreground_key:
            self._foreground_key = map_key
            self._cut_outs = {}
            self._foreground = None
        if self._foreground is None or changed or (not in_battle and self._palette_reads % FOREGROUND_READS == 0):
            if self._foreground is None or not in_battle:  # a battle may load its effects over the piece images
                self._foreground = read_foreground(self.read, vram, self.background.indices, self.background.valid,
                                                   palette, base, self._cut_outs)

    def _displayed_camera(self, frame: np.ndarray, factor: int) -> tuple[int, int]:
        """The camera the frame was drawn with: RAM may already hold a later one, so recent values are tried."""
        current = struct.unpack("<hh", self.read(ram_offset(CAMERA), 4))
        if current not in self._cameras:
            self._cameras.insert(0, current)
            del self._cameras[CAMERA_HISTORY:]
        if len(self._cameras) == 1:
            return current
        step = 5
        sample = frame_packed(frame[::step * factor, ::step * factor])
        table = _packed(self._palette.astype(np.uint16))
        best, best_score = current, -1
        for camera in self._cameras:
            indices, valid = self.background.window(*camera)
            score = int(np.count_nonzero((sample == table[indices[::step, ::step]]) & valid[::step, ::step]))
            if score > best_score:
                best, best_score = camera, score
        return best

    def prepare(self, frame: np.ndarray):
        """What `compose` needs for this frame, or None when this map has no picture or the frame does not fit."""
        height, width = frame.shape
        factor = width // SCREEN_W
        if width % SCREEN_W or height % SCREEN_H or factor != height // SCREEN_H or factor > self.scale:
            return None  # another video mode (the game's title, some cutscenes)
        self._frames += 1
        before = self.background.key
        loaded = self.background.refresh()
        if not loaded:
            if self.background._map_key() is None:
                return None  # no map in RAM (the title screen, a map change)
            if not self.background.refresh(vram=self._read_vram()):  # nothing cached in a battle: VRAM has the tiles
                return None
        if self.background.key is None or not self._ready_picture(self.background.key[0]):
            return None
        if (self.background.key is not before or self._palette is None or self._frames % PALETTE_FRAMES == 0):
            self._set_palette(self._read_vram())
        if self._frames % RECHECK_FRAMES == 0:
            self.background.refresh(force=True)  # tiles may have finished loading, or a script changed them
        camera_x, camera_y = self._displayed_camera(frame, factor)
        layers = MapLayers(self.background.indices, self.background.valid, self._foreground)
        return (frame, self._palette, layers, self.background.view(camera_x, camera_y, factor),
                (self._picture, self._tables, camera_x, camera_y))

    def _look(self, picture, tables, shift) -> np.ndarray:
        """The whole map tinted for this time of day and fade. A look seen twice running is kept (about 60 ms to
        build once); one that changes every frame, as in the daytime transition, is applied to the screen part only."""
        if tables is None and not shift.any():
            return picture
        mapped = _shifted(tables, shift)
        key = (mapped.tobytes(),)
        cached = self._tinted.get(key)
        if cached is not None:
            return cached
        if key == self._last_look:
            if len(self._tinted) >= MAX_LOOKS:
                self._tinted.pop(next(iter(self._tinted)))
            self._tinted[key] = cached = _tint(picture, mapped)
            return cached
        self._last_look = key
        return None

    def compose(self, job) -> Composed:
        frame, palette, layers, view, (picture, tables, camera_x, camera_y) = job

        def screen_picture(shift):
            look = self._look(picture, tables, shift)
            if look is None:
                return _tint(picture_window(picture, camera_x, camera_y, self.scale), _shifted(tables, shift))
            return picture_window(look, camera_x, camera_y, self.scale)

        result = compose(frame, palette, layers, view, screen_picture)
        self.coverage = result.coverage
        self._low_coverage = result.coverage < MIN_COVERAGE
        return result

    def apply(self, frame: np.ndarray) -> Composed | None:
        job = self.prepare(frame)
        return None if job is None else self.compose(job)


class Compositor:
    """Composes on its own thread so the emulator keeps its pace; only the newest frame waits, older ones are dropped."""

    def __init__(self, layer: BackgroundLayer, deliver, on_error):
        self.layer, self.deliver, self.on_error = layer, deliver, on_error
        self._wake = threading.Condition()
        self._job = None
        self._stop = False
        self._thread = threading.Thread(target=self._run, name="hd-compositor", daemon=True)
        self._thread.start()

    def submit(self, job):
        with self._wake:
            self._job = job
            self._wake.notify()

    def _run(self):
        while True:
            with self._wake:
                while self._job is None and not self._stop:
                    self._wake.wait()
                if self._stop:
                    return
                job, self._job = self._job, None
            try:
                self.deliver(self.layer.compose(job))
            except Exception as exc:  # whatever went wrong, the game must keep showing frames
                self.on_error(f"HD backgrounds turned off: {exc}")
                return

    def stop(self):
        with self._wake:
            self._stop = True
            self._wake.notify()
        self._thread.join(2)


class HdPictures:
    """Full-size pictures per map under <project>/overrides/hd/, named NNN-MAPNAME<ext> as the user supplied them."""

    def __init__(self, directory: Path):
        self.directory = Path(directory)

    def path(self, index: int) -> Path | None:
        found = sorted(self.directory.glob(f"{index:03d}-*")) if self.directory.is_dir() else []
        return found[0] if found else None

    def maps(self) -> list[int]:
        if not self.directory.is_dir():
            return []
        return sorted({int(path.name[:3]) for path in self.directory.iterdir() if re.match(r"\d{3}-", path.name)})

    def store(self, index: int, name: str, data: bytes) -> Path:
        """Keep a checked PNG of the picture, bytes unchanged when it already is one."""
        from io import BytesIO
        with Image.open(BytesIO(data)) as opened:
            opened.load()
            if opened.format != "PNG":
                buffer = BytesIO()
                opened.convert("RGB").save(buffer, "PNG")
                data = buffer.getvalue()
        self.remove(index)
        target = self.directory / f"{index:03d}-{re.sub(r'[^A-Za-z0-9_]', '_', name)}.png"
        _atomic_write(target, data)
        return target

    def remove(self, index: int):
        while (path := self.path(index)) is not None:
            path.unlink()
