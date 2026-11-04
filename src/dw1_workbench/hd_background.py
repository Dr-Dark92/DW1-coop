"""Full-size zone backgrounds in the player, for the USA release on a PC.

The game keeps backgrounds as 128×128 8-bit tiles, so a repainted picture imported into the disc is shrunk and
reduced to 256 colours. This layer leaves the game alone and works on each finished frame instead. It rebuilds what
the game's own background looks like at the current camera from RAM (the tiles stay there, identical to the VRAM
ring, dw_decomp src/main/map.c), compares it with the frame, and:

- a pixel equal to the game's background, or the same pixel uniformly darkened (shadows, fades), comes from the
  full-size picture, darkened by the same amount;
- anything else (characters, the HUD, foreground pieces with their own palette, menus) stays the game's pixel.

Foreground pieces cut from the background palette need nothing special: where they cover a character the frame
equals the background, so the picture shows there too. A frame with too little background (cutscenes, menus, map
changes) is passed through unchanged.

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


def screen_shift(frame: np.ndarray, palette: np.ndarray, indices: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """A screen-wide shift (a fade or tint), as the commonest difference from the background in a sample."""
    sample = frame[::7, ::7]
    if not valid[::7, ::7].any():
        return np.zeros(3, np.int16)
    keep = valid[::7, ::7]
    native = palette[indices[::7, ::7]][keep]
    sample = sample[keep]
    levels = np.stack(((sample >> 19) & 31, (sample >> 11) & 31, (sample >> 3) & 31), axis=1).astype(np.int16)
    return np.array([np.bincount(levels[:, c] - native[:, c] + 31, minlength=63).argmax() - 31 for c in range(3)], np.int16)


def compose(frame: np.ndarray, palette: np.ndarray, indices: np.ndarray, valid: np.ndarray, picture,
            alternate: np.ndarray | None = None) -> Composed:
    """Merge one frame with the picture. All arrays cover the same screen window.

    `frame` is uint32 0x00RRGGBB (H × W at any whole multiple of the screen), `palette` 256 × 3 in 5-bit levels,
    `indices` and `valid` H × W (valid is False where no background tile lies). `picture(shift)` returns the screen's
    picture, uint32 at a whole multiple of the frame, with the time of day and that screen-wide shift applied.
    `alternate` is the source row one above on lines where upscaled rendering may round either way.
    Most pixels equal the background exactly, so only the others get the slower test."""
    shift = screen_shift(frame, palette, indices, valid)
    expected_levels = np.clip(palette + shift, 0, 31).astype(np.uint16)  # the game's fades clamp at black
    packed = frame_packed(frame)
    expected = _packed(expected_levels)
    exact = valid & (packed == expected[indices])
    keep = exact
    rows, columns = np.nonzero(valid & ~exact)
    residual = None
    if rows.size:
        here = frame[rows, columns]
        levels = np.stack(((here >> 19) & 31, (here >> 11) & 31, (here >> 3) & 31), axis=1).astype(np.int16)
        relative = levels - expected_levels[indices[rows, columns]].astype(np.int16)
        high, low = relative.max(axis=1), relative.min(axis=1)
        accepted = (high - low <= SPREAD) & (high <= 1) & (low >= -DARKEST)
        if alternate is not None:
            other_row = packed[rows, columns] == expected[alternate[rows, columns]]
            accepted |= other_row
            relative[other_row] = 0
        keep = exact.copy()
        keep[rows[accepted], columns[accepted]] = True
        residual = rows[accepted], columns[accepted], relative[accepted]
    coverage = float(np.count_nonzero(keep)) / keep.size
    output = 0xFF000000 | frame
    if coverage < MIN_COVERAGE:
        return Composed(output, coverage)
    layer = picture(shift)
    repeat = layer.shape[0] // frame.shape[0]
    if repeat > 1:
        output = np.repeat(np.repeat(output, repeat, axis=0), repeat, axis=1)
    if residual is not None and residual[0].size:
        layer = _shade(layer, *residual, repeat)
    mask = np.repeat(np.repeat(keep, repeat, axis=0), repeat, axis=1) if repeat > 1 else keep
    return Composed(np.where(mask, layer, output), coverage)


def _shifted(tables: np.ndarray | None, shift: np.ndarray) -> np.ndarray:
    base = np.tile(np.arange(256, dtype=np.int16), (3, 1)) if tables is None else tables.astype(np.int16)
    return np.clip(base + shift[:, None] * 8, 0, 255).astype(np.uint8)


def _tint(layer: np.ndarray, tables: np.ndarray) -> np.ndarray:
    return (0xFF000000 | tables[0][(layer >> 16) & 255].astype(np.uint32) << 16
            | tables[1][(layer >> 8) & 255].astype(np.uint32) << 8 | tables[2][layer & 255])


def _shade(layer: np.ndarray, rows, columns, relative, repeat: int) -> np.ndarray:
    """Darken the picture by the frame's own difference from the background, only at those pixels."""
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

    def window(self, camera_x: int, camera_y: int, factor: int = 1) -> tuple[np.ndarray, np.ndarray]:
        """Indices and validity of the screen at this camera, at `factor` lines per screen line; maps smaller than
        the screen are padded invalid. renderMap draws some tile rows with 127 texels over 128 lines, which the
        rasterizer rounds per drawn line: source row = floor(line × 127 / (128 × factor) + 1/2). That is exact at 1×
        (96% of those lines measured, the rest being characters); above it the rounding varies with the camera, so
        `alternate` gives the row above for those lines."""
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
        cols = np.where(inside_x, columns, 0)
        grid = np.ix_(rows, cols)
        indices = self.indices[grid]
        valid = self.valid[grid] & inside_y[:, None] & inside_x[None, :]
        self.alternate = None
        if factor > 1 and squeezed.any():
            above = np.where(squeezed, np.maximum(rows - 1, tile_rows * TILE), rows)
            self.alternate = self.indices[np.ix_(above, cols)]
        return indices, valid


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
        self._palette = rgb5(vram[start:start + 512])
        pointer = struct.unpack("<I", self.read(ram_offset(MAP_CLUTS), 4))[0]
        base = rgb5(self.read(ram_offset(pointer), 512)) if 0x80000000 <= pointer < 0x80200000 - 512 else self._palette
        self._tables = channel_tables(base, self._palette)

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
        indices, valid = self.background.window(camera_x, camera_y, factor)
        return (frame, self._palette, indices, valid, (self._picture, self._tables, camera_x, camera_y),
                self.background.alternate)

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
        frame, palette, indices, valid, (picture, tables, camera_x, camera_y), alternate = job

        def screen_picture(shift):
            look = self._look(picture, tables, shift)
            if look is None:
                return _tint(picture_window(picture, camera_x, camera_y, self.scale), _shifted(tables, shift))
            return picture_window(look, camera_x, camera_y, self.scale)

        result = compose(frame, palette, indices, valid, screen_picture, alternate)
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
            except (ValueError, IndexError, MemoryError) as exc:
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
