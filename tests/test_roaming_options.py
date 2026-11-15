import struct

from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from dw1_workbench.gameplay_options import RoamingOptions, RoamingDialog
from dw1_workbench.hud_view import HudPainter, HudState
from dw1_workbench.lifespan import LifespanPause
from dw1_workbench.live import ram_offset
from dw1_workbench import progression as P


def test_options_round_trip_and_cancel(tmp_path):
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "options.ini"), QSettings.Format.IniFormat)
    options = RoamingOptions(False, True, True, True, True)
    options.save(settings)
    assert RoamingOptions.load(settings) == options
    dialog = RoamingDialog(options)
    dialog.boxes["hp_mp"].setChecked(True)
    dialog.reject()
    assert RoamingOptions.load(settings) == options  # dialog never writes itself
    assert app is not None


def test_optional_hud_draws_only_requested_rows():
    app = QApplication.instance() or QApplication([])
    state = HudState(75, 100, 200, 50, 100, None, None, None, (180, 25, 12, 40))
    image = QImage(640, 480, QImage.Format.Format_RGB32)
    image.fill(QColor("#010203"))
    before = image.copy()
    painter = HudPainter(lambda: b"")
    painter.draw(image, state, RoamingOptions(False, False, False, False, False))
    assert image == before
    painter.draw(image, state, RoamingOptions(False, True, True, True, True))
    assert image != before
    assert image.pixelColor(520, 368) == QColor("#010203")  # HP hidden
    assert image.pixelColor(20, 350) != QColor("#010203")  # care panel present
    assert app is not None


def memory():
    data = bytearray(0x200000)
    def put(address, fmt, *values):
        struct.pack_into(fmt, data, ram_offset(address), *values)
    def get(address):
        return struct.unpack_from("<h", data, ram_offset(address))[0]
    def read(offset, size):
        return bytes(data[offset:offset + size])
    def write(offset, value):
        data[offset:offset + len(value)] = value
    put(P.PARTNER_ENTITY, "<i", 3)
    put(P.PARTNER_STATS + 16, "<h", 100)
    put(P.PARTNER_LIVES, "B", 2)
    put(P.LIFETIME, "<hh", 5, 8)
    put(P.HOUR, "<h", 23)
    return read, write, put, get


def test_pause_hourly_countdown_not_age_lives_or_evolution():
    read, write, put, get = memory()
    pause = LifespanPause(read, write)
    snapshot = pause.before_frame()
    put(P.LIFETIME, "<hh", 4, 9); put(P.HOUR, "<h", 0)
    pause.after_frame(snapshot)
    assert get(P.LIFETIME) == 5 and get(P.AGE) == 9
    assert read(ram_offset(P.PARTNER_LIVES), 1) == b"\x02"
    snapshot = pause.before_frame()
    put(P.PARTNER_ENTITY, "<i", 5); put(P.LIFETIME, "<h", 360)
    pause.after_frame(snapshot)
    assert get(P.LIFETIME) == 360


def test_pause_final_hour_guard_never_revives_or_overwrites_battle():
    read, write, put, get = memory()
    pause = LifespanPause(read, write)
    put(P.LIFETIME, "<h", 1)
    snapshot = pause.before_frame()
    assert get(P.LIFETIME) == 2
    put(P.LIFETIME, "<h", 1); put(P.HOUR, "<h", 0)
    pause.after_frame(snapshot)
    assert get(P.LIFETIME) == 1
    snapshot = pause.before_frame()
    put(P.LIFETIME, "<h", 0)
    pause.after_frame(snapshot)
    assert get(P.LIFETIME) == 0 and pause.before_frame() is None
    put(P.LIFETIME, "<h", 5); put(P.GAME_STATE, "<h", 1)
    assert pause.before_frame() is None


def test_exp_rules_can_leave_native_lifespan_and_lives_untouched():
    read, write, put, get = memory()
    rules = P.GameRules(read, write, legacy_immortality=False)
    rules.tick()
    assert get(P.LIFETIME) == 5 and get(P.AGE) == 8
    assert read(ram_offset(P.PARTNER_LIVES), 1) == b"\x02"


def test_pause_preserves_non_clock_loss_and_rolls_back_final_hour_guard():
    read, write, put, get = memory()
    pause = LifespanPause(read, write)
    snapshot = pause.before_frame()
    put(P.LIFETIME, "<h", 4)  # non-clock effect, same hour
    pause.after_frame(snapshot)
    assert get(P.LIFETIME) == 4
    put(P.LIFETIME, "<h", 1)
    snapshot = pause.before_frame()
    assert get(P.LIFETIME) == 2
    pause.after_frame(snapshot)  # no hour passed
    assert get(P.LIFETIME) == 1
    put(P.LIFETIME, "<h", 5)
    snapshot = pause.before_frame()
    put(P.LIFETIME, "<h", 4); put(P.HOUR, "<h", 0); put(P.PARTNER_LIVES, "B", 1)
    pause.after_frame(snapshot)
    assert get(P.LIFETIME) == 4  # never compensate a native lost-life event
