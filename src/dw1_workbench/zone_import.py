"""Importing .dw1zone packages (one zone or a bundle) into a project's override layer.

Nothing in the disc image or the catalogue changes. A package is checked first — format, the release it was
made from, which files were edited since export (their SHA-256 no longer matches the manifest), and every map
against the running project: an edited background-palette0.png is converted into the TFS, the MAP
must parse with this disc's map table, the TFS must parse, warps must lead to real maps, and each file must
fit the sectors its original occupies. Files identical to the disc's are recorded as such and not stored.
Changed files are kept under <project>/overrides/, from which a patched copy of the disc is built for play.
"""
from __future__ import annotations

import hashlib
import io
import json
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from .background_import import encode_background
from .errors import DiscFormatError
from .maps import MAP_COUNT, MapEntry, map_exits, parse_map, parse_tfs
from .quicksave import _atomic_write
from .zone_export import PACKAGE_FORMAT, PACKAGE_VERSION

MAX_PACKAGE_FILE = 64 * 1024 * 1024
SECTOR = 2048


class ZoneImportError(ValueError):
    pass


@dataclass
class MapImport:
    index: int
    name: str
    label: str
    files: dict[str, bytes]  # "MAP" / "TFS" -> bytes in the package
    status: dict[str, str] = field(default_factory=dict)  # "unchanged", "changed" or "missing"
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    @property
    def changed(self) -> list[str]:
        return [kind for kind, state in self.status.items() if state == "changed"]


@dataclass
class ImportReport:
    path: Path
    manifest: dict
    maps: list[MapImport]
    problems: list[str] = field(default_factory=list)  # package-level

    @property
    def ok(self) -> bool:
        return not self.problems and all(item.ok for item in self.maps)

    @property
    def changed_maps(self) -> list[MapImport]:
        return [item for item in self.maps if item.changed]


def read_package(path: Path) -> tuple[dict, dict[str, bytes], list[str], set[str]]:
    """The manifest, the files import uses, problems, and the files edited since export (hash differs)."""
    problems: list[str] = []
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ZoneImportError(f"Not a zone package: {exc}") from exc
    with archive:
        try:
            manifest = json.loads(archive.read("manifest.json"))
        except (KeyError, ValueError) as exc:
            raise ZoneImportError("The package has no readable manifest.json.") from exc
        if manifest.get("format") != PACKAGE_FORMAT:
            raise ZoneImportError("This is not a DW1 zone package.")
        if manifest.get("version", 0) > PACKAGE_VERSION:
            raise ZoneImportError(f"Package version {manifest.get('version')} is newer than this workbench supports "
                               f"({PACKAGE_VERSION}).")
        files: dict[str, bytes] = {}
        edited: set[str] = set()
        for listed in manifest.get("files", []):
            name = listed.get("path", "")
            if name.startswith("/") or ".." in name.split("/"):
                problems.append(f"Unsafe path in the manifest: {name!r}"); continue
            try:
                info = archive.getinfo(name)
            except KeyError:
                problems.append(f"{name} is listed in the manifest but missing"); continue
            if info.file_size > MAX_PACKAGE_FILE:
                problems.append(f"{name} is larger than the safety limit"); continue
            data = archive.read(name)
            if hashlib.sha256(data).hexdigest() != listed.get("sha256") or len(data) != listed.get("size"):
                edited.add(name)
            if name.endswith((".MAP", ".TFS")) or name.endswith("background-palette0.png") or name in edited:
                files[name] = data
    return manifest, files, problems, edited


def check_package(path: Path, meta: dict, entries: list[MapEntry], originals: dict[int, dict[str, dict]],
                  room: dict[int, dict[str, int]]) -> ImportReport:
    """Validate a package against this project.

    `originals[index][kind]` is the catalogue row (content_hash, size) of each map's MAP/TFS on the disc and
    `room[index][kind]` the bytes its sectors hold."""
    manifest, files, problems, edited = read_package(path)
    source = manifest.get("source") or {}
    if source.get("executable") and source.get("executable") != meta.get("executable"):
        problems.append(f"The package was made from {source.get('release')}; this project is {meta.get('release')}.")
    elif source.get("source_sha256") and source.get("source_sha256") != meta.get("source_sha256"):
        problems.append("The package was made from a different disc image of this release; import it into a "
                        "project made from that image.")
    maps: list[MapImport] = []
    for listed in manifest.get("maps", []):
        index = listed.get("index")
        if not isinstance(index, int) or not 0 <= index < min(len(entries), MAP_COUNT) or not entries[index].name:
            problems.append(f"Map {index!r} is not in this disc's map table"); continue
        entry = entries[index]
        folder = f"maps/{index:03d}-{entry.name}"
        item = MapImport(index, entry.name, listed.get("label") or entry.name,
                         {kind: files[f"{folder}/{entry.name}.{kind}"] for kind in ("MAP", "TFS")
                          if f"{folder}/{entry.name}.{kind}" in files})
        maps.append(item)
        if "MAP" not in item.files:
            item.problems.append("the package has no MAP file for it"); continue
        for name in sorted(name for name in edited if name.startswith(folder + "/")):
            short = name.rsplit("/", 1)[1]
            if short.endswith((".MAP", ".TFS")):
                item.notes.append(f"{short} edited in the package")
            elif not short == "background-palette0.png":
                item.notes.append(f"{short} edited but ignored: it is generated for viewing"
                                  + (" (edit background-palette0.png for the background)" if short.startswith("background")
                                     else " (edit the MAP file instead)"))
        for kind in list(item.files):
            _set_status(item, kind, originals, room)
        try:
            scene = parse_map(item.files["MAP"], entry)
        except DiscFormatError as exc:
            item.problems.append(f"MAP does not parse: {exc}"); continue
        picture = f"{folder}/background-palette0.png"
        if picture in edited:
            if f"{folder}/{entry.name}.TFS" in edited:
                item.problems.append("both the TFS and background-palette0.png were edited; keep only one")
            elif "TFS" not in item.files:
                item.problems.append("background-palette0.png was edited but the package has no TFS to write it into")
            else:
                try:
                    result = encode_background(parse_tfs(item.files["TFS"]), scene.setup,
                                               Image.open(io.BytesIO(files[picture])), item.files["MAP"], scene,
                                               index)
                except (DiscFormatError, OSError, ValueError) as exc:
                    item.problems.append(f"background-palette0.png could not be converted: {exc}")
                else:
                    item.files["TFS"] = result.tfs
                    item.notes.append("background-palette0.png edited: " + "; ".join(result.notes))
                    _set_status(item, "TFS", originals, room)
                    if result.map is not None:
                        item.files["MAP"] = result.map
                        _set_status(item, "MAP", originals, room)
        if "TFS" in item.files:
            try:
                parse_tfs(item.files["TFS"])
            except DiscFormatError as exc:
                item.problems.append(f"TFS does not parse: {exc}")
        for warp_exit in map_exits(scene):
            target = warp_exit.warp.map_index
            if target >= len(entries) or not entries[target].name:
                item.problems.append(f"warp slot {warp_exit.slot} leads to map {target}, which does not exist")
            elif warp_exit.warp.spawn_index >= 10:
                item.problems.append(f"warp slot {warp_exit.slot} lands on spawn {warp_exit.warp.spawn_index} (0–9)")
        if item.ok and not item.changed:
            item.notes.append("identical to the disc")
    if not maps:
        problems.append("The package lists no maps.")
    return ImportReport(Path(path), manifest, maps, problems)


def _set_status(item: MapImport, kind: str, originals, room):
    data, original = item.files[kind], originals.get(item.index, {}).get(kind)
    if original is None:
        item.status[kind] = "missing"; item.problems.append(f"the disc has no {kind} for this map"); return
    unchanged = hashlib.sha256(data).hexdigest() == original["content_hash"] and len(data) == original["size"]
    item.status[kind] = "unchanged" if unchanged else "changed"
    if not unchanged and len(data) > room[item.index][kind]:
        item.problems.append(f"{kind} is {len(data):,} bytes; its place on the disc holds {room[item.index][kind]:,}")


class OverrideStore:
    """<project>/overrides/: imported MAP/TFS files that differ from the disc, and where they came from."""

    def __init__(self, project_dir: Path):
        self.directory = Path(project_dir) / "overrides"
        self.index_path = self.directory / "overrides.json"

    def _load(self) -> dict:
        try:
            return json.loads(self.index_path.read_text())
        except (OSError, ValueError):
            return {"format": 1, "maps": {}}

    def records(self) -> dict[int, dict]:
        return {int(index): record for index, record in self._load().get("maps", {}).items()}

    def apply(self, report: ImportReport) -> list[MapImport]:
        """Store the changed files of a checked report; maps identical to the disc drop any older override."""
        if not report.ok:
            raise ZoneImportError("The package has problems; nothing was imported.")
        data = self._load()
        stored = data.setdefault("maps", {})
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for item in report.maps:
            files = {}
            for kind in item.changed:
                path = self.directory / f"{item.index:03d}-{item.name}.{kind}"
                _atomic_write(path, item.files[kind])
                files[kind] = {"file": path.name, "size": len(item.files[kind]),
                               "sha256": hashlib.sha256(item.files[kind]).hexdigest()}
            if files:
                stored[str(item.index)] = {"name": item.name, "label": item.label, "files": files,
                                           "package": report.path.name, "imported": now}
            else:
                self._drop(stored, item.index)
        _atomic_write(self.index_path, json.dumps(data, indent=2).encode())
        return report.changed_maps

    def _drop(self, stored: dict, index: int):
        record = stored.pop(str(index), None)
        for info in (record or {}).get("files", {}).values():
            (self.directory / info["file"]).unlink(missing_ok=True)

    def remove(self, index: int):
        data = self._load()
        self._drop(data.setdefault("maps", {}), index)
        _atomic_write(self.index_path, json.dumps(data, indent=2).encode())

    def file(self, index: int, kind: str) -> bytes | None:
        """An imported file's bytes, re-checked against the SHA-256 recorded when it was imported."""
        info = self.records().get(index, {}).get("files", {}).get(kind)
        if info is None:
            return None
        data = (self.directory / info["file"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != info["sha256"]:
            raise ZoneImportError(f"The imported {kind} for map {index} was changed on disk after import.")
        return data

    def replacements(self, entries: list[MapEntry]) -> dict[str, bytes]:
        """ISO path -> bytes for every imported file, for building a patched disc."""
        result = {}
        for index, record in self.records().items():
            entry = entries[index]
            for kind in record["files"]:
                result[entry.map_path if kind == "MAP" else entry.tfs_path] = self.file(index, kind)
        return result
