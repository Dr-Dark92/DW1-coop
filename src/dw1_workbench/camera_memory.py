"""Bounded reservation from the USA PsyQ malloc3 free list, between emulator frames.

Headers/units follow the same K&R layout used by extra_memory.py. This is not an empty-RAM heuristic:
the entire fixed range must belong to one validated free block before it can be removed from the list.
"""
from dataclasses import dataclass
import struct

from .extra_memory import FREE_LIST, LONGEST_LIST
from .live import RAM_BASE, ram_offset
from .coop import WORK_AREA_SIZE

# Low in the extra RAM: malloc3 hands out a free block's far end first, so a played game has filled the top of it
# long before this. A state saved near the top of the heap would refuse the camera.
BUFFER = 0x80210000
PARAMS = 0x80223000
END = 0x80223200
MARKER = 0x46524350


@dataclass(frozen=True)
class Block:
    address: int
    following: int
    units: int

    @property
    def end(self):
        return self.address + self.units * 8


def free_blocks(core):
    top = RAM_BASE + core.ram_size - WORK_AREA_SIZE
    start = struct.unpack("<I", core.read_memory(ram_offset(FREE_LIST), 4))[0]
    address, seen, blocks = start, set(), []
    for _ in range(LONGEST_LIST):
        if address in seen:
            if address == start:
                break
            raise ValueError("Heap list cycles without returning to its rover")
        if address % 8 or not RAM_BASE <= address <= top - 8:
            raise ValueError("Heap header is unaligned or out of bounds")
        following, units = struct.unpack("<II", core.read_memory(ram_offset(address), 8))
        if address + max(1, units) * 8 > top:
            raise ValueError("Heap block extent is out of bounds")
        seen.add(address)
        blocks.append(Block(address, following, units))
        address = following
    else:
        raise ValueError("Heap free list exceeds inspection limit")
    ordered = sorted(blocks, key=lambda b: b.address)
    for index, block in enumerate(ordered):
        following = ordered[(index + 1) % len(ordered)]
        if block.following != following.address:
            raise ValueError("Heap free list is not circular address order")
        if index + 1 < len(ordered) and block.end > following.address:
            raise ValueError("Heap free blocks overlap")
    return ordered


def _write_headers(core, writes):
    """No emulation may run during this small transaction. Restore prior bytes if a write fails."""
    old = [(address, core.read_memory(ram_offset(address), len(data))) for address, data in writes]
    try:
        for address, data in writes:
            core.write_memory(ram_offset(address), data)
    except Exception:
        for address, data in reversed(old):
            core.write_memory(ram_offset(address), data)
        raise


def saved_hold(core) -> bool:
    """The RAM holds a hold's header, as a state saved with the frame on does. It may be old bytes: reserve() decides."""
    return core.read_memory(ram_offset(BUFFER - 8), 8) == struct.pack("<II", MARKER, (END - (BUFFER - 8)) // 8)


class CameraReservation:
    def __init__(self, core):
        self.core = core
        self.header = BUFFER - 8
        self.units = (END - self.header) // 8
        self.active = False

    def reserve(self):
        if self.active:
            raise ValueError("Camera reservation is already active")
        if self.core.ram_size != 0x800000:
            raise ValueError("Camera memory reservation requires the 8 MB core")
        blocks = free_blocks(self.core)
        if self.core.read_memory(ram_offset(self.header), 8) == struct.pack("<II", MARKER, self.units):
            self.active = True  # a save keeps the hold, so the game can never have used this memory
            try:
                self.check()
                return
            except ValueError:  # only old bytes: a released hold leaves them inside a free block
                self.active = False
        # Keep a full header on both sides: rover/list membership remains unchanged during allocation.
        found = next((b for b in blocks if b.address <= self.header and END + 8 <= b.end), None)
        if found is None:
            raise ValueError("the game is using the memory this camera needs")
        writes = [(self.header, struct.pack("<II", MARKER, self.units)),
                  (END, struct.pack("<II", found.following, (found.end - END) // 8))]
        if found.address < self.header:
            writes.append((found.address, struct.pack("<II", END, (self.header - found.address) // 8)))
        else:  # the free block starts right at the hold, so the block before it must skip over the hold
            previous = blocks[blocks.index(found) - 1]
            if previous == found:
                raise ValueError("the game's free memory is not as expected")
            writes.append((previous.address, struct.pack("<II", END, previous.units)))
            if struct.unpack("<I", self.core.read_memory(ram_offset(FREE_LIST), 4))[0] == self.header:
                writes.append((FREE_LIST, struct.pack("<I", previous.address)))
        _write_headers(self.core, writes)
        self.active = True
        self.check()

    def check(self):
        if not self.active:
            raise ValueError("Camera memory is not reserved")
        if self.core.read_memory(ram_offset(self.header), 8) != struct.pack("<II", MARKER, self.units):
            raise ValueError("Camera allocation header was changed")
        if any(b.units and b.address < END and b.end > self.header for b in free_blocks(self.core)):
            raise ValueError("Camera allocation overlaps the game's free memory")

    def release(self):
        if not self.active:
            return
        self.check()
        blocks = free_blocks(self.core)
        before = [b for b in blocks if b.address < self.header]
        previous = before[-1] if before else blocks[-1]
        following = next(b for b in blocks if b.address == previous.following)
        units, link = self.units, following.address
        if END == following.address:
            units += following.units
            link = following.following
        if previous.end == self.header:
            writes = [(previous.address, struct.pack("<II", link, previous.units + units))]
        else:
            writes = [(self.header, struct.pack("<II", link, units)),
                      (previous.address, struct.pack("<II", self.header, previous.units))]
        writes.append((FREE_LIST, struct.pack("<I", previous.address)))
        _write_headers(self.core, writes)
        self.active = False
