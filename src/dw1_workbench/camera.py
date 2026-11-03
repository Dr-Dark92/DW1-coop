"""Experimental camera zoom for the USA release, applied live by patching the running game's code in RAM.

The game draws the overworld through a fixed 320×240 window: background tiles and foreground pieces are 2D
quads placed by setPosDataPolyFT4 around a drawing origin at the screen centre (GsInitGraph mode 4), and 3D
characters are projected by the GTE with a per-map distance H (GsSetProjection(VIEWPORT_DISTANCE)). Zooming
out by a factor s keeps them aligned if every background and foreground quad corner is multiplied by s and H
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
CALL_SITES = (0x800D71F8,  # setPosDataMapTile: background tiles
              0x800B5074, 0x800B509C)  # buildMapOverlayPrim: foreground pieces (both drawing modes)
VIEWPORT_DISTANCE = 0x80134ED4
ROUTINE = 0x801D0000  # inside 96 KB that stayed zero in every sampled save (0x801BFB76-0x801D7A40)
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
def mult(rs, rt): return rs << 21 | rt << 16 | 0x18
def mflo(rd): return rd << 11 | 0x12
def sra(rd, rt, shift): return rt << 16 | rd << 11 | shift << 6 | 0x03
def ctc2(rt, rd): return 0x48C00000 | rt << 16 | rd << 11
def jr(rs): return rs << 21 | 0x08
def jal(target): return 0x0C000000 | (target & 0x0FFFFFFF) >> 2


def routine(scale: int) -> bytes:
    """setPosDataPolyFT4(prim, x, y, w, h) with every corner multiplied by scale/256, then GTE H loaded from
    VIEWPORT_DISTANCE. Multiplies are spaced at least two instructions after the previous MFLO (R3000 rule)."""
    high, low = VIEWPORT_DISTANCE >> 16, VIEWPORT_DISTANCE & 0xFFFF
    words = [
        lh(T0, 16, SP),            # h (fifth argument)
        addiu(T1, 0, scale),
        mult(A1, T1),              # x
        addu(T3, A1, A3),          # x + w
        addu(T4, A2, T0),          # y + h
        mflo(T2),
        sra(T2, T2, 8),
        sh(T2, 8, A0),             # x0
        mult(T3, T1),
        sh(T2, 24, A0),            # x2
        lui(T6, high + (low >= 0x8000)),
        mflo(T3),
        sra(T3, T3, 8),
        sh(T3, 16, A0),            # x1
        mult(A2, T1),              # y
        sh(T3, 32, A0),            # x3
        lw(T6, low, T6),           # VIEWPORT_DISTANCE
        mflo(T5),
        sra(T5, T5, 8),
        sh(T5, 10, A0),            # y0
        mult(T4, T1),
        sh(T5, 18, A0),            # y1
        ctc2(T6, GTE_H),
        mflo(T7),
        sra(T7, T7, 8),
        sh(T7, 26, A0),            # y2
        jr(RA),
        sh(T7, 34, A0),            # y3 (delay slot)
    ]
    return struct.pack(f"<{len(words)}I", *words)


ICACHE_FIELD = b"\x0bICache_Bulk"  # Beetle PSX save-state field: 1024 × (u32 tag, u32 instruction)
INVALID_TAG = 0x2  # tags of valid lines equal the instruction's address, which is 4-aligned


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
    for entry in range(start, start + size, 8):
        state[entry] |= INVALID_TAG
    core.unserialize(bytes(state))


class CameraZoom:
    """Live zoom on a running LibretroCore: install once, then change the factor at any time."""

    def __init__(self, core):
        self.core = core
        self.scale = ONE
        self.installed = False
        self._original_calls: dict[int, bytes] = {}
        self._base_distance: int | None = None
        self._written_distance: int | None = None

    def _read_u32(self, address: int) -> int:
        return struct.unpack("<I", self.core.read_memory(ram_offset(address), 4))[0]

    def _write(self, address: int, data: bytes):
        self.core.write_memory(ram_offset(address), data)

    def install(self):
        if self.installed:
            return
        expected = jal(SET_POS_DATA_POLY_FT4)
        for site in CALL_SITES:
            if self._read_u32(site) != expected:
                raise ValueError(f"The game code at {site:#x} is not the expected call; is this the USA release?")
        if any(self.core.read_memory(ram_offset(ROUTINE), len(routine(ONE)))):
            raise ValueError("The RAM chosen for the zoom routine is in use in this game state.")
        self._write(ROUTINE, routine(self.scale))
        for site in CALL_SITES:
            self._original_calls[site] = self.core.read_memory(ram_offset(site), 4)
            self._write(site, struct.pack("<I", jal(ROUTINE)))
        self.installed = True
        flush_instruction_cache(self.core)

    def set_scale(self, scale: int):
        """Zoom factor in 1/256 (256 = 100%, 192 = 75%: shows a third more in each direction). 100% leaves the
        routine in place doing exactly what the original does."""
        self.scale = max(64, min(ONE, int(scale)))
        if self.installed:
            self._write(ROUTINE, routine(self.scale))
            flush_instruction_cache(self.core)
        self.tick()

    def tick(self):
        """Keep VIEWPORT_DISTANCE at the map's own value times the zoom; a map load resets it to its own value."""
        if not self.installed:
            return
        distance = self._read_u32(VIEWPORT_DISTANCE)
        if distance != self._written_distance:
            self._base_distance = distance  # a value we did not write: the current map's projection
        wanted = self._base_distance * self.scale // ONE
        if wanted != distance:
            self._write(VIEWPORT_DISTANCE, struct.pack("<I", wanted))
        self._written_distance = wanted

    def uninstall(self):
        """Put the game's code and projection back (the routine itself stays, unused)."""
        if not self.installed:
            return
        for site, data in self._original_calls.items():
            self._write(site, data)
        if self._base_distance is not None:
            self._write(VIEWPORT_DISTANCE, struct.pack("<I", self._base_distance))
        self.installed = False
        flush_instruction_cache(self.core)
