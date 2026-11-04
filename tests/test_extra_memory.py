from __future__ import annotations

import struct

from dw1_workbench.extra_memory import EXTRA_START, FREE_LIST, extend_heap
from dw1_workbench.live import RAM_BASE, ram_offset

SIZE = 0x800000


def heap(ram, blocks):
    """A circular free list, as InitHeap3 and free3 leave it. blocks: (address, size)."""
    for index, (address, size) in enumerate(blocks):
        following = blocks[(index + 1) % len(blocks)][0]
        ram[ram_offset(address):ram_offset(address) + 8] = struct.pack("<II", following, size)
    ram[ram_offset(FREE_LIST):ram_offset(FREE_LIST) + 4] = struct.pack("<I", blocks[0][0])


def run(ram):
    read = lambda offset, size: bytes(ram[offset:offset + size])

    def write(offset, data):
        ram[offset:offset + len(data)] = data
    return extend_heap(read, write, SIZE)


def test_extra_ram_joins_the_end_of_the_free_list():
    ram = bytearray(SIZE)
    heap(ram, [(0x80150000, 100), (0x80160000, 50), (0x801F0000, 0)])
    assert run(ram)
    assert struct.unpack_from("<I", ram, ram_offset(0x801F0000))[0] == EXTRA_START
    assert struct.unpack_from("<II", ram, ram_offset(EXTRA_START)) == (0x80150000, (0x600000 - 0x1000) // 8)
    assert struct.unpack_from("<I", ram, ram_offset(0x80150000))[0] == 0x80160000


def test_the_rover_can_be_anywhere_and_it_extends_once():
    ram = bytearray(SIZE)
    heap(ram, [(0x80160000, 50), (0x801F0000, 0), (0x80150000, 100)])
    assert run(ram)
    before = bytes(ram)
    assert run(ram)
    assert bytes(ram) == before


def test_no_list_yet_or_a_broken_one_is_left_alone():
    ram = bytearray(SIZE)
    assert not run(ram)
    heap(ram, [(0x80150000, 100)])
    ram[ram_offset(0x80150000):ram_offset(0x80150000) + 4] = struct.pack("<I", 0x12345678)
    assert not run(ram)
    assert not any(ram[ram_offset(EXTRA_START):ram_offset(EXTRA_START) + 8])
