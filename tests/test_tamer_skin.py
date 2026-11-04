import json
import struct
from types import SimpleNamespace

import pytest

from dw1_workbench import tamer_skin as T
from dw1_workbench.alltim import SLOT_SIZE
from dw1_workbench.camera import GPURAM_FIELD
from dw1_workbench.live import ram_offset


def model_file(fill=1, animation=b"ANIM" * 4):
    """A tiny MMD: 1 object, 2 vertices, 1 normal, 1 triangle."""
    vertices = struct.pack("<8h", fill, 2, 3, 0, 4, 5, 6, 0)
    normals = struct.pack("<4h", 0, 4096, 0, 0)
    triangle = struct.pack("<4B", 9, 6, 0, 0x34) + struct.pack("<2BH2BH2BH", 1, 2, 0x7A00, 3, 4, 5, 5, 6, 0) + struct.pack("<6H", 0, 0, 0, 1, 0, 0)
    table = struct.pack("<6Ii", 28, 2, 28 + 16, 1, 28 + 24, 1, 0)
    tmd = struct.pack("<3I", 0x41, 0, 1) + table + vertices + normals + triangle
    return struct.pack("<2I", 8, 8 + len(tmd)) + tmd + animation


def texture_file(colour=7, pixel=9):
    tim = bytearray(SLOT_SIZE)
    struct.pack_into("<2I", tim, 0, 0x10, 8)
    struct.pack_into("<I4H", tim, 8, 12 + 32 * 2, 0, 488, 16, 2)
    tim[20:20 + 64] = struct.pack("<H", colour) * 32
    at = 8 + 12 + 64
    struct.pack_into("<I4H", tim, at, 12 + 4 * 2 * 2, 320, 0, 4, 2)
    tim[at + 12:at + 12 + 16] = struct.pack("<H", pixel) * 8
    return bytes(tim)


class Core:
    def __init__(self):
        self.ram = bytearray(0x200000)
        self.vram = bytearray(1024 * 512 * 2)
        self.loads = 0

    def read_memory(self, offset, size):
        return bytes(self.ram[offset:offset + size])

    def write_memory(self, offset, data):
        self.ram[offset:offset + len(data)] = data

    def serialize(self):
        return b"x" * 16 + GPURAM_FIELD + struct.pack("<I", len(self.vram)) + bytes(self.vram) + b"y"

    def unserialize(self, state):
        start = state.find(GPURAM_FIELD) + len(GPURAM_FIELD) + 4
        self.vram[:] = state[start:start + len(self.vram)]
        self.loads += 1


BASE = 0x801A0000


def loaded_core(model):
    core = Core()
    core.write_memory(ram_offset(BASE), T.as_loaded(model, BASE, 1)[0])
    core.write_memory(ram_offset(T.TAMER_ENTITY + T.ANIMATION_POINTER), struct.pack("<I", BASE + struct.unpack_from("<I", model, 4)[0]))
    return core


def test_as_loaded_matches_what_the_game_does():
    model = model_file()
    loaded = T.as_loaded(model, BASE, 1)[0]
    assert struct.unpack_from("<I", loaded, 12)[0] == 1
    assert struct.unpack_from("<I", loaded, T.OBJECT_TABLE)[0] == BASE + T.OBJECT_TABLE + 28
    packet = T.OBJECT_TABLE + 28 + 24
    assert struct.unpack_from("<I", loaded, packet + 8)[0] >> 16 & 0x1F == T.TAMER_PIXEL_PAGE
    assert struct.unpack_from("<H", loaded, packet)[0] == 1  # the run's length, which the renderer walks by
    assert loaded[-16:] == model[-16:] and len(loaded) == len(model)


def test_runs_of_one_kind_of_polygon_get_their_length():
    triangle = struct.pack("<4B", 9, 6, 0, 0x34) + bytes(24)
    quad = struct.pack("<4B", 12, 8, 0, 0x3C) + bytes(32)
    packets = quad * 2 + triangle * 3
    table = struct.pack("<6Ii", 28, 0, 28, 0, 28, 5, 0)
    model = struct.pack("<2I", 8, 40 + len(packets)) + struct.pack("<3I", 0x41, 0, 1) + table + packets
    loaded = T.as_loaded(model, BASE, 1)[0]
    at = T.OBJECT_TABLE + 28
    assert struct.unpack_from("<H", loaded, at)[0] == 2 and loaded[at + 36:at + 38] == bytes((12, 8))
    assert struct.unpack_from("<H", loaded, at + 72)[0] == 3 and loaded[at + 100:at + 102] == bytes((9, 6))


def model_with_detail():
    """One object in the file's own room, its polygons in the detail, as if appended."""
    triangle = struct.pack("<4B", 9, 6, 0, 0x34) + bytes(24)
    vertices = struct.pack("<4h", 7, 8, 9, 0)
    table_end = 28
    head = struct.pack("<3I", 0x41, 0, 1)
    size = 8 + 12 + table_end + len(vertices) + 16
    table = struct.pack("<6Ii", table_end, 1, table_end, 0, size - T.OBJECT_TABLE, 2, 0)
    return struct.pack("<2I", 8, size - 16) + head + table + vertices + bytes(16), triangle * 2


def test_detail_is_mapped_where_it_goes():
    model, detail = model_with_detail()
    loaded, placed = T.as_loaded(model, BASE, 1, detail=detail)
    assert len(loaded) == len(model) and len(placed) == len(detail)
    vertices, _, _, _, primitives = struct.unpack_from("<5I", loaded, T.OBJECT_TABLE)
    assert vertices == BASE + T.OBJECT_TABLE + 28 and primitives == T.DETAIL_ADDRESS
    assert struct.unpack_from("<H", placed)[0] == 2 and struct.unpack_from("<I", placed, 8)[0] >> 16 & 0x1F == T.TAMER_PIXEL_PAGE


def test_detail_goes_to_ram_and_is_noticed_when_lost():
    model, detail = model_with_detail()
    core = loaded_core(model_file(1))
    # The original's animations sit where this file says, so the same base is found.
    core.write_memory(ram_offset(T.TAMER_ENTITY + T.ANIMATION_POINTER), struct.pack("<I", BASE + struct.unpack_from("<I", model, 4)[0]))
    core.write_memory(ram_offset(BASE + 4), model[4:8]); core.write_memory(ram_offset(BASE + 16), model[16:20])
    skin = T.TamerSkin(core, model, texture_file(), detail)
    assert skin.apply() is True
    assert core.read_memory(ram_offset(T.DETAIL_ADDRESS), len(detail)) == T.as_loaded(model, BASE, 1, detail=detail)[1]
    assert skin.apply() is False


def test_detail_past_its_room_is_refused():
    model, _ = model_with_detail()
    with pytest.raises(ValueError):
        T.TamerSkin(Core(), model, texture_file(), bytes(T.DETAIL_CAPACITY + 1))


def test_swap_writes_model_and_texture_once():
    original, wanted = model_file(1), model_file(9)
    core = loaded_core(original)
    skin = T.TamerSkin(core, wanted, texture_file())
    assert skin.apply()
    assert core.read_memory(ram_offset(BASE + T.OBJECT_TABLE + 28), 2) == struct.pack("<h", 9)
    assert core.loads == 1
    assert struct.unpack_from("<H", core.vram, (256 * 1024 + 320) * 2)[0] == 9  # the Tamer's texture lives at y 256
    assert struct.unpack_from("<H", core.vram, 488 * 1024 * 2)[0] == 7
    assert not skin.apply() and core.loads == 1  # already this character
    core.write_memory(ram_offset(BASE + T.OBJECT_TABLE + 28 + 24), bytes([0x23, 0]))  # the game writes to packet headers
    assert not skin.apply() and core.loads == 1


def test_game_reloading_its_tamer_is_swapped_again():
    original, wanted = model_file(1), model_file(9)
    core = loaded_core(original)
    skin = T.TamerSkin(core, wanted, texture_file())
    skin.apply()
    core.write_memory(ram_offset(BASE), T.as_loaded(original, BASE, 1)[0])
    skin._frames = 0
    skin.tick()
    assert core.read_memory(ram_offset(BASE + T.OBJECT_TABLE + 28), 2) == struct.pack("<h", 9) and core.loads == 2


def test_nothing_is_written_until_the_tamer_is_there():
    core = Core()
    skin = T.TamerSkin(core, model_file(9), texture_file())
    assert not skin.apply() and core.loads == 0  # no pointer yet (title screen)
    core.write_memory(ram_offset(T.TAMER_ENTITY + T.ANIMATION_POINTER), struct.pack("<I", BASE + 100))
    assert not skin.apply() and core.loads == 0  # somewhere that isn't this file
    assert not any(core.ram[ram_offset(BASE):ram_offset(BASE) + 200])


def test_bad_files_are_refused():
    with pytest.raises(ValueError):
        T.TamerSkin(Core(), b"\0" * 64, texture_file())
    with pytest.raises(ValueError):
        T.TamerSkin(Core(), model_file(), b"\0" * 64)
    assert T.patch_vram(b"no field here", texture_file()) is None


def test_characters_are_folders_with_all_their_files(tmp_path):
    good = tmp_path / "tai"; good.mkdir()
    (good / "skin.json").write_text(json.dumps({"name": "Tai"}))
    (good / "model.mmd").write_bytes(b"m"); (good / "texture.tim").write_bytes(b"t")
    partial = tmp_path / "partial"; partial.mkdir()
    (partial / "skin.json").write_text(json.dumps({"name": "No files"}))
    broken = tmp_path / "broken"; broken.mkdir()
    (broken / "skin.json").write_text("not json")
    (tmp_path / "file.txt").write_text("x")
    found = T.load_characters(tmp_path)
    assert [c.name for c in found] == ["Tai"] and found[0].data() == (b"m", b"t", b"")
    (good / "detail.bin").write_bytes(b"d")
    assert found[0].data() == (b"m", b"t", b"d")
    assert T.load_characters(tmp_path / "missing") == [] and T.load_characters(None) == []
