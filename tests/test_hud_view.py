from __future__ import annotations

import os
import struct

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from dw1_workbench import hud_view as U
from dw1_workbench.live import LiveState, ram_offset
from dw1_workbench.progression import PARTNER_ENTITY, PARTNER_STATS


@pytest.fixture(scope="module")
def application():
    yield QApplication.instance() or QApplication([])


class Ram:
    def __init__(self):
        self.data = bytearray(0x200000)
        self.put(PARTNER_ENTITY, "<i", 3)
        self.put(PARTNER_ENTITY + 4, "<I", 0x80100000)
        self.put(0x80100000 + 120, "<3i", 625, 0, -1450)
        self.put(U.TAMER_POSITION + 4, "<I", 0x80100100)
        self.put(0x80100100 + 120, "<3i", 826, 0, -1458)
        self.put(PARTNER_STATS + 16, "<4h", 2000, 1000, 500, 250)
        self.put(U.STATUS_UI_OFFSET_X, "<h", 75)

    def put(self, address, fmt, *values):
        struct.pack_into(fmt, self.data, ram_offset(address), *values)

    def read(self, offset, size):
        return bytes(self.data[offset:offset + size])


def grid(*tiles, exits=()):
    cells = bytearray([0x80]) * 10000
    for x, y in tiles:
        cells[x + y * 100] = 0
    for x, y in exits:
        cells[x + y * 100] = 110
    return bytes(cells)


def test_shown_only_with_the_games_status_bars():
    ram = Ram()
    state = U.read_hud(ram.read, None)
    assert (state.hp, state.max_hp, state.mp, state.max_mp, state.offset) == (500, 2000, 250, 1000, 75)
    assert state.tamer == pytest.approx((58.26, 64.58))
    ram.put(U.STATUS_UI_OFFSET_X, "<h", 240)  # game time stopped: battle, menu, text
    assert U.read_hud(ram.read, None) is None
    ram.put(U.STATUS_UI_OFFSET_X, "<h", 0)  # never drawn yet: the title screen
    assert U.read_hud(ram.read, None) is None
    ram.put(U.STATUS_UI_OFFSET_X, "<h", 75)
    ram.put(PARTNER_STATS + 16, "<h", 0)  # no partner yet
    assert U.read_hud(ram.read, None) is None


def test_map_shows_only_the_part_of_the_grid_in_use(application):
    image, (left, top, side) = U.map_tiles(grid((40, 50), (41, 50), (45, 52), exits=[(42, 50)]))
    assert side == 8 and left <= 40 and left + side > 45 and top <= 50 and top + side > 52
    assert image.pixelColor(40 - left, 50 - top) == U.MAP_COLOURS["walkable"]
    assert image.pixelColor(42 - left, 50 - top) == U.MAP_COLOURS["exit"]
    assert image.pixelColor(0, 0).alpha() == 0
    assert U.map_tiles(bytes([0x80]) * 10000) is None


@pytest.mark.parametrize("size", [(320, 240), (640, 480), (1280, 960)])
def test_draws_at_any_frame_size_in_the_right_places(application, size):
    image = QImage(*size, QImage.Format.Format_RGB32)
    image.fill(QColor(1, 2, 3))
    live = LiveState(109, None, None, grid(*[(x, 50) for x in range(40, 60)]))
    state = U.HudState(75, 2000, 2000, 0, 1000, live, (50.0, 50.0), None)
    painter = U.HudPainter(lambda: b"")
    painter.draw(image, state)
    k = size[0] // 320
    assert image.pixelColor(300 * k, 40 * k) != QColor(1, 2, 3)  # the map, top right
    assert image.pixelColor(250 * k, 150 * k) == QColor(1, 2, 3)  # nothing between the map and the bars
    assert image.pixelColor(260 * k, U.FIRST_BAR_Y * k + 3 * k) == QColor(U.FILLS["hp"][0])  # HP full
    empty = image.pixelColor(260 * k, (U.FIRST_BAR_Y + U.BAR_STEP) * k + 3 * k)
    assert empty == QColor(U.EMPTY)  # MP empty
    assert image.pixelColor(260 * k, 208 * k) == QColor(1, 2, 3)  # the game's own bars are left alone
    assert image.pixelColor(60 * k, 60 * k) == QColor(1, 2, 3)  # and the clock's corner


def test_map_is_small_and_lets_the_game_show_through(application):
    image = QImage(1280, 960, QImage.Format.Format_RGB32)
    image.fill(QColor(200, 0, 0))
    live = LiveState(109, None, None, grid(*[(x, 50) for x in range(40, 60)]))
    U.HudPainter(lambda: b"").draw(image, U.HudState(75, 100, 100, 100, 100, live, (50.0, 50.5), None))
    assert image.pixelColor((U.MAP_RIGHT - U.MAP_SIDE - 2) * 4, 20 * 4) == QColor(200, 0, 0)  # beside it: untouched
    inside = image.pixelColor((U.MAP_RIGHT - 6) * 4, (U.MAP_TOP + 6) * 4)
    assert inside != QColor(200, 0, 0) and inside.red() > 60  # empty map area: tinted, background still visible
    # The Tamer's marker is solid, not faded with the panel.
    left, top = U.MAP_RIGHT - U.MAP_SIDE + U.MAP_INSET, U.MAP_TOP + U.MAP_INSET
    tiles, (first_x, first_y, side) = U.map_tiles(live.grid)
    cell = (U.MAP_SIDE - 2 * U.MAP_INSET) / side
    assert image.pixelColor(round((left + (50.0 - first_x) * cell) * 4), round((top + (50.5 - first_y) * cell) * 4)) == QColor("#ffffff")


def test_sliding_with_the_status_bars_moves_everything(application):
    image = QImage(320, 240, QImage.Format.Format_RGB32)
    image.fill(QColor(1, 2, 3))
    live = LiveState(109, None, None, grid((40, 50)))
    U.HudPainter(lambda: b"").draw(image, U.HudState(239, 100, 100, 100, 100, live, None, None))
    assert image.pixelColor(300, 40) == QColor(1, 2, 3) and image.pixelColor(260, 184) == QColor(1, 2, 3)


def test_native_bar_is_read_from_vram_and_missing_art_falls_back(application):
    vram = bytearray(1024 * 512 * 2)
    for y in range(U.BAR_HEIGHT):
        for x in range(0, U.BAR_WIDTH, 4):
            struct.pack_into("<H", vram, ((U.BAR_V + y) * 1024 + U.BAR_PAGE_X + x // 4) * 2, 0x1111)
    struct.pack_into("<H", vram, (U.BAR_CLUT_Y * 1024 + U.BAR_CLUT_X + 1) * 2, 31)
    bar = U.native_bar(b"x" + U.GPURAM_FIELD + bytes(4) + bytes(vram))
    assert bar.pixelColor(0, 0) == QColor(255, 0, 0)
    assert U.native_bar(b"no such field") is None
    assert U.native_bar(b"x" + U.GPURAM_FIELD + bytes(4) + bytes(1024 * 512 * 2)) is None  # not resident
    assert U.fallback_bar().size() == bar.size()


def test_ally_panel_mirrors_the_controls_at_any_size(application):
    from dw1_workbench.coop import ATTACK, MODERATE, RUN, YOUR_CALL, AllyPanel
    panel = AllyPanel("Greymon", "Lv 20 · EXP 10/420", 1500, 2000, 500, 2000, (RUN, MODERATE, ATTACK, YOUR_CALL), 3,
                      YOUR_CALL, "ModokiBetamon", False, False, False)
    for size in ((320, 240), (1280, 960)):
        image = QImage(*size, QImage.Format.Format_RGB32)
        image.fill(QColor(1, 2, 3))
        U.draw_ally_panel(image, panel, None, U.HudPainter(lambda: b""))
        k = size[0] // 320
        assert image.pixelColor(25 * k, 200 * k) != QColor(1, 2, 3)  # the commands, bottom left
        assert image.pixelColor(260 * k, (U.P2_BAR_Y + 3) * k) == QColor(U.FILLS["hp"][0])  # HP bar, bottom right
        assert image.pixelColor(200 * k, 60 * k) == QColor(1, 2, 3)  # nothing over the fight


def test_cursor_palette_is_on_the_gpus_16_pixel_grid():
    assert U.CURSOR_CLUT[0] % 16 == 0
