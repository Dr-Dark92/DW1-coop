import struct

from dw1_workbench.camera import (CALL_SITES, ICACHE_FIELD, ONE, ROUTINE,
                                   INVALID_TAG, flush_instruction_cache,
                                   jal, routine, sync_instruction_cache)


class FakeCore:
    def __init__(self, state, words):
        self.state = bytearray(state)
        self.words = words
        self.loads = 0

    def serialize(self):
        return bytes(self.state)

    def unserialize(self, state):
        self.state = bytearray(state)
        self.loads += 1

    def read_memory(self, offset, size):
        return struct.pack("<I", self.words[offset])


def state_with_cache(tags_words):
    state = bytearray(b"prefix" + ICACHE_FIELD + struct.pack("<I", 8192) + bytes(8192))
    start = state.find(ICACHE_FIELD) + len(ICACHE_FIELD) + 4
    for index, (tag, word) in tags_words.items():
        struct.pack_into("<2I", state, start + index * 8, tag, word)
    return state, start


def test_sync_updates_only_a_resident_word_and_leaves_misses_to_ram():
    state, start = state_with_cache({126: (0x800D71F8, 0x11111111), 29: (0x800CC070, 0x22222222)})
    core = FakeCore(state, {0xD71F8: 0x33333333})
    assert sync_instruction_cache(core, [0x800D71F8, 0x800B5074])
    assert struct.unpack_from("<I", core.state, start + 126 * 8 + 4)[0] == 0x33333333
    assert struct.unpack_from("<I", core.state, start + 29 * 8 + 4)[0] == 0x22222222
    assert core.loads == 1


def test_flush_preserves_word_address_and_sets_beetle_invalid_flag():
    state, start = state_with_cache({0: (0x8000000C, 1)})
    core = FakeCore(state, {})
    flush_instruction_cache(core)
    assert struct.unpack_from("<I", core.state, start)[0] == 0x8000000E
    assert INVALID_TAG == 2


def test_invalid_cached_word_is_not_mistaken_for_resident_code():
    state, _ = state_with_cache({126: (0x800D71FA, 123)})
    core = FakeCore(state, {})
    assert not sync_instruction_cache(core, [0x800D71F8])
    assert core.loads == 0


def test_zoom_routine_size_and_original_target():
    assert len(routine(ONE)) < 0x200  # code must not overlap the execution counter
    assert jal(0x800E56EC) == 0x0C0395BB
    assert CALL_SITES == (0x800B5074,) and ROUTINE == 0x801D0000  # foreground relative-position call only


def run_quad(scale, x, y, width, height, offset):
    """Execute the emitted integer instructions (independent of how they were generated)."""
    from dw1_workbench.camera import DRAWING_OFFSET_X, DRAWING_OFFSET_Y, HOOK_CALLS, VIEWPORT_DISTANCE
    regs = [0] * 32
    regs[4:8] = [0x1000, x, y, width]
    regs[29] = 0x2000
    memory = {0x2010: height, DRAWING_OFFSET_X: offset[0], DRAWING_OFFSET_Y: offset[1],
              VIEWPORT_DISTANCE: 800, HOOK_CALLS: 0}
    low, gte_h = 0, None
    for word in struct.unpack(f'<{len(routine(scale)) // 4}I', routine(scale)):
        op, rs, rt, rd = word >> 26, word >> 21 & 31, word >> 16 & 31, word >> 11 & 31
        imm = word & 65535
        signed = imm if imm < 32768 else imm - 65536
        addr = (regs[rs] + signed) & 0xFFFFFFFF
        if op == 15: regs[rt] = imm << 16
        elif op in (0x21, 0x23): regs[rt] = memory[addr]
        elif op in (0x29, 0x2B): memory[addr] = regs[rt]
        elif op == 9: regs[rt] = regs[rs] + signed
        elif op == 0:
            fn = word & 63
            if fn == 0x21: regs[rd] = regs[rs] + regs[rt]
            elif fn == 0x23: regs[rd] = regs[rs] - regs[rt]
            elif fn == 0x18: low = regs[rs] * regs[rt]
            elif fn == 0x12: regs[rd] = low
            elif fn == 3: regs[rd] = regs[rt] >> (word >> 6 & 31)
            elif fn not in (0, 8): raise AssertionError(hex(word))
        elif op == 0x12 and (word >> 21 & 31) == 6 and rd == 26: gte_h = regs[rt]
        else: raise AssertionError(hex(word))
        regs[0] = 0
    return [(memory[0x1000 + a], memory[0x1000 + a + 2]) for a in (8, 16, 24, 32)], memory[HOOK_CALLS], gte_h


def test_foreground_quads_scale_around_the_screen_centre():
    # Pieces are drawn at primitive + DRAWING_OFFSET; characters shrink towards screen (160, 120), which is
    # primitive (160 - offset) for them.
    for offset in ((160, 120), (109, 384), (-45, 220)):
        centre = (160 - offset[0], 120 - offset[1])
        for scale in (256, 192, 128):
            corners, calls, gte_h = run_quad(scale, -70, 35, 128, 100, offset)
            expected = [(centre[0] + (x - centre[0]) * scale // 256, centre[1] + (y - centre[1]) * scale // 256)
                        for x, y in ((-70, 35), (58, 35), (-70, 135), (58, 135))]
            assert corners == expected and calls == 1 and gte_h == 800


def test_ring_layouts_fit_vram_pages_and_cover_the_view():
    from dw1_workbench.camera import ENGINE_BUFFER, ENGINE_BUFFER_LIMIT, ring_layout
    assert ring_layout(192) == (84, [768 + 42 * c for c in range(6)], [0, 84, 168, 256])
    assert ring_layout(128) == (64, [768 + 32 * c for c in range(8)], [0, 64, 128, 256, 320])
    for scale in range(128, 256):
        tile, columns, rows = ring_layout(scale)
        assert len(columns) >= -(-320 * 256 // (scale * 128)) + 1 and len(rows) >= -(-240 * 256 // (scale * 128)) + 1
        assert columns[-1] + tile // 2 <= 1024 and all((y & 255) + tile <= 256 for y in rows) and rows[-1] + tile <= 384
        assert all((x - (x & ~63)) * 2 + tile <= 256 for x in columns)  # stays inside one texture page
        if scale < 230:
            assert ENGINE_BUFFER + tile * tile <= ENGINE_BUFFER_LIMIT


def test_engine_params_match_the_c_layout():
    from dw1_workbench.camera import ENGINE_MAGIC, ENGINE_PARAMS_SIZE, engine_params
    params = engine_params(192)
    assert len(params) == ENGINE_PARAMS_SIZE == 252
    magic, frames, uploads, scale, inverse, tile, columns, rows, budget = struct.unpack_from("<iiihhhhhh", params)
    assert (magic, frames, scale, inverse, tile, columns, rows) == (ENGINE_MAGIC, 0, 192, 341, 84, 6, 4)
    assert params[68:116] == b"\xff" * 48 and params[116:116 + 84] == bytes((i * 128 + 64) // 84 for i in range(84))
    import pytest
    with pytest.raises(ValueError):
        engine_params(256)


def test_generated_engine_code_matches_its_source():
    import hashlib
    from pathlib import Path
    import dw1_workbench.camera_engine_code as code
    source = Path(code.__file__).with_name("camera_engine.c").read_bytes()
    assert code.SOURCE_SHA256 == hashlib.sha256(source).hexdigest(), "run tools/build_camera_engine.py"
    assert code.LOAD_ADDRESS == 0x801D1000 and code.ENTRY == code.LOAD_ADDRESS and len(code.CODE) < 0x2000


GPURAM = b"\r&GPURAM[0][0]"


class RamCore:
    """Main RAM plus a save state holding the instruction cache and VRAM, like Beetle PSX."""

    def __init__(self):
        from dw1_workbench.camera import (MAP_TILE_DATA, MAP_TILE_X, MAP_TILE_Y, NATIVE_UPLOADS, RENDER_MAP_LOOP,
                                           RENDER_MAP_LOOP_WORDS, SET_POS_DATA_POLY_FT4, VIEWPORT_DISTANCE)
        from dw1_workbench.live import ram_offset
        self.ram = bytearray(0x200000)
        self.vram = bytearray(1024 * 512 * 2)
        for site in CALL_SITES:
            self.write_memory(ram_offset(site), struct.pack('<I', jal(SET_POS_DATA_POLY_FT4)))
        self.write_memory(ram_offset(RENDER_MAP_LOOP), struct.pack('<4I', *RENDER_MAP_LOOP_WORDS))
        for address, words in NATIVE_UPLOADS.items():
            self.write_memory(ram_offset(address), struct.pack('<2I', *words))
        self.write_memory(ram_offset(VIEWPORT_DISTANCE), struct.pack('<I', 800))
        base = ram_offset(MAP_TILE_DATA)
        self.write_memory(base + 0x1BB4, struct.pack('<hhbb', 256, 192, 5, 4))
        self.write_memory(ram_offset(MAP_TILE_X), b"\x01"); self.write_memory(ram_offset(MAP_TILE_Y), b"\x00")
        for y in range(4):
            for x in range(5):  # tile pixels at 0x80020000 + index * 0x4000, value = index + 1
                index = x + y * 5
                image = 0x80020000 + index * 0x4000
                self.write_memory(ram_offset(image), bytes([index + 1]) * 0x4000)
                self.write_memory(base + index * 20, struct.pack('<Ihhhhh', image, index, x * 128, y * 128,
                                                                 768 + (x % 4) * 64, (y % 3) * 128))
        self.original = bytes(self.ram)

    def read_memory(self, offset, size): return bytes(self.ram[offset:offset + size])
    def write_memory(self, offset, data): self.ram[offset:offset + len(data)] = data

    def serialize(self):
        cache = state_with_cache({})[0]
        return bytes(cache) + GPURAM + struct.pack('<I', len(self.vram)) + bytes(self.vram)

    def unserialize(self, state):
        at = state.find(GPURAM) + len(GPURAM) + 4
        self.vram[:] = state[at:at + len(self.vram)]


def test_install_zoom_and_restore_native_code_projection_and_tile_ring():
    from dw1_workbench.camera import (ENGINE_PARAMS, NATIVE_UPLOADS, RENDER_MAP_LOOP, VIEWPORT_DISTANCE,
                                       CameraZoom, j, jr, RA, RENDER_MAP_AFTER_LOOP)
    from dw1_workbench.camera_engine_code import CODE, ENTRY, LOAD_ADDRESS
    from dw1_workbench.live import ram_offset
    import pytest
    core = RamCore()
    u32 = lambda address: struct.unpack('<I', core.read_memory(ram_offset(address), 4))[0]
    zoom = CameraZoom(core)
    with pytest.raises(ValueError):
        zoom.install(256)  # 100% uses the game's own renderer
    zoom.install(192)
    assert core.read_memory(ram_offset(LOAD_ADDRESS), len(CODE)) == CODE
    assert struct.unpack('<4I', core.read_memory(ram_offset(RENDER_MAP_LOOP), 16)) == (jal(ENTRY), 0, j(RENDER_MAP_AFTER_LOOP), 0)
    assert all(u32(address) == jr(RA) for address in NATIVE_UPLOADS)
    assert u32(CALL_SITES[0]) == jal(ROUTINE)
    zoom.tick()
    assert u32(VIEWPORT_DISTANCE) == 800  # nothing changes until the engine is seen running
    core.write_memory(ram_offset(ENGINE_PARAMS + 4), struct.pack('<I', 1))
    zoom.tick()
    assert u32(VIEWPORT_DISTANCE) == 600 and zoom.scale == 192
    zoom.set_scale(128)
    core.write_memory(ram_offset(ENGINE_PARAMS + 4), struct.pack('<I', 2))
    zoom.tick()
    assert u32(VIEWPORT_DISTANCE) == 400 and struct.unpack_from('<h', core.read_memory(ram_offset(ENGINE_PARAMS + 16), 2))[0] == 64
    with pytest.raises(ValueError):
        CameraZoom(core).install(192)  # an earlier patch is still in place
    from dw1_workbench.camera import ENGINE_BUFFER
    core.write_memory(ram_offset(ENGINE_BUFFER), b"tile data")  # the engine's downscale buffer after uploads
    zoom.uninstall()
    assert bytes(core.ram) == core.original
    # The game's own 4x3 ring is back in VRAM: tile (x, y) at (768 + 64 * (x % 4), 128 * (y % 3)).
    for x, y in ((1, 0), (4, 2), (2, 1)):
        index = x + y * 5
        start = ((128 * (y % 3) + 5) * 1024 + 768 + 64 * (x % 4)) * 2
        assert core.vram[start:start + 128] == bytes([index + 1]) * 128
    CameraZoom(core).install(171)
