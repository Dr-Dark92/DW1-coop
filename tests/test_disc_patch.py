from __future__ import annotations

import pytest

from conftest import iso_to_raw, make_iso
from dw1_workbench.disc import inspect_image
from dw1_workbench.disc_patch import (DATA, EDC, RAW_SECTOR, build_patched_image, edc, encode_mode2_form1,
                                      plan_patch)
from dw1_workbench.errors import ValidationError
from dw1_workbench.iso9660 import read_inventory


def test_encoder_is_stable_and_codes_cover_the_data():
    sector = bytearray(RAW_SECTOR)
    sector[:12] = b"\x00" + b"\xff" * 10 + b"\x00"; sector[12:16] = b"\x00\x02\x00\x02"
    sector[DATA:DATA + 2048] = bytes(range(256)) * 8
    encoded = encode_mode2_form1(sector)
    assert encode_mode2_form1(encoded) == encoded
    assert int.from_bytes(encoded[EDC:EDC + 4], "little") == edc(encoded[0x10:EDC])
    changed = bytearray(encoded); changed[DATA] ^= 1
    assert encode_mode2_form1(changed)[EDC:] != encoded[EDC:]
    assert encoded[12:16] == b"\x00\x02\x00\x02"  # header restored after computing ECC


def raw_image(tmp_path):
    raw = iso_to_raw(make_iso(tmp_path / "game.iso"), tmp_path / "game.bin")
    data = bytearray(raw.read_bytes())
    for start in range(0, len(data), RAW_SECTOR):  # give the synthetic image real codes, like a pressed disc
        data[start:start + RAW_SECTOR] = encode_mode2_form1(data[start:start + RAW_SECTOR])
    raw.write_bytes(data)
    (tmp_path / "game.cue").write_text('FILE "game.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n')
    return inspect_image(tmp_path / "game.cue")


def read_file(layout, path):
    with layout.open() as reader:
        entry = next(entry for entry in read_inventory(reader) if entry.path == path)
        return reader.read_extent(entry.extent, entry.size)


def test_unchanged_files_give_an_identical_copy(tmp_path):
    layout = raw_image(tmp_path)
    original = read_file(layout, "SYSTEM.CNF")
    cue = build_patched_image(layout, {"SYSTEM.CNF": original}, tmp_path / "out")
    assert cue.with_suffix(".bin").read_bytes() == layout.data_file.read_bytes()
    assert 'FILE "patched-' in cue.read_text()


def test_replacement_is_written_with_codes_and_new_size(tmp_path):
    layout = raw_image(tmp_path)
    replacement = b"BOOT = cdrom:\\SLUS_010.32;1\r\nTCB = 4\r\n"
    cue = build_patched_image(layout, {"SYSTEM.CNF": replacement}, tmp_path / "out")
    patched = inspect_image(cue)
    assert read_file(patched, "SYSTEM.CNF") == replacement
    data = cue.with_suffix(".bin").read_bytes()
    for start in range(0, len(data), RAW_SECTOR):
        assert encode_mode2_form1(data[start:start + RAW_SECTOR]) == data[start:start + RAW_SECTOR]
    assert layout.data_file.read_bytes() != data  # the original stays untouched
    assert build_patched_image(layout, {"SYSTEM.CNF": replacement}, tmp_path / "out") == cue  # reused


def test_files_cannot_grow_past_their_sectors(tmp_path):
    layout = raw_image(tmp_path)
    with pytest.raises(ValidationError, match="cannot grow"):
        plan_patch(layout, {"SYSTEM.CNF": bytes(2049)})
    with pytest.raises(ValidationError, match="Not on the disc"):
        plan_patch(layout, {"MISSING.BIN": b""})
