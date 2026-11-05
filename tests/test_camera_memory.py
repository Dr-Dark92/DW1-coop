import struct

import pytest

from dw1_workbench.camera_memory import CameraReservation, BUFFER, END, free_blocks
from dw1_workbench.extra_memory import FREE_LIST
from dw1_workbench.live import ram_offset
from test_fixed_framing import ProofCore


def test_claim_is_removed_from_free_list_and_release_coalesces():
    core = ProofCore()
    original = [(b.address, b.units) for b in free_blocks(core)]
    claim = CameraReservation(core)
    claim.reserve()
    claim.check()
    assert all(not b.units or b.end <= BUFFER - 8 or b.address >= END for b in free_blocks(core))
    claim.release()
    assert [(b.address, b.units) for b in free_blocks(core)] == original


def test_live_allocation_next_to_camera_is_not_undone_on_release():
    core = ProofCore()
    claim = CameraReservation(core)
    claim.reserve()
    # Simulate malloc3 consuming eight units at the high end of the lower free block.
    prefix = next(b for b in free_blocks(core) if b.address == 0x80200000)
    core.write_memory(ram_offset(prefix.address + 4), struct.pack("<I", prefix.units - 8))
    claim.release()
    assert any(b.address == BUFFER - 8 for b in free_blocks(core))  # cannot merge over the live allocation
    assert next(b for b in free_blocks(core) if b.address == prefix.address).units == prefix.units - 8


@pytest.mark.parametrize("bad", [0x12345678, 0x80200001])
def test_malformed_heap_is_rejected_without_writes(bad):
    core = ProofCore()
    core.write_memory(ram_offset(FREE_LIST), struct.pack("<I", bad))
    original = bytes(core.ram)
    with pytest.raises(ValueError): CameraReservation(core).reserve()
    assert bytes(core.ram) == original


def test_allocated_or_corrupted_range_is_rejected():
    core = ProofCore()
    core.write_memory(ram_offset(0x80200000 + 4), struct.pack("<I", 100))
    before = bytes(core.ram)
    with pytest.raises(ValueError, match="using the memory"):
        CameraReservation(core).reserve()
    assert bytes(core.ram) == before
    core = ProofCore()
    claim = CameraReservation(core)
    claim.reserve()
    core.write_memory(ram_offset(BUFFER - 8), bytes(8))
    with pytest.raises(ValueError, match="header"):
        claim.check()


def test_failed_header_write_rolls_back():
    core = ProofCore()
    before = bytes(core.ram)
    real_write, count = core.write_memory, [0]

    def fail_once(offset, data):
        count[0] += 1
        if count[0] == 2:
            raise OSError("simulated write failure")
        real_write(offset, data)

    core.write_memory = fail_once
    with pytest.raises(OSError): CameraReservation(core).reserve()
    assert bytes(core.ram) == before


def test_reservation_works_when_a_free_block_starts_right_at_it():
    """A save from before the hold was permanent: the game used the small block below, up to the hold's start."""
    from test_extra_memory import heap
    core = ProofCore()
    top = 0x80800000 - 0x1000
    heap(core.ram, [(0x801F0000, 0), (0x80200000, 16), (BUFFER - 8, (top - (BUFFER - 8)) // 8)])
    core.write_memory(ram_offset(FREE_LIST), struct.pack("<I", BUFFER - 8))
    claim = CameraReservation(core)
    claim.reserve()
    blocks = free_blocks(core)
    assert [b.address for b in blocks] == [0x801F0000, 0x80200000, END]
    assert struct.unpack("<I", core.read_memory(ram_offset(FREE_LIST), 4))[0] == 0x80200000
