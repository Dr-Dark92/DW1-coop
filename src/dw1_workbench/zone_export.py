"""Zone packages: one zone or a bundle of zones exported as a single versioned .dw1zone (ZIP) file.

A package keeps the original MAP and TFS bytes unchanged (with SHA-256), the decoded scene data, rendered
backgrounds and floor grids, pathing (walkable regions, spawn-to-exit routes, map-to-map routes inside the
package), the links in and out of the package, and, optionally, what was observed while playing. Unknown
fields are carried as raw values. The package contains game data from the user's own disc: keep it private.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import tempfile
import zipfile
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw

from . import __version__
from .errors import Cancelled
from .live import model_tile
from .maps import (FLOOR_GRID, MapEntry, MapScene, TfsFile, floor_grid_with_digimon, follow_description,
                   map_exits, render_background, trigger_summary)
from .pathing import MapPathing, map_pathing, regions, routes_from

PACKAGE_FORMAT = "dw1-zone-package"
PACKAGE_VERSION = 1
SUFFIX = ".dw1zone"
REGION_COLOURS = [(91, 155, 213), (237, 125, 49), (112, 173, 71), (255, 192, 0), (158, 72, 14), (68, 114, 196),
                  (165, 165, 165), (38, 68, 120)]


@dataclass(frozen=True)
class MapSource:
    """One map's decoded data and the catalogue rows and bytes of its MAP and TFS files."""
    entry: MapEntry
    scene: MapScene
    tfs: TfsFile | None
    map_bytes: bytes
    tfs_bytes: bytes | None
    map_row: dict
    tfs_row: dict | None


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower() or "zone"


def _jsonable(value):
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, bytes):
        return value.hex()
    return value


def _png(image: Image.Image) -> bytes:
    buffer = io.BytesIO(); image.save(buffer, "PNG")
    return buffer.getvalue()


def _row_provenance(row: dict | None) -> dict | None:
    if row is None:
        return None
    provenance = {key: row[key] for key in ("original_path", "size", "extent", "offset", "decoder", "decoder_version")
                  if key in row.keys()}
    provenance["sha256"] = row["content_hash"] if "content_hash" in row.keys() else None
    return provenance


def _checked(data: bytes, row: dict | None, label: str) -> bytes:
    """The original bytes, after confirming they still hash to what the catalogue recorded at import."""
    expected = row["content_hash"] if row is not None and "content_hash" in row.keys() else None
    if expected and hashlib.sha256(data).hexdigest() != expected:
        raise ValueError(f"{label} no longer matches its catalogue SHA-256; re-import the disc before exporting.")
    return data


def pathing_image(scene: MapScene, pathing: MapPathing, scale: int = 4, highlight=None) -> Image.Image:
    """Walkable regions in colour (blocked tiles dark), places marked, every spawn-to-exit route drawn;
    `highlight` (a Route) is drawn on top in yellow."""
    label, _ = regions(scene.floor_grid)
    image = Image.new("RGB", (FLOOR_GRID, FLOOR_GRID), (32, 36, 43))
    pixels = image.load()
    for index, region in enumerate(label):
        if region >= 0:
            pixels[index % FLOOR_GRID, index // FLOOR_GRID] = REGION_COLOURS[region % len(REGION_COLOURS)]
    image = image.resize((FLOOR_GRID * scale, FLOOR_GRID * scale), Image.Resampling.NEAREST)
    draw = ImageDraw.Draw(image)
    for route in pathing.routes:
        if len(route.path) > 1:
            draw.line([((x + 0.5) * scale, (y + 0.5) * scale) for x, y in route.path], fill=(255, 255, 255), width=1)
    if highlight is not None and len(highlight.path) > 1:
        draw.line([((x + 0.5) * scale, (y + 0.5) * scale) for x, y in highlight.path], fill=(255, 230, 0), width=3)
    for place in pathing.places:
        colour = {"spawn": (0, 230, 255), "exit": (255, 60, 60), "toilet": (230, 120, 255)}[place.kind]
        for x, y in place.tiles:
            draw.rectangle((x * scale, y * scale, (x + 1) * scale - 1, (y + 1) * scale - 1),
                           outline=colour, fill=None if place.kind != "spawn" else colour)
    return image


def _warps(scene: MapScene, map_names: dict[int, str]) -> list[dict]:
    return [{"slot": warp_exit.slot, "to_map": warp_exit.warp.map_index, "to_label": map_names.get(warp_exit.warp.map_index),
             "to_spawn": warp_exit.warp.spawn_index, "walk_through": warp_exit.walk_through,
             "exit_tiles": warp_exit.cell_count, "direction": warp_exit.direction} for warp_exit in map_exits(scene)]


def map_document(source: MapSource, pathing: MapPathing, map_names: dict[int, str], character_names: dict[int, str],
                 inbound: list[dict]) -> dict:
    entry, scene = source.entry, source.scene
    return {
        "index": entry.index, "name": entry.name, "zone": entry.area_name, "label": map_names.get(entry.index, entry.name),
        "files": {"map": _row_provenance(source.map_row), "tfs": _row_provenance(source.tfs_row)},
        "table_entry": {"area_id": entry.area_id, "sound_id": entry.sound_id, "has_time_cycle": entry.has_time_cycle,
                        "has_digimon": entry.has_digimon, "door_id": entry.door_id, "toilet_id": entry.toilet_id,
                        "toilet": entry.toilet, "doors": _jsonable(entry.doors), "raw_hex": entry.raw.hex(),
                        "executable_offset": entry.offset},
        "setup": _jsonable(scene.setup),
        "spawns": [{"index": index, "position": spawn.position, "rotation": spawn.rotation,
                    "tile": model_tile(spawn.position[0], spawn.position[2])} for index, spawn in enumerate(scene.spawns)],
        "warps": _warps(scene, map_names),
        "entrances": inbound,
        "digimon_placements": [{**_jsonable(digimon), "name": character_names.get(digimon.character_id),
                                "tile": model_tile(digimon.position[0], digimon.position[2]),
                                "behaviour": follow_description(digimon.ai_type, digimon.tracking_range)}
                               for digimon in scene.digimon],
        "sprite_objects": _jsonable(scene.objects), "sprite_instances": _jsonable(scene.instances),
        "embedded_images": {"8bpp_offsets": scene.image_offsets, "4bpp_offsets": scene.object_image_offsets},
        "triggers": {kind: {str(value): count for value, count in values.items()}
                     for kind, values in trigger_summary(scene).items()},
        "floor_grid": {"width": FLOOR_GRID, "height": FLOOR_GRID, "row_0": "far edge (north)",
                       "rows_hex": [scene.floor_grid[row * FLOOR_GRID:(row + 1) * FLOOR_GRID].hex()
                                    for row in range(FLOOR_GRID)]},
    }


def pathing_document(pathing: MapPathing) -> dict:
    return {
        "rules": "tiles with value & 0x80 block movement (checkMapCollisionX/Z); 8-way steps, diagonals need both "
                 "orthogonal tiles open; distances in tiles, diagonal = sqrt(2); a character's radius is ignored",
        "regions": [{"region": index, "tiles": size} for index, size in enumerate(pathing.region_sizes)],
        "places": [{**_jsonable(place), "regions": pathing.place_regions.get(place.id, ())} for place in pathing.places],
        "routes": [{"from": route.start, "to": route.end, "distance": route.distance, "path": route.path}
                   for route in pathing.routes],
    }


def export_package(destination: Path, zones: list[str], sources: dict[int, MapSource], all_scenes: dict[int, MapScene],
                   entries: list[MapEntry], map_names: dict[int, str], character_names: dict[int, str], meta: dict,
                   observations: dict | None = None, all_palettes: bool = False, cancelled=lambda: False,
                   progress=lambda value, text: None) -> dict:
    """Write the package for `zones` (all maps in `sources` belong to them) and return its manifest."""
    destination = Path(destination)
    if destination.suffix != SUFFIX:
        destination = destination.with_suffix(SUFFIX)
    indices = sorted(sources)
    if not indices:
        raise ValueError("The selected zones have no decoded maps.")
    inbound: dict[int, list[dict]] = {index: [] for index in indices}
    for source_index, scene in all_scenes.items():
        for warp_exit in map_exits(scene):
            target = warp_exit.warp.map_index
            if target in inbound:
                inbound[target].append({"from_map": source_index, "from_label": map_names.get(source_index),
                                        "slot": warp_exit.slot, "to_spawn": warp_exit.warp.spawn_index,
                                        "walk_through": warp_exit.walk_through,
                                        "inside_package": source_index in sources})
    files: list[dict] = []
    handle, temporary = tempfile.mkstemp(dir=destination.parent, prefix=f".{destination.name}.", suffix=".partial")
    os.close(handle)
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as package:
            def add(name: str, data: bytes, kind: str):
                package.writestr(name, data)
                files.append({"path": name, "kind": kind, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})

            pathing: dict[int, MapPathing] = {}
            for done, index in enumerate(indices):
                if cancelled():
                    raise Cancelled("Zone export cancelled.")
                source = sources[index]
                entry, scene = source.entry, source.scene
                progress(5 + 75 * done // len(indices), f"Exporting {map_names.get(index, entry.name)}")
                folder = f"maps/{index:03d}-{entry.name}"
                pathing[index] = map_pathing(entry, scene, map_names)
                add(f"{folder}/{entry.name}.MAP", _checked(source.map_bytes, source.map_row, entry.map_path),
                    "original MAP file")
                if source.tfs_bytes is not None:
                    add(f"{folder}/{entry.name}.TFS", _checked(source.tfs_bytes, source.tfs_row, entry.tfs_path),
                        "original TFS file")
                add(f"{folder}/map.json", json.dumps(map_document(source, pathing[index], map_names, character_names,
                                                                  inbound[index]), indent=1).encode(), "decoded map")
                add(f"{folder}/pathing.json", json.dumps(pathing_document(pathing[index])).encode(), "pathing")
                add(f"{folder}/floor-grid.png", _png(floor_grid_with_digimon(scene)), "floor grid with Digimon")
                add(f"{folder}/pathing.png", _png(pathing_image(scene, pathing[index])), "pathing overview")
                if source.tfs is not None:
                    for palette in range(len(source.tfs.palettes) if all_palettes else min(1, len(source.tfs.palettes))):
                        try:
                            image = render_background(source.tfs, scene.setup, palette)
                        except Exception:
                            break  # backgrounds the decoder rejects are left out; the raw TFS is included
                        add(f"{folder}/background-palette{palette}.png", _png(image), "background")
            progress(85, "Planning routes between maps")
            route_table = []
            for start in indices:
                if cancelled():
                    raise Cancelled("Zone export cancelled.")
                for end, (distance, legs) in sorted(routes_from(pathing, start).items()):
                    if end != start:
                        route_table.append({"from_map": start, "to_map": end, "distance": distance,
                                            "legs": [_jsonable(leg) for leg in legs]})
            zone_documents = []
            for zone in zones:
                members = [index for index in indices if sources[index].entry.area_name == zone]
                leaving = [{"from_map": index, **warp} for index in members
                           for warp in _warps(sources[index].scene, map_names) if warp["to_map"] not in sources]
                document = {"zone": zone, "maps": [{"index": index, "label": map_names.get(index)} for index in members],
                            "entrances_from_outside": [{"to_map": index, **link} for index in members
                                                       for link in inbound[index] if not link["inside_package"]],
                            "exits_to_outside": leaving}
                zone_documents.append(document)
                add(f"zones/{slug(zone)}.json", json.dumps(document, indent=1).encode(), "zone")
            add("routes.json", json.dumps({"rules": "walk-through exits only; script-driven exits are not decoded",
                                           "routes": route_table}).encode(), "map-to-map routes")
            if observations:
                progress(92, "Adding observations")
                for name, data, kind in observations.get("files", ()):
                    add(f"observations/{name}", data, kind)
                add("observations/observations.json", json.dumps(observations.get("records", {}), indent=1).encode(),
                    "observations (seen while playing, not decoded)")
            manifest = {
                "format": PACKAGE_FORMAT, "version": PACKAGE_VERSION, "kind": "zone" if len(zones) == 1 else "bundle",
                "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "tool": {"name": "dw1-coop-workbench", "version": __version__},
                "source": {key: meta.get(key) for key in ("release", "executable", "source_sha256", "source_size")},
                "parser_versions": meta.get("parser_versions"),
                "background_palettes": "all" if all_palettes else "first (the original TFS holds every palette)",
                "zones": zones, "maps": [{"index": index, "label": map_names.get(index)} for index in indices],
                "external_links": sum(len(document["exits_to_outside"]) + len(document["entrances_from_outside"])
                                      for document in zone_documents),
                "limits": ["map scripts are not decoded: script-driven exits, spawn rules and item spawns are missing",
                           "3D map geometry and door models are not decoded",
                           "contains game data from the user's own disc; do not share publicly"],
                "files": files,
            }
            package.writestr("manifest.json", json.dumps(manifest, indent=1))
        progress(100, "Package written")
        os.replace(temporary, destination)
        return manifest
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
