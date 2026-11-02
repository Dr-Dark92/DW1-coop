from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .errors import DiscFormatError

_FILE = re.compile(r'^\s*FILE\s+(?:"([^"]+)"|(\S+))\s+(\S+)\s*$', re.I)
_TRACK = re.compile(r'^\s*TRACK\s+(\d+)\s+(\S+)\s*$', re.I)
_INDEX = re.compile(r'^\s*INDEX\s+(\d+)\s+(\d+):(\d+):(\d+)\s*$', re.I)


@dataclass(frozen=True)
class CueTrack:
    number: int
    mode: str
    file_path: Path
    indices: dict[int, int] = field(default_factory=dict)


@dataclass(frozen=True)
class CueSheet:
    path: Path
    tracks: tuple[CueTrack, ...]

    @property
    def data_track(self) -> CueTrack:
        tracks = [t for t in self.tracks if t.mode.upper() != "AUDIO"]
        if len(tracks) != 1:
            raise DiscFormatError(
                f"Expected exactly one data track; found {len(tracks)}. Multi-data-track layouts are unsupported."
            )
        return tracks[0]


def parse_cue(path: Path) -> CueSheet:
    try:
        text = path.read_text(encoding="utf-8-sig", errors="strict")
    except (OSError, UnicodeError) as exc:
        raise DiscFormatError(f"Could not read CUE sheet: {exc}") from exc
    current_file: Path | None = None
    tracks: list[dict] = []
    for line_no, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.upper().startswith(("REM ", "TITLE ", "PERFORMER ", "CATALOG ")):
            continue
        match = _FILE.match(raw)
        if match:
            name, file_type = match.group(1) or match.group(2), match.group(3).upper()
            if file_type not in {"BINARY", "MOTOROLA"}:
                raise DiscFormatError(f"CUE line {line_no}: unsupported FILE type {file_type}.")
            candidate = (path.parent / name).resolve()
            try:
                candidate.relative_to(path.parent.resolve())
            except ValueError as exc:
                raise DiscFormatError(f"CUE line {line_no}: referenced file escapes the CUE directory.") from exc
            current_file = candidate
            continue
        match = _TRACK.match(raw)
        if match:
            if current_file is None:
                raise DiscFormatError(f"CUE line {line_no}: TRACK appears before FILE.")
            tracks.append({"number": int(match.group(1)), "mode": match.group(2).upper(),
                           "file_path": current_file, "indices": {}})
            continue
        match = _INDEX.match(raw)
        if match:
            if not tracks:
                raise DiscFormatError(f"CUE line {line_no}: INDEX appears before TRACK.")
            mm, ss, ff = map(int, match.groups()[1:])
            if ss >= 60 or ff >= 75:
                raise DiscFormatError(f"CUE line {line_no}: invalid MSF timestamp.")
            tracks[-1]["indices"][int(match.group(1))] = (mm * 60 + ss) * 75 + ff
            continue
        # PREGAP and POSTGAP affect timing but not the supported data-track payload.
        if line.upper().startswith(("PREGAP ", "POSTGAP ", "FLAGS ", "ISRC ")):
            continue
        raise DiscFormatError(f"CUE line {line_no}: unsupported or malformed directive: {line}")
    if not tracks:
        raise DiscFormatError("CUE sheet contains no tracks.")
    for track in tracks:
        if not track["file_path"].is_file():
            raise DiscFormatError(f"CUE track file is missing: {track['file_path'].name}")
        if 1 not in track["indices"]:
            raise DiscFormatError(f"CUE track {track['number']:02d} has no INDEX 01.")
    numbers = [t["number"] for t in tracks]
    if numbers != sorted(set(numbers)):
        raise DiscFormatError("CUE track numbers are duplicated or out of order.")
    return CueSheet(path.resolve(), tuple(CueTrack(**t) for t in tracks))
