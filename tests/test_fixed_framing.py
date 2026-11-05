"""Fixed proof remains isolated: test exact code hooks, native RAM projection and complete restoration."""
import struct

import pytest

from dw1_workbench import fixed_framing as F
from dw1_workbench.camera import CALL_SITES, ENGINE_PARAMS, VIEWPORT_DISTANCE, jal
from dw1_workbench.fixed_engine_code import NATIVE_ENTRIES, PROJECTION_ENTRY, FOREGROUND_ENTRY, RENDER_ENTRY, SPRITE_ENTRY
from dw1_workbench.camera_memory import PARAMS, BUFFER, END, free_blocks
from dw1_workbench.live import ram_offset
from test_camera import RamCore


class ProofCore(RamCore):
    ram_size = 8 * 1024 * 1024

    def __init__(self):
        super().__init__()
        self.ram.extend(bytes(self.ram_size - len(self.ram)))
        from test_extra_memory import heap
        heap(self.ram, [(0x801F0000, 0), (0x80200000, (0x600000 - 0x1000) // 8)])
        self.gte = bytearray(128)
        struct.pack_into("<I", self.gte, 26 * 4, 800)
        self.write_memory(ram_offset(0x800EFB68 + 32), struct.pack("<I", jal(0x800981C8)))
        self.write_memory(ram_offset(0x800EFB68 + 64), struct.pack("<I", jal(F.RENDER_OBJECTS)))
        self.write_memory(ram_offset(F.GS_SORT_SPRITE), struct.pack("<2I", *F.GS_SORT_SPRITE_HEAD))
        for address, limit in F.OVERLAY_LIMITS.items():
            self.write_memory(ram_offset(address), struct.pack("<I", limit[0]))
        self.original = bytes(self.ram)

    def serialize(self):
        return super().serialize() + F.GTE_CR + bytes(self.gte)

    def unserialize(self, state):
        super().unserialize(state)
        at = state.find(F.GTE_CR)
        if at >= 0:
            self.gte[:] = state[at + len(F.GTE_CR):at + len(F.GTE_CR) + 128]


def test_projection_fields_require_unique_complete_verified_layout():
    state = b"prefix" + F.GTE_CR + bytes(128)
    assert F.gte_projection_offset(state) == 6 + len(F.GTE_CR) + 96
    for bad in (b"", state[:-1], state + F.GTE_CR + bytes(128)):
        with pytest.raises(ValueError): F.gte_projection_offset(bad)


def test_fixed_hook_is_render_only_and_restores_all_ram_and_gte():
    core = ProofCore()
    original_gte = bytes(core.gte)
    proof = F.FixedFramingProof(core)
    proof.install()
    read = lambda address: struct.unpack("<I", core.read_memory(ram_offset(address), 4))[0]
    assert read(0x800EFB68 + 32) == jal(PROJECTION_ENTRY)
    assert all(read(site) == jal(FOREGROUND_ENTRY) for site in CALL_SITES)
    assert core.read_memory(ram_offset(PARAMS + 62), 2) == b"\1\0"
    assert read(0x800EFB68 + 64) == jal(RENDER_ENTRY)
    assert read(F.GS_SORT_SPRITE) >> 26 == 2  # j
    assert (read(F.GS_SORT_SPRITE) & 0x3FFFFFF) << 2 == SPRITE_ENTRY & 0x0FFFFFFF
    assert struct.unpack("<3I", core.read_memory(ram_offset(F.SPRITE_STUB + 0), 12))[:2] == F.GS_SORT_SPRITE_HEAD
    for index, address in enumerate(F.NATIVE_CAMERA):
        assert (read(address) & 0x3FFFFFF) << 2 == NATIVE_ENTRIES[index] & 0x0FFFFFFF
        assert read(F.NATIVE_STUBS + index * 16 + 8) >> 26 == 2
    assert all(not b.units or b.end <= BUFFER - 8 or b.address >= END for b in free_blocks(core))
    assert core.read_memory(ram_offset(0x801C0000), 0x13200) == core.original[0x1C0000:0x1D3200]
    assert read(VIEWPORT_DISTANCE) == 800
    proof.tick()
    assert read(VIEWPORT_DISTANCE) == 800
    with pytest.raises(ValueError, match="fixed framing"):
        proof.set_scale(128)
    struct.pack_into("<iiI", core.gte, 24 * 4, 3 << 16, -27 << 16, 718)
    proof.uninstall()
    from dw1_workbench.extra_memory import FREE_LIST
    # The allocator may move its rover; original game bytes and old scratch area must otherwise be intact.
    before, after = bytearray(core.original[:0x200000]), bytearray(core.ram[:0x200000])
    at = ram_offset(FREE_LIST)
    before[at:at + 4] = after[at:at + 4]
    assert after == before
    assert [(b.address, b.units) for b in free_blocks(core)] == [(0x801F0000, 0), (0x80200000, (0x600000 - 0x1000) // 8)]
    assert bytes(core.gte) == original_gte


@pytest.mark.parametrize("address,data", [(F.GAME_STATE, b"\1"), (F.SCRIPT_MAP_CHANGE_STATE, b"\1\0")])
def test_scene_guard_rejects_before_any_patch(address, data):
    core = ProofCore()
    core.write_memory(ram_offset(address), data)
    original = bytes(core.ram)
    with pytest.raises(ValueError, match="stops"):
        F.FixedFramingProof(core).install()
    assert bytes(core.ram) == original


def test_a_camera_function_that_starts_with_a_jump_refuses_the_frame():
    core = ProofCore()
    core.write_memory(ram_offset(F.MAP_SIZE), b"\5\4")
    core.write_memory(ram_offset(F.NATIVE_CAMERA[0] + 4), struct.pack("<I", jal(0x80090000)))
    with pytest.raises(ValueError, match="camera function"):
        F.FixedFramingProof(core, F.DOUBLE_FRAME).install()


def test_compiled_camera_call_uses_the_native_projection_and_puts_the_wide_one_back(tmp_path):
    import ctypes
    from pathlib import Path
    import shutil
    import subprocess
    if not shutil.which("cc"):
        pytest.skip("C compiler unavailable")
    text = Path(F.__file__).with_name("camera_engine.c").read_text()
    start = text.index("static s32 native_call(")
    function = text[start:text.index("s32 fixed_native_0(", start)]
    source = """
typedef int s32;
typedef s32 (*NativeFn)(s32, s32, s32);
static int memory, h, gx, gy, seen[3];
static struct { s32 scale; } params = { 128 };
static struct { s32 geom_x, geom_y, drawing, native_distance; void *native_entries[5]; } fixed = { 7, 9 };
#define PARAMS (&params)
#define FIXED (&fixed)
#define VIEWPORT_DISTANCE 0
#define AT(address, type) memory
void GsSetProjection(s32 distance) { h = distance; }
static void set_geom_fine(s32 x, s32 y) { gx = x; gy = y; }
static s32 camera(s32 a, s32 b, s32 c) { seen[0] = h; seen[1] = gx; seen[2] = gy; return a + b + c; }
""" + function.replace("(void *)FIXED->native_entries[index]", "camera") + """
int run(int drawing, int value, int *out) {
 memory = value; fixed.drawing = drawing; fixed.native_distance = 1511;
 out[0] = native_call(0, 1, 2, 3); out[1] = seen[0]; out[2] = seen[1]; out[3] = seen[2]; out[4] = h; out[5] = gx; out[6] = gy;
 return 0;
}
"""
    path = tmp_path / "native.c"
    path.write_text(source)
    library = tmp_path / "native.so"
    subprocess.run(["cc", "-shared", "-fPIC", str(path), "-o", str(library)], check=True)
    result = (ctypes.c_int * 7)()
    ctypes.CDLL(str(library)).run(1, 755, result)  # while drawing the RAM value is the scaled one
    assert list(result) == [6, 1511, 0, 0, 755, 7, 9]
    ctypes.CDLL(str(library)).run(0, 2000, result)  # a map load has just set a new distance
    assert list(result) == [6, 2000, 0, 0, 1000, 7, 9]


def test_double_frame_needs_a_map_twice_the_view():
    core = ProofCore()
    core.write_memory(ram_offset(F.MAP_SIZE), b"\4\4")
    with pytest.raises(ValueError, match="artwork"):
        F.FixedFramingProof(core, F.DOUBLE_FRAME).install()  # 512 wide cannot fill 640
    core.write_memory(ram_offset(F.MAP_SIZE), b"\5\4")
    proof = F.FixedFramingProof(core, F.DOUBLE_FRAME)
    proof.install()
    assert struct.unpack("<h", core.read_memory(ram_offset(PARAMS + 12), 2))[0] == F.DOUBLE_FRAME
    proof.uninstall()


def test_controller_is_off_for_battles_and_comes_back_with_the_memory_still_held():
    notices = []
    core = ProofCore()
    core.write_memory(ram_offset(F.MAP_SIZE), b"\5\4")
    native = bytes(core.ram[ram_offset(CALL_SITES[0]):ram_offset(CALL_SITES[0]) + 4])
    controller = F.FrameController(core, notices.append)
    for _ in range(F.SETTLE_FRAMES - 1):
        controller.tick()
    assert not controller.active  # waits out the fade-in
    controller.tick()
    assert controller.active and controller.proof.reservation.active
    core.write_memory(ram_offset(F.GAME_STATE), b"\1")
    controller.tick()
    assert not controller.active and bytes(core.ram[ram_offset(CALL_SITES[0]):ram_offset(CALL_SITES[0]) + 4]) == native
    assert controller.proof.reservation.active  # the game can't allocate over it during the battle
    for _ in range(F.SETTLE_FRAMES + 2):
        controller.tick()
    assert not controller.active  # still in the battle
    core.write_memory(ram_offset(F.GAME_STATE), b"\0")
    for _ in range(F.RETURN_FRAMES):
        controller.tick()
    assert controller.active and not notices  # back during the fade, not a second later


def test_loading_a_save_with_the_hold_returns_at_once():
    core = ProofCore()
    core.write_memory(ram_offset(F.MAP_SIZE), b"\5\4")
    controller = F.FrameController(core, pytest.fail)
    for _ in range(F.SETTLE_FRAMES):
        controller.tick()
    controller.suspend(for_save=True)
    controller.forget()
    for _ in range(F.RETURN_FRAMES):
        controller.tick()
    assert controller.active


def test_coming_back_on_a_map_still_loading_is_not_a_final_refusal():
    notices = []
    core = ProofCore()
    core.write_memory(ram_offset(F.MAP_SIZE), b"\5\4")
    controller = F.FrameController(core, notices.append)
    for _ in range(F.SETTLE_FRAMES):
        controller.tick()
    core.write_memory(ram_offset(F.MAP_SIZE), b"\0\0")
    core.write_memory(ram_offset(F.GAME_STATE), b"\1")
    controller.tick()
    core.write_memory(ram_offset(F.GAME_STATE), b"\0")
    for _ in range(F.RETURN_FRAMES + 3):
        controller.tick()
    assert not controller.active and not notices
    core.write_memory(ram_offset(F.MAP_SIZE), b"\5\4")
    controller.tick()
    assert controller.active and not notices


def test_controller_saves_native_code_keeps_the_hold_and_returns_at_once():
    core = ProofCore()
    core.write_memory(ram_offset(F.MAP_SIZE), b"\5\4")
    controller = F.FrameController(core, pytest.fail)
    for _ in range(F.SETTLE_FRAMES):
        controller.tick()
    controller.suspend(for_save=True)
    assert not controller.active and core.read_memory(ram_offset(F.GS_SORT_SPRITE), 8) == struct.pack("<2I", *F.GS_SORT_SPRITE_HEAD)
    assert controller.proof.reservation.active
    controller.tick()
    assert controller.active


def test_a_loaded_save_keeps_its_hold_and_old_bytes_do_not_count_as_one():
    core = ProofCore()
    core.write_memory(ram_offset(F.MAP_SIZE), b"\5\4")
    held = F.FixedFramingProof(core, F.DOUBLE_FRAME)
    held.install(); held.uninstall(release=False)
    blocks = [(b.address, b.units) for b in free_blocks(core)]
    again = F.FixedFramingProof(core, F.DOUBLE_FRAME)  # what forget() makes after a load
    again.install()
    assert [(b.address, b.units) for b in free_blocks(core)] == blocks
    again.uninstall(release=True)
    assert core.read_memory(ram_offset(BUFFER - 8), 4) == struct.pack("<I", 0x46524350)  # left behind in free memory
    third = F.FixedFramingProof(core, F.DOUBLE_FRAME)
    third.install()
    assert not any(b.units and b.address < END and b.end > BUFFER - 8 for b in free_blocks(core))


def test_overlay_pieces_are_drawn_across_the_whole_wider_view():
    core = ProofCore()
    core.write_memory(ram_offset(F.MAP_SIZE), b"\5\4")
    F.FixedFramingProof(core, F.DOUBLE_FRAME).install()
    limits = [struct.unpack("<h", core.read_memory(ram_offset(address), 2))[0] for address in F.OVERLAY_LIMITS]
    assert limits == [-360, 680, -300, 540]


def test_controller_stays_normal_on_a_small_map_until_the_map_changes():
    notices = []
    core = ProofCore()
    core.write_memory(ram_offset(F.MAP_SIZE), b"\3\3")
    controller = F.FrameController(core, notices.append)
    for _ in range(F.SETTLE_FRAMES * 3):
        controller.tick()
    assert not controller.active and len(notices) == 1 and "artwork" in notices[0]
    core.write_memory(ram_offset(F.MAP_SIZE), b"\5\4")
    core.write_memory(ram_offset(F.SCRIPT_MAP_CHANGE_STATE), b"\1\0")
    controller.tick()
    core.write_memory(ram_offset(F.SCRIPT_MAP_CHANGE_STATE), b"\0\0")
    for _ in range(F.SETTLE_FRAMES):
        controller.tick()
    assert controller.active


def test_small_map_or_unsupported_core_is_not_patched():
    core = ProofCore()
    core.write_memory(ram_offset(F.MAP_SIZE), b"\1\1")
    with pytest.raises(ValueError, match="artwork"):
        F.FixedFramingProof(core).install()
    core.ram_size = 0x200000
    with pytest.raises(ValueError, match="8 MB"):
        F.FixedFramingProof(core).install()


@pytest.mark.parametrize("frame_scale", [230, F.DOUBLE_FRAME])
def test_compiled_projection_wrapper_matches_independent_affine_geometry(tmp_path, frame_scale):
    """Run the actual C helper/wrapper on the host with hardware calls stubbed, not a Python reimplementation."""
    import ctypes
    from pathlib import Path
    import shutil
    import subprocess
    compiler = shutil.which("cc")
    if not compiler:
        pytest.skip("C compiler unavailable")
    text = Path(F.__file__).with_name("camera_engine.c").read_text()

    def function(signature):
        start = text.index(signature)
        brace, depth = text.index("{", start), 1
        end = brace + 1
        while depth:
            depth += (text[end] == "{") - (text[end] == "}")
            end += 1
        return text[start:end]

    source = """
typedef int s32; typedef signed char s8; typedef short s16;
static int memory[6], h, gx, gy;
static struct { s32 scale; } params;
#define PARAMS (&params)
static struct { s32 geom_x, geom_y; } fixed;
#define FIXED (&fixed)
#define CAMERA_X 0
#define CAMERA_Y 1
#define MAP_WIDTH 2
#define MAP_HEIGHT 3
#define DRAWING_OFFSET_X 4
#define DRAWING_OFFSET_Y 5
#define AT(address, type) memory[address]
void GsSetProjection(s32 distance) { h = distance; }
static void set_geom_fine(s32 x, s32 y) { gx = x; gy = y; }
""" + function("static s32 fixed_anchor(") + "\n" + function("void fixed_set_projection(") + """
void evaluate(int frame_scale, int camera, int offset_x, int offset_y, int *out) {
 params.scale=frame_scale; memory[0]=camera; memory[1]=0; memory[2]=5; memory[3]=6; memory[4]=offset_x; memory[5]=offset_y;
 fixed_set_projection(1511); out[0]=h; out[1]=gx; out[2]=gy;
 out[3]=fixed_anchor(camera,640,320,frame_scale); out[4]=fixed_anchor(0,768,240,frame_scale);
}
"""
    path = tmp_path / "projection.c"
    path.write_text(source)
    library = tmp_path / "projection.so"
    subprocess.run([compiler, "-shared", "-fPIC", str(path), "-o", str(library)], check=True)
    evaluate = ctypes.CDLL(str(library)).evaluate
    result = (ctypes.c_int * 5)()
    scale = frame_scale / 256
    for camera in (0, 100, 320):
        for ox, oy in ((127, 384), (160, 120), (-45, 220)):
            evaluate(frame_scale, camera, ox, oy, result)
            assert result[0] == 1511 * frame_scale // 256  # the RAM value remains native elsewhere
            anchor_x, anchor_y = result[3:]
            assert camera - anchor_x * (1 - scale) / scale >= -0.01
            assert camera + (320 - anchor_x * (1 - scale)) / scale <= 640.01
            for px, py in ((160, 60), (210, 200), (10, 10)):
                actual = (scale * (px - ox) + ox + result[1] / 65536, scale * (py - oy) + oy + result[2] / 65536)
                expected = (scale * px + anchor_x * (1 - scale), scale * py + anchor_y * (1 - scale))
                assert max(abs(a - b) for a, b in zip(actual, expected)) < 0.01  # exact: no pixel steps as the camera moves
