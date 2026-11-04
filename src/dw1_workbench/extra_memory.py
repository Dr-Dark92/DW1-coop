"""Hands the extended core's extra RAM to the game (USA).

The console has 2 MB, and the game's heap fills most of it: a large model doesn't fit. A core built with 8 MB (see
docs/FORMATS.md) has the rest above 0x80200000, but the game doesn't know. Its malloc3 and free3 (PsyQ, a K&R allocator)
keep a circular, address-sorted list of free blocks, {next, size in 8-byte units}, so the extra RAM becomes one more free
block after the list's last, and every allocation in the game can use it. A save holds the list, so a state saved
on the original core is extended again after it loads.
"""
from __future__ import annotations

import struct

from .coop import WORK_AREA_SIZE
from .live import RAM_BASE, RAM_SIZE, ram_offset

FREE_LIST = 0x80115A24  # the allocator's rover
EXTRA_START = RAM_BASE + RAM_SIZE
LONGEST_LIST = 4096


def extend_heap(read, write, ram_size: int) -> bool:
    """True once the game's free list includes the extra RAM. False while there's no list yet, or it looks wrong."""
    top = RAM_BASE + ram_size
    next_block = lambda block: struct.unpack("<I", read(ram_offset(block), 4))[0]
    start = struct.unpack("<I", read(ram_offset(FREE_LIST), 4))[0]
    if not RAM_BASE <= start < top - 8:
        return False
    block, last = start, None
    for _ in range(LONGEST_LIST):
        if block >= EXTRA_START:
            return True
        following = next_block(block)
        if not RAM_BASE <= following < top - 8:
            return False
        if following <= block:
            last = block
        block = following
        if block == start:
            break
    else:
        return False
    if last is None:
        return False
    write(ram_offset(EXTRA_START), struct.pack("<II", next_block(last), (top - WORK_AREA_SIZE - EXTRA_START) // 8))
    write(ram_offset(last), struct.pack("<I", EXTRA_START))
    return True
