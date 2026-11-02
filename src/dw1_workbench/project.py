from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable

from . import __version__
from .catalog import Catalogue
from .disc import inspect_image, sha256_file
from .iso9660 import hash_entries, identify_release, read_inventory


def resource_id(path: str, content_hash: str) -> str:
    return "disc:" + hashlib.sha256((path + "\0" + content_hash).encode()).hexdigest()[:24]


def import_project(source: Path, project_dir: Path, cancelled=lambda: False,
                   progress: Callable[[int, str], None] | None = None) -> Catalogue:
    project_dir = project_dir.resolve()
    if project_dir.exists() and any(project_dir.iterdir()):
        raise ValueError("Project directory must be empty; use Open Project to resume an existing project.")
    catalogue = Catalogue(project_dir)
    catalogue.initialize()
    catalogue.set_meta(import_status="in_progress", source_path=str(source.resolve()),
                       tool_version=__version__, parser_versions={"iso9660": 1, "cue": 1, "tim": 1},
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
            category = "textures" if fmt == "TIM" else ("executable" if fmt == "PS-X EXE" else "disc")
            rows.append((resource_id(entry.path, digest), entry.path,
                         str(PurePosixPath(entry.path).parent), None, entry.size, entry.extent,
                         fmt, digest, "identified" if fmt != "unknown" else "unknown", category))
        catalogue.replace_inventory(rows)
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
            completed_at=datetime.now(timezone.utc).isoformat(), file_count=len(rows))
        catalogue.log("info", f"Imported {len(rows)} files; {release.release} ({release.confidence}).")
        if progress:
            progress(100, "Import complete")
        return catalogue
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
