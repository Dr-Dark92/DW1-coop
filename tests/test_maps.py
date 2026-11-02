import struct

import pytest

from dw1_workbench.errors import DiscFormatError, ValidationError
from dw1_workbench.maps import (AREA_NAME_POINTERS, DOOR_TABLE_OFFSET, EMPTY_TILE, EXIT_COLOURS, MAP_TABLE_OFFSET,
                                TOILET_TABLE_OFFSET, TRIGGER_COLOURS, USA_LOAD_ADDRESS, export_background,
                                floor_grid_image, grid_direction, layout_zones, map_exits, map_links,
                                parse_map, parse_map_table, parse_tfs, render_background, tfs_layout,
                                trigger_kind, zone_links)

AREA_STRINGS = 0x9A400  # after the map table, which ends at 0x992d4 + 255 * 16 = 0x9a2c4


def make_executable(entries=(("TEST01", 1, 0, 0x85, 1, 1, 0), ("TEST02", 0, 0, 0x40, 0, 0, 1))):
    data = bytearray(0xA0000)
    data[:8] = b"PS-X EXE"
    for index, (name, images, objects, flags, door, toilet, area) in enumerate(entries):
        struct.pack_into("10s6B", data, MAP_TABLE_OFFSET + index * 16, name.encode(), images, objects,
                         flags, door, toilet, area)
    for area, text in enumerate((b"     Test Woods     ", b"  Test Town  ")):
        address = AREA_STRINGS + area * 32
        data[address:address + len(text)] = text
        struct.pack_into("<I", data, AREA_NAME_POINTERS + area * 4, USA_LOAD_ADDRESS + address)
    struct.pack_into("<4h", data, TOILET_TABLE_OFFSET, 1, 2, 3, 4)
    door = DOOR_TABLE_OFFSET
    data[door:door + 6] = bytes((7, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF))
    for part, value in enumerate((10, 20, 30, 1024)):
        struct.pack_into("<6h", data, door + 6 + part * 12, value, 0, 0, 0, 0, 0)
    return bytes(data)


def make_tfs(tiles=2, palettes=1, extra=0):
    data = bytearray(struct.pack("<HHI", 9, 9, palettes))
    for _ in range(palettes):
        colours = [0] * 256
        colours[1], colours[2] = 0x001F, 0x8000 | (0x1F << 5)  # red; green with STP set
        data += struct.pack("<256H", *colours)
    for tile in range(tiles):
        data += struct.pack("<HH", tile * 64, 0) + bytes([tile + 1]) * (128 * 128)
    return bytes(data + bytes(extra))


def make_map(*, tiles=(0, 1), width=2, height=1, digimon=1, waypoints=2, tim=b"\x10\x00\x00\x00", warps=None,
             floor=None):
    header_size = 4 + 4 + 4 + 4 + 4  # setup, one 8-bpp image, objects, elements, floor
    tim_offset = header_size
    setup_offset = tim_offset + 8
    setup = struct.pack("<3i3i", 1, 2, 3, 4, 5, 6)
    setup += b"".join(struct.pack("<3i3I", i, i, i, 255, 128, 0) for i in range(3))
    setup += struct.pack("<3II4i4i2I", 0, 0, 0, 1813, -1, -1, -1, -1, 0, 0, 0, 0, width, height)
    setup += struct.pack(f"<{len(tiles)}I", *tiles)
    object_offset = setup_offset + len(setup)
    objects = struct.pack("<H", 1) + struct.pack("<4H3h2H", 8, 16, 32, 24, -5, 0, 7, 3, 4)
    objects += struct.pack("<H", 1) + struct.pack("<8h8h3H", *range(8), *range(10, 18), 100, 200, 1)
    elements_offset = object_offset + len(objects)
    spawns = b"".join(struct.pack("<10h", *[(axis + 1) * 10 + slot for slot in range(10)]) for axis in range(4))
    warps = warps or [(1, 0)] + [(0xFFFF, 0)] * 9
    elements = spawns + struct.pack("<10H", *(w[0] for w in warps)) + struct.pack("<10H", *(w[1] for w in warps))
    elements += struct.pack("<H", digimon)
    for _ in range(digimon):
        elements += struct.pack("<2H3h3h2H2B8H3H4H4H3hH8h", 3, 1, 10, 0, 20, 0, 1024, 0, 300, 0, 5, 0,
                                *range(1, 9), 100, 2, 0, 46, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, waypoints, *range(8))
        elements += b"".join(struct.pack("<3h", i, 0, -i) for i in range(waypoints))
    floor_offset = elements_offset + len(elements)
    floor = floor or bytes([0, 128]) * 5000
    header = struct.pack("<6I", setup_offset, tim_offset, object_offset, elements_offset, floor_offset, 0)[:header_size]
    return header + tim + bytes(4) + setup + objects + elements + floor


def test_map_table_fields_paths_and_links():
    first, second = parse_map_table(make_executable())[:2]
    assert (first.name, first.map_path, first.tfs_path) == ("TEST01", "MAP/MAP1/TEST01.MAP", "MAP/MAP1/TEST01.TFS")
    assert (first.sound_id, first.has_digimon, first.has_time_cycle) == (5, True, True)
    assert (first.area_id, first.area_name) == (0, "Test Woods")
    assert first.toilet == (1, 2, 3, 4)
    assert [(d.model_id, d.position, d.rotation) for d in first.doors] == [(7, (10, 20, 30), 1024)]
    assert (second.area_name, second.has_time_cycle, second.has_digimon, second.doors) == ("Test Town", False, False, ())


def test_map_table_allows_names_shared_across_directories_only():
    # entry 15 lives in MAP2, so it may reuse entry 0's name (USA YAKA25 does this)
    shared = [("TEST01", 0, 0, 0, 0, 0, 0)] * 1 + [("", 0, 0, 0, 0, 0, 0)] * 14 + [("TEST01", 0, 0, 0, 0, 0, 0)]
    entries = parse_map_table(make_executable(shared))
    assert entries[15].map_path == "MAP/MAP2/TEST01.MAP"
    with pytest.raises(DiscFormatError, match="duplicate path"):
        parse_map_table(make_executable([("TEST01", 0, 0, 0, 0, 0, 0)] * 2))


@pytest.mark.parametrize(("entry", "message"), [
    (("TEST01", 0, 0, 0, 0, 0, 70), "area-name index"),
    (("TEST01", 0, 0, 0, 0, 11, 0), "toilet index"),
    (("TEST-1", 0, 0, 0, 0, 0, 0), "invalid name"),
])
def test_map_table_rejects_out_of_range_links(entry, message):
    with pytest.raises(DiscFormatError, match=message):
        parse_map_table(make_executable([entry]))


def test_background_assembles_tiles_in_grid_order_with_opaque_stp():
    entry = parse_map_table(make_executable())[0]
    scene = parse_map(make_map(), entry)
    image = render_background(parse_tfs(make_tfs()), scene.setup)
    assert image.size == (256, 128)
    assert image.getpixel((0, 0)) == (255, 0, 0, 255)
    assert image.getpixel((200, 64)) == (0, 255, 0, 255)  # STP does not make backgrounds translucent


def test_background_leaves_empty_tiles_transparent():
    entry = parse_map_table(make_executable())[0]
    scene = parse_map(make_map(tiles=(EMPTY_TILE, 0), digimon=1), entry)
    image = render_background(parse_tfs(make_tfs(tiles=1)), scene.setup)
    assert image.getpixel((10, 10))[3] == 0 and image.getpixel((200, 10)) == (255, 0, 0, 255)


def test_background_rejects_out_of_sequence_tiles():
    entry = parse_map_table(make_executable())[0]
    scene = parse_map(make_map(tiles=(1, 0)), entry)
    with pytest.raises(DiscFormatError, match="in sequence"):
        render_background(parse_tfs(make_tfs()), scene.setup)


def test_tfs_rejects_partial_tiles_and_bad_palette_counts():
    with pytest.raises(DiscFormatError, match="whole number"):
        parse_tfs(make_tfs(extra=3))
    with pytest.raises(DiscFormatError, match="palette count"):
        tfs_layout(struct.pack("<HHI", 0, 0, 0), 8)
    assert tfs_layout(make_tfs()[:8], len(make_tfs(tiles=2, palettes=1))) == (1, 2)


def test_scene_spawns_warps_objects_digimon_and_floor_grid():
    entry = parse_map_table(make_executable())[0]
    scene = parse_map(make_map(), entry)
    assert scene.setup.camera_origin == (1, 2, 3) and scene.setup.viewer_distance == 1813
    assert scene.spawns[2].position == (12, 22, 32) and scene.spawns[2].rotation == 42
    assert [(slot, warp.map_index) for slot, warp in scene.used_warps] == [(0, 1)]
    assert scene.objects[0].uv == (8, 16) and scene.instances[0].frames[1] == (1, 11)
    digimon = scene.digimon[0]
    assert (digimon.character_id, digimon.position, digimon.script_id) == (3, (10, 0, 20), 5)
    assert digimon.stats["brains"] == 8 and digimon.moves[0] == (46, 1) and len(digimon.waypoints) == 2
    assert scene.floor_grid[:2] == bytes([0, 128]) and len(scene.floor_grid) == 10000


def test_scene_ignores_placements_when_table_says_no_digimon():
    entry = parse_map_table(make_executable())[1]
    entry = type(entry)(**{**entry.__dict__, "image_count": 1})
    scene = parse_map(make_map(), entry)
    assert scene.digimon_count == 1 and scene.digimon == ()


@pytest.mark.parametrize(("kwargs", "message"), [
    ({"tim": b"\x11\x00\x00\x00"}, "not a TIM"),
    ({"waypoints": 65}, "waypoint count"),
    ({"tiles": (0,), "width": 17}, "grid"),
])
def test_scene_rejects_malformed_data(kwargs, message):
    entry = parse_map_table(make_executable())[0]
    with pytest.raises(DiscFormatError, match=message):
        parse_map(make_map(**kwargs), entry)


def test_scene_rejects_truncated_files():
    entry = parse_map_table(make_executable())[0]
    with pytest.raises(DiscFormatError, match="outside its bounded section"):
        parse_map(make_map()[:-100], entry)


def test_background_export_never_overwrites(tmp_path):
    entry = parse_map_table(make_executable())[0]
    image = render_background(parse_tfs(make_tfs()), parse_map(make_map(), entry).setup)
    target = tmp_path / "bg.png"
    export_background(image, target, {"map": "TEST01"})
    assert target.exists() and target.with_suffix(".png.json").exists()
    with pytest.raises(ValidationError):
        export_background(image, target, {"map": "TEST01"})


def floor_with_exit(slot, rows, value_fill=0):
    grid = bytearray([value_fill]) * 10000
    for row in rows:
        for column in range(45, 55):
            grid[row * 100 + column] = 110 + slot
    return bytes(grid)


@pytest.mark.parametrize(("value", "kind"), [
    (0, "walkable"), (128, "blocked"), (51, "step script"), (79, "step script"), (80, "interact script"),
    (109, "interact script"), (110, "exit"), (119, "exit"), (120, "toilet"), (30, "unknown"),
])
def test_trigger_kinds_follow_the_game_tile_rules(value, kind):
    assert trigger_kind(value) == kind


def test_grid_direction_treats_row_zero_as_north():
    assert grid_direction(50, 2) == "north" and grid_direction(50, 97) == "south"
    assert grid_direction(97, 50) == "east" and grid_direction(2, 50) == "west"
    assert grid_direction(90, 90) == "south-east" and grid_direction(50, 50) == "centre"


def test_map_exits_mark_walk_through_and_table_only_warps():
    entry = parse_map_table(make_executable())[0]
    warps = [(1, 0), (0, 2)] + [(0xFFFF, 0)] * 8
    scene = parse_map(make_map(warps=warps, floor=floor_with_exit(0, (96, 97))), entry)
    south, scripted = map_exits(scene)
    assert (south.slot, south.walk_through, south.cell_count, south.direction) == (0, True, 20, "south")
    assert (scripted.slot, scripted.walk_through, scripted.direction) == (1, False, None)


def test_zone_links_merge_both_directions_and_flip_compass():
    entries = parse_map_table(make_executable())
    woods = parse_map(make_map(warps=[(1, 0)] + [(0xFFFF, 0)] * 9, floor=floor_with_exit(0, (2, 3))), entries[0])
    town_entry = type(entries[1])(**{**entries[1].__dict__, "image_count": 1})
    town = parse_map(make_map(warps=[(0, 0)] + [(0xFFFF, 0)] * 9), town_entry)
    links = map_links(entries, {0: woods, 1: town})
    (zone,) = zone_links(entries, links)
    assert (zone.source, zone.target, zone.walk_through, zone.scripted) == ("Test Town", "Test Woods", 1, 1)
    # Woods exits north to Town, so from Town (the sorted first zone) Woods lies south.
    assert zone.directions == {"south": 1}


def test_layout_is_deterministic_and_follows_exit_direction():
    entries = parse_map_table(make_executable())
    woods = parse_map(make_map(warps=[(1, 0)] + [(0xFFFF, 0)] * 9, floor=floor_with_exit(0, (96, 97))), entries[0])
    links = zone_links(entries, map_links(entries, {0: woods}))
    first = layout_zones(["Test Town", "Test Woods"], links)
    assert first == layout_zones(["Test Town", "Test Woods"], links)
    # Woods' exit to Town is on its south edge, so Town is placed below Woods (screen y grows downward).
    assert first["Test Town"][1] > first["Test Woods"][1]


def test_floor_grid_image_colours_triggers_and_exit_slots():
    entry = parse_map_table(make_executable())[0]
    scene = parse_map(make_map(floor=floor_with_exit(3, (50,), value_fill=128)), entry)
    image = floor_grid_image(scene)
    assert image.getpixel((50, 50)) == EXIT_COLOURS[3] and image.getpixel((0, 0)) == TRIGGER_COLOURS["blocked"]
