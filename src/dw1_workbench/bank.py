"""P6 Digi-Bank: stored partners and new eggs, USA only; source layouts: dw_decomp aaf69a4.

A stored partner is its Stats (include/dw/entity.h: base stats, equipped moves, current HP/MP), its
PartnerPara (include/dw/params.h: care, age, weight, battles) and its own EXP. Learned techniques stay with
the player, as they do in the original game across rebirths (PartnerEntity.learnedMoves is never swapped).

Making a stored partner active runs the native evolution sequence to its form (that loads the model), then
writes the stored record over what the sequence set. A new egg hatches one of the four native rebirth
babies (REINCARNATE_BABY_TYPE) with the stats initializeReincarnatedPartner gives it (src/main/partner_impl.c).
"""
from __future__ import annotations

import struct

from .digidex import read_skill
from .live import ram_offset
from .progression import (CLASS_NAMES, DIGIMON_DATA, DIGIMON_LEVEL, DIGIMON_PARA_SIZE, FULL_LIFETIME, PARTNER_ENTITY,
                          PARTNER_PARA, PARTNER_STATS, Progress, level_for, stats_for, total_exp)

STATS_SIZE, PARA_SIZE = 32, 0x84
IMPORTED_FORM_LEVEL = 45  # an imported form the egg list offers (a test entry): it starts as a grown partner
RAISE_DATA, RAISE_DATA_SIZE, DEFAULT_WEIGHT = 0x801225BC, 28, 21
# setReincarnateStats arguments: HP, MP, Offense, Defense, Speed, Brains (no inheritance: not a natural death).
EGGS = {1: (90, 110, 10, 11, 9, 10), 15: (120, 100, 16, 8, 8, 6), 29: (100, 140, 12, 6, 6, 8), 43: (100, 120, 8, 8, 8, 8)}
# PartnerPara offsets.
CONDITION, MISSED_SLEEP, TIREDNESS_SLEEP_TIMER, TIREDNESS, SUB_TIREDNESS = 0, 20, 22, 34, 36
DISCIPLINE, HAPPINESS, UNUSED3, TIMES_SICK = 40, 42, 44, 48
WEIGHT, LIFETIME, AGE, CARE_MISTAKES, BATTLES, EVO_TIMER = 66, 72, 74, 82, 84, 86
# initializeReincarnatedPartner's PartnerPara values, after the evolution sequence has set the form's weight.
EGG_PARA = ((DISCIPLINE, 50), (HAPPINESS, 50), (UNUSED3, 50), (TIMES_SICK, 0), (LIFETIME, FULL_LIFETIME), (AGE, 0),
            (EVO_TIMER, 0), (CARE_MISTAKES, 0), (BATTLES, 0), (MISSED_SLEEP, 0), (TIREDNESS_SLEEP_TIMER, 0),
            (TIREDNESS, 0), (SUB_TIREDNESS, 0))


def _ram(read, address, size):
    return read(ram_offset(address), size)


def species(read, kind) -> tuple[str, int]:
    record = _ram(read, DIGIMON_DATA + kind * DIGIMON_PARA_SIZE, DIGIMON_PARA_SIZE)
    return record[:20].split(b"\0", 1)[0].decode("ascii", errors="replace") or f"Digimon {kind}", record[DIGIMON_LEVEL]


def capture(read, exp: int) -> dict:
    """The active partner as a bank record."""
    return {"kind": struct.unpack("<i", _ram(read, PARTNER_ENTITY, 4))[0], "exp": exp,
            "stats": _ram(read, PARTNER_STATS, STATS_SIZE).hex(), "para": _ram(read, PARTNER_PARA, PARA_SIZE).hex()}


def egg(kind: int) -> dict:
    if kind not in EGGS:
        raise ValueError(f"No egg hatches Digimon {kind}")
    return {"kind": kind, "exp": 0, "stats": None, "para": None}


def imported_form(kind: int) -> dict:
    """A partner for a form imported over a native Digimon; its stats are its level's."""
    return {"kind": kind, "exp": total_exp(IMPORTED_FORM_LEVEL), "stats": None, "para": None}


def _new_stats(record: dict) -> tuple:
    """HP, MP, Offense, Defense, Speed, Brains of a partner that has never been active."""
    if record["kind"] in EGGS:
        return EGGS[record["kind"]]
    stats = stats_for(level_for(record["exp"]))
    return tuple(stats[name] for name in ("hp", "mp", "offense", "defense", "speed", "brains"))


def apply(read, write, record: dict):
    """Write a stored partner over the active one, after the evolution sequence loaded its form."""
    if record["stats"] is not None:
        write(ram_offset(PARTNER_STATS), bytes.fromhex(record["stats"]))
    else:
        hp, mp, offense, defense, speed, brains = _new_stats(record)
        write(ram_offset(PARTNER_STATS), struct.pack("<4h", offense, defense, speed, brains))
        write(ram_offset(PARTNER_STATS + 16), struct.pack("<4h", hp, mp, hp, mp))
    if record["para"] is not None:
        write(ram_offset(PARTNER_PARA), bytes.fromhex(record["para"]))
    else:
        write(ram_offset(PARTNER_PARA + CONDITION), bytes(4))
        for offset, value in EGG_PARA:
            write(ram_offset(PARTNER_PARA + offset), struct.pack("<h", value))


def values(read, record: dict) -> dict:
    """What a bank page shows: name, stage, level, stats, care and equipped technique ids."""
    name, stage = species(read, record["kind"])
    if record["stats"] is not None:
        stats = bytes.fromhex(record["stats"])
        offense, defense, speed, brains = struct.unpack_from("<4h", stats, 0)
        hp, mp, current_hp, current_mp = struct.unpack_from("<4h", stats, 16)
        moves = stats[12:16]
    else:
        hp, mp, offense, defense, speed, brains = _new_stats(record)
        current_hp, current_mp, moves = hp, mp, bytes([0x2E, 255, 255, 255])
    if record["para"] is not None:
        para = bytes.fromhex(record["para"])
        care = {key: struct.unpack_from("<h", para, offset)[0] for key, offset in
                (("age", AGE), ("weight", WEIGHT), ("discipline", DISCIPLINE), ("happiness", HAPPINESS),
                 ("battles", BATTLES), ("mistakes", CARE_MISTAKES))}
    else:
        weight = _ram(read, RAISE_DATA + record["kind"] * RAISE_DATA_SIZE + DEFAULT_WEIGHT, 1)[0]
        care = {"age": 0, "weight": weight, "discipline": 50, "happiness": 50, "battles": 0, "mistakes": 0}
    table = _ram(read, DIGIMON_DATA + record["kind"] * DIGIMON_PARA_SIZE + 35, 16)
    techniques = [table[move - 0x2E] for move in dict.fromkeys(moves) if 0x2E <= move < 0x3E and table[move - 0x2E] < 122]
    return {"name": name, "stage": stage, "progress": Progress(record["exp"]), "hp": hp, "mp": mp,
            "current_hp": current_hp, "current_mp": current_mp, "offense": offense, "defense": defense,
            "speed": speed, "brains": brains, **care, "techniques": techniques}


def pages(read, record: dict) -> list[list[tuple]]:
    """Bank status pages of (style, text, technique id) rows, styled like the Digi-Dex (digidex.entry_rows)."""
    v = values(read, record)
    done, need = v["progress"].into_level
    level = f"Level: {v['progress'].level}" + (f"   EXP {done}/{need}" if need else "   max")
    first = [("info", f"Stage: {CLASS_NAMES.get(v['stage'], 'Unknown')}", None), ("info", level, None),
             ("stat", f"HP {v['current_hp']}/{v['hp']}   MP {v['current_mp']}/{v['mp']}", None),
             ("stat", f"Off {v['offense']}   Def {v['defense']}", None),
             ("stat", f"Speed {v['speed']}   Brains {v['brains']}", None),
             ("stat", f"Age {v['age']}d   Weight {v['weight']}g", None)]
    if record["stats"] is None:
        first.append(("note", "Newly hatched from an egg." if record["kind"] in EGGS else "A new partner.", None))
    second = [("header", "Care", None), ("stat", f"Discipline {v['discipline']}   Happiness {v['happiness']}", None),
              ("stat", f"Battles {v['battles']}   Mistakes {v['mistakes']}", None), ("header", "Equipped techniques", None)]
    second += [("skill", read_skill(read, move)["name"], move) for move in v["techniques"]] or [("note", "None", None)]
    return [first, second]
