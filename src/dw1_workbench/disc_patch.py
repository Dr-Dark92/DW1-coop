"""A patched copy of the user's disc image with some files replaced, for playing imported zones.

The original image is never modified. Replacement files must fit the sectors their originals already occupy
(ISO 9660 extents are not moved); the directory record's size is updated when it changes. Raw Mode 2/Form 1
sectors get their EDC and ECC regenerated (ECMA-130 / the CD-ROM XA rules, as psx-spx documents them), so the
copy reads like a pressed disc; raw Mode 1 sectors are refused, cooked 2048-byte images need no codes.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .disc import DiscLayout
from .errors import Cancelled, DiscFormatError, ValidationError

SECTOR = 2048
RAW_SECTOR = 2352
# Mode 2/Form 1 raw sector: sync 12, header 4 (0x0C), subheader 8 (0x10), data 2048 (0x18), EDC 4 (0x818),
# ECC P 172 (0x81C), ECC Q 104 (0x8C8).
HEADER, SUBHEADER, DATA, EDC, ECC_P, ECC_Q = 0x0C, 0x10, 0x18, 0x818, 0x81C, 0x8C8

_ECC_F = [0] * 256
_ECC_B = [0] * 256
_EDC = [0] * 256
for _i in range(256):
    _j = ((_i << 1) ^ (0x11D if _i & 0x80 else 0)) & 0xFF
    _ECC_F[_i] = _j
    _ECC_B[_i ^ _j] = _i
    _value = _i
    for _ in range(8):
        _value = (_value >> 1) ^ (0xD8018001 if _value & 1 else 0)
    _EDC[_i] = _value


def edc(data: bytes) -> int:
    value = 0
    for byte in data:
        value = (value >> 8) ^ _EDC[(value ^ byte) & 0xFF]
    return value


def _ecc_block(source: bytes | bytearray, major_count: int, minor_count: int, major_mult: int, minor_inc: int) -> bytes:
    size = major_count * minor_count
    output = bytearray(major_count * 2)
    for major in range(major_count):
        index = (major >> 1) * major_mult + (major & 1)
        a = b = 0
        for _ in range(minor_count):
            value = source[index]
            index += minor_inc
            if index >= size:
                index -= size
            a ^= value; b ^= value
            a = _ECC_F[a]
        a = _ECC_B[_ECC_F[a] ^ b]
        output[major], output[major + major_count] = a, a ^ b
    return bytes(output)


def encode_mode2_form1(sector: bytes | bytearray) -> bytes:
    """Recompute EDC and ECC for a raw Mode 2/Form 1 sector (the header is zero while computing ECC)."""
    raw = bytearray(sector)
    if len(raw) != RAW_SECTOR:
        raise ValueError("A raw sector is 2352 bytes.")
    raw[EDC:EDC + 4] = edc(raw[SUBHEADER:EDC]).to_bytes(4, "little")
    header = bytes(raw[HEADER:SUBHEADER])
    raw[HEADER:SUBHEADER] = bytes(4)
    raw[ECC_P:ECC_Q] = _ecc_block(raw[HEADER:ECC_P], 86, 24, 2, 86)
    raw[ECC_Q:RAW_SECTOR] = _ecc_block(raw[HEADER:ECC_Q], 52, 43, 86, 88)
    raw[HEADER:SUBHEADER] = header
    return bytes(raw)


@dataclass(frozen=True)
class DirectoryRecord:
    path: str
    extent: int
    size: int
    sector: int  # data-track sector holding the record
    offset: int  # byte offset of the record inside that sector


def _u32(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset:offset + 4], "little")


def find_records(layout: DiscLayout, paths: set[str]) -> dict[str, DirectoryRecord]:
    """Locate the directory records of `paths` (upper-case, '/'-separated, no version suffix)."""
    wanted = {path.upper() for path in paths}
    found: dict[str, DirectoryRecord] = {}
    with layout.open() as reader:
        pvd = reader.read_sector(16)
        queue = [(PurePosixPath(), _u32(pvd, 156 + 2), _u32(pvd, 156 + 10))]
        seen = set()
        while queue and len(found) < len(wanted):
            parent, extent, size = queue.pop(0)
            if extent in seen:
                continue
            seen.add(extent)
            for index in range((size + SECTOR - 1) // SECTOR):
                data = reader.read_sector(extent + index)
                position = 0
                while position < SECTOR and data[position]:
                    length = data[position]
                    name_length = data[position + 32]
                    raw = data[position + 33:position + 33 + name_length]
                    if raw not in (b"\x00", b"\x01"):
                        name = raw.decode("ascii", "replace").split(";", 1)[0]
                        path = str(parent / name) if str(parent) != "." else name
                        child_extent, child_size = _u32(data, position + 2), _u32(data, position + 10)
                        if data[position + 25] & 2:
                            queue.append((PurePosixPath(path), child_extent, child_size))
                        elif path.upper() in wanted:
                            found[path.upper()] = DirectoryRecord(path, child_extent, child_size, extent + index, position)
                    position += length
    missing = wanted - set(found)
    if missing:
        raise ValidationError(f"Not on the disc: {', '.join(sorted(missing))}.")
    return found


def patched_cue(layout: DiscLayout, data_name: str) -> str:
    """The original CUE sheet pointing at the patched copy (other tracks, e.g. audio, are kept as they were)."""
    if layout.source.suffix.lower() != ".cue":
        return f'FILE "{data_name}" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n'
    lines, replaced = [], False
    for line in layout.source.read_text(errors="replace").splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("FILE"):
            referenced = stripped[4:].strip()
            name = referenced[1:referenced.index('"', 1)] if referenced.startswith('"') else referenced.split()[0]
            if (layout.source.parent / name).resolve() == layout.data_file.resolve():
                line, replaced = line.replace(name, data_name, 1), True
            else:  # other files (audio tracks) stay where they are
                line = line.replace(name, str((layout.source.parent / name).resolve()), 1)
        lines.append(line)
    if not replaced:
        raise ValidationError("The CUE sheet does not reference the data file being patched.")
    return "\n".join(lines) + "\n"


def _replacement_key(replacements: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for path in sorted(replacements):
        digest.update(path.upper().encode() + b"\0" + hashlib.sha256(replacements[path]).digest())
    return digest.hexdigest()[:16]


def plan_patch(layout: DiscLayout, replacements: dict[str, bytes]) -> dict[str, DirectoryRecord]:
    """Check every replacement fits the sectors its original occupies; returns the records to patch."""
    if layout.sector_size == RAW_SECTOR and layout.user_offset != DATA:
        raise ValidationError("Only raw Mode 2/Form 1 or cooked 2048-byte images can be patched.")
    records = find_records(layout, set(replacements))
    for path, data in replacements.items():
        record = records[path.upper()]
        room = (record.size + SECTOR - 1) // SECTOR * SECTOR
        if len(data) > room:
            raise ValidationError(f"{path} is {len(data):,} bytes but its place on the disc holds {room:,}; files "
                                  "cannot grow past their original sectors.")
    return records


def build_patched_image(layout: DiscLayout, replacements: dict[str, bytes], directory: Path,
                        cancelled=lambda: False, progress=lambda value, text: None) -> Path:
    """Write (or reuse) a patched copy of the image in `directory`; returns the file to give the emulator
    (a CUE for raw images). Unchanged replacements produce a byte-identical copy."""
    records = plan_patch(layout, replacements)
    directory = Path(directory)
    key = _replacement_key(replacements)
    target = directory / f"patched-{key}{layout.data_file.suffix.lower() or '.bin'}"
    cue = target.with_suffix(".cue")
    result = cue if layout.sector_size == RAW_SECTOR else target
    if target.is_file() and (layout.sector_size != RAW_SECTOR or cue.is_file()):
        return result
    directory.mkdir(parents=True, exist_ok=True)
    for old in directory.glob("patched-*"):  # one patched image at a time: they are as large as the disc
        old.unlink(missing_ok=True)
    handle, temporary = tempfile.mkstemp(dir=directory, prefix=".patched.", suffix=".partial")
    os.close(handle)
    try:
        progress(0, "Copying the disc image")
        total, copied = layout.data_file.stat().st_size, 0
        with layout.data_file.open("rb") as source, open(temporary, "wb") as output:
            while chunk := source.read(8 * 1024 * 1024):
                if cancelled():
                    raise Cancelled("Building the patched disc was cancelled.")
                output.write(chunk); copied += len(chunk)
                progress(80 * copied // max(total, 1), "Copying the disc image")
        progress(85, "Writing imported files")
        with open(temporary, "r+b") as output:
            def write_sector(lba: int, data: bytes):
                position = (layout.start_sector + lba) * layout.sector_size
                if layout.sector_size == RAW_SECTOR:
                    output.seek(position); raw = bytearray(output.read(RAW_SECTOR))
                    raw[DATA:DATA + SECTOR] = data
                    output.seek(position); output.write(encode_mode2_form1(raw))
                else:
                    output.seek(position); output.write(data)

            def read_sector(lba: int) -> bytes:
                output.seek((layout.start_sector + lba) * layout.sector_size + layout.user_offset)
                return output.read(SECTOR)

            for path, data in sorted(replacements.items()):
                record = records[path.upper()]
                sectors = (record.size + SECTOR - 1) // SECTOR
                padded = data + bytes(sectors * SECTOR - len(data))
                for index in range(sectors):
                    block = padded[index * SECTOR:(index + 1) * SECTOR]
                    if block != read_sector(record.extent + index):
                        write_sector(record.extent + index, block)
                if len(data) != record.size:
                    sector = bytearray(read_sector(record.sector))
                    sector[record.offset + 10:record.offset + 18] = len(data).to_bytes(4, "little") + \
                        len(data).to_bytes(4, "big")
                    write_sector(record.sector, bytes(sector))
        os.replace(temporary, target)
        if layout.sector_size == RAW_SECTOR:
            cue.write_text(patched_cue(layout, target.name))
        (directory / f"patched-{key}.json").write_text(json.dumps(
            {"source": str(layout.data_file), "files": {path: {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                                                       for path, data in sorted(replacements.items())}}, indent=2))
        progress(100, "Patched disc ready")
        return result
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
