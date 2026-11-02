from __future__ import annotations

import hashlib
import re
import struct
from dataclasses import dataclass

from .errors import DiscFormatError

NAME_TABLE_OFFSET = 0xA3B44
DISPLAY_TABLE_OFFSET = 0x9CEB4
DISPLAY_ENTRY_SIZE = 52
DISPLAY_NAME_SIZE = 20
ENTRY_SIZE = 8
ENTRY_COUNT = 180
DECODER_VERSION = "slus-character-table-v2"
MMD_INSPECTOR_VERSION = "mmd-boundary-v1"
_NAME = re.compile(r"^[A-Za-z0-9_]{1,7}$")


@dataclass(frozen=True)
class CharacterEntry:
    resource_id: int
    internal_name: str
    executable_offset: int
    raw: bytes
    model_path: str
    texture_slot: int
    display_name: str | None
    display_name_offset: int


@dataclass(frozen=True)
class MmdStructure:
    tmd_offset: int
    animation_offset: int
    tmd_id: int
    tmd_flags: int
    object_count: int


def parse_character_table(executable: bytes) -> list[CharacterEntry]:
    """Parse the USA executable's fixed 180-entry internal resource-name table."""
    end = max(NAME_TABLE_OFFSET + ENTRY_COUNT * ENTRY_SIZE,
              DISPLAY_TABLE_OFFSET + ENTRY_COUNT * DISPLAY_ENTRY_SIZE)
    if len(executable) < end or not executable.startswith(b"PS-X EXE"):
        raise DiscFormatError("USA executable is truncated or lacks its PS-X EXE header.")
    entries = []
    names = set()
    for resource_id in range(ENTRY_COUNT):
        offset = NAME_TABLE_OFFSET + resource_id * ENTRY_SIZE
        raw = executable[offset:offset + ENTRY_SIZE]
        name_bytes, separator, padding = raw.partition(b"\0")
        if not separator or any(padding):
            raise DiscFormatError(f"Character table entry {resource_id} is not NUL padded.")
        try:
            name = name_bytes.decode("ascii")
        except UnicodeDecodeError as exc:
            raise DiscFormatError(f"Character table entry {resource_id} is not ASCII.") from exc
        if not _NAME.fullmatch(name) or name in names:
            raise DiscFormatError(f"Character table entry {resource_id} has invalid or duplicate name {name!r}.")
        names.add(name)
        display_offset = DISPLAY_TABLE_OFFSET + resource_id * DISPLAY_ENTRY_SIZE
        display_raw = executable[display_offset:display_offset + DISPLAY_NAME_SIZE]
        display_bytes, separator, padding = display_raw.partition(b"\0")
        if not separator or any(padding):
            raise DiscFormatError(f"Character display name {resource_id} is not NUL padded.")
        try:
            display_name = display_bytes.decode("ascii")
        except UnicodeDecodeError as exc:
            raise DiscFormatError(f"Character display name {resource_id} is not ASCII.") from exc
        if display_name and not display_name.isprintable():
            raise DiscFormatError(f"Character display name {resource_id} is invalid.")
        model_path = f"CHDAT/MMD{resource_id // 30}/{name}.MMD"
        entries.append(CharacterEntry(resource_id, name, offset, raw, model_path, resource_id,
                                      display_name or None, display_offset))
    return entries


def inspect_mmd(data: bytes, total_size: int | None = None) -> MmdStructure:
    """Validate the MMD/TMD boundary only; geometry and animation remain unsupported."""
    if len(data) < 20:
        raise DiscFormatError("MMD header is truncated.")
    file_size = len(data) if total_size is None else total_size
    tmd_offset, animation_offset, tmd_id, flags, object_count = struct.unpack_from("<IIIII", data)
    if tmd_offset != 8:
        raise DiscFormatError(f"Unsupported MMD TMD offset {tmd_offset:#x}; expected 0x8.")
    if animation_offset <= tmd_offset + 12 or animation_offset > file_size:
        raise DiscFormatError("MMD animation offset is outside the file or overlaps the TMD header.")
    if tmd_id != 0x41:
        raise DiscFormatError(f"Embedded TMD has invalid id {tmd_id:#x}.")
    if flags not in {0, 1}:
        raise DiscFormatError(f"Embedded TMD has unsupported flags {flags:#x}.")
    if not 1 <= object_count <= 1024:
        raise DiscFormatError(f"Embedded TMD object count {object_count} is outside the safety limit.")
    object_table_end = tmd_offset + 12 + object_count * 28
    if object_table_end > animation_offset:
        raise DiscFormatError("Embedded TMD object table overlaps animation data.")
    return MmdStructure(tmd_offset, animation_offset, tmd_id, flags, object_count)


def entry_hash(entry: CharacterEntry) -> str:
    return hashlib.sha256(entry.raw).hexdigest()
