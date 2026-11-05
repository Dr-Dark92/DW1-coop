import os
import struct

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from dw1_workbench import status_panel as S
from dw1_workbench.hud_view import GPURAM_FIELD
from dw1_workbench.live import ram_offset


def state_with_digits():
    vram = bytearray(1024 * 512 * 2)
    for row in range(S.DIGIT_HEIGHT):
        for digit in range(10):
            at = (S.DIGIT_Y + row) * 2048 + S.DIGIT_X * 2 + digit * 4
            struct.pack_into("<H", vram, at, 0x0101)  # texels 0 and 2 of each digit
    return b"junk" + GPURAM_FIELD + b"\0\0\0\0" + bytes(vram)


def memory(render=S.RENDER_DIGIMON_MENU, frame=S.BOX_OPEN_FRAME, state=S.BOX_OPEN_STATE, menu=1, tab=0):
    ram = bytearray(0x200000)
    box = ram_offset(S.UI_BOX_DATA + 36)
    struct.pack_into("<hh", ram, box + S.BOX_FRAME, frame, state)
    struct.pack_into("<I", ram, box + S.BOX_RENDER, render)
    ram[ram_offset(S.MENU_STATE)] = menu
    ram[ram_offset(S.DIGIMON_MENU_STATE)] = tab
    return lambda offset, size: bytes(ram[offset:offset + size])


def test_only_the_open_status_tab_counts():
    assert S.status_screen_open(memory())
    assert not S.status_screen_open(memory(render=0x800B9000))  # the Player menu's box
    assert not S.status_screen_open(memory(frame=3))  # still opening
    assert not S.status_screen_open(memory(state=0))  # closing
    assert not S.status_screen_open(memory(menu=0))  # its strings are still being drawn
    assert not S.status_screen_open(memory(tab=1))  # the Tech tab


def test_digits_come_from_video_memory():
    digits = S.read_digits(state_with_digits())
    assert len(digits) == 10 and len(digits[0]) == S.DIGIT_HEIGHT
    assert digits[3][0][:3] == [True, False, True]
    assert S.read_digits(b"no vram here") is None
    assert S.read_digits(b"junk" + GPURAM_FIELD + bytes(1024 * 512 * 2 + 4)) is None  # no font loaded


def test_boxes_are_drawn_in_the_screens_colours_and_scale_with_the_image():
    QApplication.instance() or QApplication([])
    digits = S.read_digits(state_with_digits())
    for scale in (1, 4):
        image = QImage(320 * scale, 240 * scale, QImage.Format.Format_RGB32)
        image.fill(0x123456)
        S.draw_level_boxes(image, S.LevelBoxes(7, 94), digits)
        at = lambda x, y: image.pixelColor(x * scale, y * scale)
        assert at(S.LEVEL_LABEL_X, S.LABEL_Y) == S.PLATE
        assert at(S.LEVEL_LABEL_X + 1, S.LABEL_Y + 1) == S.STROKE  # the top of the L
        assert at(S.LEVEL_BOX_X, S.BOX_Y) == S.BOX_TOP_LEFT
        assert at(S.LEVEL_BOX_X + S.LEVEL_BOX_WIDTH - 1, S.BOX_Y + 5) == S.BOX_BOTTOM_RIGHT
        assert at(S.LEVEL_BOX_X + 3 + S.DIGIT_WIDTH, S.BOX_Y + 2) == S.TEXT  # right-aligned, in the second place
        assert at(S.LEVEL_BOX_X + 3, S.BOX_Y + 2) == S.BOX_FILL
        assert at(S.NEXT_BOX_X + S.NEXT_BOX_WIDTH + 1, S.BOX_Y) .rgb() & 0xFFFFFF == 0x123456  # nothing past the box


def test_the_highest_level_shows_a_dash():
    QApplication.instance() or QApplication([])
    image = QImage(320, 240, QImage.Format.Format_RGB32)
    image.fill(0)
    S.draw_level_boxes(image, S.LevelBoxes(99, None), S.read_digits(state_with_digits()))
    assert image.pixelColor(S.NEXT_BOX_X + 3 + 4 * S.DIGIT_WIDTH, S.BOX_Y + 7) == S.TEXT
