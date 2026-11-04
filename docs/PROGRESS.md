# Milestone progress

## Complete

- PySide6 first-launch import/open-project experience.
- Cancellable worker thread with progress and actionable failure reporting.
- Thread-safe cancellation event delivered directly from the GUI, distinct cancelled metadata/UI state, and close-time worker draining without forced termination.
- ISO/BIN/CUE inspection with explicit supported sector layouts.
- Bounded ISO 9660 inventory and USA DW1 release validation.
- SQLite catalogue with source identity, parser versions, hashes, statuses, logs, and association-ready schema.
- Reopen workflow and stale/missing source protection.
- Disc browser, selected-file export, and 256 KiB hex view.
- Sector-aware streaming hashes and atomic background exports with cancellation cleanup; bounded prefix reads for hex preview.
- Standard standalone TIM preview and palette/STP-aware PNG + provenance JSON export.
- USA `CHDAT/ALLTIM.TIM` fixed-slot decoding into 180 catalogue children with exact provenance.
- USA executable character-resource parsing: 180 stable IDs/internal names, 179 verified display-name labels, same-index ALLTIM associations, and 178 validated MMD paths/boundaries.
- Characters and Digimon catalogue view with verified names/evidence, associated texture preview, and PNG + provenance export.
- Bounded static MMD/TMD parser (modes `0x34`/`0x3c`), USA skeleton table, and initial-pose reading, with strict rejection of unsupported or malformed data.
- Cancellable background model loading and an orbit/zoom textured preview with wireframe and untextured modes.
- Static Agumon (ID 3) verified against `DW1ModelConverter` `17d1a19` output and confirmed visually on a desktop display.
- Worker progress and results delivered to the GUI thread through bound slots (fixes a Wayland segfault that offscreen tests did not show).
- USA map table, TFS background, and MAP scene parsing; maps catalogued with area-name zones, scene/background files, warp links, and Digimon-placement links.
- Maps and Zones browser: searchable zone tree, background preview per palette with PNG + provenance export, scene data, followable outbound warps (walk-through exit and direction, or script/table-only) and inbound "linked from" maps, and a colour-coded tile-trigger grid.
- Zone relation map: 65 zones linked by warps, laid out by exit direction, with pan/zoom and open-in-browser, built in a cancellable background worker.
- All 241 file-backed maps verified against `DW1ModelConverter` `17d1a19` map output.
- Animation clips: bounded parser and an exact frame stepper following the decompiled game code; playback with clip selection, play/pause/step/restart, optional root motion, and animated texture copies in the model preview.
- All 4,843 clips of the 178 USA MMDs verified against `DW1ModelConverter` `17d1a19` glTF animations.
- Animations tab: Digimon list with search, evolution-stage and partner-range filters, a per-clip table, and the shared model/animation player (also used by Characters and Digimon); unsupported geometry still lists clips. Confirmed on the desktop by the user.
- Animation clip names from the game's own use of each animation ID (Digimon and player sets, with confidence and evidence) and technique names for clips 46+ via each Digimon's move list; 94% of USA clips named.
- Evolution stage parsed from the executable's level byte and catalogued as a `stage` label; the GUI falls back to the executable for older projects.
- Play tab (Python libretro frontend): loads a user-supplied libretro PlayStation core through `ctypes` and runs the imported disc on a worker thread at the core's frame rate, with Qt video and audio, keyboard input, pause/reset/fast-forward/full screen, nine quick-save slots plus an automatic Resume slot (atomic writes, screenshots, notes, SHA-256 and core checks), launch-into-slot, a persistent memory card and a launch log. Tested against a synthetic C core built during the test run, and run by the user on the desktop with Beetle PSX 0.9.44.1 and `SCPH1001.BIN`: boot, quick save, quick load, Resume slot and memory card confirmed.
- Live zone inspection (USA): current map, player/partner tile and the live tile grid read from console RAM using dw_decomp addresses, shown in the play window's status bar and a live map; the live grid matched the decoded MAP floor grid on all 10,000 tiles of the two maps checked. F9 inspections (category, note, screenshot, position, trigger, live grid, compressed save state) are listed in the Play tab and in Maps and Zones, and the game can be started from one.
- Hostile Digimon: follow modes decoded (ignores/watches/chases, tracking box around the post), shown on each map's floor grid in Maps and Zones and, live, with HP, reacting state and whether contact starts a battle (autotalk), plus a status-bar warning; checked against a ModokiBetamon in the running game.
- Items: the 128 item names from the executable; live ground items and chests on the live map, in reach hints, stored with inspections, and an observed item-spawn log per map and tile shown in Maps and Zones (map scripts, which place items, are not decoded). Checked with a Digimushroom in Native Forest.
- Zone pathing and `.dw1zone` export and import (single zone or bundle): walkability from the game's collision rule, spawn-to-exit routes, a cross-map route planner; packages with original files (hash-checked), decoded data, images, pathing and routes; import with validation into a project override layer, and play through a patched disc copy with regenerated EDC/ECC. Unchanged round trip byte-identical; a one-tile edit confirmed in the running game.
- P4 first native single-Digimon package path: `.dw1digimon` export, background import validation/review/apply, same-ID model/texture overrides, imported model/animation preview, original restore, and combined zone/Digimon patched-disc building. Agumon native model/texture round trip is byte-identical; a controlled palette edit persists/reopens, appears in the preview and matches the patched ALLTIM bytes without altering the other 179 slots or original BIN. Verified in game: an imported Koromon palette edit loaded from the patched disc when entering Green Gym (VRAM held only the edited palette; the change appeared on Koromon and nowhere else).
- P4 stage bundles (`.dw1digimons`): export every supported Digimon of an evolution stage (or all) as native packages in one file, with unsupported models listed and skipped; import validates each member and applies all of them with one index write or none. Real disc: 144 of 168 staged Digimon packaged and re-imported unchanged; a bundle edit produced the same patched `ALLTIM.TIM` as the in-game check.
- World zoom (USA, 50–100%) — **switched off after the user's desktop test crashed the game and misplaced the player and NPCs**: an in-game background tile engine (C compiled for the R3000, installed live into unused RAM) keeps its own ring of tiles downscaled to the drawn size in the game's tile VRAM, streams them as the camera moves and draws them; foreground pieces and the projection scale around the screen centre, matching the game's drawing-offset switch at ordering-table entry 32. At 99.6% the engine reproduces the native frame; indoor and outdoor (walking) captures, save/load while zoomed and return to native verified. Black beyond map edges; camera clamps and object culling still assume 320×240.
- P6 game rules (USA, in the player): partner levels 0–99 from battle EXP (Rookie 10, Champion 30, Ultimate 90; ×1.5/×2/×3 for 2/3/4 enemies; (L+1)(L+20)/2 EXP per level), start at level 20, +100 HP/MP and +10 other stats per level, aging and death off; kept with quick saves. Checked in the running game, including a real won battle from the user's inspections (+10 EXP for one Rookie). Manual Rookie-to-Champion evolution is now available at level 21 through option 7 of the in-game menu, restricted to native links and confirmed by the player. Automatic natural/random/virus evolution is guarded. Agumon → Greymon verified through the native sequence on Windows, with separate native/guarded automatic-evolution comparisons; user saves unchanged. Other stage thresholds, Fresh/In-Training EXP, Digimon storage and stat balance remain pending.
- HD backgrounds (USA, in the player): a full-size picture per map replaces the game's background pixels in each finished frame, keeping characters, HUD, shadows, fades and time of day; nothing patched, on/off while playing. Checked on the real game standing, walking, in a battle through the win, and at night on `MAYO00`. The user set a picture on File City `TWNA02` and reports the result on the desktop as much better.
- Catalogue-backed empty/unsupported category views.
- Synthetic tests for valid/invalid layouts, missing CUE tracks, malformed/truncated input, traversal rejection, persistence, cancellation recovery, safe export, and known TIM output.

- Digi-Dex (USA): only initialized battle opponents unlock entries; persistent per-disc knowledge, anonymous locked rows, native stage/rank, drop/chance, variant stats, linked/equipped techniques, possible decoded locations and observed battle hours. In-game option 8 plus Windows F4/search dialog. Real ModokiBetamon battle and rendered UI verified; script-controlled spawn schedules remain unknown and source placements are conditional.

## Partial

- Digimon imports: existing relative MMD layout/length, executable skeleton, animation slot/reference compatibility and texture VRAM layout only. PNG edits use the existing palette with approximation reported. Unsupported primitives (24 staged Digimon, including Gabumon, are left out of bundles), arbitrary glTF conversion, new skeletons, larger models and character retargeting are not supported. Native package export is distinct from GLB/glTF model export.



- CUE: one data track only; audio is described but not processed.
- ISO 9660: common primary-directory traversal only.
- Release validation: product-code and executable-header evidence, not a published full-disc hash match.
- TIM: standard standalone images only; no automatic embedded/container scan.
- Executable analysis: identity/header, character resource tables, skeleton tables, and the map table only.
- MMD static geometry: 151 of 178 models accepted and matching the reference converter; 27 rejected for unsupported primitive types (translucent, flat-shaded, untextured, unlit) or invalid normal indices.
- Maps: sprite objects are parsed but not rendered; embedded map TIMs are located, not previewed; door models are not shown; palette time-of-day meaning is unverified; script-driven transitions are missing from the relation map until map scripts are decoded.

## Not implemented

- General game archive unpacking beyond the fixed ALLTIM layout.
- Model export (GLB/glTF with provenance).
- Files larger than their original sectors (would need an ISO rebuild); an editor for zone contents (packages are edited outside the workbench).
- Standalone TMD decoding.
- Sound playback for animation cues, and the per-type texture cycling outside animation data.
- Map scripts (needed for script-driven transitions) and 3D map geometry.
- Audio/video decoding.
- Disassembly/decompilation integration.
- Player: gamepad input, key remapping, core options UI, and audio-driven frame pacing (pacing is clock-based; audio is dropped rather than delayed).
- Co-op gameplay, native runtime, or networking.

## Real-disc validation (user-provided USA image)

Validated read-only through its CUE on 2026-11-02:

- raw Mode 2/Form 1, 2352-byte sectors; one data track;
- `SYSTEM.CNF` boots `SLUS_010.32`, whose file has a PS-X EXE header;
- 818 ISO directory-record files inventoried; 1,178 total catalogue resources after 180 ALLTIM children and 180 character-resource records;
- extension counts: 242 `.MAP`, 242 `.TFS`, 178 `.MMD`, 66 `.TMD`, 52 `.TIM`, 18 `.BIN`, 7 `.VHB`, 4 `.STR`, and 9 other files;
- reopen/source identity passed; a direct raw-sector read of `SYSTEM.CNF` matched both catalogue SHA-256 and streamed export;
- all 52 `.TIM` files decoded with the standard decoder; ALLTIM's 180 fixed slots decoded;
- ID 3 was confirmed as internal `AGUM`, display name `Agumon`, texture slot 3, and `CHDAT/MMD0/AGUM.MMD`; its texture decoded as 256×128, 4 bpp, 16 palettes and was PNG/JSON-exported outside Git;
- 178 MMD headers/boundaries validated; IDs 62 (`wEAG`) and 114 (`tAKA`, empty display-name field) have no matching MMD path and remain explicitly absent;
- reopening the final catalogue, a streamed `SYSTEM.CNF` export with catalogue-hash equality, and the Agumon offscreen GUI texture preview passed;
- source BIN SHA-256: `06084490ea0db34099b31495ccba4ec18fb0ff25c7683c508e9d2db93d4495f4`;
- CUE SHA-256: `82b297b9b499fea9c7f361a497b933b7f971394148212eca03e4798ddc0c98d1`.

Static Agumon model, checked from the same image on 2026-11-02:

- the workbench parse matched the glTF output of `DW1ModelConverter` `17d1a19` on skeleton, all 690 ordered triangle corners, material, initial pose, world-space assembly, and per-face palette texels (details in [FORMATS.md](FORMATS.md#static-agumon-verification));
- the desktop preview (Wayland) was inspected by the user and confirmed correct and fully textured after fixing two GUI defects found there: an off-thread repaint segfault and 30 dropped zero-area-UV faces.

All MMD models, compared the same way on 2026-11-02:

- 178 MMD files extracted with catalogue-hash equality and converted by the pinned converter;
- 151 accepted by the parser; all match on skeleton, ordered corners, materials, initial pose, and world-space assembly (largest difference 0.00014 units); 135 also match every centroid texel, and the 180 differing samples in the other 16 are explained by the converter's single-palette texture map;
- 27 rejected for primitive types outside the supported subset or invalid normal indices (details in [FORMATS.md](FORMATS.md#all-model-comparison));
- only Agumon was inspected visually on a desktop display.

Maps and zones, checked from the same image on 2026-11-02:

- a fresh import catalogued 244 map records (241 decoded, 3 without files) in 65 zones, 449 warp links, and 709 Digimon-placement links; resource count 1,422;
- all 241 file-backed maps and 512 background images matched the converter's map export (details in [FORMATS.md](FORMATS.md#maps-and-zones));
- the Maps and Zones tab was inspected on the desktop by the user, who could not find zones linked south of Green Gym. Cause: only the warp table was shown, and Green Gym's other routes are script-driven or inbound. Search, inbound links, exit directions, the decoded tile-trigger grid, and the zone relation map were added, exercised offscreen, and then confirmed correct by the user on the desktop.

Animation clips, checked from the same image on 2026-11-02:

- all 178 MMD animation tables parsed (4,843 clips, none rejected) and compared with the converter's glTF animations over 5,686,560 sampled channel values plus every sound and texture event (details in [FORMATS.md](FORMATS.md#animation-clips));
- with real-valued velocities every clip matches the converter; with the game's integer stepping, differences stay within rounding except the documented root-motion rule for animation IDs 35 and 36;
- Agumon's clips were played in the model preview (walk cycle, looping idle with texture copies, root motion), offscreen and then on the desktop, where the user confirmed playback looks correct.

This validates this supplied USA image and implemented paths, not every retail revision, region, malformed disc, path-table-only entry, rejected models, map scripts, or sound. Automated coverage remains synthetic and contains no proprietary data; the converter comparisons are manual checks kept outside Git.
