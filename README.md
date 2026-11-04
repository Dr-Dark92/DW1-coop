# DW1-coop: DW1-settings and DW1-play

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
- Native Digimon package export/import (`.dw1digimon`): one selected resource's MMD (geometry and animations), complete ALLTIM texture slot/palettes, skeleton/animation reference JSON, texture PNG and source provenance. Import validates same-ID, same-skeleton, same-layout edits, previews the resulting texture, and stores removable project overrides. Model/animation previews and patched-disc play consume the overrides. Verified with an unchanged Agumon round trip and a controlled palette edit, and in game: an edited Koromon texture showed on the patched disc after entering a map where Koromon stands. Stage bundles (`.dw1digimons`) hold one package per Digimon of an evolution stage (or all stages) and import all-or-nothing.
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
dw1-settings
```

Alternatively: `python -m dw1_workbench.app`.

There are two apps. **DW1-settings** (`dw1-settings`) is the full workbench: it reopens the game you opened last, and its **Game** menu opens another project, imports a disc or creates desktop shortcuts. **DW1-play** (`dw1-play`, or `python -m dw1_workbench.play_app`) goes straight into the game with the settings from DW1-settings' Play tab, from the Resume slot when there is one, and closes with the game. The two can't run the same game at once, since they share its saves. Both use an Agumon icon rendered once from your own disc and kept on this computer; no game art is bundled. DW1-play has no window of its own for changing settings: use DW1-settings for that.

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

## HD backgrounds (full-size zone pictures)

In **Maps and Zones**, select a map and choose **Set HD picture…**, then pick your upscaled or repainted picture (the whole map, lined up with its background). Importing a zone package with an edited `background-palette0.png` also keeps that picture at full size. Play as usual: the game window's **HD backgrounds** box turns the layer on and off while playing, and maps without a picture look as they always did.

The player compares every finished frame with the game's own background and replaces only the pixels that are background, so characters, the HUD and menus stay the game's, while shadows, fades and the time of day carry over to your picture. The picture isn't converted to 8-bit, shrunk or written to the disc, and it applies only in this player. Animated foreground pieces, effects, art that isn't part of the background, and semi-transparent menu boxes stay at the game's resolution. See `docs/FORMATS.md` for details and measurements.

## Map and HP/MP on the game screen

While you play, the top right of the game screen shows the map: the walkable floor, exits in gold, you in white, your partner in cyan, and other Digimon in red (they chase you), yellow (they watch you) or orange (they ignore you). Your partner's HP and MP sit directly above the game's happiness and discipline bars, in the same bar style with current/maximum values. They slide in and out with the game's own status bars, so they hide in battles, menus and text scenes. The **Map and HP/MP** box in the game window turns them off. F10 still opens the larger Live map panel beside the game for inspection work; it now starts closed.

## Co-op battle test (prototype)

In the Play tab, tick **Co-op battle test**, choose player 2's Digimon and starting level, and tick **Game rules (P6)** so EXP is earned. When an enemy starts a fight with you, player 2's Digimon joins beside your partner. In battle a panel at the bottom of the screen shows it, in the game's own command art, and player 2 uses the same controls as you on the same keyboard: **J/L** move along the commands, **K** confirms, **U** fires the finisher when it's ready. The game's AI picks the techniques, as it does for your partner. Player 2 gains the same EXP and Bits as you (kept in `coop_player2.json` in the save folder), levels up from it, and shares the Digi-Dex. It runs in one game window, for battles only, and is a prototype: there is no item throwing for player 2 yet. Quick saves don't record player 2's progress. If it hits an error, it turns itself off and the game carries on.

**Extended core (8 MB).** A Beetle PSX core rebuilt with 8 MB of RAM gives the game about 6 MB more for models. Build it from the revision of your installed core with `docs/core-8mb.patch` (`make HAVE_LIGHTREC=0`), then choose it as the core in the Play tab. Quick saves from the standard core load on it. Quick saves made on it don't load on the standard core, so keep a copy of any you want to go back to. Memory cards work on both.

**Split screen** is option 10 in the game menu (S). Player 2 chooses a save, which can't be the one player 1 is playing from, and their game starts beside yours. Player 2's keys: **I/J/K/L** D-pad, **U** Cross, **O** Circle, **Y** Square, **H** Triangle, **N** Start, **M** Select, **7/8** L/R. The same option stops it. Player 2 plays a copy of that save in the `player2` folder inside the save folder, so your saves aren't changed. When both games are on the same map, each shows the other player's Tamer and Digimon walking there. It's hidden during your own scripts and battles. The console's 2 MB is nearly full, so on the standard core a Digimon that doesn't fit on a map won't appear: you get a message and the game carries on. An Agumon fits on the maps tried, but a Greymon doesn't. The extended core below fixes this. When either player meets an enemy on a shared map, that game runs the fight with the other player's real Digimon as the ally. The other game pauses and both halves of the screen show the fight. The player whose game runs it uses their own controls; the other moves along the ally's commands with their Left and Right, confirms with Cross, and fires the finisher with Square (player 1: arrow keys, Z, A; player 2: J, L, U, Y). Both get the EXP and Bits, and the enemy goes in both Digi-Dexes. While split screen is on, the **Co-op battle test** setting isn't used. On a shared map the enemies are shared too: one beaten in either game is gone from both, and player 2's enemies stand where player 1's do. Player 2's game takes player 1's clock. Story progress and inventory are still separate, and player 2's game is silent, so a fight player 2 starts has no sound. Option 11, **LAN Co-op**, is a placeholder.

## Game rules (levels, EXP, no aging or death)

In the Play tab, tick **Game rules (P6)** (USA release only) before **Play**. Your partner then:

- starts at level 20 with at least the stats of that level (2,000 HP/MP, 200 Offense, Defense, Speed and Brains); each level adds +100 HP/MP and +10 to the others, up to level 99
- gains EXP for won battles: Rookie 10, Champion 30, Ultimate 90 per enemy, ×1.5 for 2 enemies, ×2 for 3, ×3 for 4
- needs (L + 1)(L + 20) / 2 EXP to go from level L to L + 1 (420 from 20 to 21)
- does not age or die: lifetime, age and lives are held

The play window's status bar shows the level and EXP, and wins and level-ups appear as messages. Level and EXP are kept by the player and with each quick save, not on the memory card. See `docs/FORMATS.md` for addresses and checks.

## Digimon export and import

In **Characters and Digimon**, select the target ID and choose **Export Digimon package…**. Save outside the repository. A `.dw1digimon` file is a ZIP: keep its paths and `manifest.json` intact when repacking after edits.

Edit `model.MMD` within its existing native layout, or edit `texture.TIM`, or paint `texture.png`. PNG import uses the existing palette (palette 0 by default), approximates colours outside that palette, preserves unchanged pixel indices and all CLUT rows, and requires native dimensions and supported alpha (0/128/255). Use native TIM edits to change palette colours. Do not edit both texture representations. Skeleton, names, stats, stages, IDs, animation-reference JSON and provenance are reference data; executable editing and glTF-to-MMD conversion are unsupported.

Select that same ID, choose **Import Digimon package…**, review the changes and decoded texture, then **Apply import**. The package must match the source disc identity. MMD length, object layout, animation slot IDs, sound/texture-copy references, and texture/VRAM layout must remain compatible. Unsupported geometry is rejected explicitly. Re-importing an original unchanged package or **Restore selected Digimon from disc** removes that character's active overrides. Cross-character retargeting is not implemented.

Both **Characters and Digimon** and **Animations** have a class filter (All classes, Newborn (Fresh), In-Training, Rookie, Champion, Ultimate, No class). **Export class bundle…** writes a `.dw1digimons` file for the class the filter shows, or for all classes: a ZIP of standard `.dw1digimon` packages (`packages/NNN-KEY.dw1digimon`) and `bundle.json`. Imported resources are exported in place of the disc originals. Digimon whose model uses primitive types the parser rejects are left out and listed in `bundle.json` (11 of 39 Rookies; 24 of 168 across all stages). To edit one, extract its package, edit it as above, put it back and update its `size`/`sha256` in `bundle.json`, or leave the hash for the import to report it as edited. **Import Digimon package…** also opens bundles: every member is validated like a single package, the review lists each Digimon and its changes, and applying commits all of them in one index write or none. Unchanged members drop any earlier import of that Digimon, so after applying, every listed Digimon matches the bundle.

Enable **Play with imported resources** in the Play tab to build/reuse a patched disc copy containing both zone and Digimon overrides. Start normal play/load a memory-card save so the game reads the changed resources; an older emulator save state may still contain the original assets in RAM/VRAM. No original disc, BIOS or memory-card files are changed by export/import. The game reads a Digimon's texture from ALLTIM each time its model is loaded (`loadDigimonTexture`), so a changed Digimon appears when a map that uses it is entered; the partner and Digimon already on screen keep what they loaded. Checked in game with Koromon in Green Gym.

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

With **Game rules (P6)** enabled (USA), the player replaces the visible post-battle stat panel with EXP gained, resulting level, and EXP remaining to the next level. Normal battle rewards and confirm controls still run. This presentation change has synthetic and rendered-preview validation; real-game verification is pending.

With **Game rules (P6)** and Beetle PSX, automatic natural/random/virus evolution is disabled. Open the in-game Triangle menu and choose **7. Evolution**. A Rookie at **level 21 or higher** can select only its linked Champions; press Cross to select and Cross again to confirm, or Circle to return. The native evolution sequence handles models and bonuses. Level 21 is the current testing threshold; other stages remain pending. Verified with Agumon → Greymon on Windows. Restart the app after updating the code.

**Digi-Dex (USA):** open **8. Digi-Dex** in the P6 in-game menu, or use **F4 / Digi-Dex** in the Windows player. Only battling a Digimon unlocks it; seeing an NPC or sharing its map does not. Entries show stage/rank, possible item drop and chance, known map placements, observed battle hours (day/evening/night), stats for encountered variants, species-linked skills and the skills equipped in the encountered battle. Unproven spawn schedules are marked unknown; decoded placements can depend on story/scripts. Discovery persists per disc in `digidex.json` and is retained when loading an older quick save. Restart the app to load the feature.

The P6 in-game menu now uses a three-column icon grid with the original six native icons and matching Evolution/Digi-Dex icons. Both Circle and Triangle close the main menu; Up/Down move by rows and Left/Right by items. Evolution opens a native-link relation chart from Newborn and In-training through Rookie, Champion and Ultimate; arrows browse forms/stages, Cross selects a linked Champion and Cross again confirms at level 21. Mega remains pending content.

The evolution Tree now follows the original chart layout: focused Digimon portrait in the center, predecessors on the left, colored branches to next forms on the right. Up/Down choose a branch; Left/Right explore backward/forward through the native links; Cross selects a linked Champion and Cross again confirms. Portraits come from the user’s resident game UI atlas. The Tree shows only forms your partner has been while playing in this workbench (recorded per disc in `evolution_history.json`, kept when loading older quick saves) plus the current partner’s next forms as anonymous **???** boxes: they can still be chosen to evolve, but not explored, and get their name and portrait once reached. Forms from before this record started are hidden until reached again. Menu opening, navigation and choosing have short audio cues.

**Digi-Dex avatars:** unlocked entries show each Digimon’s textured model, rendered like the Characters and Digimon tab, in the in-game Dex list, on its pages and in the F4 window. Locked entries show **?**. Avatars are rendered in the background from the disc being played (discovered Digimon first) and cached as PNGs in the player store’s `avatars` folder; the first run takes about a minute, later runs load the cache. 29 of 179 models use geometry the model parser does not yet support and show no avatar.

**Graphics:** the Play tab's **Graphics** row sets the picture before **Play**: resolution (Native, **2x** recommended, 4x may drop frames), **Smooth colours** (turns off the PlayStation's dithering grain, on by default) and **Steady 3D** (PGXP: less wobbly polygons and warped textures, off by default). Higher resolution sharpens the 3D characters, HUD and menus; the backgrounds are pre-rendered pictures and keep their original detail. In the game window, **Screen** picks 4:3, **16:9 fill** (the original shape with a blurred copy of the picture at the sides) or **16:9 stretch**, and **Smooth** blends pixels when the picture is enlarged; both apply at once and are remembered. True 16:9 needs wider backgrounds: the core's widescreen hack only widens the 3D, so it stretches the backgrounds and misplaces characters, and is not offered. Frame pacing now uses a high-resolution timer, which removed the uneven 31 ms / 2 ms frames behind the occasional stutter.

**Regeneration and training EXP (P6):** out of battle your partner regains 1% of its max HP and MP every second of game time (about 100 s from empty to full); it does not run in battle or revive a fainted partner. Each finished gym training session gives +5 EXP on top of its normal stat gains (a status message shows it).

**Digi-Bank:** the 9th icon of the P6 in-game menu. It has 4 slots, each showing the stored Digimon's avatar, name and level; Cross opens its status (stage, level and EXP, HP/MP, Offense/Defense/Speed/Brains, age, weight, then care and equipped techniques on page 2). Cross on the status page, then Cross again, makes it your partner: your current partner takes its slot, and the swap plays the native evolution sequence. **Select new egg** (when a slot is free) offers the four native eggs (Botamon, Punimon, Poyomon, Yuramon); Cross twice hatches it into the bank at level 0. Each partner keeps its own level and EXP; learned techniques stay shared, as in the original game. A Fresh evolves at level 6 and an In-Training at level 12 through **Evolution**. The bank is saved with your level (and with each quick save, so loading one restores its bank).

**Digi-Dex look and skills:** the in-game Dex and the F4 window use the menu's deep blue/gold theme: gold section bands, gold labels with white values, stats in columns, notes in pale blue. Each skill shows its element icon and colour (Fire, Battle, Air, Earth, Ice, Mech, Filth) with its name, power and MP; a coloured dot marks a status effect (purple Poison, pink Confuse, yellow Stun, tan Flat). The F4 window also lists range (Short/Long/Wide/Support) and the status by name. Values come from the game's technique table (`MOVE_DATA`) in RAM while playing; element icons are drawn by the workbench, not copied from the game. Confirmed victory rewards now survive the battle’s map reload and later area changes.
