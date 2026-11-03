"""Zone inspection records made while playing: what was observed, where, with a screenshot and a save state.

Observations are stored separately from decoded data. Each record keeps the live map index, the player's
world position and tile, the trigger value the running game holds there, and the game's live tile grid, so
it can be compared with the decoded MAP file later. The save state lets the inspection be revisited.
"""
from __future__ import annotations

import json
import shutil
import zlib
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .quicksave import _atomic_write

CATEGORIES = ("Gateway / exit", "Toilet", "Interactive object", "Path / boundary", "NPC / Digimon", "Other")
FORMAT_VERSION = 1


class InspectionError(RuntimeError):
    pass


@dataclass(frozen=True)
class Inspection:
    id: str
    created: str
    map_index: int
    map_name: str
    position: tuple[int, int, int] | None
    tile: tuple[int, int] | None
    trigger: int | None
    partner_tile: tuple[int, int] | None
    grid_differences: int | None  # tiles where the live grid differs from the decoded MAP file; None if unknown
    category: str
    note: str
    core: str
    core_version: str
    npcs: tuple = ()  # (character ID, name, tile, behaviour) of each NPC the game had on the map


def _npc(entry) -> tuple:
    character_id, name, tile, *rest = entry
    return (int(character_id), str(name), tuple(tile), str(rest[0]) if rest else "")


class InspectionStore:
    def __init__(self, directory: Path):
        self.directory = Path(directory)

    def _folder(self, inspection_id: str) -> Path:
        if not inspection_id or "/" in inspection_id or "\\" in inspection_id or inspection_id.startswith("."):
            raise InspectionError(f"Invalid inspection id {inspection_id!r}.")
        return self.directory / inspection_id

    def add(self, *, map_index: int, map_name: str, position, tile, trigger, partner_tile, grid_differences,
            category: str, note: str, core: str, core_version: str, screenshot: bytes | None,
            state: bytes | None, live_grid: bytes | None, npcs=()) -> Inspection:
        now = datetime.now(timezone.utc)
        inspection_id = now.strftime("%Y%m%d-%H%M%S-%f")
        folder = self._folder(inspection_id)
        record = Inspection(inspection_id, now.isoformat(timespec="seconds"), map_index, map_name,
                            tuple(position) if position else None, tuple(tile) if tile else None, trigger,
                            tuple(partner_tile) if partner_tile else None, grid_differences, category,
                            note.strip(), core, core_version,
                            tuple(_npc(entry) for entry in npcs))
        if screenshot:
            _atomic_write(folder / "screen.png", screenshot)
        if state:
            _atomic_write(folder / "state.zz", zlib.compress(state, 1))
        if live_grid:
            _atomic_write(folder / "grid.bin", live_grid)
        _atomic_write(folder / "inspection.json",
                      json.dumps({"format": FORMAT_VERSION, **asdict(record)}, indent=2).encode())
        return record

    def get(self, inspection_id: str) -> Inspection | None:
        try:
            record = json.loads((self._folder(inspection_id) / "inspection.json").read_text())
            fields = {key: record[key] for key in Inspection.__dataclass_fields__ if key != "npcs"}
            fields["npcs"] = tuple(_npc(entry) for entry in record.get("npcs", []))
        except (OSError, ValueError, KeyError, TypeError):
            return None
        for key in ("position", "tile", "partner_tile"):
            fields[key] = tuple(fields[key]) if fields[key] is not None else None
        return Inspection(**fields)

    def list(self, map_index: int | None = None) -> list[Inspection]:
        if not self.directory.is_dir():
            return []
        records = [record for folder in sorted(self.directory.iterdir()) if folder.is_dir()
                   and (record := self.get(folder.name)) is not None]
        return [record for record in records if map_index is None or record.map_index == map_index]

    def screenshot_path(self, inspection_id: str) -> Path | None:
        path = self._folder(inspection_id) / "screen.png"
        return path if path.is_file() else None

    def has_state(self, inspection_id: str) -> bool:
        return (self._folder(inspection_id) / "state.zz").is_file()

    def state(self, inspection_id: str) -> bytes:
        try:
            return zlib.decompress((self._folder(inspection_id) / "state.zz").read_bytes())
        except (OSError, zlib.error) as exc:
            raise InspectionError(f"Inspection {inspection_id} has no usable save state: {exc}") from exc

    def live_grid(self, inspection_id: str) -> bytes | None:
        try:
            return (self._folder(inspection_id) / "grid.bin").read_bytes()
        except OSError:
            return None

    def delete(self, inspection_id: str):
        shutil.rmtree(self._folder(inspection_id), ignore_errors=True)
