from __future__ import annotations

import hashlib
import json
import math
import struct
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from .errors import DiscFormatError, ValidationError

# USA SLUS_010.32 file offsets (virtual address - 0x80090000), matching the
# DW1ModelConverter 17d1a19 SLUS_DATA addresses 0x801292d4/0x801291bc/0x80122e10/0x80122e60.
MAP_TABLE_OFFSET = 0x992D4
MAP_ENTRY_SIZE = 16
MAP_COUNT = 255
AREA_NAME_POINTERS = 0x991BC
AREA_NAME_COUNT = 70
AREA_NAME_SIZE = 28
TOILET_TABLE_OFFSET = 0x92E10
TOILET_COUNT = 10
DOOR_TABLE_OFFSET = 0x92E60
DOOR_RECORD_SIZE = 54
USA_LOAD_ADDRESS = 0x80090000

TABLE_DECODER_VERSION = "slus-map-table-v1"
TFS_DECODER_VERSION = "tfs-v1"
MAP_DECODER_VERSION = "map-scene-v1"

TFS_TILE = 128
TFS_TILE_RECORD = 4 + TFS_TILE * TFS_TILE
TFS_PALETTE_SIZE = 512
MAX_PALETTES = 16
MAX_MAP_FILE = 4 * 1024 * 1024
MAX_TFS_FILE = 8 + MAX_PALETTES * TFS_PALETTE_SIZE + 16 * 16 * TFS_TILE_RECORD  # 16x16 grid limit
MAX_GRID = 16
EMPTY_TILE = 0xFFFFFFFF
FLOOR_GRID = 100
MAX_OBJECTS = 1024
MAX_DIGIMON = 64
MAX_WAYPOINTS = 64
SLOTS = 10
NO_WARP = MAP_COUNT


@dataclass(frozen=True)
class DoorPlacement:
    model_id: int
    position: tuple[int, int, int]
    rotation: int


@dataclass(frozen=True)
class MapEntry:
    index: int
    name: str
    image_count: int
    object_count: int
    sound_id: int
    has_time_cycle: bool
    has_digimon: bool
    door_id: int
    toilet_id: int
    area_id: int
    area_name: str
    area_name_offset: int
    toilet: tuple[int, int, int, int] | None
    doors: tuple[DoorPlacement, ...]
    raw: bytes

    @property
    def offset(self) -> int:
        return MAP_TABLE_OFFSET + self.index * MAP_ENTRY_SIZE

    @property
    def directory(self) -> str:
        # Upstream exporter: entry i lives in MAP{1 + i // 15}.
        return f"MAP/MAP{1 + self.index // 15}"

    @property
    def map_path(self) -> str:
        return f"{self.directory}/{self.name}.MAP"

    @property
    def tfs_path(self) -> str:
        return f"{self.directory}/{self.name}.TFS"


@dataclass(frozen=True)
class TfsFile:
    header_width: int
    header_height: int
    palettes: tuple[tuple[int, ...], ...]
    tile_offsets: tuple[int, ...]
    data: bytes


@dataclass(frozen=True)
class Light:
    position: tuple[int, int, int]
    color: tuple[int, int, int]


@dataclass(frozen=True)
class MapSetup:
    camera_origin: tuple[int, int, int]
    camera_target: tuple[int, int, int]
    lights: tuple[Light, Light, Light]
    unused_ambient: tuple[int, int, int]
    viewer_distance: int
    liked_area: tuple[int, int, int, int]
    disliked_area: tuple[int, int, int, int]
    grid_width: int
    grid_height: int
    tiles: tuple[int, ...]


@dataclass(frozen=True)
class SpriteObject:
    uv: tuple[int, int]
    size: tuple[int, int]
    position: tuple[int, int, int]
    clut: int
    transparency: int


@dataclass(frozen=True)
class SpriteInstance:
    frames: tuple[tuple[int, int], ...]
    position: tuple[int, int]
    flag: int


@dataclass(frozen=True)
class Spawn:
    position: tuple[int, int, int]
    rotation: int


@dataclass(frozen=True)
class Warp:
    map_index: int
    spawn_index: int


@dataclass(frozen=True)
class MapDigimon:
    character_id: int
    ai_type: int
    position: tuple[int, int, int]
    rotation: tuple[int, int, int]
    tracking_range: int
    script_id: int
    stats: dict[str, int]
    bits: int
    charge_mode: int
    moves: tuple[tuple[int, int], ...]
    flee_position: tuple[int, int, int]
    waypoint_speeds: tuple[int, ...]
    waypoints: tuple[tuple[int, int, int], ...]
    unknown: dict[str, int]


@dataclass(frozen=True)
class MapScene:
    setup: MapSetup
    image_offsets: tuple[int, ...]
    object_image_offsets: tuple[int, ...]
    objects: tuple[SpriteObject, ...]
    instances: tuple[SpriteInstance, ...]
    spawns: tuple[Spawn, ...]
    warps: tuple[Warp, ...]
    digimon_count: int
    digimon: tuple[MapDigimon, ...]
    floor_grid: bytes

    @property
    def used_warps(self) -> tuple[tuple[int, Warp], ...]:
        return tuple((slot, warp) for slot, warp in enumerate(self.warps) if warp.map_index < NO_WARP)


def _check_range(offset: int, size: int, limit: int, label: str) -> None:
    if offset < 0 or size < 0 or offset > limit or size > limit - offset:
        raise DiscFormatError(f"{label} is outside its bounded section.")


def _ascii(raw: bytes, label: str) -> str:
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise DiscFormatError(f"{label} is not ASCII.") from exc
    if not text.isprintable():
        raise DiscFormatError(f"{label} contains non-printable characters.")
    return text


def parse_map_table(executable: bytes) -> list[MapEntry]:
    """Parse the USA executable's 255-entry map table, area names, toilets, and door placements."""
    end = max(MAP_TABLE_OFFSET + MAP_COUNT * MAP_ENTRY_SIZE, AREA_NAME_POINTERS + AREA_NAME_COUNT * 4)
    if len(executable) < end or not executable.startswith(b"PS-X EXE"):
        raise DiscFormatError("USA executable is truncated or lacks its PS-X EXE header.")
    area_pointers = struct.unpack_from(f"<{AREA_NAME_COUNT}I", executable, AREA_NAME_POINTERS)
    entries, paths = [], set()
    for index in range(MAP_COUNT):
        offset = MAP_TABLE_OFFSET + index * MAP_ENTRY_SIZE
        raw = executable[offset:offset + MAP_ENTRY_SIZE]
        name_bytes, separator, padding = raw[:10].partition(b"\0")
        if not separator or any(padding):
            raise DiscFormatError(f"Map table entry {index} name is not NUL padded.")
        name = _ascii(name_bytes, f"Map table entry {index} name")
        # Names may repeat across directories (USA YAKA25 is entry 65 and 232); paths may not.
        path = f"MAP{1 + index // 15}/{name}"
        if name and (path in paths or not name.replace("_", "").isalnum()):
            raise DiscFormatError(f"Map table entry {index} has invalid name or duplicate path {path!r}.")
        paths.add(path)
        image_count, object_count, flags, door_id, toilet_id, area_id = raw[10:16]
        if area_id >= AREA_NAME_COUNT:
            raise DiscFormatError(f"Map table entry {index} has area-name index {area_id} outside the table.")
        area_offset = area_pointers[area_id] - USA_LOAD_ADDRESS
        _check_range(area_offset, AREA_NAME_SIZE, len(executable), f"Area name {area_id}")
        area_raw = executable[area_offset:area_offset + AREA_NAME_SIZE].split(b"\0", 1)[0]
        area_name = _ascii(area_raw, f"Area name {area_id}").strip()
        toilet = None
        if toilet_id:
            if toilet_id > TOILET_COUNT:
                raise DiscFormatError(f"Map table entry {index} has toilet index {toilet_id} outside the table.")
            toilet = struct.unpack_from("<4h", executable, TOILET_TABLE_OFFSET + (toilet_id - 1) * 8)
        doors = ()
        if door_id:
            record = DOOR_TABLE_OFFSET + (door_id - 1) * DOOR_RECORD_SIZE
            # The door table's length is not established; a u8 index cannot pass the map table, which follows it.
            if record + DOOR_RECORD_SIZE > MAP_TABLE_OFFSET:
                raise DiscFormatError(f"Map table entry {index} has door index {door_id} outside the table.")
            models = executable[record:record + 6]
            xs, ys, zs, rotations = (struct.unpack_from("<6h", executable, record + 6 + part * 12) for part in range(4))
            doors = tuple(DoorPlacement(models[slot], (xs[slot], ys[slot], zs[slot]), rotations[slot])
                          for slot in range(6) if models[slot] != 0xFF)
        entries.append(MapEntry(index, name, image_count, object_count, flags & 0x1F, not flags & 0x40,
                                bool(flags & 0x80), door_id, toilet_id, area_id, area_name, area_offset,
                                toilet, doors, raw))
    return entries


def entry_hash(entry: MapEntry) -> str:
    return hashlib.sha256(entry.raw).hexdigest()


def tfs_layout(header: bytes, size: int) -> tuple[int, int]:
    """Return (palette count, tile count) from the 8-byte TFS header and the file size."""
    if len(header) < 8 or size < 8:
        raise DiscFormatError("TFS header is truncated.")
    palette_count = struct.unpack_from("<I", header, 4)[0]
    if not 1 <= palette_count <= MAX_PALETTES:
        raise DiscFormatError(f"TFS palette count {palette_count} is outside the safety limit.")
    _check_range(8, palette_count * TFS_PALETTE_SIZE, size, "TFS palettes")
    remainder = size - 8 - palette_count * TFS_PALETTE_SIZE
    if remainder % TFS_TILE_RECORD:
        raise DiscFormatError("TFS tile data is not a whole number of 128x128 tiles.")
    tile_count = remainder // TFS_TILE_RECORD
    if tile_count > MAX_GRID * MAX_GRID:
        raise DiscFormatError(f"TFS tile count {tile_count} exceeds the safety limit.")
    return palette_count, tile_count


def parse_tfs(data: bytes) -> TfsFile:
    """Parse a TFS background: 256-colour palettes followed by 128x128 8-bit tiles."""
    palette_count, tile_count = tfs_layout(data[:8], len(data))
    width, height = struct.unpack_from("<HH", data)
    tiles_start = 8 + palette_count * TFS_PALETTE_SIZE
    palettes = tuple(struct.unpack_from("<256H", data, 8 + index * TFS_PALETTE_SIZE) for index in range(palette_count))
    offsets = tuple(tiles_start + index * TFS_TILE_RECORD + 4 for index in range(tile_count))
    return TfsFile(width, height, palettes, offsets, data)


def background_color(value: int) -> tuple[int, int, int, int]:
    # Backgrounds are drawn opaque: only the zero word is transparent and STP is ignored.
    if value == 0:
        return (0, 0, 0, 0)
    r, g, b = ((value >> shift) & 31 for shift in (0, 5, 10))
    return ((r * 255 + 15) // 31, (g * 255 + 15) // 31, (b * 255 + 15) // 31, 255)


def render_background(tfs: TfsFile, setup: MapSetup, palette_index: int = 0) -> Image.Image:
    """Assemble the background from the map's tile grid; empty tiles stay transparent."""
    if not 0 <= palette_index < len(tfs.palettes):
        raise DiscFormatError(f"TFS has no palette {palette_index}.")
    used = [tile for tile in setup.tiles if tile != EMPTY_TILE]
    if used != list(range(len(used))) or len(used) != len(tfs.tile_offsets):
        raise DiscFormatError("Map tile grid does not reference the TFS tiles in sequence.")
    palette = bytes(channel for value in tfs.palettes[palette_index] for channel in background_color(value))
    canvas = Image.new("RGBA", (setup.grid_width * TFS_TILE, setup.grid_height * TFS_TILE))
    for cell, tile in enumerate(setup.tiles):
        if tile == EMPTY_TILE:
            continue
        start = tfs.tile_offsets[tile]
        image = Image.frombytes("P", (TFS_TILE, TFS_TILE), tfs.data[start:start + TFS_TILE * TFS_TILE])
        image.putpalette(palette, rawmode="RGBA")
        canvas.paste(image.convert("RGBA"),
                     ((cell % setup.grid_width) * TFS_TILE, (cell // setup.grid_width) * TFS_TILE))
    return canvas


# Floor-grid trigger values, from DW1-SydPatches b9671fa (Tamer.cpp tick, Map.cpp getTileTrigger):
# 0x80 impassable; 50-79 step-on script; 80-109 interact (cross button) script;
# 110-119 walk-through exit using warp slot value-110; 120 toilet script.
BLOCKED = 0x80
WARP_TRIGGER = 110


def trigger_kind(value: int) -> str:
    if value == 0:
        return "walkable"
    if value == BLOCKED:
        return "blocked"
    if 50 <= value < 80:
        return "step script"
    if 80 <= value < 110:
        return "interact script"
    if WARP_TRIGGER <= value < WARP_TRIGGER + SLOTS:
        return "exit"
    if value == 120:
        return "toilet"
    return "unknown"


DIRECTIONS = ("north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west")


def grid_direction(column: float, row: float) -> str:
    """Compass label for a grid position; row 0 is the far (+Z, screen-top) edge, called north."""
    dx, dy = column - 49.5, row - 49.5
    if max(abs(dx), abs(dy)) < 10:
        return "centre"
    angle = math.degrees(math.atan2(dx, -dy)) % 360
    return DIRECTIONS[int((angle + 22.5) // 45) % 8]


@dataclass(frozen=True)
class MapExit:
    slot: int
    warp: Warp
    cell_count: int
    centroid: tuple[float, float] | None
    direction: str | None

    @property
    def walk_through(self) -> bool:
        return self.cell_count > 0


def map_exits(scene: MapScene) -> tuple[MapExit, ...]:
    """Every used warp slot, with the floor-grid exit tiles that trigger it (none means script or unused)."""
    cells: dict[int, list[tuple[int, int]]] = {}
    for index, value in enumerate(scene.floor_grid):
        if WARP_TRIGGER <= value < WARP_TRIGGER + SLOTS:
            cells.setdefault(value - WARP_TRIGGER, []).append((index % FLOOR_GRID, index // FLOOR_GRID))
    exits = []
    for slot, warp in scene.used_warps:
        tiles = cells.get(slot, [])
        if tiles:
            centroid = (sum(x for x, _ in tiles) / len(tiles), sum(y for _, y in tiles) / len(tiles))
            exits.append(MapExit(slot, warp, len(tiles), centroid, grid_direction(*centroid)))
        else:
            exits.append(MapExit(slot, warp, 0, None, None))
    return tuple(exits)


def trigger_summary(scene: MapScene) -> dict[str, dict[int, int]]:
    summary: dict[str, dict[int, int]] = {}
    for value in scene.floor_grid:
        kind = summary.setdefault(trigger_kind(value), {})
        kind[value] = kind.get(value, 0) + 1
    return summary


@dataclass(frozen=True)
class MapLink:
    source: int
    target: int
    slot: int
    walk_through: bool
    direction: str | None


@dataclass
class ZoneLink:
    source: str
    target: str
    walk_through: int = 0
    scripted: int = 0
    directions: dict[str, int] | None = None


def map_links(entries: list[MapEntry], scenes: dict[int, MapScene]) -> list[MapLink]:
    return [MapLink(index, warp_exit.warp.map_index, warp_exit.slot, warp_exit.walk_through, warp_exit.direction)
            for index, scene in sorted(scenes.items()) for warp_exit in map_exits(scene)
            if warp_exit.warp.map_index < len(entries) and entries[warp_exit.warp.map_index].name]


def zone_links(entries: list[MapEntry], links: list[MapLink]) -> list[ZoneLink]:
    """Aggregate map links into undirected links between different area-name zones."""
    merged: dict[tuple[str, str], ZoneLink] = {}
    for link in links:
        source, target = entries[link.source].area_name, entries[link.target].area_name
        if source == target:
            continue
        key = tuple(sorted((source, target)))
        zone = merged.setdefault(key, ZoneLink(key[0], key[1], directions={}))
        if link.walk_through:
            zone.walk_through += 1
            # store the direction as seen from key[0]; a link out of key[1] points the opposite way
            direction = link.direction if source == key[0] else _opposite(link.direction)
            zone.directions[direction] = zone.directions.get(direction, 0) + 1
        else:
            zone.scripted += 1
    return sorted(merged.values(), key=lambda zone: (zone.source, zone.target))


def _opposite(direction: str | None) -> str | None:
    if direction in DIRECTIONS:
        return DIRECTIONS[(DIRECTIONS.index(direction) + 4) % 8]
    return direction


def _unit(direction: str | None) -> tuple[float, float] | None:
    if direction not in DIRECTIONS:
        return None
    angle = math.radians(DIRECTIONS.index(direction) * 45)
    return math.sin(angle), -math.cos(angle)


def layout_zones(zones: list[str], links: list[ZoneLink], iterations: int = 600) -> dict[str, tuple[float, float]]:
    """Deterministic force layout; walk-through exits pull the neighbour toward the exit's compass direction."""
    count = len(zones)
    position = {zone: (math.cos(2 * math.pi * i / max(count, 1)) * 400, math.sin(2 * math.pi * i / max(count, 1)) * 400)
                for i, zone in enumerate(zones)}
    spring = 160.0
    for step in range(iterations):
        heat = 40.0 * (1 - step / iterations) + 1.0
        force = {zone: [0.0, 0.0] for zone in zones}
        for i, a in enumerate(zones):
            ax, ay = position[a]
            for b in zones[i + 1:]:
                dx, dy = ax - position[b][0], ay - position[b][1]
                distance_sq = max(dx * dx + dy * dy, 1.0)
                push = spring * spring / distance_sq
                force[a][0] += dx * push; force[a][1] += dy * push
                force[b][0] -= dx * push; force[b][1] -= dy * push
        for link in links:
            (ax, ay), (bx, by) = position[link.source], position[link.target]
            weight = 1.0 if link.walk_through else 0.4
            dx, dy = bx - ax, by - ay
            distance = math.hypot(dx, dy) or 1.0
            pull = weight * (distance - spring) / distance * 0.5
            force[link.source][0] += dx * pull; force[link.source][1] += dy * pull
            force[link.target][0] -= dx * pull; force[link.target][1] -= dy * pull
            if link.directions:
                direction = max(link.directions, key=link.directions.get)
                unit = _unit(direction)
                if unit:
                    tx, ty = ax + unit[0] * spring, ay + unit[1] * spring
                    force[link.target][0] += (tx - bx) * 0.6; force[link.target][1] += (ty - by) * 0.6
                    force[link.source][0] -= (tx - bx) * 0.6; force[link.source][1] -= (ty - by) * 0.6
        for zone in zones:
            fx, fy = force[zone]
            length = math.hypot(fx, fy)
            if length > heat:
                fx, fy = fx / length * heat, fy / length * heat
            position[zone] = (position[zone][0] + fx, position[zone][1] + fy)
    return position


def export_background(image: Image.Image, destination: Path, provenance: dict) -> tuple[Path, Path]:
    """Write a background PNG and its JSON provenance sidecar; never overwrite."""
    destination = destination.resolve()
    metadata_path = destination.with_suffix(destination.suffix + ".json")
    if destination.exists() or metadata_path.exists():
        raise ValidationError("PNG or metadata export already exists.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, "PNG")
    metadata = {"decoder": "dw1-workbench TFS", "decoder_version": TFS_DECODER_VERSION,
                "colour_semantics": "zero colour word alpha 0; all other words opaque, STP ignored",
                **provenance}
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return destination, metadata_path


TRIGGER_COLOURS = {
    "walkable": (205, 205, 205), "blocked": (45, 48, 56), "step script": (230, 160, 40),
    "interact script": (70, 140, 230), "toilet": (140, 90, 40), "unknown": (255, 0, 0),
}
EXIT_COLOURS = ((40, 180, 90), (200, 60, 160), (0, 190, 190), (220, 80, 60), (150, 110, 230),
                (120, 200, 40), (240, 200, 0), (110, 70, 40), (250, 120, 170), (60, 90, 160))


def trigger_colour(value: int) -> tuple[int, int, int]:
    kind = trigger_kind(value)
    return EXIT_COLOURS[value - WARP_TRIGGER] if kind == "exit" else TRIGGER_COLOURS[kind]


def floor_grid_image(scene: MapScene) -> Image.Image:
    """The 100x100 floor grid, one pixel per cell, coloured by trigger kind (exits by warp slot)."""
    palette = bytes(channel for value in range(256) for channel in trigger_colour(value))
    image = Image.frombytes("P", (FLOOR_GRID, FLOOR_GRID), scene.floor_grid)
    image.putpalette(palette)
    return image.convert("RGB")


class _Reader:
    def __init__(self, data: bytes, offset: int, label: str):
        self.data, self.offset, self.label = data, offset, label

    def read(self, fmt: str):
        size = struct.calcsize(fmt)
        _check_range(self.offset, size, len(self.data), self.label)
        values = struct.unpack_from(fmt, self.data, self.offset)
        self.offset += size
        return values


def _parse_setup(data: bytes, offset: int) -> MapSetup:
    reader = _Reader(data, offset, "Map setup")
    origin, target = reader.read("<3i"), reader.read("<3i")
    lights = []
    for _ in range(3):
        position, color = reader.read("<3i"), reader.read("<3I")
        lights.append(Light(position, color))
    ambient = reader.read("<3I")
    (distance,) = reader.read("<I")
    liked, disliked = reader.read("<4i"), reader.read("<4i")
    width, height = reader.read("<2I")
    if not (1 <= width <= MAX_GRID and 1 <= height <= MAX_GRID):
        raise DiscFormatError(f"Map background grid {width}x{height} is outside the safety limit.")
    tiles = reader.read(f"<{width * height}I")
    return MapSetup(origin, target, tuple(lights), ambient, distance, liked, disliked, width, height, tiles)


def _parse_digimon(reader: _Reader) -> MapDigimon:
    character_id, ai_type = reader.read("<2H")
    position, rotation = reader.read("<3h"), reader.read("<3h")
    tracking_range, unknown2 = reader.read("<2H")
    script_id, unknown3 = reader.read("<2B")
    stat_values = reader.read("<8H")
    bits, charge_mode, unknown5 = reader.read("<3H")
    moves, weights = reader.read("<4H"), reader.read("<4H")
    flee = reader.read("<3h")
    (waypoint_count,) = reader.read("<H")
    if waypoint_count > MAX_WAYPOINTS:
        raise DiscFormatError(f"Map Digimon waypoint count {waypoint_count} exceeds the safety limit.")
    speeds = reader.read("<8h")
    waypoints = tuple(reader.read("<3h") for _ in range(waypoint_count))
    stats = dict(zip(("hp", "mp", "max_hp", "max_mp", "offense", "defense", "speed", "brains"), stat_values))
    return MapDigimon(character_id, ai_type, position, rotation, tracking_range, script_id, stats, bits,
                      charge_mode, tuple(zip(moves, weights)), flee, speeds, waypoints,
                      {"unk2": unknown2, "unk3": unknown3, "unk5": unknown5})


def parse_map(data: bytes, entry: MapEntry) -> MapScene:
    """Parse a MAP scene file using the image/object counts from its map-table entry."""
    header = _Reader(data, 0, "Map header")
    (setup_offset,) = header.read("<I")
    image_offsets = header.read(f"<{entry.image_count}I")
    object_image_offsets = header.read(f"<{entry.object_count}I")
    has_objects = bool(entry.image_count or entry.object_count)
    object_offset = header.read("<I")[0] if has_objects else None
    elements_offset, floor_offset = header.read("<2I")
    for offset in (*image_offsets, *object_image_offsets):
        _check_range(offset, 8, len(data), "Embedded map TIM")
        if data[offset:offset + 4] != b"\x10\x00\x00\x00":
            raise DiscFormatError(f"Embedded map image at 0x{offset:x} is not a TIM.")
    setup = _parse_setup(data, setup_offset)
    objects, instances = (), ()
    if object_offset is not None:
        reader = _Reader(data, object_offset, "Map objects")
        (count,) = reader.read("<H")
        if count > MAX_OBJECTS:
            raise DiscFormatError(f"Map object count {count} exceeds the safety limit.")
        objects = tuple(SpriteObject((uv_x, uv_y), (w, h), (x, y, z), clut, transparency)
                        for uv_x, uv_y, w, h, x, y, z, clut, transparency in (reader.read("<4H3h2H") for _ in range(count)))
        (count,) = reader.read("<H")
        if count > MAX_OBJECTS:
            raise DiscFormatError(f"Map object instance count {count} exceeds the safety limit.")
        records = []
        for _ in range(count):
            states, durations = reader.read("<8h"), reader.read("<8h")
            x, y, flag = reader.read("<3H")
            records.append(SpriteInstance(tuple(zip(states, durations)), (x, y), flag))
        instances = tuple(records)
    reader = _Reader(data, elements_offset, "Map elements")
    xs, ys, zs, rotations = (reader.read("<10h") for _ in range(4))
    targets, target_spawns = reader.read("<10H"), reader.read("<10H")
    (digimon_count,) = reader.read("<H")
    digimon = ()
    # Upstream discards placements when the table says the map has no Digimon; do not parse them then.
    if entry.has_digimon:
        if digimon_count > MAX_DIGIMON:
            raise DiscFormatError(f"Map Digimon count {digimon_count} exceeds the safety limit.")
        digimon = tuple(_parse_digimon(reader) for _ in range(digimon_count))
    _check_range(floor_offset, FLOOR_GRID * FLOOR_GRID, len(data), "Map floor grid")
    return MapScene(setup, image_offsets, object_image_offsets, objects, instances,
                    tuple(Spawn((xs[i], ys[i], zs[i]), rotations[i]) for i in range(SLOTS)),
                    tuple(Warp(targets[i], target_spawns[i]) for i in range(SLOTS)),
                    digimon_count, digimon, data[floor_offset:floor_offset + FLOOR_GRID * FLOOR_GRID])
