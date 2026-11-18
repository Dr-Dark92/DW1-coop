"""Which sprite pieces a map's scripts show or hide as the story goes on (USA, dw_decomp src/main/script_interp.c).

The game keeps its scripts in SCN\\DG.SCN: a table of 2048 32-bit offsets (script n starts at the n-th), each script
a table of (u16 offset, u16 section) pairs ended by section 0xFFFF, then its code. SCN\\MAPHEAD.SCN is script 0, a
single such table of sections, one per screen, each starting with SET_SCRIPT (0xFB, _, script, screen), which names
the screen's own script (sections are numbered one above their screen; 0 and 1 are both screen 0's). Entering a map
runs its MAPHEAD section, then section 0xFE of its own script.
SET_OBJ_VISIBILITY (0x57, _, instance, flag) sets an instance's flag as setMapObjectsFlag does: 1 hides it, 0 shows it.

simulate() walks those two sections the way the interpreter would, deciding each IF from `triggers_set`: True for
a game that has done everything (every story trigger set), False for a new game. Other tests (stats, items, money,
script values) can't be known and count as false, the jumps follow, and the walk stops at text menus, SWITCH and
anything it can't size. The result is each instance's flag after entering, for instances the walk set.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

SCRIPT_TABLE = 2048
END_SECTION, END_SCRIPT, SET_SCRIPT = 0xFE, 0xFF, 0xFB
ENTRY_SECTION = 0xFE
IF, JUMP_TO, JUMP_AND_LINK, JUMP_RETURN, JUMP_TO_FILE, JUMP_TO_FILE_AND_LINK = 0x19, 0x16, 0x13, 0x15, 0x17, 0x14
SHOW_TEXTBOX, SET_OBJ_VISIBILITY = 0x1A, 0x57
MAX_STEPS = 4000

# instruction lengths, opcode included, from include/dw/script.h's formats ("_" is a padding byte)
LENGTHS = {
    0x13: 4, 0x14: 6, 0x15: 2, 0x16: 4, 0x17: 6, 0x1B: 2, 0x1C: 4, 0x1D: 4, 0x1E: 4, 0x1F: 4, 0x20: 4, 0x21: 2, 0x22: 2,
    0x23: 2, 0x24: 4, 0x25: 2, 0x26: 4, 0x27: 2, 0x28: 4, 0x29: 4, 0x2A: 6, 0x2B: 6, 0x2C: 10, 0x2D: 2, 0x2E: 2, 0x2F: 2,
    0x30: 2, 0x31: 4, 0x32: 4, 0x33: 4, 0x34: 4, 0x35: 4, 0x36: 4, 0x37: 2, 0x38: 6, 0x39: 6, 0x3A: 4, 0x3B: 4, 0x3C: 4,
    0x3D: 2, 0x3E: 2, 0x3F: 4, 0x46: 2, 0x47: 4, 0x48: 2, 0x49: 2, 0x4A: 2, 0x4B: 4, 0x4C: 4, 0x4D: 4, 0x4E: 8, 0x4F: 6,
    0x50: 4, 0x51: 4, 0x52: 8, 0x53: 4, 0x54: 2, 0x55: 8, 0x56: 4, 0x57: 4, 0x58: 2, 0x5A: 4, 0x5B: 2, 0x5C: 2, 0x5D: 2,
    0x5E: 2, 0x5F: 2, 0x64: 2, 0x65: 2, 0x66: 2, 0x67: 4, 0x68: 4, 0x69: 2, 0x6A: 4, 0x6C: 8, 0x6D: 4, 0x6E: 8, 0x6F: 4,
    0x70: 4, 0x71: 10, 0x72: 6, 0x73: 6, 0x74: 6, 0x75: 12, 0x76: 2, 0x77: 6, 0x78: 2, 0x79: 2, 0x7A: 4, 0x7B: 2, 0x7C: 8,
    0x7D: 10, 0x7E: 4, 0xFB: 6,
}
# IF conditions after their (op, pad) pair: trigger, script value, jump if true, jump if false; then the 0x20 group
CONDITION_ARGUMENTS = {0x00: 2, 0x08: 2, 0x10: 2, 0x18: 2}
COMPARE_ARGUMENTS = {0: 4, 1: 4, 2: 2, 3: 2, 4: 4, 5: 6}  # stat, card, move, condition, item count, money


class ScriptError(ValueError):
    pass


@dataclass(frozen=True)
class Scripts:
    """DG.SCN's scripts (by number) and MAPHEAD.SCN (script 0)."""
    dg: bytes
    maphead: bytes

    def script(self, number: int) -> bytes:
        if number == 0:
            return self.maphead
        if not 0 < number < SCRIPT_TABLE - 1 or len(self.dg) < SCRIPT_TABLE * 4:
            raise ScriptError(f"No script {number}.")
        start, end = struct.unpack_from("<2I", self.dg, number * 4)
        if not 0 < start < end <= len(self.dg):
            raise ScriptError(f"Script {number} is outside DG.SCN.")
        return self.dg[start:end]

    @staticmethod
    def section(script: bytes, section: int) -> int | None:
        for at in range(0, len(script) - 3, 4):
            offset, number = struct.unpack_from("<HH", script, at)
            if number == 0xFFFF:
                return None
            if number == section:
                return offset if offset < len(script) else None
        return None

    def map_head(self, screen: int) -> tuple[int, int] | None:
        """A screen's MAPHEAD section (its offset) and the script its SET_SCRIPT names, or None."""
        for at in range(0, len(self.maphead) - 3, 4):
            offset, number = struct.unpack_from("<HH", self.maphead, at)
            if number == 0xFFFF:
                return None
            if offset + 6 <= len(self.maphead) and self.maphead[offset] == SET_SCRIPT:
                script, named = struct.unpack_from("<HH", self.maphead, offset + 2)
                if named == screen:
                    return offset, script
        return None


def _text_length(script: bytes, at: int) -> int:
    """SHOW_TEXTBOX's text (script_textbox.c): units of 2 bytes (control 3 takes 4) up to a newline (13) followed by
    a zero byte, which takes 2 more."""
    p = at
    while p + 1 < len(script):
        control = script[p]
        if control == 3:
            p += 4
            continue
        p += 2
        if control == 13 and p < len(script) and script[p] == 0:
            return p + 2 - at
    raise ScriptError("Text runs past the end of its script.")


def _if(script: bytes, at: int, triggers_set: bool) -> int:
    """Evaluate an IF at `at` (its 0x19); the offset execution goes on at."""
    p, result = at + 2, False
    while True:
        if p + 1 >= len(script):
            raise ScriptError("IF runs past the end of its script.")
        op = script[p]
        if op == IF:
            return p + 2
        group, p = op & 0x38, p + 2
        if group == 0x10 or group == 0x18:
            (target,) = struct.unpack_from("<H", script, p)
            p += 2
            if result == (group == 0x10):
                return target
            continue
        if group == 0x00:
            condition = triggers_set if op & 7 == 0 else not triggers_set
        elif group == 0x20:
            condition = False
            p += COMPARE_ARGUMENTS.get(op & 7, 0) - CONDITION_ARGUMENTS[0x00]
        elif group in CONDITION_ARGUMENTS:
            condition = False
        else:
            raise ScriptError(f"Unknown IF condition 0x{op:02x}.")
        p += CONDITION_ARGUMENTS[0x00]
        if op & 0x80:
            result = result and condition
        elif op & 0x40:
            result = result or condition
        else:
            result = condition


def _walk(scripts: Scripts, number: int, at: int, triggers_set: bool, flags: dict[int, int],
          set_scripts: list[tuple[int, int]]) -> None:
    script, stack, steps, seen = scripts.script(number), [], 0, set()
    while True:
        steps += 1
        if steps > MAX_STEPS or at >= len(script) or (number, at) in seen:
            return
        seen.add((number, at))
        op = script[at]
        if op in (END_SECTION, END_SCRIPT):
            if not stack:
                return
            number, at = stack.pop()
            script = scripts.script(number)
            continue
        if op == IF:
            at = _if(script, at, triggers_set)
        elif op == SHOW_TEXTBOX:
            at += 2 + _text_length(script, at + 2)
        elif op == SET_OBJ_VISIBILITY:
            flags[script[at + 2]] = script[at + 3]
            at += 4
        elif op == SET_SCRIPT:
            set_scripts.append(struct.unpack_from("<HH", script, at + 2))
            at += 6
        elif op == JUMP_TO:
            at = struct.unpack_from("<H", script, at + 2)[0]
        elif op == JUMP_AND_LINK:
            stack.append((number, at + 4))
            at = struct.unpack_from("<H", script, at + 2)[0]
        elif op == JUMP_RETURN:
            if not stack:
                return
            number, at = stack.pop()
            script = scripts.script(number)
        elif op in (JUMP_TO_FILE, JUMP_TO_FILE_AND_LINK):
            target, section = struct.unpack_from("<HH", script, at + 2)
            if op == JUMP_TO_FILE_AND_LINK:
                stack.append((number, at + 6))
            try:
                script, number = scripts.script(target), target
            except ScriptError:
                return
            found = Scripts.section(script, section)
            if found is None:
                return
            at = found
        elif op in LENGTHS:
            at += LENGTHS[op]
        else:
            return  # menus (SET_SELECTION), SWITCH, DATA and unknown codes: the walk stops here


def simulate(scripts: Scripts, screen: int, triggers_set: bool) -> dict[int, int]:
    """Each instance's flag after entering `screen`, for the instances its entry scripts set."""
    flags: dict[int, int] = {}
    head = scripts.map_head(screen)
    if head is None:
        return flags
    offset, own = head
    _walk(scripts, 0, offset, triggers_set, flags, [])
    if own:
        try:
            script = scripts.script(own)
        except ScriptError:
            return flags
        entry = Scripts.section(script, ENTRY_SECTION)
        if entry is not None:
            _walk(scripts, own, entry, triggers_set, flags, [])
    return flags
