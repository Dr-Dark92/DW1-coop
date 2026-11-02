from pathlib import Path

import pytest

from dw1_workbench.cue import parse_cue
from dw1_workbench.disc import inspect_image
from dw1_workbench.errors import DiscFormatError
from conftest import iso_to_raw


def test_cooked_and_raw_layouts(synthetic_iso, tmp_path):
    cooked = inspect_image(synthetic_iso)
    assert (cooked.sector_size, cooked.user_offset) == (2048, 0)
    raw_path = iso_to_raw(synthetic_iso, tmp_path / "disc.bin")
    raw = inspect_image(raw_path)
    assert (raw.sector_size, raw.user_offset) == (2352, 24)


def test_cue_references_data_track(synthetic_iso, tmp_path):
    iso_to_raw(synthetic_iso, tmp_path / "game track.bin")
    cue = tmp_path / "game.cue"
    cue.write_text('FILE "game track.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n')
    assert inspect_image(cue).data_file.name == "game track.bin"


def test_missing_cue_track(tmp_path):
    cue = tmp_path / "bad.cue"
    cue.write_text('FILE "missing.bin" BINARY\n TRACK 01 MODE2/2352\n INDEX 01 00:00:00\n')
    with pytest.raises(DiscFormatError, match="missing"):
        parse_cue(cue)


def test_cue_path_traversal(tmp_path):
    outside = tmp_path.parent / "outside.bin"; outside.write_bytes(b"x")
    cue = tmp_path / "bad.cue"
    cue.write_text('FILE "../outside.bin" BINARY\n TRACK 01 MODE2/2352\n INDEX 01 00:00:00\n')
    with pytest.raises(DiscFormatError, match="escapes"):
        parse_cue(cue)


def test_unsupported_and_truncated(tmp_path):
    bad = tmp_path / "bad.bin"; bad.write_bytes(b"x" * 5000)
    with pytest.raises(DiscFormatError): inspect_image(bad)
    truncated = tmp_path / "short.iso"; truncated.write_bytes(bytes(17 * 2048))
    with pytest.raises(DiscFormatError): inspect_image(truncated)
