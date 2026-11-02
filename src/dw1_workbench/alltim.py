from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Callable

from .disc import SectorReader
from .errors import Cancelled, DiscFormatError
from .iso9660 import IsoEntry
from .tim import TimImage, decode_tim

SLOT_SIZE = 0x4800
SLOT_COUNT = 180
DECODER_VERSION = "alltim-fixed-slots-v1"


@dataclass(frozen=True)
class AllTimSlot:
    index: int
    offset: int
    size: int
    sha256: str
    tim: TimImage


def decode_alltim(reader: SectorReader, entry: IsoEntry,
                  cancelled: Callable[[], bool] = lambda: False) -> list[AllTimSlot]:
    """Decode the 180 fixed nine-sector TIM slots used by USA CHDAT/ALLTIM.TIM."""
    expected = SLOT_COUNT * SLOT_SIZE
    if entry.path.upper() != "CHDAT/ALLTIM.TIM":
        raise DiscFormatError("The fixed-slot decoder only supports CHDAT/ALLTIM.TIM.")
    if entry.size != expected:
        raise DiscFormatError(f"ALLTIM size is {entry.size:#x}; expected {expected:#x}.")
    slots = []
    for index in range(SLOT_COUNT):
        if cancelled():
            raise Cancelled("Import cancelled while decoding ALLTIM slots.")
        offset = index * SLOT_SIZE
        data = reader.read_extent_range(entry.extent, entry.size, offset, SLOT_SIZE, cancelled)
        tim = decode_tim(data)
        slots.append(AllTimSlot(index, offset, SLOT_SIZE, hashlib.sha256(data).hexdigest(), tim))
    return slots
