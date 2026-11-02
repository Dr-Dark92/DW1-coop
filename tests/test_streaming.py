from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from dw1_workbench.disc import DiscLayout
from dw1_workbench.errors import Cancelled
from dw1_workbench.iso9660 import IsoEntry, hash_entries, safe_export
from dw1_workbench.alltim import SLOT_COUNT, SLOT_SIZE, decode_alltim
from test_tim import make_4bit_tim


def cooked_layout(path: Path, data: bytes) -> DiscLayout:
    padding = (-len(data)) % 2048
    path.write_bytes(data + bytes(padding))
    return DiscLayout(path, path, 2048, 0, 0, path.stat().st_size // 2048, "test cooked")


def raw_layout(path: Path, payloads: list[bytes]) -> DiscLayout:
    image = bytearray()
    for payload in payloads:
        sector = bytearray(2352)
        sector[:12] = b"\x00" + b"\xff" * 10 + b"\x00"
        sector[15] = 2
        sector[16:20] = sector[20:24] = b"\x00\x00\x08\x00"
        sector[24:2072] = payload.ljust(2048, b"\x00")
        image.extend(sector)
    path.write_bytes(image)
    return DiscLayout(path, path, 2352, 24, 0, len(payloads), "test raw")


def test_prefix_read_does_not_reject_large_resource(tmp_path):
    content = bytes(range(256)) * 2048
    layout = cooked_layout(tmp_path / "large.iso", content)
    with layout.open() as reader:
        prefix = reader.read_extent_prefix(0, len(content), 256 * 1024)
    assert len(prefix) == 256 * 1024
    assert prefix == content[:len(prefix)]


def test_streaming_hash_matches_expected(tmp_path):
    content = (b"streaming-hash-content\x00" * 1000) + bytes(range(251))
    layout = cooked_layout(tmp_path / "hash.iso", content)
    entry = IsoEntry("DATA.DAT", 0, len(content), False)
    with layout.open() as reader:
        result = hash_entries(reader, [entry])
    assert result[0][1] == hashlib.sha256(content).hexdigest()


def test_raw_extent_crosses_sector_and_chunk_boundaries(tmp_path):
    payloads = [bytes([index]) * 2048 for index in (0x11, 0x22, 0x33)]
    layout = raw_layout(tmp_path / "raw.bin", payloads)
    with layout.open() as reader:
        chunks = list(reader.iter_extent(0, 3 * 2048 - 17, chunk_size=3000))
    logical = b"".join(payloads)[:-17]
    assert [len(chunk) for chunk in chunks] == [3000, 3000, len(logical) - 6000]
    assert b"".join(chunks) == logical


def test_cancellation_after_hashing_started(tmp_path):
    content = b"x" * (8 * 2048)
    layout = cooked_layout(tmp_path / "cancel.iso", content)
    entry = IsoEntry("LARGE.DAT", 0, len(content), False)
    calls = 0

    def cancelled():
        nonlocal calls
        calls += 1
        return calls >= 4

    with layout.open() as reader, pytest.raises(Cancelled):
        hash_entries(reader, [entry], cancelled=cancelled)
    assert calls >= 4


def test_cancelled_export_removes_partial_file(tmp_path):
    content = bytes(range(256)) * 32
    layout = cooked_layout(tmp_path / "export.iso", content)
    entry = IsoEntry("EXPORT.DAT", 0, len(content), False)
    destination = tmp_path / "out" / "EXPORT.DAT"
    calls = 0

    def cancelled():
        nonlocal calls
        calls += 1
        return calls >= 4

    with layout.open() as reader, pytest.raises(Cancelled):
        safe_export(reader, entry, destination, cancelled=cancelled, chunk_size=2048)
    assert not destination.exists()
    assert not list(destination.parent.glob("*.partial"))
    assert not list(destination.parent.glob(".*.partial"))


def test_alltim_fixed_slots_have_exact_provenance(tmp_path):
    tim = make_4bit_tim()
    slot = tim + bytes(SLOT_SIZE - len(tim))
    content = slot * SLOT_COUNT
    layout = cooked_layout(tmp_path / "alltim.iso", content)
    entry = IsoEntry("CHDAT/ALLTIM.TIM", 0, len(content), False)
    with layout.open() as reader:
        slots = decode_alltim(reader, entry)
    assert len(slots) == 180
    assert slots[73].offset == 73 * SLOT_SIZE
    assert slots[73].size == SLOT_SIZE
    assert slots[73].sha256 == hashlib.sha256(slot).hexdigest()
    assert slots[73].tim.image.size == (4, 1)
