"""Experimental camera zoom for the USA release, applied live by patching the running game's code in RAM.

The game draws the overworld through a fixed 320×240 window: background tiles and foreground pieces are 2D
quads placed by setPosDataPolyFT4 in coordinates whose (0, 0) is the screen centre, and 3D
characters are projected by the GTE with a per-map distance H (GsSetProjection(VIEWPORT_DISTANCE)). Zooming
out by a factor s requires scaling every background and foreground corner around (0, 0) while H
becomes s·VIEWPORT_DISTANCE; effects and sprites that read VIEWPORT_DISTANCE scale with it.

The patch installs a scaled copy of setPosDataPolyFT4 in RAM the game was not seen to use, and points the
background tile call (setPosDataMapTile) and the foreground piece calls (buildMapOverlayPrim) at it; the copy
also loads GTE H from VIEWPORT_DISTANCE every time it runs. The HUD and menus keep the original routine.
Addresses are from jype0/dw_decomp aaf69a4 (config/us/symbols.txt). Known limits of the prototype: the game
streams at most 4×3 background tiles, so a wider view shows unloaded (black) tiles at the edges, the camera
still stops where a 320×240 window would reach the map edge, and objects are culled at the old screen bounds.
"""
from __future__ import annotations

import struct

from .live import ram_offset

SET_POS_DATA_POLY_FT4 = 0x800E56EC
# buildMapOverlayPrim's relative-position call (foreground pieces). Its other call places snow and mist particles
# in absolute screen coordinates in front of the drawing-offset switch; those stay native. Background tiles are
# drawn by the tile engine, so setPosDataMapTile is not hooked.
CALL_SITES = (0x800B5074,)
VIEWPORT_DISTANCE = 0x80134ED4
DRAWING_OFFSET_X = 0x80134EC0
DRAWING_OFFSET_Y = 0x80134EC4
HOOK_CALLS = 0x801D0200
MAP_TILE_DATA = 0x8014F084
MAP_TILE_X = 0x80134DC2
MAP_TILE_Y = 0x80134DC4
ROUTINE = 0x801D0000  # inside 96 KB that stayed zero in every sampled save (0x801BFB76-0x801D7A40)
# Background tile engine (camera_engine.c): its code, its parameter block and its downscale buffer.
ENGINE_PARAMS = 0x801D3000
ENGINE_BUFFER = 0x801C0000
ENGINE_BUFFER_LIMIT = 0x801D0000  # the routine starts here; the downscale buffer holds one T × T tile
ENGINE_MAGIC = 0x4D5A5744  # 'DWZM'
ENGINE_PARAMS_SIZE = 252  # sizeof(Params) in camera_engine.c
# renderMap's tile loop starts by loading the ordering table and branching to the row test; these four words
# become "jal engine; nop; j after-loop; nop" so the engine draws the background instead.
RENDER_MAP_LOOP = 0x800D590C
RENDER_MAP_LOOP_WORDS = (0x8F8293A4, 0x0000A021, 0x8C420004, 0x10000071)
RENDER_MAP_AFTER_LOOP = 0x800D5AEC  # renderMapOverlays(...) setup
# The game's own background uploads (setupMap, scrolling) would overwrite the engine's ring while zoomed.
NATIVE_UPLOADS = {0x800D6C74: (0x27BDFFC8, 0xAFBF0028),  # uploadMapTileImages
                  0x800D8B50: (0x27BDFFD0, 0xAFBF0020),  # updateTileRow
                  0x800D8CD8: (0x27BDFFD0, 0xAFBF0024)}  # updateTileColumn
RING_X, RING_WIDTH, RING_HEIGHT, PAGE_HEIGHT = 768, 256, 384, 256  # native tile area in VRAM (16-bit units)
UPLOAD_BUDGET = 6  # tiles the engine may upload per frame (a full new row or column at 50%)
GPURAM_FIELD = b"\r&GPURAM[0][0]"  # Beetle PSX save-state field: 1024 × 512 16-bit VRAM
ONE = 256  # zoom factors are fixed point: 256 = 100%
GTE_H = 26  # GTE control register holding the projection distance

A0, A1, A2, A3, T0, T1, T2, T3, T4, T5, T6, T7, SP, RA = 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 29, 31


def _i(op, rs, rt, imm):
    return op << 26 | rs << 21 | rt << 16 | imm & 0xFFFF


def addiu(rt, rs, imm): return _i(0x09, rs, rt, imm)
def lh(rt, offset, base): return _i(0x21, base, rt, offset)
def lw(rt, offset, base): return _i(0x23, base, rt, offset)
def sh(rt, offset, base): return _i(0x29, base, rt, offset)
def lui(rt, imm): return _i(0x0F, 0, rt, imm)
def addu(rd, rs, rt): return rs << 21 | rt << 16 | rd << 11 | 0x21
def subu(rd, rs, rt): return rs << 21 | rt << 16 | rd << 11 | 0x23
def sw(rt, offset, base): return _i(0x2B, base, rt, offset)
def mult(rs, rt): return rs << 21 | rt << 16 | 0x18
def mflo(rd): return rd << 11 | 0x12
def sra(rd, rt, shift): return rt << 16 | rd << 11 | shift << 6 | 0x03
def ctc2(rt, rd): return 0x48C00000 | rt << 16 | rd << 11
def jr(rs): return rs << 21 | 0x08
def jal(target): return 0x0C000000 | (target & 0x0FFFFFFF) >> 2
def j(target): return 0x08000000 | (target & 0x0FFFFFFF) >> 2


def ring_layout(scale: int) -> tuple[int, list[int], list[int]]:
    """(tile size T, VRAM x of each ring column, VRAM y of each ring row) for a zoom of scale/256.

    The view shows 320/s × 240/s background pixels, so up to ceil(that / 128) + 1 tiles each way. Tiles are
    stored T × T (T ≈ 128·s, the size they are drawn at) in the native ring area, 8-bit, and may not cross a
    256-row texture page. The bottom texture coordinate must also fit in a byte,
    so a slot ending at the first page boundary would stretch a clipped tile."""
    need_columns = -(-320 * ONE // (scale * 128)) + 1
    need_rows = -(-240 * ONE // (scale * 128)) + 1
    for tile in range(min(128, 128 * scale // ONE) & ~1, 31, -2):
        columns = min(8, RING_WIDTH // (tile // 2))
        rows = [y for y in range(0, PAGE_HEIGHT - tile, tile)]
        rows += [y for y in range(PAGE_HEIGHT, RING_HEIGHT - tile + 1, tile)]
        if columns >= need_columns and len(rows) >= need_rows:
            return tile, [RING_X + column * (tile // 2) for column in range(columns)], rows[:8]
    raise ValueError(f"No background ring fits a {scale * 100 // ONE}% zoom.")


def engine_params(scale: int) -> bytes:
    """The engine's Params block (layout asserted in camera_engine.c) for a zoom of scale/256 (below 100%)."""
    tile, columns, rows = ring_layout(scale)
    if scale >= ONE or ENGINE_BUFFER + tile * tile > ENGINE_BUFFER_LIMIT:
        raise ValueError("The tile engine is only for zoomed-out views (100% uses the game's own renderer).")
    sample = bytes((index * 128 + 64) // tile for index in range(tile)).ljust(128, b"\0")
    return (struct.pack("<iiihhhhhh", ENGINE_MAGIC, 0, 0, scale, round(65536 / scale), tile, len(columns),
                        len(rows), UPLOAD_BUDGET)
            + struct.pack("<8h", *columns, *[0] * (8 - len(columns)))
            + struct.pack("<8h", *rows, *[0] * (8 - len(rows)))
            + struct.pack("<hhhhI", -1, 0, 0, 0, 0) + b"\xff" * 48 + sample + bytes(8))


def routine(scale: int) -> bytes:
    """setPosDataPolyFT4(prim, x, y, w, h) for foreground pieces, scaled around the screen centre, then GTE H
    loaded from VIEWPORT_DISTANCE and the call counted at HOOK_CALLS.

    Each frame the game sets the GPU drawing offset to DRAWING_OFFSET_X/Y (GsSetOrign) for everything behind
    ordering-table entry 32, and at entry 32 switches it to the screen centre (160, 120) for what is drawn in
    front (applyDrawOffset; dw_decomp src/main/main.c). Characters are in front and the GTE offset is 0, so a
    scaled H shrinks them towards the screen centre. Foreground pieces (depth >= 35) and background tiles are
    behind, drawn at primitive + DRAWING_OFFSET, so they must scale around primitive (160 - DOX, 120 - DOY).
    A MULT is never issued within two instructions of the previous MFLO (R3000 HI/LO rule)."""
    high = VIEWPORT_DISTANCE >> 16
    assert DRAWING_OFFSET_X >> 16 == high and DRAWING_OFFSET_Y >> 16 == high
    CX, CY = 24, 25
    words = [
        lui(T6, high),
        lw(CX, DRAWING_OFFSET_X & 0xFFFF, T6),
        lw(CY, DRAWING_OFFSET_Y & 0xFFFF, T6),
        lh(T0, 16, SP),            # h (fifth argument)
        addiu(T1, 0, 160),
        subu(CX, T1, CX),          # centre x = 160 - DRAWING_OFFSET_X
        addiu(T1, 0, 120),
        subu(CY, T1, CY),          # centre y = 120 - DRAWING_OFFSET_Y
        addiu(T1, 0, scale),
        subu(A1, A1, CX),          # x - centre
        subu(A2, A2, CY),          # y - centre
        mult(A1, T1),
        addu(T3, A1, A3),          # x + w - centre
        addu(T4, A2, T0),          # y + h - centre
        mflo(T2),
        sra(T2, T2, 8),
        addu(T2, T2, CX),
        sh(T2, 8, A0),             # x0
        mult(T3, T1),
        sh(T2, 24, A0),            # x2
        lw(T6, VIEWPORT_DISTANCE & 0xFFFF, T6),
        mflo(T3),
        sra(T3, T3, 8),
        addu(T3, T3, CX),
        sh(T3, 16, A0),            # x1
        mult(A2, T1),
        sh(T3, 32, A0),            # x3
        mflo(T5),
        sra(T5, T5, 8),
        addu(T5, T5, CY),
        sh(T5, 10, A0),            # y0
        mult(T4, T1),
        sh(T5, 18, A0),            # y1
        ctc2(T6, GTE_H),
        mflo(T7),
        sra(T7, T7, 8),
        addu(T7, T7, CY),
        sh(T7, 26, A0),            # y2
        lui(T6, HOOK_CALLS >> 16),
        lw(T0, HOOK_CALLS & 0xFFFF, T6),
        0,                         # R3000 load delay
        addiu(T0, T0, 1),
        sw(T0, HOOK_CALLS & 0xFFFF, T6),
        jr(RA),
        sh(T7, 34, A0),            # y3 (delay slot)
    ]
    return struct.pack(f"<{len(words)}I", *words)


ICACHE_FIELD = b"\x0bICache_Bulk"  # Beetle PSX save-state field: 1024 × (u32 tag, u32 instruction)
INVALID_TAG = 0x2  # Beetle stores each word's address with bit 1 set for an invalid word.


def flush_instruction_cache(core):
    """Make the emulated CPU refetch code from RAM, like the game calling FlushCache().

    The PS1 caches instructions; Beetle PSX emulates that, so code written into RAM from outside keeps running
    the old cached instructions. The cache is part of the save state, so the state is taken, every line is marked
    invalid, and the state is loaded straight back."""
    state = bytearray(core.serialize())
    at = state.find(ICACHE_FIELD)
    if at < 0:
        raise ValueError("This core's save state has no instruction cache field; live patching is unsupported.")
    size = struct.unpack_from("<I", state, at + len(ICACHE_FIELD))[0]
    start = at + len(ICACHE_FIELD) + 4
    if size % 8:
        raise ValueError("Malformed Beetle PSX instruction-cache save-state field.")
    for entry in range(start, start + size, 8):
        tag = struct.unpack_from("<I", state, entry)[0]
        struct.pack_into("<I", state, entry, tag | INVALID_TAG)
    core.unserialize(bytes(state))


def sync_instruction_cache(core, addresses):
    """Refresh only patched words that are already resident in Beetle's I-cache.

    A RAM write is sufficient for a not-yet-cached address: the CPU will miss and
    refill it normally.  For a resident line, replacing just the changed words
    avoids a save-state-wide invalidation, which older Beetle builds do not
    handle safely while a game is running.
    """
    state = bytearray(core.serialize())
    at = state.find(ICACHE_FIELD)
    if at < 0:
        return False
    size = struct.unpack_from("<I", state, at + len(ICACHE_FIELD))[0]
    if size % 8 or size != 1024 * 8:
        raise ValueError("Malformed Beetle PSX instruction-cache save-state field.")
    start = at + len(ICACHE_FIELD) + 4
    changed = False
    for address in sorted(set(addresses)):
        if address & 3:
            raise ValueError(f"Instruction address is not word aligned: {address:#x}")
        entry = start + (((address >> 2) & 0x3FF) * 8)
        tag = struct.unpack_from("<I", state, entry)[0]
        # Beetle ReadInstruction compares TV to the complete virtual address.
        # Bit 1 marks an invalid word; bits 2/3 belong to its address.
        if tag != address:
            continue
        word = core.read_memory(ram_offset(address), 4)
        struct.pack_into("<I", state, entry + 4, struct.unpack("<I", word)[0])
        changed = True
    if changed:
        core.unserialize(bytes(state))
    return changed


class CameraZoom:
    """Live zoom on a running LibretroCore: install once, then change the factor at any time.

    Below 100% the background is drawn by the tile engine (camera_engine.c) from its own ring of downscaled
    tiles, foreground pieces by the scaled quad routine, and 3D through a scaled projection. Saving or leaving
    zoom restores the game's code, projection and its own 4×3 tile ring, so saved states are native."""

    def __init__(self, core):
        self.core = core
        self.scale = ONE
        self.requested_scale = ONE
        self.installed = False
        self._original: dict[int, bytes] = {}
        self._base_distance: int | None = None
        self._written_distance: int | None = None
        self._last_frames = 0

    def _read_u32(self, address: int) -> int:
        return struct.unpack("<I", self.core.read_memory(ram_offset(address), 4))[0]

    def _write(self, address: int, data: bytes):
        self.core.write_memory(ram_offset(address), data)

    def _patch(self, address: int, words):
        self._original.setdefault(address, self.core.read_memory(ram_offset(address), 4 * len(words)))
        self._write(address, struct.pack(f"<{len(words)}I", *words))

    def install(self, scale: int | None = None):
        """Patch the running game for a zoom of scale/256 (default: the requested zoom), which must be below 100%."""
        if self.installed:
            return
        if scale is not None:
            self.requested_scale = max(64, min(ONE, int(scale)))
        if self.requested_scale >= ONE:
            raise ValueError("Choose a zoom below 100% to install the camera patch.")
        from .camera_engine_code import CODE, ENTRY, LOAD_ADDRESS
        current = [self._read_u32(site) for site in CALL_SITES]
        if current == [jal(ROUTINE)] * len(CALL_SITES) or self._read_u32(RENDER_MAP_LOOP) == jal(ENTRY):
            raise ValueError("This save contains an earlier camera patch. Load a save made with native zoom.")
        for site, value in zip(CALL_SITES, current):
            if value != jal(SET_POS_DATA_POLY_FT4):
                raise ValueError(f"The game code at {site:#x} is not the expected call; is this the USA release?")
        loop = struct.unpack("<4I", self.core.read_memory(ram_offset(RENDER_MAP_LOOP), 16))
        uploads = {address: struct.unpack("<2I", self.core.read_memory(ram_offset(address), 8))
                   for address in NATIVE_UPLOADS}
        if loop != RENDER_MAP_LOOP_WORDS or uploads != NATIVE_UPLOADS:
            raise ValueError("renderMap or the tile uploads are not the expected USA code.")
        if any(self.core.read_memory(ram_offset(ROUTINE), ENGINE_PARAMS + ENGINE_PARAMS_SIZE - ROUTINE)) or any(
                self.core.read_memory(ram_offset(ENGINE_BUFFER), ENGINE_BUFFER_LIMIT - ENGINE_BUFFER)):
            raise ValueError("The RAM chosen for the camera code is in use in this game state.")
        self._write(ROUTINE, routine(ONE))
        self._write(HOOK_CALLS, bytes(4))
        self._write(LOAD_ADDRESS, CODE)
        self._write(ENGINE_PARAMS, engine_params(self.requested_scale))
        self.scale = ONE  # foreground and projection follow once the engine is seen running
        for site in CALL_SITES:
            self._patch(site, [jal(ROUTINE)])
        self._patch(RENDER_MAP_LOOP, [jal(ENTRY), 0, j(RENDER_MAP_AFTER_LOOP), 0])
        for address in NATIVE_UPLOADS:
            self._patch(address, [jr(RA), 0])
        self.installed = True
        self._last_frames = 0
        self._sync()

    def _sync(self):
        from .camera_engine_code import CODE, LOAD_ADDRESS
        words = [*CALL_SITES, *range(RENDER_MAP_LOOP, RENDER_MAP_LOOP + 16, 4),
                 *(address + offset for address in NATIVE_UPLOADS for offset in (0, 4)),
                 *range(ROUTINE, ROUTINE + len(routine(ONE)), 4), *range(LOAD_ADDRESS, LOAD_ADDRESS + len(CODE), 4)]
        sync_instruction_cache(self.core, words)

    def set_scale(self, scale: int):
        """Zoom factor in 1/256 (256 = 100%, 192 = 75%: shows a third more in each direction)."""
        self.requested_scale = max(64, min(ONE, int(scale)))
        self.tick()

    def tick(self):
        """Apply the requested zoom once the engine is running, and keep the projection at the map's own value
        times the zoom (a map load resets it)."""
        if not self.installed:
            return
        frames = self._read_u32(ENGINE_PARAMS + 4)
        if frames == self._last_frames:
            return  # never shrink characters alone while the background is not being drawn by the engine
        self._last_frames = frames
        if self.requested_scale != self.scale and self.requested_scale < ONE:
            self.scale = self.requested_scale
            self._write(ROUTINE, routine(self.scale))
            sync_instruction_cache(self.core, range(ROUTINE, ROUTINE + len(routine(self.scale)), 4))
            params = bytearray(engine_params(self.scale))
            params[4:12] = self.core.read_memory(ram_offset(ENGINE_PARAMS + 4), 8)  # keep the counters
            if self.core.read_memory(ram_offset(ENGINE_PARAMS + 16), 2) == params[16:18]:
                params[56:116] = self.core.read_memory(ram_offset(ENGINE_PARAMS + 56), 60)  # same ring: keep tiles
            self._write(ENGINE_PARAMS, bytes(params))
        distance = self._read_u32(VIEWPORT_DISTANCE)
        if distance != self._written_distance:
            self._base_distance = distance  # a value we did not write: the current map's projection
        wanted = self._base_distance * self.scale // ONE
        if wanted != distance:
            self._write(VIEWPORT_DISTANCE, struct.pack("<I", wanted))
        self._written_distance = wanted

    @property
    def engine_frames(self) -> int:
        return self._read_u32(ENGINE_PARAMS + 4) if self.installed else 0

    @property
    def engine_uploads(self) -> int:
        return self._read_u32(ENGINE_PARAMS + 8) if self.installed else 0

    def _restore_native_ring(self):
        """Put the game's own 4×3 ring of full-size tiles back into VRAM, as uploadMapTileImages would."""
        base = ram_offset(MAP_TILE_DATA)
        width, height = struct.unpack("<bb", self.core.read_memory(base + 0x1BB8, 2))
        tile_x = struct.unpack("<b", self.core.read_memory(ram_offset(MAP_TILE_X), 1))[0]
        tile_y = struct.unpack("<b", self.core.read_memory(ram_offset(MAP_TILE_Y), 1))[0]
        if not (0 < width and 0 < height and width * height <= 35 and 0 <= tile_x < width and 0 <= tile_y < height):
            return
        state = bytearray(self.core.serialize())
        at = state.find(GPURAM_FIELD)
        if at < 0:
            raise ValueError("This core's save state has no VRAM field.")
        vram = at + len(GPURAM_FIELD) + 4
        for row in range(min(3, height - tile_y)):
            for column in range(min(4, width - tile_x)):
                index = tile_x + column + (tile_y + row) * width
                image, tile_id, _, _, tex_u, tex_v = struct.unpack("<Ihhhhh", self.core.read_memory(base + index * 20, 14))
                pixels = (self.core.read_memory(ram_offset(image), 128 * 128)
                          if tile_id != -1 and image else bytes(128 * 128))
                for line in range(128):
                    start = vram + ((tex_v + line) * 1024 + tex_u) * 2
                    state[start:start + 128] = pixels[line * 128:(line + 1) * 128]
        self.core.unserialize(bytes(state))

    def uninstall(self):
        """Put the game's code, projection and background ring back."""
        if not self.installed:
            return
        from .camera_engine_code import CODE, LOAD_ADDRESS
        for address, data in self._original.items():
            self._write(address, data)
        if self._base_distance is not None:
            self._write(VIEWPORT_DISTANCE, struct.pack("<I", self._base_distance))
        self.installed = False
        self._write(ROUTINE, bytes(len(routine(ONE))))
        self._write(HOOK_CALLS, bytes(4))
        self._write(LOAD_ADDRESS, bytes(len(CODE)))
        self._write(ENGINE_PARAMS, bytes(ENGINE_PARAMS_SIZE))
        self._write(ENGINE_BUFFER, bytes(ENGINE_BUFFER_LIMIT - ENGINE_BUFFER))  # last downscaled tile
        self._sync()
        self._restore_native_ring()
        self._original.clear()
