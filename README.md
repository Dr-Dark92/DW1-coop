# DW1-coop Asset Workbench — milestone 1

A read-only desktop workbench for importing a user-owned Digimon World 1 disc image, validating one evidenced release, inventorying its ISO 9660 filesystem, and preserving resource provenance in a local SQLite catalogue.

This is an asset-analysis foundation with a Play tab that runs your own disc through a libretro PlayStation core you install yourself. It is **not** co-op gameplay, a native port, or a universal asset decoder. No game image, BIOS, emulator core, or proprietary game data is included or downloaded.

Camera zoom status: **switched off.** The USA World zoom worked in offscreen tests, but in desktop play it crashed the game and misplaced the player and NPCs, so the slider is disabled (`WORLD_ZOOM_ENABLED` in `player.py`) and nothing patches the running game. The tile engine and notes are kept for later work (see docs/FORMATS.md).

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
- Native Digimon package export/import (`.dw1digimon`): one selected resource's MMD (geometry and animations), complete ALLTIM texture slot/palettes, skeleton/animation reference JSON, texture PNG and source provenance. Import validates same-ID, same-skeleton, same-layout edits, previews the resulting texture, and stores removable project overrides. Model/animation previews and patched-disc play consume the overrides. Verified with an unchanged Agumon round trip and a controlled palette edit; normal-game asset reload remains to be checked.
- Bounded static MMD/TMD geometry parsing (textured Gouraud triangles and quads), USA skeleton tables, and the initial pose, shown in an orbitable textured preview. 151 of the 178 USA models are accepted and match an independent converter; the other 27 use unsupported primitive types and are rejected. Agumon was also checked visually on a desktop display. GLB/glTF export is not implemented; native MMD is included in Digimon packages.
- Maps and Zones browser: the USA map table (244 named maps grouped into 65 area-name zones), TFS background preview with every palette and PNG + provenance export, and decoded scene data — camera, lights, spawn points, outbound and inbound warps (followable between maps, with exit direction), Digimon placements with stats, sprite objects, and the 100×100 tile-trigger grid — plus a zone relation map laid out by exit direction. All 241 file-backed maps match an independent converter; script-driven transitions are not decoded.
- Thread-safe import cancellation and streamed hashing/file export; partial exports are removed.
- Animations tab: every Digimon with a model, searchable and filterable by evolution stage (from the game's own level byte) and partner range, with a per-clip table (clip names from the game code's own use of each animation ID, technique names for attack clips) and a player using the game's own frame-by-frame stepping (loops, root motion, texture copies); all 4,843 clips of the 178 USA models match an independent converter. Models with unsupported geometry list their clips without a preview.
- Play tab: runs the imported disc through a user-supplied libretro PlayStation core (Beetle PSX recommended) loaded in-process from Python, on Linux and Windows. Video, audio, keyboard input, pause, reset, fast-forward, full screen, nine quick-save slots with screenshots and notes (F5/F8), an automatic Resume slot on close, launch straight into a slot, a persistent memory card, and a launch log. The World zoom control is present but disabled (it crashed the game in desktop play). For the USA release it reads the current map, player, partner and NPC positions and the live tile grid from console RAM, shows them in the status bar and a live map, and records zone inspections (F9) with screenshot, position and save state. The disc is only read; saves live in the user data folder.
- Zone pathing and export: a Pathing view per map (walkable areas by the game's collision rule, routes from each arrival point to each exit and toilet, a route planner across maps over walk-through exits), **Export zone or bundle…**, which writes one zone or a bundle of zones as a `.dw1zone` package (original MAP/TFS files with hashes, decoded scene data, rendered backgrounds and grids, pathing, map-to-map routes, links in and out; no play-time observations), and **Import zone or bundle…**, which checks a package against this disc and stores changed MAP/TFS files as project overrides. To change a background, edit `background-palette0.png` in the package (any size with the same shape): import scales it to the PS1's resolution, fits it to 256 colours, re-tints the other time-of-day palettes and repaints the foreground layer (bushes and branches drawn in front of characters) from the same picture where those pieces are cut-outs of the background; animated pieces, light and mist effects, snow particles and separate foreground art keep their original look, and the import notes list what was kept. Hand-edited MAP/TFS files need no manifest update. **Play with imported resources** in the Play tab builds a patched copy of the disc (with regenerated sector error-correction codes) and plays it; the original image is never modified, and files must fit their original sectors.
- Honest unsupported states for GLB/glTF export, sound playback, general archives, map scripts, and 3D map geometry.

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

## Digimon export and import

In **Characters and Digimon**, select the target ID and choose **Export Digimon package…**. Save outside the repository. A `.dw1digimon` file is a ZIP: keep its paths and `manifest.json` intact when repacking after edits.

Edit `model.MMD` within its existing native layout, or edit `texture.TIM`, or paint `texture.png`. PNG import uses the existing palette (palette 0 by default), approximates colours outside that palette, preserves unchanged pixel indices and all CLUT rows, and requires native dimensions and supported alpha (0/128/255). Use native TIM edits to change palette colours. Do not edit both texture representations. Skeleton, names, stats, stages, IDs, animation-reference JSON and provenance are reference data; executable editing and glTF-to-MMD conversion are unsupported.

Select that same ID, choose **Import Digimon package…**, review the changes and decoded texture, then **Apply import**. The package must match the source disc identity. MMD length, object layout, animation slot IDs, sound/texture-copy references, and texture/VRAM layout must remain compatible. Unsupported geometry is rejected explicitly. Re-importing an original unchanged package or **Restore selected Digimon from disc** removes that character's active overrides. Stage bundles and cross-character retargeting are not implemented.

Enable **Play with imported resources** in the Play tab to build/reuse a patched disc copy containing both zone and Digimon overrides. Start normal play/load a memory-card save so the game reads the changed resources; an older emulator save state may still contain the original assets in RAM/VRAM. No original disc, BIOS or memory-card files are changed by export/import. The native Agumon round trip, patched ALLTIM bytes, EDC/ECC and imported preview were checked; in-game character reload after an edit still needs verification.

## Safety and privacy

- Disc extents, directory records, texture dimensions, and preview sizes are bounded and validated.
- Hashing and exports stream logical 2048-byte data across cooked or raw sectors instead of loading complete resources into memory. Hex preview reads only its 256 KiB prefix.
- CUE references may not escape the CUE directory.
- ISO names are treated as untrusted; extraction is selected-file-only.
- The `.gitignore` excludes common disc images, analysis-project directories and native Digimon packages.
- Do not place projects or extracted game data in this source tree.

## Research and attribution

The implementation was scoped after inspecting the public descriptions and source-facing workflows of:

- [jype0/dw_decomp](https://github.com/jype0/dw_decomp) — USA disc extraction through `dumpsxiso`, executable decompilation workflow; MIT licensed.
- [SydMontague/DW1-SydPatches](https://github.com/SydMontague/DW1-SydPatches) — USA-version extraction/rebuild and reverse-engineering context.
- [Operation-Decoded/DW1ModelConverter](https://github.com/Operation-Decoded/DW1ModelConverter) — MIT-licensed character-table/MMD association and container research context; its glTF output (revision `17d1a19`) was the independent reference for verifying the static Agumon model.
- [psx-spx CD-ROM format](https://psx-spx.consoledev.net/cdromformat/) and [file formats](https://psx-spx.consoledev.net/cdromfileformats/) — sector, ISO 9660, PS-X EXE, and TIM structures.

No source code from those projects is copied into this repository. Their project descriptions do not establish complete DW1 asset decoding, and this workbench does not claim it.
