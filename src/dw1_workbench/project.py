from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable

from . import __version__
from .alltim import DECODER_VERSION as ALLTIM_DECODER_VERSION, decode_alltim
from .catalog import Catalogue
from .characters import (DECODER_VERSION as CHARACTER_DECODER_VERSION,
                         LEVEL_FIELD, MMD_INSPECTOR_VERSION, entry_hash, inspect_mmd,
                         parse_character_table)
from .disc import inspect_image, sha256_file
from .errors import Cancelled
from .iso9660 import hash_entries, identify_release, read_inventory
from .maps import (MAP_DECODER_VERSION, MAX_MAP_FILE, TABLE_DECODER_VERSION as MAP_TABLE_DECODER_VERSION,
                   TFS_DECODER_VERSION, EMPTY_TILE, entry_hash as map_entry_hash, parse_map,
                   parse_map_table, tfs_layout)

MAP_TABLE_EVIDENCE = ("USA executable 255x16-byte map table at 0x992d4 with area-name pointers at 0x991bc; "
                      "upstream exporter reads entry i from MAP{1 + i//15}/{name}.MAP/.TFS. "
                      "All 241 file-backed entries matched DW1ModelConverter 17d1a19 output.")


def resource_id(path: str, content_hash: str) -> str:
    return "disc:" + hashlib.sha256((path + "\0" + content_hash).encode()).hexdigest()[:24]


def _import_maps(catalogue, layout, executable_path, executable_entry, entries_by_path, disc_ids,
                 character_ids, cancelled):
    """Catalogue the map table, validate each MAP/TFS pair, and link warps and placements."""
    with layout.open() as reader:
        executable = reader.read_extent(executable_entry.extent, executable_entry.size, 2 * 1024 * 1024)
        entries = parse_map_table(executable)
        map_ids = {entry.index: resource_id(f"{executable_path}#map/{entry.index:03d}", map_entry_hash(entry))
                   for entry in entries if entry.name}
        rows, associations, labels, updates = [], [], [], []
        scene_count = 0
        for entry in entries:
            if not entry.name:
                continue
            if cancelled():
                raise Cancelled("Import cancelled while validating maps.")
            map_id = map_ids[entry.index]
            map_file, tfs_file = entries_by_path.get(entry.map_path), entries_by_path.get(entry.tfs_path)
            status = "identified"
            if map_file is not None and tfs_file is not None:
                scene = parse_map(reader.read_extent(map_file.extent, map_file.size, MAX_MAP_FILE), entry)
                palette_count, tile_count = tfs_layout(reader.read_extent_prefix(tfs_file.extent, tfs_file.size, 8),
                                                       tfs_file.size)
                if tile_count != sum(tile != EMPTY_TILE for tile in scene.setup.tiles):
                    raise ValueError(f"{entry.tfs_path} tile count does not match {entry.map_path}.")
                status = "decoded"
                scene_count += 1
                associations += [(map_id, disc_ids[entry.map_path], "scene", MAP_TABLE_EVIDENCE, "confirmed"),
                                 (map_id, disc_ids[entry.tfs_path], "background", MAP_TABLE_EVIDENCE, "confirmed")]
                updates += [("DW1 MAP scene", "dw1-workbench MAP scene", MAP_DECODER_VERSION, "decoded",
                             "Header, setup, sprite objects, elements, and floor grid validated.", "confirmed",
                             disc_ids[entry.map_path]),
                            ("DW1 TFS background", "dw1-workbench TFS", TFS_DECODER_VERSION, "decoded",
                             f"{palette_count} palette(s), {tile_count} 128x128 tiles; matches its map grid.",
                             "confirmed", disc_ids[entry.tfs_path])]
                for target in sorted({warp.map_index for _, warp in scene.used_warps}):
                    if target in map_ids:
                        associations.append((map_id, map_ids[target], "warp", MAP_TABLE_EVIDENCE, "confirmed"))
                for character in sorted({digimon.character_id for digimon in scene.digimon}):
                    if character in character_ids:
                        associations.append((map_id, character_ids[character], "digimon_placement",
                                             MAP_TABLE_EVIDENCE, "confirmed"))
            rows.append((map_id, f"{executable_path}#map/{entry.index:03d}/{entry.name}", executable_path,
                         entry.offset, 16, executable_entry.extent, "DW1 map table entry", map_entry_hash(entry),
                         "dw1-workbench map table", MAP_TABLE_DECODER_VERSION, status, "maps",
                         MAP_TABLE_EVIDENCE if status == "decoded" else
                         MAP_TABLE_EVIDENCE + " This entry's MAP/TFS files are absent from the disc.", "confirmed"))
            associations.append((disc_ids[executable_path], map_id, "contains", MAP_TABLE_EVIDENCE, "confirmed"))
            labels.append((map_id, "area_name", entry.area_name,
                           f"USA executable area-name pointer {entry.area_id} -> 0x{entry.area_name_offset:x}; "
                           "28-byte space-padded field.", "confirmed"))
    catalogue.add_resources(rows, associations)
    catalogue.update_resource_analysis(updates)
    catalogue.add_labels(labels)
    return len(rows), scene_count


def import_project(source: Path, project_dir: Path, cancelled=lambda: False,
                   progress: Callable[[int, str], None] | None = None) -> Catalogue:
    project_dir = project_dir.resolve()
    if project_dir.exists() and any(project_dir.iterdir()):
        raise ValueError("Project directory must be empty; use Open Project to resume an existing project.")
    catalogue = Catalogue(project_dir)
    catalogue.initialize()
    catalogue.set_meta(import_status="in_progress", source_path=str(source.resolve()),
                       tool_version=__version__, parser_versions={"iso9660": 1, "cue": 1, "tim": 1,
                                                                  "alltim": 1, "characters": 3,
                                                                  "mmd_boundary": 1, "map_table": 1,
                                                                  "map_scene": 1, "tfs": 1},
                       started_at=datetime.now(timezone.utc).isoformat())
    try:
        layout = inspect_image(source)
        source_hash = sha256_file(layout.data_file,
                                  (lambda p, m: progress(p // 4, m)) if progress else None, cancelled)
        descriptor = source.resolve()
        descriptor_stat = descriptor.stat()
        descriptor_hash = (source_hash if descriptor == layout.data_file else sha256_file(descriptor, cancelled=cancelled))
        with layout.open() as reader:
            entries = read_inventory(reader, cancelled,
                                     (lambda p, m: progress(25 + p // 3, m)) if progress else None)
            release = identify_release(reader, entries)
            hashed = hash_entries(reader, entries, cancelled,
                                  (lambda p, m: progress(50 + p // 2, m)) if progress else None)
        rows = []
        for entry, digest, fmt in hashed:
            if cancelled():
                raise Cancelled("Import cancelled while building the resource catalogue.")
            if entry.path.upper() == "CHDAT/ALLTIM.TIM":
                fmt = "DW1 ALLTIM container"
            category = "textures" if fmt == "TIM" else ("executable" if fmt == "PS-X EXE" else "disc")
            if fmt == "DW1 ALLTIM container":
                category = "textures"
            rows.append((resource_id(entry.path, digest), entry.path,
                         str(PurePosixPath(entry.path).parent), None, entry.size, entry.extent,
                         fmt, digest, "identified" if fmt != "unknown" else "unknown", category))
        catalogue.replace_inventory(rows)
        entries_by_path = {entry.path: entry for entry, _, _ in hashed}
        disc_ids = {row[1]: row[0] for row in rows}
        alltim_entry = next((entry for entry, _, _ in hashed
                             if entry.path.upper() == "CHDAT/ALLTIM.TIM"), None)
        alltim_count = 0
        texture_ids = {}
        if alltim_entry is not None:
            parent_id = next(row[0] for row in rows if row[1].upper() == "CHDAT/ALLTIM.TIM")
            with layout.open() as reader:
                slots = decode_alltim(reader, alltim_entry, cancelled)
            evidence = ("Fixed 0x4800-byte slots: upstream game code reads digiType*9 sectors "
                        "for 9 sectors; every slot passed structural TIM decoding.")
            child_rows = []
            associations = []
            for slot in slots:
                child_id = resource_id(f"{alltim_entry.path}#{slot.index:03d}", slot.sha256)
                child_rows.append((child_id, f"{alltim_entry.path}#{slot.index:03d}",
                                   alltim_entry.path, slot.offset, slot.size, alltim_entry.extent,
                                   "DW1 ALLTIM TIM slot", slot.sha256, "dw1-workbench ALLTIM",
                                   ALLTIM_DECODER_VERSION, "decoded", "textures", evidence, "confirmed"))
                associations.append((parent_id, child_id, "contains", evidence, "confirmed"))
                texture_ids[slot.index] = child_id
            catalogue.add_resources(child_rows, associations)
            alltim_count = len(child_rows)
        character_count = 0
        mmd_count = 0
        character_ids = {}
        executable_entry = entries_by_path.get(release.executable)
        if executable_entry is not None and len(texture_ids) == 180:
            with layout.open() as reader:
                executable = reader.read_extent(executable_entry.extent, executable_entry.size,
                                                2 * 1024 * 1024)
                characters = parse_character_table(executable)
                mmd_updates = []
                mmd_structures = {}
                for character in characters:
                    model_entry = entries_by_path.get(character.model_path)
                    if model_entry is None:
                        continue
                    header = reader.read_extent_prefix(model_entry.extent, model_entry.size, 20)
                    mmd_structures[character.model_path] = inspect_mmd(header, model_entry.size)
                    mmd_updates.append(("MMD container (embedded TMD + animation)",
                                        "dw1-workbench MMD boundary inspector", MMD_INSPECTOR_VERSION,
                                        "identified",
                                        "Header and embedded TMD/animation boundary validated; geometry and animation are not decoded.",
                                        "confirmed", disc_ids[character.model_path]))
            catalogue.update_resource_analysis(mmd_updates)
            evidence = ("USA executable 180x8-byte internal filename table; upstream loader uses index i "
                        "for MMD{i//30}/{name}.MMD and ALLTIM slot i. Disc paths and MMD boundaries validated.")
            character_rows = []
            character_associations = []
            character_labels = []
            for character in characters:
                digest = entry_hash(character)
                character_id = resource_id(f"{release.executable}#character/{character.resource_id:03d}", digest)
                original_path = f"{release.executable}#character/{character.resource_id:03d}/{character.internal_name}"
                character_rows.append((character_id, original_path, release.executable,
                                       character.executable_offset, 8, executable_entry.extent,
                                       "DW1 character resource entry", digest,
                                       "dw1-workbench character table", CHARACTER_DECODER_VERSION,
                                       "identified", "characters", evidence, "confirmed"))
                character_associations.append((disc_ids[release.executable], character_id,
                                               "contains", evidence, "confirmed"))
                character_ids[character.resource_id] = character_id
                if character.display_name is not None:
                    character_labels.append((character_id, "display_name", character.display_name,
                                             f"USA executable DigimonPara record at 0x{character.display_name_offset:x}; "
                                             "20-byte NUL-padded display-name field.", "confirmed"))
                if character.stage is not None:
                    character_labels.append((character_id, "stage", character.stage,
                                             f"USA executable DigimonPara level byte at "
                                             f"0x{character.display_name_offset + LEVEL_FIELD:x} = {character.level}; "
                                             "Level enum names from DW1-SydPatches.", "confirmed"))
                character_associations.append((character_id, texture_ids[character.texture_slot],
                                               "texture", evidence, "confirmed"))
                if character.model_path in mmd_structures:
                    character_associations.append((character_id, disc_ids[character.model_path],
                                                   "model_container", evidence, "confirmed"))
            catalogue.add_resources(character_rows, character_associations)
            catalogue.add_labels(character_labels)
            character_count = len(character_rows)
            mmd_count = len(mmd_structures)
        map_count = scene_count = 0
        if executable_entry is not None and any(path.startswith("MAP/") for path in entries_by_path):
            map_count, scene_count = _import_maps(catalogue, layout, release.executable, executable_entry,
                                                  entries_by_path, disc_ids, character_ids, cancelled)
        stat = layout.data_file.stat()
        catalogue.set_meta(
            import_status="complete", source_sha256=source_hash, source_size=stat.st_size,
            source_mtime_ns=stat.st_mtime_ns, source_data_path=str(layout.data_file),
            source_descriptor_sha256=descriptor_hash, source_descriptor_size=descriptor_stat.st_size,
            source_descriptor_mtime_ns=descriptor_stat.st_mtime_ns,
            source_kind=layout.description, sector_size=layout.sector_size,
            user_offset=layout.user_offset, start_sector=layout.start_sector,
            sector_count=layout.sector_count, release=release.release,
            executable=release.executable, release_evidence=release.confidence,
            completed_at=datetime.now(timezone.utc).isoformat(), file_count=len(rows),
            resource_count=len(rows) + alltim_count + character_count + map_count,
            alltim_slot_count=alltim_count, character_count=character_count,
            mmd_boundary_count=mmd_count, map_count=map_count, map_scene_count=scene_count)
        catalogue.log("info", f"Imported {len(rows)} files; {release.release} ({release.confidence}).")
        if progress:
            progress(100, "Import complete")
        return catalogue
    except Cancelled as exc:
        catalogue.set_meta(import_status="cancelled", cancelled_at=datetime.now(timezone.utc).isoformat(),
                           cancellation=str(exc))
        catalogue.log("warning", str(exc))
        raise
    except Exception as exc:
        catalogue.set_meta(import_status="failed", failure=str(exc))
        catalogue.log("error", str(exc))
        raise


def source_state(catalogue: Catalogue) -> tuple[bool, str]:
    meta = catalogue.meta()
    path = Path(meta.get("source_data_path", ""))
    if not path.is_file():
        return False, "Source image is unavailable; catalogue browsing remains available, but export/preview is disabled."
    stat = path.stat()
    if stat.st_size != meta.get("source_size") or stat.st_mtime_ns != meta.get("source_mtime_ns"):
        return False, "Source image metadata changed. Re-import into a new project before reading file content."
    descriptor = Path(meta.get("source_path", ""))
    if not descriptor.is_file():
        return False, "The imported CUE/image descriptor is unavailable. Re-import before reading file content."
    descriptor_stat = descriptor.stat()
    if (descriptor_stat.st_size != meta.get("source_descriptor_size") or
            descriptor_stat.st_mtime_ns != meta.get("source_descriptor_mtime_ns")):
        return False, "The imported CUE/image descriptor changed. Re-import into a new project."
    return True, "Source image identity matches recorded size and modification time."


def layout_from_catalogue(catalogue: Catalogue):
    from .disc import DiscLayout
    meta = catalogue.meta()
    ok, message = source_state(catalogue)
    if not ok:
        raise ValueError(message)
    return DiscLayout(Path(meta["source_path"]), Path(meta["source_data_path"]), meta["sector_size"],
                      meta["user_offset"], meta["start_sector"], meta["sector_count"], meta["source_kind"])
