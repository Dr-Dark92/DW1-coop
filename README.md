# DW1-coop Asset Workbench — milestone 1

A read-only desktop workbench for importing a user-owned Digimon World 1 disc image, validating one evidenced release, inventorying its ISO 9660 filesystem, and preserving resource provenance in a local SQLite catalogue.

This is an asset-analysis foundation. It is **not** co-op gameplay, a native port, or a universal asset decoder. No game image or proprietary game data is included or downloaded.

## Current scope

- ISO, standalone BIN, and CUE/BIN input.
- Explicit 2048-byte cooked sectors and 2352-byte raw Mode 1 or Mode 2/Form 1 sectors.
- Strict CUE validation for one data track, with additional audio tracks tolerated.
- USA `SLUS_010.32` validation using `SYSTEM.CNF` and the named file's `PS-X EXE` header.
- ISO 9660 file inventory with sizes, extents, SHA-256, format detection, bounded hex viewing, and selected-file export.
- Persistent project catalogue and reopen workflow; complete disc data is not copied.
- Standalone standard PlayStation TIM preview and PNG + JSON provenance export.
- Confirmed USA `CHDAT/ALLTIM.TIM` fixed-slot inventory: 180 nine-sector TIM resources with offsets and hashes.
- Verified USA resource-table associations for 180 IDs: internal/display names, same-index textures, and 178 structurally bounded MMD containers.
- Character-resource browser with associated decoded texture preview and PNG + provenance export.
- Bounded static MMD/TMD geometry parsing (textured Gouraud triangles and quads), USA skeleton tables, and the initial pose, shown in an orbitable textured preview. 151 of the 178 USA models are accepted and match an independent converter; the other 27 use unsupported primitive types and are rejected. Agumon was also checked visually on a desktop display. Model export is not implemented.
- Maps and Zones browser: the USA map table (244 named maps grouped into 65 area-name zones), TFS background preview with every palette and PNG + provenance export, and decoded scene data — camera, lights, spawn points, outbound and inbound warps (followable between maps, with exit direction), Digimon placements with stats, sprite objects, and the 100×100 tile-trigger grid — plus a zone relation map laid out by exit direction. All 241 file-backed maps match an independent converter; script-driven transitions are not decoded.
- Thread-safe import cancellation and streamed hashing/file export; partial exports are removed.
- Animations tab: every Digimon with a model, searchable and filterable by evolution stage (from the game's own level byte) and partner range, with a per-clip table (clip names from the game code's own use of each animation ID, technique names for attack clips) and a player using the game's own frame-by-frame stepping (loops, root motion, texture copies); all 4,843 clips of the 178 USA models match an independent converter. Models with unsupported geometry list their clips without a preview.
- Honest unsupported states for model export, sound playback, general archives, map scripts, and 3D map geometry.

See [docs/FORMATS.md](docs/FORMATS.md) for exact support boundaries and evidence.

## Setup and launch

Python 3.11 or newer is required. From this repository:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -c constraints.lock -e '.[dev]'
dw1-workbench
```

Alternatively: `python -m dw1_workbench.app`.

Run tests with:

```bash
pytest -q
```

All tests use synthetic fixtures. The automated suite does not contain or require commercial game data.

## Import workflow

1. Select **Import game image…** and choose an ISO, BIN, or CUE.
2. Choose a new empty project directory outside the source repository (a name ending in `.dw1project` is recommended).
3. Wait for layout inspection, source hashing, release validation, inventory, and per-file hashing.
4. Reopen later with **Open analysis project…**. If the original source is absent or its size/mtime changed, content reads are disabled to avoid silently using stale offsets.

The source is opened read-only. Exports run in a cancellable background worker, never overwrite existing files, and use a temporary file that is removed on cancellation or error. Failed and cancelled imports are recorded as distinct states. Closing the application requests cancellation and waits for active workers; it never forcibly terminates a thread.

## Safety and privacy

- Disc extents, directory records, texture dimensions, and preview sizes are bounded and validated.
- Hashing and exports stream logical 2048-byte data across cooked or raw sectors instead of loading complete resources into memory. Hex preview reads only its 256 KiB prefix.
- CUE references may not escape the CUE directory.
- ISO names are treated as untrusted; extraction is selected-file-only.
- The `.gitignore` excludes common disc images and analysis-project directories.
- Do not place projects or extracted game data in this source tree.

## Research and attribution

The implementation was scoped after inspecting the public descriptions and source-facing workflows of:

- [jype0/dw_decomp](https://github.com/jype0/dw_decomp) — USA disc extraction through `dumpsxiso`, executable decompilation workflow; MIT licensed.
- [SydMontague/DW1-SydPatches](https://github.com/SydMontague/DW1-SydPatches) — USA-version extraction/rebuild and reverse-engineering context.
- [Operation-Decoded/DW1ModelConverter](https://github.com/Operation-Decoded/DW1ModelConverter) — MIT-licensed character-table/MMD association and container research context; its glTF output (revision `17d1a19`) was the independent reference for verifying the static Agumon model.
- [psx-spx CD-ROM format](https://psx-spx.consoledev.net/cdromformat/) and [file formats](https://psx-spx.consoledev.net/cdromfileformats/) — sector, ISO 9660, PS-X EXE, and TIM structures.

No source code from those projects is copied into this repository. Their project descriptions do not establish complete DW1 asset decoding, and this workbench does not claim it.
