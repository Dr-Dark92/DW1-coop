from __future__ import annotations

import ctypes

import pytest

from dw1_workbench.libretro import (BUTTON, PIXEL_XRGB8888, LibretroCore, LibretroError, _library, core_info)


@pytest.fixture
def game(tmp_path):
    path = tmp_path / "game.cue"; path.write_bytes(b"FILE")
    return path


def start(fake_core, tmp_path, game, **options):
    core = LibretroCore(fake_core, tmp_path / "bios", tmp_path / "saves", options=options or None)
    core.start(game)
    return core


def test_core_info_reads_identity_without_starting(fake_core):
    info = core_info(fake_core)
    assert (info.name, info.version, info.need_fullpath) == ("Fake Core", "1.0", True)


def test_rejects_a_library_that_is_not_a_core(fake_core, tmp_path):
    import shutil, subprocess
    source = tmp_path / "plain.c"; source.write_text("int plain_value = 1;\n")
    library = tmp_path / ("plain" + fake_core.suffix)
    compiler = next(shutil.which(name) for name in ("cc", "gcc", "clang") if shutil.which(name))
    subprocess.run([compiler, "-shared", "-fPIC", "-o", str(library), str(source)], check=True)
    with pytest.raises(LibretroError, match="not a libretro core"):
        core_info(library)
    with pytest.raises(LibretroError, match="Could not load"):
        core_info(tmp_path / "missing.so")


def test_start_answers_environment_and_reports_timing(fake_core, tmp_path, game):
    log = []
    core = LibretroCore(fake_core, tmp_path / "bios", tmp_path / "saves", log=log.append)
    core.start(game)
    try:
        lib = _library(fake_core)
        lib.fake_system_dir.restype = lib.fake_option_value.restype = ctypes.c_char_p
        assert lib.fake_system_dir() == str(tmp_path / "bios").encode()
        assert lib.fake_option_value() == b"first"  # the default from SET_VARIABLES
        assert core.pixel_format == PIXEL_XRGB8888
        assert (core.fps, core.sample_rate, core.aspect_ratio) == (60.0, 44100, 2.0)
        assert core.option_choices == {"fake_option": ["first", "second"]}
        assert any("Fake Core 1.0" in line for line in log)
    finally:
        core.stop()


def test_option_override_reaches_core(fake_core, tmp_path, game):
    core = start(fake_core, tmp_path, game, fake_option="second")
    try:
        lib = _library(fake_core); lib.fake_option_value.restype = ctypes.c_char_p
        assert lib.fake_option_value() == b"second"
    finally:
        core.stop()


def test_frames_audio_dupes_and_input(fake_core, tmp_path, game):
    core = start(fake_core, tmp_path, game)
    try:
        frame, audio = core.run_frame()
        assert (frame.width, frame.height, frame.pitch) == (8, 4, 32)
        assert frame.data[:4] == bytes((0, 0, 0, 0xFF))
        assert len(audio) == 4 * 4
        core.buttons = (1 << BUTTON["Start"]) | (1 << BUTTON["R3"])
        duplicate, audio = core.run_frame()
        assert duplicate is None  # the core repeated frame 0
        assert audio[:2] == (1).to_bytes(2, "little")
        assert core.save_ram()[1] == 1 << BUTTON["Start"]  # low byte of the bitmask the core read
    finally:
        core.stop()


def test_quick_save_round_trip_and_rejection(fake_core, tmp_path, game):
    core = start(fake_core, tmp_path, game)
    try:
        for _ in range(4):
            core.run_frame()
        state = core.serialize()
        core.run_frame(); core.run_frame()
        core.unserialize(state)
        frame, _ = core.run_frame()
        assert frame.data[0] == 4
        with pytest.raises(LibretroError, match="rejected"):
            core.unserialize(b"WRONG!!!")
    finally:
        core.stop()


def test_save_ram_load_and_reset(fake_core, tmp_path, game):
    core = start(fake_core, tmp_path, game)
    try:
        core.run_frame(); core.run_frame()
        assert core.save_ram()[0] == 1
        assert core.load_save_ram(b"\x07" * 32)
        assert core.save_ram() == b"\x07" * 16
        core.reset()
        frame, _ = core.run_frame()
        assert frame.data[0] == 0
    finally:
        core.stop()


def test_failed_boot_releases_the_core(fake_core, tmp_path):
    bad = tmp_path / "bad.cue"; bad.write_bytes(b"X")
    core = LibretroCore(fake_core, tmp_path, tmp_path)
    with pytest.raises(LibretroError, match="could not load the game"):
        core.start(bad)
    good = tmp_path / "good.cue"; good.write_bytes(b"G")
    again = LibretroCore(fake_core, tmp_path, tmp_path); again.start(good); again.stop()


def test_only_one_core_runs_at_a_time(fake_core, tmp_path, game):
    first = start(fake_core, tmp_path, game)
    try:
        with pytest.raises(LibretroError, match="already running"):
            LibretroCore(fake_core, tmp_path, tmp_path).start(game)
    finally:
        first.stop()
