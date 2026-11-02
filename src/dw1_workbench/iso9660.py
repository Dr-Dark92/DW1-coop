from __future__ import annotations

import hashlib
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable

from .disc import SectorReader
from .errors import Cancelled, DiscFormatError, ValidationError


@dataclass(frozen=True)
class IsoEntry:
    path: str
    extent: int
    size: int
    is_dir: bool
    offset: int = 0
    extent_size: int | None = None


@dataclass(frozen=True)
class ReleaseEvidence:
    release: str
    executable: str
    boot_line: str
    confidence: str


SUPPORTED_EXECUTABLES = {
    "SLUS_010.32": "Digimon World (USA, SLUS-01032)",
}


def _u32both(data: bytes, offset: int) -> int:
    if offset + 8 > len(data):
        raise DiscFormatError("Truncated ISO 9660 both-endian integer.")
    little = int.from_bytes(data[offset:offset + 4], "little")
    big = int.from_bytes(data[offset + 4:offset + 8], "big")
    if little != big:
        raise DiscFormatError("ISO 9660 endian copies disagree.")
    return little


def _record(data: bytes, offset: int, parent: PurePosixPath) -> tuple[IsoEntry, int]:
    length = data[offset]
    if length < 34 or offset + length > len(data):
        raise DiscFormatError("Malformed ISO 9660 directory record.")
    extent = _u32both(data, offset + 2)
    size = _u32both(data, offset + 10)
    flags = data[offset + 25]
    name_len = data[offset + 32]
    if 33 + name_len > length:
        raise DiscFormatError("Malformed ISO 9660 file identifier.")
    raw_name = data[offset + 33:offset + 33 + name_len]
    if raw_name in (b"\x00", b"\x01"):
        name = "." if raw_name == b"\x00" else ".."
    else:
        try:
            name = raw_name.decode("ascii").split(";", 1)[0]
        except UnicodeDecodeError as exc:
            raise DiscFormatError("Non-ASCII ISO 9660 identifier is unsupported.") from exc
        if not name or name in {".", ".."} or "/" in name or "\\" in name:
            raise DiscFormatError(f"Unsafe ISO 9660 identifier: {name!r}")
    path = str(parent / name) if str(parent) != "." else name
    return IsoEntry(path, extent, size, bool(flags & 2)), length


def read_inventory(reader: SectorReader, cancelled=lambda: False,
                   progress: Callable[[int, str], None] | None = None) -> list[IsoEntry]:
    pvd = reader.read_sector(16)
    if pvd[0] != 1 or pvd[1:6] != b"CD001" or pvd[6] != 1:
        raise DiscFormatError("Unsupported or malformed ISO 9660 primary volume descriptor.")
    root, _ = _record(pvd, 156, PurePosixPath())
    if not root.is_dir:
        raise DiscFormatError("ISO 9660 root record is not a directory.")
    result: list[IsoEntry] = []
    queue = [(PurePosixPath(), root)]
    visited: set[tuple[int, int]] = set()
    while queue:
        if cancelled():
            raise Cancelled("Import cancelled while reading filesystem inventory.")
        parent, directory = queue.pop(0)
        key = (directory.extent, directory.size)
        if key in visited:
            continue
        visited.add(key)
        if len(visited) > 4096:
            raise DiscFormatError("Directory count exceeds safety limit (4096).")
        if directory.size > 16 * 1024 * 1024:
            raise DiscFormatError("Directory exceeds the safety limit (16 MiB).")
        blob = b"".join(reader.iter_extent(directory.extent, directory.size,
                                            cancelled=cancelled))
        pos = 0
        while pos < len(blob):
            if cancelled():
                raise Cancelled("Import cancelled while reading filesystem inventory.")
            if blob[pos] == 0:
                pos = ((pos // 2048) + 1) * 2048
                continue
            entry, length = _record(blob, pos, parent)
            pos += length
            if entry.path in {".", ".."} or PurePosixPath(entry.path).name in {".", ".."}:
                continue
            if entry.extent < 0 or entry.extent + (entry.size + 2047) // 2048 > reader.layout.sector_count:
                raise DiscFormatError(f"Out-of-bounds extent for {entry.path}.")
            result.append(entry)
            if entry.is_dir:
                queue.append((PurePosixPath(entry.path), entry))
        if progress:
            progress(min(70, 5 + len(result) // 4), f"Inventoried {len(result)} entries")
    return sorted(result, key=lambda item: item.path)


def identify_release(reader: SectorReader, entries: Iterable[IsoEntry]) -> ReleaseEvidence:
    files = {e.path.upper(): e for e in entries if not e.is_dir}
    system = files.get("SYSTEM.CNF")
    if system is None:
        raise ValidationError("SYSTEM.CNF is missing; this is not a supported DW1 disc layout.")
    text = reader.read_extent(system.extent, system.size, 64 * 1024).decode("ascii", "replace")
    match = re.search(r"(?im)^\s*BOOT\s*=\s*cdrom:\\(?:[^\r\n]*\\)?([A-Z]{4}[_-]\d{3}\.\d{2})(?:;\d+)?", text)
    if not match:
        raise ValidationError("SYSTEM.CNF has no recognizable PlayStation BOOT executable.")
    executable = match.group(1).upper().replace("-", "_")
    if executable not in SUPPORTED_EXECUTABLES:
        raise ValidationError(
            f"Disc boots {executable}; this milestone only verifies the USA SLUS_010.32 layout."
        )
    exe_entry = next((e for key, e in files.items() if PurePosixPath(key).name == executable), None)
    if exe_entry is None:
        raise ValidationError(f"SYSTEM.CNF names {executable}, but that file is absent.")
    header = reader.read_extent(exe_entry.extent, min(exe_entry.size, 2048), 2048)
    if not header.startswith(b"PS-X EXE"):
        raise ValidationError(f"{executable} does not have a PS-X EXE header.")
    return ReleaseEvidence(SUPPORTED_EXECUTABLES[executable], executable,
                           next(line.strip() for line in text.splitlines() if "BOOT" in line.upper()),
                           "confirmed from SYSTEM.CNF product code and PS-X EXE header")


def hash_entries(reader: SectorReader, entries: Iterable[IsoEntry], cancelled=lambda: False,
                 progress: Callable[[int, str], None] | None = None) -> list[tuple[IsoEntry, str, str]]:
    files = [e for e in entries if not e.is_dir]
    output = []
    for index, entry in enumerate(files):
        if cancelled():
            raise Cancelled("Import cancelled while hashing disc files.")
        digest = hashlib.sha256()
        head = bytearray()
        for chunk in reader.iter_extent(entry.extent, entry.size, cancelled=cancelled):
            digest.update(chunk)
            if len(head) < 64:
                head.extend(chunk[:64 - len(head)])
        fmt = detect_format(entry.path, bytes(head))
        output.append((entry, digest.hexdigest(), fmt))
        if progress:
            progress(70 + int(29 * (index + 1) / max(len(files), 1)), f"Hashing {entry.path}")
    return output


def detect_format(path: str, head: bytes) -> str:
    suffix = PurePosixPath(path).suffix.upper()
    if head.startswith(b"PS-X EXE"):
        return "PS-X EXE"
    if len(head) >= 8 and head[:4] == b"\x10\x00\x00\x00" and int.from_bytes(head[4:8], "little") & ~0x0F == 0:
        return "TIM"
    if suffix == ".CNF":
        return "PSX SYSTEM.CNF"
    if suffix in {".STR", ".XA"}:
        return suffix[1:] + " (not decoded)"
    return "unknown"


def safe_export(reader: SectorReader, entry: IsoEntry, destination: Path, overwrite: bool = False,
                cancelled=lambda: False, progress: Callable[[int, str], None] | None = None,
                chunk_size: int = 1024 * 1024) -> Path:
    destination = destination.resolve()
    if destination.exists() and not overwrite:
        raise ValidationError(f"Export already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Destination is selected as a complete file path; reject symlink parents.
    if any(part.is_symlink() for part in [destination.parent, *destination.parents]):
        raise ValidationError("Export path contains a symbolic-link directory.")
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.partial")
    written = 0
    try:
        with temporary.open("xb") as stream:
            extent_size = entry.extent_size if entry.extent_size is not None else entry.size
            for chunk in reader.iter_extent_range(entry.extent, extent_size, entry.offset, entry.size,
                                                  chunk_size, cancelled):
                stream.write(chunk)
                written += len(chunk)
                if progress:
                    progress(int(written * 100 / max(entry.size, 1)), f"Exporting {entry.path}")
            stream.flush()
            os.fsync(stream.fileno())
        if cancelled():
            raise Cancelled("Export cancelled.")
        if destination.exists() and not overwrite:
            raise ValidationError(f"Export already exists: {destination}")
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return destination
