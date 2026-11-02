import struct

import pytest

from dw1_workbench.characters import LOAD_ADDRESS, MOVE_NAME_COUNT, MOVE_NAMES_OFFSET, parse_move_names
from dw1_workbench.clip_names import TECH_ANIMATION_START, clip_name
from dw1_workbench.errors import DiscFormatError


def test_fixed_ids_differ_between_digimon_and_the_player_character():
    assert clip_name(4, resource_id=3).name == "Run"
    assert clip_name(3, resource_id=0).name == "Run"  # the player's own set: 3 is running
    assert clip_name(3, resource_id=3).name == "Walk (unwell)"
    assert clip_name(43, resource_id=5).confidence == "confirmed"


def test_technique_clips_use_the_digimon_move_list_and_names():
    moves = (2, 0xFF, 59) + (0xFF,) * 13
    names = ["Fire Tower", "Prominence Beam", "Spit Fire"] + [None] * 56 + ["Pepper Breath"]
    first = clip_name(TECH_ANIMATION_START, 3, moves, names)
    assert first.name == "Technique: Spit Fire" and "moves[0] = 2" in first.evidence
    assert clip_name(TECH_ANIMATION_START + 1, 3, moves, names) is None  # empty move slot
    assert clip_name(TECH_ANIMATION_START + 2, 3, moves, names).name == "Technique: Pepper Breath"


def test_ids_without_evidence_stay_unnamed():
    assert clip_name(21, resource_id=3) is None
    assert clip_name(21, resource_id=0) is None


def name_table(*names):
    data = bytearray(0xA0000)
    data[:8] = b"PS-X EXE"
    strings = 0x9A000
    pointers = []
    for index in range(MOVE_NAME_COUNT):
        name = names[index] if index < len(names) else b"Bubble"
        if name is None:
            pointers.append(LOAD_ADDRESS)  # unused slot points at the executable header
            continue
        data[strings:strings + len(name) + 1] = name + b"\0"
        pointers.append(LOAD_ADDRESS + strings)
        strings += len(name) + 1
    struct.pack_into(f"<{MOVE_NAME_COUNT}I", data, MOVE_NAMES_OFFSET, *pointers)
    return bytes(data)


def test_move_names_keep_game_spelling_and_mark_unused_slots():
    names = parse_move_names(name_table(b"Fire Tower", None, b"Inifinity Burn"))
    assert names[:3] == ["Fire Tower", None, "Inifinity Burn"] and len(names) == MOVE_NAME_COUNT


def test_move_names_reject_pointers_outside_the_executable():
    data = bytearray(name_table())
    struct.pack_into("<I", data, MOVE_NAMES_OFFSET + 4, LOAD_ADDRESS + 0x200000)
    with pytest.raises(DiscFormatError, match="outside the executable"):
        parse_move_names(bytes(data))
