from __future__ import annotations

import math

from dw1_workbench.maps import parse_map, parse_map_table
from dw1_workbench.pathing import distances_from, map_pathing, passable, plan_route, regions, routes_from
from test_maps import make_executable, make_map


def grid(blocked=(), values=None):
    cells = bytearray(100 * 100)
    for x, y in blocked:
        cells[x + y * 100] = 0x80
    for (x, y), value in (values or {}).items():
        cells[x + y * 100] = value
    return bytes(cells)


def test_bit_seven_blocks_and_other_triggers_are_walkable():
    cells = grid(values={(0, 0): 0x80, (1, 0): 0xFF, (2, 0): 110, (3, 0): 120, (4, 0): 83})
    assert passable(cells)[:5] == [False, False, True, True, True]


def test_regions_split_by_walls():
    wall = [(50, y) for y in range(100)]
    label, sizes = regions(grid(wall))
    assert sorted(sizes) == [4900, 5000] and label[50] == -1 and label[0] != label[99]


def test_diagonal_needs_both_sides_open_and_costs_root_two():
    distance, _ = distances_from(grid(), (10, 10))
    assert math.isclose(distance[11 + 11 * 100], math.sqrt(2))
    # two diagonal walls touching at a corner: no squeezing through, so the way round is longer
    pinched = grid([(11, 10), (10, 11)])
    distance, _ = distances_from(pinched, (10, 10))
    assert distance[11 + 11 * 100] > math.sqrt(2)


def two_maps():
    entries = parse_map_table(make_executable())
    entries[1] = type(entries[1])(**{**entries[1].__dict__, "image_count": 1})  # make_map writes one 8-bpp image
    exit_a = {(60, y): 110 for y in range(45, 52)}  # map 0 exit slot 0 -> map 1 spawn 0
    exit_b = {(40, y): 110 for y in range(45, 52)}  # map 1 exit slot 0 -> map 0 spawn 0
    wall = [(30, y) for y in range(100)]
    first = parse_map(make_map(floor=grid(wall, exit_a), warps=[(1, 0)] + [(0xFFFF, 0)] * 9), entries[0])
    second = parse_map(make_map(floor=grid((), exit_b), warps=[(0, 0)] + [(0xFFFF, 0)] * 9), entries[1])
    return entries, first, second


def test_map_pathing_routes_spawns_to_exits():
    entries, first, _ = two_maps()
    pathing = map_pathing(entries[0], first, {1: "Test Town · TEST02"})
    spawns = [place for place in pathing.places if place.kind == "spawn"]
    assert len(spawns) == 10 and spawns[0].tiles == ((50, 49),)  # make_map's spawns all sit near the centre
    exit_place = next(place for place in pathing.places if place.kind == "exit")
    assert exit_place.label == "exit 0 → Test Town · TEST02 (east)" and exit_place.target == (1, 0)
    route = pathing.route("spawn 0", "exit 0")
    assert route.distance == 10.0 and route.path[0] == (50, 49) and route.path[-1][0] == 60
    assert sorted(pathing.region_sizes) == [3000, 6900]


def test_plan_route_crosses_maps_and_stays_inside_the_package():
    entries, first, second = two_maps()
    pathing = {0: map_pathing(entries[0], first), 1: map_pathing(entries[1], second)}
    legs = plan_route(pathing, 0, 1)
    assert [(leg.map_index, leg.start, leg.end, leg.distance) for leg in legs] == [(0, "spawn 0", "exit 0", 10.0)]
    assert routes_from(pathing, 1)[0][0] == 10.0
    assert plan_route({0: pathing[0]}, 0, 1) is None  # map 1 is outside this "package"
