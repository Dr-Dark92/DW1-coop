"""On-screen map and partner HP/MP for the USA release, drawn over each finished frame in the game's own HUD style.

The game draws its status bars at x = STATUS_UI_OFFSET_X + 160, y = 205 and 217 and slides them off screen while game
time is stopped, as in battles and menus (renderStatusBars, dw_decomp src/main/partner_impl.c). The HP and MP bars
follow it, 12 lines apart as the game's are, using the game's own bar frame from VRAM; the map slides with them.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter

from .live import LiveState, ram_offset
from .maps import follow_kind, trigger_kind
from .progression import CURRENT_HP, CURRENT_MP, PARTNER_ENTITY, PARTNER_STATS, STAT_FIELDS

STATUS_UI_OFFSET_X = 0x80134C50  # i16: 75 while the status bars show, up to 240 while they are off screen
OFFSET_SHOWN, OFFSET_HIDDEN = 75, 240
TAMER_POSITION = 0x8015576C
BAR_WIDTH, BAR_HEIGHT, BAR_STEP = 75, 8, 12
FIRST_BAR_Y = 181  # the game's own bars are at 205 and 217
SEGMENTS, SEGMENT = 25, 2  # the game's fill: 25 blocks of 2 × 2 from x + 18, y + 3
MAP_SIDE, MAP_RIGHT, MAP_TOP = 52, 316, 4
MAP_INSET = 2
MAP_OPACITY = 0.6  # the game shows through the map; the markers stay solid
MAP_DETAIL = 4  # the map panel is prepared at this many pixels per game pixel
# The status-bar frame in VRAM: 75 × 8 at (0, 236) of the 4-bit page at x = 320, drawn with the CLUT at (96, 500).
BAR_PAGE_X, BAR_V, BAR_CLUT_X, BAR_CLUT_Y = 320, 236, 96, 500
GPURAM_FIELD = b"\r&GPURAM[0][0]"

FRAME_BLUE, FRAME_LIGHT, FRAME_MID, FRAME_DARK = "#3080c0", "#c8c8c8", "#707070", "#000000"
PANEL = QColor(16, 24, 40, 150)  # the workbench's in-game menus use this navy
FILLS = {"hp": ("#f08080", "#c02830"), "mp": ("#6098c0", "#0058b0")}
EMPTY = "#282828"
MAP_COLOURS = {"walkable": QColor(74, 112, 84), "exit": QColor("#fad990"), "toilet": QColor("#6098c0")}
OTHER_TILE = QColor(96, 136, 104)  # script triggers and the like: still floor
NPC_COLOURS = {"chases you": QColor(232, 65, 60), "watches you": QColor(242, 201, 76), "ignores you": QColor(255, 154, 31)}

HEART = ("..XX...XX..", ".XRRX.XRRX.", "XRWRRXRRRRX", "XRRRRRRRRRX", "XRRRRRRRRRX", ".XRRRRRRRX.",
         "..XRRRRRX..", "...XRRRX...", "....XRX....", ".....X.....", "...........")
ORB = (".....X.....", "....XBX....", "....XBX....", "...XBBBX...", "..XBLBBBX..", ".XBLBBBBBX.", ".XBLBBBBBX.",
       ".XBBBBBBBX.", "..XBBBBBX..", "...XXXXX...", "...........")
ICON_COLOURS = {"X": "#000000", "R": "#d83038", "W": "#ffd0d0", "B": "#2070d0", "L": "#a0d0ff"}
DIGITS = {"0": "111101101101111", "1": "010110010010111", "2": "111001111100111", "3": "111001111001111",
          "4": "101101111001001", "5": "111100111001111", "6": "111100111101111", "7": "111001010010010",
          "8": "111101111101111", "9": "111101111001111", "/": "001001010100100"}


@dataclass(frozen=True)
class HudState:
    offset: int  # STATUS_UI_OFFSET_X
    hp: int
    max_hp: int
    mp: int
    max_mp: int
    live: LiveState | None
    tamer: tuple[float, float] | None
    partner: tuple[float, float] | None


def _floor_position(read, entity: int) -> tuple[float, float] | None:
    """An entity's place on the 100 × 100 floor grid, in fractions of a tile so the marker moves smoothly."""
    pointer = struct.unpack("<I", read(ram_offset(entity + 4), 4))[0]
    if not 0x80000000 <= pointer < 0x80200000 - 136:
        return None
    x, _, z = struct.unpack("<3i", read(ram_offset(pointer + 120), 12))
    return x / 100 + 50, 50 - z / 100


def read_hud(read, live: LiveState | None) -> HudState | None:
    """What to draw this frame, or None while the game's own status bars are off screen or there is no partner."""
    offset = struct.unpack("<h", read(ram_offset(STATUS_UI_OFFSET_X), 2))[0]
    kind = struct.unpack("<i", read(ram_offset(PARTNER_ENTITY), 4))[0]
    if not OFFSET_SHOWN <= offset < OFFSET_HIDDEN or not 0 < kind < 180:
        return None
    fields = dict(STAT_FIELDS)
    block = read(ram_offset(PARTNER_STATS), 24)
    max_hp, max_mp = struct.unpack_from("<h", block, fields["hp"])[0], struct.unpack_from("<h", block, fields["mp"])[0]
    hp, mp = struct.unpack_from("<h", block, CURRENT_HP)[0], struct.unpack_from("<h", block, CURRENT_MP)[0]
    if max_hp <= 0 or max_mp < 0:
        return None
    return HudState(offset, max(hp, 0), max_hp, max(mp, 0), max_mp, live, _floor_position(read, TAMER_POSITION),
                    _floor_position(read, PARTNER_ENTITY))


def native_bar(state: bytes) -> QImage | None:
    """The game's status-bar frame, decoded from the player's own VRAM; None if it is not resident."""
    at = state.find(GPURAM_FIELD)
    if at < 0:
        return None
    vram = state[at + len(GPURAM_FIELD) + 4:at + len(GPURAM_FIELD) + 4 + 1024 * 512 * 2]
    if len(vram) != 1024 * 512 * 2:
        return None
    image = QImage(BAR_WIDTH, BAR_HEIGHT, QImage.Format.Format_ARGB32)
    shown = 0
    for y in range(BAR_HEIGHT):
        for x in range(BAR_WIDTH):
            word = struct.unpack_from("<H", vram, ((BAR_V + y) * 1024 + BAR_PAGE_X + x // 4) * 2)[0]
            index = (word >> ((x % 4) * 4)) & 15
            colour = struct.unpack_from("<H", vram, (BAR_CLUT_Y * 1024 + BAR_CLUT_X + index) * 2)[0]
            image.setPixelColor(x, y, QColor((colour & 31) * 255 // 31, ((colour >> 5) & 31) * 255 // 31,
                                             ((colour >> 10) & 31) * 255 // 31, 0 if colour == 0 else 255))
            shown += colour != 0
    return image if shown > BAR_WIDTH * BAR_HEIGHT // 2 else None


def fallback_bar() -> QImage:
    """The same frame drawn by hand, for the moments the game's own is not in VRAM."""
    image = QImage(BAR_WIDTH, BAR_HEIGHT, QImage.Format.Format_ARGB32)
    image.fill(QColor(0, 0, 0, 0))
    painter = QPainter(image)
    painter.fillRect(0, 0, BAR_WIDTH, BAR_HEIGHT, QColor(FRAME_DARK))
    painter.fillRect(0, 0, BAR_WIDTH, 1, QColor(FRAME_BLUE))
    painter.fillRect(0, BAR_HEIGHT - 1, BAR_WIDTH, 1, QColor(FRAME_BLUE))
    painter.fillRect(1, 1, BAR_WIDTH - 2, 1, QColor(FRAME_MID))
    painter.fillRect(15, 2, 2, BAR_HEIGHT - 3, QColor(FRAME_LIGHT))
    painter.end()
    return image


def map_tiles(grid: bytes) -> tuple[QImage, tuple[int, int, int]] | None:
    """The floor as an image of just the part of the 100 × 100 grid in use, and that square's (left, top, side)."""
    used = [(index % 100, index // 100) for index, value in enumerate(grid) if not value & 0x80]
    if not used:
        return None
    xs, ys = [x for x, _ in used], [y for _, y in used]
    side = max(max(xs) - min(xs), max(ys) - min(ys)) + 3
    left, top = (min(xs) + max(xs) + 1 - side) // 2, (min(ys) + max(ys) + 1 - side) // 2
    image = QImage(side, side, QImage.Format.Format_ARGB32)
    image.fill(QColor(0, 0, 0, 0))
    for x, y in used:
        kind = trigger_kind(grid[x + y * 100])
        image.setPixelColor(x - left, y - top, MAP_COLOURS.get(kind, OTHER_TILE))
    return image, (left, top, side)


class HudPainter:
    """Draws the HUD at the image's own size; everything is laid out in the game's 320 × 240 units."""

    def __init__(self, serialize):
        self.serialize = serialize
        self._bar: QImage | None = None
        self._bar_tries = 0
        self._grid = None
        self._tiles = None
        self._panel = None

    def _frame(self) -> QImage:
        if self._bar is None and self._bar_tries < 3:
            self._bar_tries += 1
            self._bar = native_bar(self.serialize())
        return self._bar if self._bar is not None else fallback_bar()

    def prepare(self):
        """Read the game's bar art on the emulator thread (the core may only be used there)."""
        self._frame()

    def draw(self, image: QImage, state: HudState) -> None:
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        scale = image.width() / 320
        painter.scale(scale, image.height() / 240)
        x = state.offset + 160
        self._bar_row(painter, x, FIRST_BAR_Y, "hp", HEART, state.hp, state.max_hp)
        self._bar_row(painter, x, FIRST_BAR_Y + BAR_STEP, "mp", ORB, state.mp, state.max_mp)
        if state.live is not None:
            self._map(painter, state, state.offset - OFFSET_SHOWN)
        painter.end()

    def _bar_row(self, painter, x, y, kind, icon, value, maximum):
        painter.drawImage(QRectF(x, y, BAR_WIDTH, BAR_HEIGHT), self._frame())
        filled = round(SEGMENTS * min(value, maximum) / maximum) if maximum else 0
        light, dark = FILLS[kind]
        fill_x, fill_y = x + 18, y + 3
        painter.fillRect(QRectF(fill_x, fill_y, SEGMENTS * SEGMENT, SEGMENT), QColor(EMPTY))
        if filled:
            painter.fillRect(QRectF(fill_x, fill_y, filled * SEGMENT, 1), QColor(light))
            painter.fillRect(QRectF(fill_x, fill_y + 1, filled * SEGMENT, 1), QColor(dark))
        for row, line in enumerate(icon):
            for column, cell in enumerate(line):
                if cell != ".":
                    painter.fillRect(QRectF(x + 3 + column, y - 2 + row, 1, 1), QColor(ICON_COLOURS[cell]))
        self._number(painter, x - 2, y + 2, f"{value}/{maximum}")

    @staticmethod
    def _number(painter, right, top, text):
        """Right-aligned, in the 3 × 5 block digits, outlined so it reads over any background."""
        width = len(text) * 4 - 1
        painter.fillRect(QRectF(right - width - 1, top - 1, width + 2, 7), QColor(0, 0, 0, 170))
        for place, character in enumerate(text):
            left = right - width + place * 4
            pattern = DIGITS[character]
            for row in range(5):
                for column in range(3):
                    if pattern[row * 3 + column] == "1":
                        painter.fillRect(QRectF(left + column, top + row, 1, 1), QColor("#ffffff"))

    def _map_panel(self, live) -> QImage:
        """The frame and floor as one image, so the whole panel can be drawn see-through; redone when the grid changes."""
        if live.grid != self._grid or self._panel is None:
            self._grid, self._tiles = live.grid, map_tiles(live.grid)
            side = MAP_SIDE * MAP_DETAIL
            panel = QImage(side, side, QImage.Format.Format_ARGB32_Premultiplied)
            panel.fill(QColor(0, 0, 0, 0))
            painter = QPainter(panel)
            painter.scale(MAP_DETAIL, MAP_DETAIL)
            painter.fillRect(QRectF(0, 0, MAP_SIDE, MAP_SIDE), QColor(FRAME_BLUE))
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            painter.fillRect(QRectF(1, 1, MAP_SIDE - 2, MAP_SIDE - 2), PANEL)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            if self._tiles is not None:
                painter.drawImage(QRectF(MAP_INSET, MAP_INSET, MAP_SIDE - 2 * MAP_INSET, MAP_SIDE - 2 * MAP_INSET),
                                  self._tiles[0])
            painter.end()
            self._panel = panel
        return self._panel

    def _map(self, painter, state, slide):
        left, top = MAP_RIGHT - MAP_SIDE + slide, MAP_TOP
        live = state.live
        panel = self._map_panel(live)
        painter.setOpacity(MAP_OPACITY)
        painter.drawImage(QRectF(left, top, MAP_SIDE, MAP_SIDE), panel)
        painter.setOpacity(1)
        if self._tiles is None:
            return
        _, (first_x, first_y, side) = self._tiles
        inner = QRectF(left + MAP_INSET, top + MAP_INSET, MAP_SIDE - 2 * MAP_INSET, MAP_SIDE - 2 * MAP_INSET)
        cell = inner.width() / side
        place = lambda x, y: QPointF(inner.left() + (x - first_x) * cell, inner.top() + (y - first_y) * cell)
        painter.setClipRect(inner)
        for npc in live.npcs:
            self._marker(painter, place(npc.tile[0] + 0.5, npc.tile[1] + 0.5), NPC_COLOURS[follow_kind(npc.follow_mode)], 1.5)
        if state.partner is not None:
            self._marker(painter, place(*state.partner), QColor("#50d8f0"), 1.5)
        if state.tamer is not None:
            self._marker(painter, place(*state.tamer), QColor("#ffffff"), 2)
        painter.setClipping(False)

    @staticmethod
    def _marker(painter, centre, colour, size):
        half = size / 2
        painter.fillRect(QRectF(centre.x() - half - 0.5, centre.y() - half - 0.5, size + 1, size + 1), QColor("#000000"))
        painter.fillRect(QRectF(centre.x() - half, centre.y() - half, size, size), colour)
