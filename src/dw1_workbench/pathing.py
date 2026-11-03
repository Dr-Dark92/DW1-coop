"""Walkability and routes on decoded maps.

Within a map: the game blocks movement into a tile when its floor-grid value has bit 7 set
(jype0/dw_decomp aaf69a4 src/main/map_collision.c, checkMapCollisionX/Z test `value & 0x80`), so every
other value — walkable floor, scripts, exits, toilets — can be walked on. Routes move between the eight
neighbouring tiles; a diagonal step needs both tiles beside it to be open, because the game checks X and Z
movement separately. Distances are in tiles (a diagonal step counts √2). The game also keeps a character's
radius clear of blocked tiles, so real routes hug walls a little less tightly than these.

Across maps: a spawn point is where a warp lands; from it the Tamer can walk to the map's walk-through exits,
each of which warps to another map's spawn. Exits driven by map scripts have no floor-grid tiles and are not
followed, because the scripts are not decoded.
"""
from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field

from .live import model_tile
from .maps import FLOOR_GRID, MapEntry, MapScene, map_exits

BLOCKED_BIT = 0x80
TOILET = 120
DIAGONAL = math.sqrt(2)
_STEPS = ((1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
          (1, 1, DIAGONAL), (1, -1, DIAGONAL), (-1, 1, DIAGONAL), (-1, -1, DIAGONAL))


def passable(grid: bytes) -> list[bool]:
    return [not value & BLOCKED_BIT for value in grid]


def _open(open_tiles: list[bool], x: int, y: int) -> bool:
    return 0 <= x < FLOOR_GRID and 0 <= y < FLOOR_GRID and open_tiles[x + y * FLOOR_GRID]


def regions(grid: bytes) -> tuple[list[int], list[int]]:
    """Connected walkable areas (orthogonal moves): a region number per tile (-1 blocked) and each region's size."""
    open_tiles = passable(grid)
    label = [-1] * (FLOOR_GRID * FLOOR_GRID)
    sizes = []
    for start in range(FLOOR_GRID * FLOOR_GRID):
        if not open_tiles[start] or label[start] != -1:
            continue
        region, stack, count = len(sizes), [start], 0
        label[start] = region
        while stack:
            index = stack.pop(); count += 1
            x, y = index % FLOOR_GRID, index // FLOOR_GRID
            for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                neighbour = nx + ny * FLOOR_GRID
                if _open(open_tiles, nx, ny) and label[neighbour] == -1:
                    label[neighbour] = region; stack.append(neighbour)
        sizes.append(count)
    return label, sizes


def distances_from(grid: bytes, start: tuple[int, int]) -> tuple[list[float], list[int]]:
    """Dijkstra from one tile: distance to every tile (inf if unreachable) and the previous tile on its route."""
    open_tiles = passable(grid)
    distance = [math.inf] * (FLOOR_GRID * FLOOR_GRID)
    previous = [-1] * (FLOOR_GRID * FLOOR_GRID)
    if not _open(open_tiles, *start):
        return distance, previous
    origin = start[0] + start[1] * FLOOR_GRID
    distance[origin] = 0.0
    queue = [(0.0, origin)]
    while queue:
        cost, index = heapq.heappop(queue)
        if cost > distance[index]:
            continue
        x, y = index % FLOOR_GRID, index // FLOOR_GRID
        for dx, dy, step in _STEPS:
            nx, ny = x + dx, y + dy
            if not _open(open_tiles, nx, ny):
                continue
            if dx and dy and not (_open(open_tiles, x + dx, y) and _open(open_tiles, x, y + dy)):
                continue  # no squeezing between two diagonal walls
            neighbour = nx + ny * FLOOR_GRID
            if cost + step < distance[neighbour] - 1e-9:
                distance[neighbour], previous[neighbour] = cost + step, index
                heapq.heappush(queue, (cost + step, neighbour))
    return distance, previous


def _trace(previous: list[int], end: int) -> list[tuple[int, int]]:
    path = []
    while end != -1:
        path.append((end % FLOOR_GRID, end // FLOOR_GRID)); end = previous[end]
    return path[::-1]


@dataclass(frozen=True)
class Place:
    """A point of interest on a map: a spawn (arrival point), a walk-through exit, or toilet tiles."""
    id: str
    kind: str  # "spawn", "exit" or "toilet"
    label: str
    tiles: tuple[tuple[int, int], ...]
    target: tuple[int, int] | None = None  # exits: (map index, spawn index) the warp leads to
    aliases: tuple[int, ...] = ()  # spawns: other spawn slots at the same position (often unused defaults)
    blocked: bool = False  # spawns: on a blocked tile, so nothing can be walked from it (likely unused)

    @property
    def spawn_indices(self) -> tuple[int, ...]:
        return (int(self.id.split()[1]), *self.aliases) if self.kind == "spawn" else ()


@dataclass(frozen=True)
class Route:
    start: str  # Place ids
    end: str
    distance: float | None  # tiles; None when unreachable
    path: tuple[tuple[int, int], ...] = ()


@dataclass(frozen=True)
class MapPathing:
    map_index: int
    places: tuple[Place, ...]
    routes: tuple[Route, ...]  # from every spawn to every exit and toilet
    region_sizes: tuple[int, ...]
    place_regions: dict[str, tuple[int, ...]] = field(default_factory=dict)

    def route(self, start: str, end: str) -> Route | None:
        return next((route for route in self.routes if route.start == start and route.end == end), None)

    def spawn_place(self, spawn_index: int) -> Place | None:
        return next((place for place in self.places if spawn_index in place.spawn_indices), None)


def map_places(entry: MapEntry, scene: MapScene, map_names: dict[int, str] | None = None) -> tuple[Place, ...]:
    names = map_names or {}
    open_tiles = passable(scene.floor_grid)
    places, by_position = [], {}
    for index, spawn in enumerate(scene.spawns):
        key = (spawn.position[0], spawn.position[2])
        if key in by_position:
            by_position[key].append(index)
            continue
        by_position[key] = [index]
    for indices in by_position.values():
        spawn = scene.spawns[indices[0]]
        tile = model_tile(spawn.position[0], spawn.position[2])
        label = f"spawn {indices[0]}" + (f" (also {', '.join(map(str, indices[1:]))})" if len(indices) > 1 else "")
        places.append(Place(f"spawn {indices[0]}", "spawn", label, (tile,), aliases=tuple(indices[1:]),
                            blocked=not _open(open_tiles, *tile)))
    cells: dict[int, list[tuple[int, int]]] = {}
    for index, value in enumerate(scene.floor_grid):
        if 110 <= value < 120:
            cells.setdefault(value - 110, []).append((index % FLOOR_GRID, index // FLOOR_GRID))
        elif value == TOILET:
            cells.setdefault(-1, []).append((index % FLOOR_GRID, index // FLOOR_GRID))
    for warp_exit in map_exits(scene):
        if warp_exit.walk_through:
            target = warp_exit.warp.map_index
            places.append(Place(f"exit {warp_exit.slot}", "exit",
                                f"exit {warp_exit.slot} → {names.get(target, f'map {target}')}"
                                + (f" ({warp_exit.direction})" if warp_exit.direction else ""),
                                tuple(cells.get(warp_exit.slot, ())), (target, warp_exit.warp.spawn_index)))
    if cells.get(-1):
        places.append(Place("toilet", "toilet", "toilet", tuple(cells[-1])))
    return tuple(places)


def map_pathing(entry: MapEntry, scene: MapScene, map_names: dict[int, str] | None = None,
                with_paths: bool = True) -> MapPathing:
    places = map_places(entry, scene, map_names)
    label, sizes = regions(scene.floor_grid)
    place_regions = {place.id: tuple(sorted({label[x + y * FLOOR_GRID] for x, y in place.tiles
                                             if label[x + y * FLOOR_GRID] >= 0})) for place in places}
    routes = []
    for start in (place for place in places if place.kind == "spawn" and not place.blocked):
        distance, previous = distances_from(scene.floor_grid, start.tiles[0])
        for end in (place for place in places if place.kind != "spawn"):
            best = min(((distance[x + y * FLOOR_GRID], x + y * FLOOR_GRID) for x, y in end.tiles), default=(math.inf, -1))
            if math.isinf(best[0]):
                routes.append(Route(start.id, end.id, None))
            else:
                routes.append(Route(start.id, end.id, round(best[0], 2),
                                    tuple(_trace(previous, best[1])) if with_paths else ()))
    return MapPathing(entry.index, places, tuple(routes), tuple(sizes), place_regions)


@dataclass(frozen=True)
class Leg:
    map_index: int
    start: str  # place id where this map is entered ("spawn n")
    end: str  # place id it is left by ("exit n"), or the destination spawn on the last map
    distance: float


def routes_from(pathing: dict[int, MapPathing], start_map: int,
                start_spawn: int | None = None) -> dict[int, tuple[float, list[Leg]]]:
    """Fewest tiles walked from start_map (from start_spawn, or whichever spawn is nearest) to every reachable map,
    over walk-through exits. Only maps in `pathing` are used, so a zone or bundle plans routes inside itself."""
    if start_map not in pathing:
        return {}
    queue: list[tuple[float, int, int, str]] = []
    best: dict[tuple[int, str], float] = {}
    came_from: dict[tuple[int, str], tuple[tuple[int, str], Leg] | None] = {}
    order = 0
    for place in pathing[start_map].places:
        if place.kind == "spawn" and not place.blocked and (start_spawn is None or start_spawn in place.spawn_indices):
            node = (start_map, place.id)
            best[node], came_from[node] = 0.0, None
            heapq.heappush(queue, (0.0, order, start_map, place.id)); order += 1
    reached: dict[int, tuple[float, list[Leg]]] = {}
    while queue:
        cost, _, map_index, spawn = heapq.heappop(queue)
        node = (map_index, spawn)
        if cost > best.get(node, math.inf):
            continue
        if map_index not in reached:
            legs, step = [], came_from[node]
            while step is not None:
                legs.append(step[1]); step = came_from[step[0]]
            reached[map_index] = (round(cost, 2), legs[::-1])
        places = {place.id: place for place in pathing[map_index].places}
        for route in pathing[map_index].routes:
            if route.start != spawn or route.distance is None or places[route.end].kind != "exit":
                continue
            target_map, target_spawn = places[route.end].target
            arrival = pathing[target_map].spawn_place(target_spawn) if target_map in pathing else None
            if arrival is None or arrival.blocked:
                continue
            following = (target_map, arrival.id)
            total = cost + route.distance
            if total < best.get(following, math.inf) - 1e-9:
                best[following] = total
                came_from[following] = (node, Leg(map_index, spawn, route.end, route.distance))
                heapq.heappush(queue, (total, order, target_map, following[1])); order += 1
    return reached


def plan_route(pathing: dict[int, MapPathing], start_map: int, end_map: int,
               start_spawn: int | None = None) -> list[Leg] | None:
    """The legs of the shortest walk-through route between two maps, or None if there is none."""
    reached = routes_from(pathing, start_map, start_spawn)
    return reached[end_map][1] if end_map in reached else None
