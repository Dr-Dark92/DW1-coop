"""Quick-save slots and memory-card files for the player, kept outside the disc and the project.

Each slot is a core save state plus a screenshot and a small JSON record (time, note, core identity,
size and SHA-256), written atomically so an interrupted save never leaves a half-written slot.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SLOT_COUNT = 9
RESUME_SLOT = 0  # written automatically when the player closes
MEMORY_CARD = "memory_card_1.srm"
FORMAT_VERSION = 1


class QuickSaveError(RuntimeError):
    pass


@dataclass(frozen=True)
class SlotInfo:
    slot: int
    created: str
    note: str
    core: str
    core_version: str
    size: int
    sha256: str

    @property
    def label(self) -> str:
        return "Resume" if self.slot == RESUME_SLOT else f"Slot {self.slot}"


def _atomic_write(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".partial")
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


class QuickSaveStore:
    """Slots for one disc; `directory` is normally <saves root>/<first 16 hex digits of the disc SHA-256>."""

    def __init__(self, directory: Path):
        self.directory = Path(directory)

    def _paths(self, slot: int):
        if not 0 <= slot <= SLOT_COUNT:
            raise QuickSaveError(f"Slot {slot} is outside 0–{SLOT_COUNT}.")
        stem = self.directory / f"slot{slot}"
        return stem.with_suffix(".state"), stem.with_suffix(".png"), stem.with_suffix(".json")

    def save(self, slot: int, state: bytes, screenshot: bytes | None, core: str, core_version: str,
             note: str = "", progress: bytes | None = None) -> SlotInfo:
        """`progress` is the player's level/EXP record (progression.Progress), kept with the slot when given."""
        state_path, image_path, info_path = self._paths(slot)
        info = SlotInfo(slot, datetime.now(timezone.utc).isoformat(timespec="seconds"), note.strip(), core,
                        core_version, len(state), hashlib.sha256(state).hexdigest())
        _atomic_write(state_path, state)
        if screenshot:
            _atomic_write(image_path, screenshot)
        else:
            image_path.unlink(missing_ok=True)
        if progress is not None:
            _atomic_write(self.progress_path(slot), progress)
        else:
            self.progress_path(slot).unlink(missing_ok=True)
        record = {"format": FORMAT_VERSION, **{key: getattr(info, key) for key in info.__dataclass_fields__}}
        _atomic_write(info_path, json.dumps(record, indent=2).encode())  # written last: marks the slot complete
        return info

    def info(self, slot: int) -> SlotInfo | None:
        _, _, info_path = self._paths(slot)
        try:
            record = json.loads(info_path.read_text())
            return SlotInfo(**{key: record[key] for key in SlotInfo.__dataclass_fields__})
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def slots(self) -> list[SlotInfo]:
        return [info for slot in range(SLOT_COUNT + 1) if (info := self.info(slot)) is not None]

    def screenshot_path(self, slot: int) -> Path | None:
        _, image_path, _ = self._paths(slot)
        return image_path if image_path.is_file() else None

    def load(self, slot: int, core: str | None = None) -> bytes:
        """The slot's state, after checking it is complete and unchanged; refuses another core's state."""
        state_path, _, _ = self._paths(slot)
        info = self.info(slot)
        if info is None:
            raise QuickSaveError(f"{'Resume' if slot == RESUME_SLOT else f'Slot {slot}'} is empty.")
        if core is not None and info.core != core:
            raise QuickSaveError(f"{info.label} was made by {info.core}, not {core}.")
        try:
            state = state_path.read_bytes()
        except OSError as exc:
            raise QuickSaveError(f"{info.label} could not be read: {exc}") from exc
        if len(state) != info.size or hashlib.sha256(state).hexdigest() != info.sha256:
            raise QuickSaveError(f"{info.label} is damaged (its state file does not match its record).")
        return state

    def progress_path(self, slot: int) -> Path:
        return self._paths(slot)[0].with_suffix(".progress.json")

    def progress(self, slot: int) -> bytes | None:
        try:
            return self.progress_path(slot).read_bytes()
        except OSError:
            return None

    def delete(self, slot: int):
        for path in (*self._paths(slot), self.progress_path(slot)):
            path.unlink(missing_ok=True)

    @property
    def memory_card_path(self) -> Path:
        return self.directory / MEMORY_CARD

    def read_memory_card(self) -> bytes | None:
        try:
            return self.memory_card_path.read_bytes()
        except FileNotFoundError:
            return None

    def write_memory_card(self, data: bytes):
        _atomic_write(self.memory_card_path, data)
