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
DECODER_VERSION = "slus-character-table-v3"
# DigimonPara (jype0/dw_decomp include/dw/params.h): name[20], boneCount, radius, height, type, level, ...
LEVEL_FIELD = 29
MOVES_FIELD = 35  # uint8_t moves[16]: technique IDs; 0xff is empty
MOVE_COUNT = 16
# MOVE_NAMES (dw_decomp config/us/symbols.txt 0x80126054): 122 pointers to technique names.
MOVE_NAMES_OFFSET = 0x80126054 - 0x80090000
MOVE_NAME_COUNT = 122
LOAD_ADDRESS = 0x80090000
# Level enum names from DW1-SydPatches src/extern/dw1.hpp; 0 is UNDEFINED (humans, NPCs, a few Digimon).
STAGES = {1: "Fresh", 2: "In-Training", 3: "Rookie", 4: "Champion", 5: "Ultimate"}
# dw_decomp NUM_PARTNER_DIGIMON: resource IDs below this are the partner range (ID 0 is the player character).
PARTNER_COUNT = 66
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
    level: int = 0
    moves: tuple[int, ...] = ()

    @property
    def stage(self) -> str | None:
        return STAGES.get(self.level)

    @property
    def in_partner_range(self) -> bool:
        return 0 < self.resource_id < PARTNER_COUNT


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
        level = executable[display_offset + LEVEL_FIELD]
        if level > max(STAGES):
            raise DiscFormatError(f"Character {resource_id} has unknown level {level}.")
        moves = tuple(executable[display_offset + MOVES_FIELD:display_offset + MOVES_FIELD + MOVE_COUNT])
        entries.append(CharacterEntry(resource_id, name, offset, raw, model_path, resource_id,
                                      display_name or None, display_offset, level, moves))
    return entries


def parse_move_names(executable: bytes) -> list[str | None]:
    """Technique names from the USA executable's MOVE_NAMES pointer table; None for empty entries."""
    end = MOVE_NAMES_OFFSET + MOVE_NAME_COUNT * 4
    if len(executable) < end or not executable.startswith(b"PS-X EXE"):
        raise DiscFormatError("USA executable is truncated or lacks its PS-X EXE header.")
    names = []
    for index, pointer in enumerate(struct.unpack_from(f"<{MOVE_NAME_COUNT}I", executable, MOVE_NAMES_OFFSET)):
        offset = pointer - LOAD_ADDRESS
        if offset <= 0:  # points at the executable header: an unused slot
            names.append(None)
            continue
        if offset >= len(executable) or b"\0" not in executable[offset:offset + 64]:
            raise DiscFormatError(f"Technique name {index} points outside the executable.")
        raw = executable[offset:executable.index(b"\0", offset)]
        try:
            name = raw.decode("ascii")
        except UnicodeDecodeError as exc:
            raise DiscFormatError(f"Technique name {index} is not ASCII.") from exc
        if not name.isprintable():
            raise DiscFormatError(f"Technique name {index} is not printable.")
        names.append(name)
    return names


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
