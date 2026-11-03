# DW1-coop Asset Workbench — milestone 1

A read-only desktop workbench for importing a user-owned Digimon World 1 disc image, validating one evidenced release, inventorying its ISO 9660 filesystem, and preserving resource provenance in a local SQLite catalogue.

This is an asset-analysis foundation with a Play tab that runs your own disc through a libretro PlayStation core you install yourself. It is **not** co-op gameplay, a native port, or a universal asset decoder. No game image, BIOS, emulator core, or proprietary game data is included or downloaded.

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
- Play tab: runs the imported disc through a user-supplied libretro PlayStation core (Beetle PSX recommended) loaded in-process from Python, on Linux and Windows. Video, audio, keyboard input, pause, reset, fast-forward, full screen, nine quick-save slots with screenshots and notes (F5/F8), an automatic Resume slot on close, launch straight into a slot, a persistent memory card, and a launch log. For the USA release it reads the current map, player, partner and NPC positions and the live tile grid from console RAM, shows them in the status bar and a live map, and records zone inspections (F9) with screenshot, position and save state. The disc is only read; saves live in the user data folder.
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

## Playing the game

The Play tab needs two things you supply:

- **A libretro PlayStation core.** Beetle PSX is recommended. On Arch/CachyOS: `sudo pacman -S libretro-beetle-psx` (installs `/usr/lib/libretro/mednafen_psx_libretro.so`, which the tab finds automatically). On Windows, use `mednafen_psx_libretro.dll` from the libretro buildbot or a RetroArch `cores` folder.
- **Your PlayStation BIOS.** Point the BIOS folder at the folder containing it. The USA disc needs a North American BIOS (`scph5501.bin`, `scph1001.bin`, `scph7003.bin`, `scph7001.bin` or `scph101.bin`); the game has been run with `SCPH1001.BIN`.

Press **Play**, or select a quick save and press **Play from selected slot**. In the game window F1 lists the controls: arrows for the D-pad, Z/X/A/S for Cross/Circle/Square/Triangle, Enter for Start, Backspace for Select, F5/F8 to quick save/load the selected slot, F6/F7 to change slot, Tab (held) to fast-forward, P to pause and F11 for full screen. Type a note (for example the zone) before saving to label the slot.

**Zone inspection** (USA release): the status bar shows the map you are in, your tile and its trigger (walkable, blocked, exit through warp slot N, toilet, script), and whether the game's live tile grid matches the decoded MAP file. F10 toggles a live map of that grid with you (white), your partner (cyan) and the Digimon the game has on the map: red chases you, yellow turns to watch you, orange ignores you. Each chaser's or watcher's box (where it notices you) is drawn dashed, and solid while it is reacting; the list under the map says which start a battle on contact, and the status bar warns when one is after you. Ground items (green diamonds, named) and chests (gold, grey once taken) are drawn too, the status bar says when an item is in reach, and every ground item seen is logged per map and tile; Maps and Zones lists and marks those observed spawns, since the map scripts that place items are not decoded yet. Maps and Zones draws the same posts and boxes on each map's floor grid for every placement the MAP file lists. F9 records an inspection: pick what it is (gateway, toilet, object, path, NPC, other), add a note, and the screenshot, position, live grid and a save state are kept. Inspections are listed in the Play tab (play from one to return to the spot, or open its map in Maps and Zones) and in each map's scene details. They are observations, kept apart from decoded data.

Quick saves and the memory card are stored per disc image (keyed by its SHA-256) under the user data folder: `~/.local/share/DW1-coop/play/` on Linux and `%LOCALAPPDATA%\DW1-coop\play\` on Windows. Quick saves are core save states, so they only load in the same core; the memory card works like a real one. Gamepads, key remapping and core options are not configurable yet.

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
