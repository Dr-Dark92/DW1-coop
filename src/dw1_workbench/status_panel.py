"""LVL and NEXT on the game's Status screen, beside WEIGHT and in its style (USA, with the P6 game rules).

The screen's labels are baked into the game's menu art, which has no L, V, N or X, so they are drawn from the same
plate and strokes in `LETTERS`. The digits are the game's own, read from video memory. Layout in 320 × 240 units.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QImage, QPainter

from .hud_view import GPURAM_FIELD
from .live import ram_offset

UI_BOX_DATA = 0x8013D390  # 36 bytes each; the Digimon menu is box 1
BOX_FRAME, BOX_STATE, BOX_RENDER = 16, 18, 32
BOX_OPEN_FRAME, BOX_OPEN_STATE = 5, 1
RENDER_DIGIMON_MENU = 0x800B93D4
MENU_STATE, DIGIMON_MENU_STATE = 0x80134D34, 0x80134D36  # 1 once drawn; 0 is the Status tab

DIGIT_X, DIGIT_Y, DIGIT_WIDTH, DIGIT_HEIGHT = 704, 496, 8, 10  # 4-bit texels in VRAM, 0-9 side by side
PLATE, STROKE = QColor(80, 216, 240), QColor(72, 56, 224)
BOX_TOP_LEFT, BOX_BOTTOM_RIGHT, BOX_FILL, TEXT = QColor(0, 0, 0), QColor(104, 128, 160), QColor(48, 72, 88), QColor(208, 208, 208)
LABEL_Y, LABEL_HEIGHT = 86, 10
BOX_Y, BOX_HEIGHT = 84, 15
LEVEL_LABEL_X, LEVEL_BOX_X, LEVEL_BOX_WIDTH, LEVEL_DIGITS = 154, 168, 28, 2
NEXT_LABEL_X, NEXT_BOX_X, NEXT_BOX_WIDTH, NEXT_DIGITS = 200, 219, 44, 5
LETTERS = {"L": ("#..", "#..", "#..", "#..", "#..", "#..", "#..", "###"),
           "V": ("#.#", "#.#", "#.#", "#.#", "#.#", "#.#", "#.#", ".#."),
           "N": ("#..#", "##.#", "##.#", "##.#", "#.##", "#.##", "#.##", "#..#"),
           "E": ("###", "#..", "#..", "###", "#..", "#..", "#..", "###"),
           "X": ("#.#", "#.#", "#.#", ".#.", ".#.", "#.#", "#.#", "#.#"),
           "T": ("###", ".#.", ".#.", ".#.", ".#.", ".#.", ".#.", ".#.")}


@dataclass(frozen=True)
class LevelBoxes:
    level: int
    to_next: int | None  # None at the highest level


def status_screen_open(read) -> bool:
    box = read(ram_offset(UI_BOX_DATA + 36), 36)
    return (struct.unpack_from("<I", box, BOX_RENDER)[0] == RENDER_DIGIMON_MENU
            and struct.unpack_from("<hh", box, BOX_FRAME) == (BOX_OPEN_FRAME, BOX_OPEN_STATE)
            and read(ram_offset(MENU_STATE), 1)[0] == 1 and read(ram_offset(DIGIMON_MENU_STATE), 1)[0] == 0)


def read_digits(state: bytes) -> list[list[list[bool]]] | None:
    """The ten digits as rows of set texels, from a save state, or None when this core's states have no VRAM field."""
    at = state.find(GPURAM_FIELD)
    if at < 0:
        return None
    vram = at + len(GPURAM_FIELD) + 4
    if vram + 1024 * 512 * 2 > len(state):
        return None
    texels = []
    for row in range(DIGIT_HEIGHT):
        words = struct.unpack_from(f"<{DIGIT_WIDTH * 10 // 4}H", state, vram + (DIGIT_Y + row) * 2048 + DIGIT_X * 2)
        texels.append([bool(word >> shift & 15) for word in words for shift in (0, 4, 8, 12)])
    glyphs = [[texels[row][digit * DIGIT_WIDTH:(digit + 1) * DIGIT_WIDTH] for row in range(DIGIT_HEIGHT)]
              for digit in range(10)]
    return glyphs if all(any(any(line) for line in glyph) for glyph in glyphs) else None


def _label(painter: QPainter, unit: float, x: int, text: str) -> None:
    """Letters one texel apart on a plate one texel wider all round, as AGE and WEIGHT are."""
    width = sum(len(LETTERS[letter][0]) for letter in text) + len(text) + 1
    _fill(painter, unit, x, LABEL_Y, width, LABEL_HEIGHT, PLATE)
    at = x + 1
    for letter in text:
        for row, line in enumerate(LETTERS[letter]):
            for column, shown in enumerate(line):
                if shown == "#":
                    _fill(painter, unit, at + column, LABEL_Y + 1 + row, 1, 1, STROKE)
        at += len(LETTERS[letter][0]) + 1


def _fill(painter: QPainter, unit: float, x: int, y: int, width: int, height: int, colour: QColor) -> None:
    left, top = round(x * unit), round(y * unit)
    painter.fillRect(QRect(left, top, round((x + width) * unit) - left, round((y + height) * unit) - top), colour)


def _box(painter: QPainter, unit: float, x: int, width: int, value: str, digits, places: int) -> None:
    _fill(painter, unit, x, BOX_Y, width, BOX_HEIGHT, BOX_FILL)
    _fill(painter, unit, x, BOX_Y, width, 1, BOX_TOP_LEFT)
    _fill(painter, unit, x, BOX_Y, 1, BOX_HEIGHT - 1, BOX_TOP_LEFT)
    _fill(painter, unit, x, BOX_Y + BOX_HEIGHT - 1, width, 1, BOX_BOTTOM_RIGHT)
    _fill(painter, unit, x + width - 1, BOX_Y + 1, 1, BOX_HEIGHT - 1, BOX_BOTTOM_RIGHT)
    at = x + 3 + (places - len(value)) * DIGIT_WIDTH  # right-aligned, as the game's numbers are
    for character in value:
        if character == "-":
            _fill(painter, unit, at, BOX_Y + 7, 6, 1, TEXT)
        else:
            for row, line in enumerate(digits[int(character)]):
                for column, shown in enumerate(line):
                    if shown:
                        _fill(painter, unit, at + column, BOX_Y + 2 + row, 1, 1, TEXT)
        at += DIGIT_WIDTH


def draw_level_boxes(image: QImage, boxes: LevelBoxes, digits) -> None:
    unit = image.width() / 320
    painter = QPainter(image)
    _label(painter, unit, LEVEL_LABEL_X, "LVL")
    _box(painter, unit, LEVEL_BOX_X, LEVEL_BOX_WIDTH, str(boxes.level), digits, LEVEL_DIGITS)
    _label(painter, unit, NEXT_LABEL_X, "NEXT")
    _box(painter, unit, NEXT_BOX_X, NEXT_BOX_WIDTH, "-" if boxes.to_next is None else str(boxes.to_next), digits, NEXT_DIGITS)
    painter.end()
