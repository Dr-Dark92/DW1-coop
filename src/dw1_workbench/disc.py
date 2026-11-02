from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable

from .cue import parse_cue
from .errors import Cancelled, DiscFormatError

Progress = Callable[[int, str], None]


@dataclass(frozen=True)
class DiscLayout:
    source: Path
    data_file: Path
    sector_size: int
    user_offset: int
    start_sector: int
    sector_count: int
    description: str

    def open(self) -> "SectorReader":
        return SectorReader(self)


class SectorReader:
    def __init__(self, layout: DiscLayout):
        self.layout = layout
        self._file: BinaryIO | None = None

    def __enter__(self):
        self._file = self.layout.data_file.open("rb")
        return self

    def __exit__(self, *_):
        if self._file:
            self._file.close()

    def read_sector(self, lba: int) -> bytes:
        if self._file is None:
            raise RuntimeError("SectorReader is not open")
        if lba < 0 or lba >= self.layout.sector_count:
            raise DiscFormatError(f"Sector {lba} is outside the data track.")
        absolute = self.layout.start_sector + lba
        self._file.seek(absolute * self.layout.sector_size + self.layout.user_offset)
        data = self._file.read(2048)
        if len(data) != 2048:
            raise DiscFormatError(f"Image is truncated while reading sector {lba}.")
        return data

    def read_extent(self, lba: int, size: int, limit: int | None = None) -> bytes:
        if size < 0 or (limit is not None and size > limit):
            raise DiscFormatError(f"Extent size {size} exceeds the allowed limit.")
        sectors = (size + 2047) // 2048
        if lba < 0 or lba + sectors > self.layout.sector_count:
            raise DiscFormatError("Filesystem extent is outside the image.")
        return b"".join(self.read_sector(lba + i) for i in range(sectors))[:size]


def _raw_mode(path: Path, start_sector: int = 0) -> tuple[int, int, str] | None:
    size = path.stat().st_size
    if size % 2352:
        return None
    with path.open("rb") as stream:
        stream.seek(start_sector * 2352)
        head = stream.read(24)
    sync = b"\x00" + b"\xff" * 10 + b"\x00"
    if len(head) < 24 or head[:12] != sync:
        return None
    if head[15] == 2 and head[16:20] == head[20:24] and not (head[18] & 0x20):
        return 2352, 24, "raw Mode 2/Form 1 (2352-byte sectors)"
    if head[15] == 1:
        return 2352, 16, "raw Mode 1 (2352-byte sectors)"
    raise DiscFormatError("Raw sector image is not Mode 1 or Mode 2/Form 1 at the data-track start.")


def inspect_image(path: Path) -> DiscLayout:
    path = path.resolve()
    if not path.is_file():
        raise DiscFormatError(f"Image does not exist: {path}")
    start = 0
    data = path
    cue_mode = None
    if path.suffix.lower() == ".cue":
        sheet = parse_cue(path)
        track = sheet.data_track
        data, cue_mode = track.file_path, track.mode
        start = track.indices[1]
        if cue_mode not in {"MODE2/2352", "MODE1/2352", "MODE1/2048", "MODE2/2048"}:
            raise DiscFormatError(f"Unsupported CUE data track mode: {cue_mode}")
    size = data.stat().st_size
    if cue_mode and cue_mode.endswith("/2048"):
        sector_size, offset, desc = 2048, 0, cue_mode.lower()
    else:
        raw = _raw_mode(data, start)
        if raw:
            sector_size, offset, desc = raw
        elif not cue_mode and size % 2048 == 0:
            sector_size, offset, desc = 2048, 0, "cooked 2048-byte sectors"
        else:
            raise DiscFormatError("Unsupported sector layout; expected 2048-byte cooked or 2352-byte raw Mode 1/Mode 2 Form 1.")
    sectors = size // sector_size - start
    if sectors <= 16:
        raise DiscFormatError("Image is too small to contain an ISO 9660 filesystem.")
    layout = DiscLayout(path, data, sector_size, offset, start, sectors, desc)
    with layout.open() as reader:
        if reader.read_sector(16)[1:6] != b"CD001":
            raise DiscFormatError("No ISO 9660 primary volume descriptor at data-track sector 16.")
    return layout


def sha256_file(path: Path, progress: Progress | None = None, cancelled=lambda: False) -> str:
    digest = hashlib.sha256()
    total, done = path.stat().st_size, 0
    with path.open("rb") as stream:
        while chunk := stream.read(4 * 1024 * 1024):
            if cancelled():
                raise Cancelled("Import cancelled while hashing source image.")
            digest.update(chunk)
            done += len(chunk)
            if progress:
                progress(int(done * 100 / max(total, 1)), "Hashing source image")
    return digest.hexdigest()
