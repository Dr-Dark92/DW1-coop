# Handover

## Windows graphics and P6 validation — 2026-11-06

- The Windows 8 MB Beetle PSX core was built from revision `71ee81e` with `docs/core-8mb.patch` using MSYS2 MinGW64 and `make -j8 HAVE_LIGHTREC=0 platform=win`. Digimon World booted and reported 8,388,608 bytes of RAM. The DLL lives outside the checkout under `D:/DW1-coop-data/core-8mb/beetle-psx-libretro`. Configure it in Play settings. The current Windows app reads `%USERPROFILE%/.dw1-coop/settings.ini`. Saves were backed up before switching cores.
- Battle background handling from `500d21c` uses native rendering throughout battle. The same boundary now covers the entire native evolution sequence, including Digi-Bank swaps. The ×2 renderer is suspended before the sequence runs, HD work is bypassed, and the palette is refreshed when the field returns. The native transformation fade is preserved. Both frozen inspection states pointed into the map-rendering path. Disposable replays from Slot 1 completed Agumon → Greymon and Agumon → Punimon via the bank. The latter stored Agumon and restored ×2. The user confirmed the final background fix in desktop play and reported improved performance on the preceding build.
- Native Forest MAYO01 inspection `20261105-183812-282202` exposed a pixelated strip at 4× with ×2 framing. A tile row ending at texture coordinate 256 was clipped and stretched. Moving the row to a slot whose endpoint fits eliminated the strip. HD match coverage on the replay rose from 76% to 92%.
- The player now wraps core frame bytes directly when it can present them without writing. Menus and results use writable images, and the split-process transport packs padded rows. A 1280×960 microbenchmark measured about 1.11 ms saved per wrapped frame, with equal pixels. The Windows battle profile previously measured 19.7 ms at the 95th percentile at 4×, including 16.0 ms in the core alone. This is not a full desktop frame-pacing benchmark. No quality settings were reduced by the optimization.
- **Compare HD and original (new game)** starts from power-on. The left pane uses project imports and selected graphics, and the right pane uses the original disc at native resolution and dithering with a temporary memory card. Both receive the same controller input and only the left plays audio. Timing and gameplay can drift between the two emulator processes. Windows smoke testing verified live frames in both panes at 4× and native resolution. A full recording run remains unverified. This is a desktop feature. The separate four-zone export was intended for the online version and was not imported.
- Windows suite results were 402 passed and 18 skipped, with the pre-existing avatar subprocess test deselected. Its fixture uses `os.write` on a Windows multiprocessing pipe handle and fails before it can simulate the intended child exit.

## Uncommitted menu / Dex follow-up — 2026-11-05

Menu additions use code-authored shaded pixel-art icons; native icons unchanged. Dex has Overview, Skills, Decoded locations and Observed encounters tabs, numeric-ID search, discovered-only filtering and bounded in-game pages. No progression/unlock/save changes.

USA source verified read-only: ID80 GOBR = green Goburimon, ID103 SNGB = blue SnowGoburimon. ID125 EGOB is a separate source-named Goburimon resource. Missing green avatar was strict MMD rejection of 0x3E. Avatar-only opt-in accepts validated 0x36/0x3E packets with source palettes; strict model/export support unchanged. PS1 framebuffer blending remains approximate. Restart to regenerate the versioned avatar cache; do not delete saves.

Full suite: **418 passed in 11.25s**, command below. Source renders80/103/125 succeeded; green/blue visually inspected. No independent complete geometry comparison this follow-up. Private outputs: `/home/drdark/dev/DW1-coop-data/goburimon-avatar-review-1/`, `goburimon-avatar-review-2/`, `menu-theme-review-2/`. Helper: `tools/preview_menu_theme.py --help` (read-only inputs, exclusive external output directory). Dex captures use existing cached portraits; new green portrait checked separately.

Next: desktop acceptance after restart, including Goburimon and all Dex sections. Wide weapon poses can still cross thumbnail edges; translucent blending needs independent verification before general model export support. No commit/push. Older test counts below are historical.

## Current state (2026-11-05)

Branch `python-player`. 412 tests pass:

```bash
QT_QPA_PLATFORM=offscreen PYTHONPATH=src /home/drdark/.venv/bin/python -m pytest -q
```

Use `PYTHONPATH=src`: the venv's editable install still points at an old copy in `/home/drdark/src`. To fix that, and get the `dw1-play` and `dw1-settings` launchers:

```bash
/home/drdark/.venv/bin/python -m pip install -e /home/drdark/dev/DW1-coop
```

Run the game with:

```bash
PYTHONPATH=src /home/drdark/.venv/bin/python -m dw1_workbench.play_app
```

**What's new, all in this push:**

- ×2 camera frame, experimental, off by default in the code. The user's own `~/.dw1-coop/settings.ini` has it on. See [FIXED_FRAMING.md](FIXED_FRAMING.md), which also lists the known issues.
- Resolution menu back with 4×, for quality. `saved_resolution` carries over the short-lived `play/fixed_x2` setting once.
- LVL and NEXT on the Status screen (`status_panel.py`).
- Codex's runtime stability work, [P0_RUNTIME.md](P0_RUNTIME.md), with five fixes after review: one read of `hd_layer` per frame, painting outside the compositor's lock (`Compositor(paint=)`), the 640 × 480 button leaving full screen properly, the picture loader surviving any error, and a pipe rather than a queue for avatars so a renderer's death can't hang the player.

**How the user tests:** they play in the real window and make inspections with a note, in `~/.dw1-coop/play/06084490ea0db340/inspections`, with `launch.log` beside them. Each has `screen.png` and `inspection.json`, and `state.zz` loads through `InspectionStore.state`. `screen.png` is the plain frame before HD composition, so it looks softer than what they saw. Saves can stop matching a disc: use the disc in the latest `launch.log` entry.

**Next, in order:**

1. The menu staying up about a second after closing, and the floating Digimon. Both are in the known issues with their inspections.
2. Try a group fight: the battle fix hasn't been seen with three or more enemies. The user was asked for an inspection just before one.
3. The review's leftovers: the 32-megapixel limit in `load_picture` rejects an 8× upscale of the largest maps; scene state is read several times per frame; HD tests use `_load_picture` rather than the runtime `PictureCache`; the World zoom plumbing in `player.py` is unreachable.
4. Then the earlier plan: shared story progress and items in co-op, item throwing, LAN, and importing Digimon from Digimon World 3.

## Fixed 90% camera proof (2026-11-05, superseded by the ×2 frame above)

Final suite **376 passed in 11.15 s**, including six new proof regressions; source compilation, generated MIPS instruction/hazard checks and `git diff --check` pass.

Read [FIXED_FRAMING.md](FIXED_FRAMING.md) for exact evidence, commands, failed candidates and limitations. User authorized building the first small-framing proof against their inspection **check screen size 1** (`20261105-053603-355650`). Implemented `fixed_framing.py`, fixed-mode C projection/foreground entries and edge-clamped shared affine anchor; regenerated checked MIPS code. `tools/compare_framing.py` uses a disposable 8 MB core, source inspection/disc read-only, external captures. Legacy mode reproduced misplacement. Corrected mode keeps projection RAM native (1511), hooks rendering before models, scales GTE H to 1357 and compensates origin. Final standing capture visually aligns, exposes extra scenery and removes the edge strip. 37 shared complete game-clock ticks have identical player/partner/NPC world coordinates. Equal-vblank comparisons differ due one game-frame startup cost, preserved in report. HUD bar probe matches but clock-face probe doesn't; full HUD equality unverified. No normal-play option, no HD compositor integration, no proven scratch reservation, no walking/battle/transition acceptance. **WORLD_ZOOM_ENABLED stays False.**

Latest comparison `/home/drdark/dev/DW1-coop-data/fixed-framing-clamped-2/`: `native-matched-clock.png`, `fixed90.png`, `report.json`. Prior baseline/failed candidates preserved outside Git. Tests cover render-only hooks, strict state fields, restoration/guards and independently checked actual C affine helper. Next: user visual review, then scratch ownership/moving-camera/HUD safety, HD integration and transition/battle gates. Preserve all preceding P0/display/checkbox changes; no commit/push.

## Display framing and 640×480 view (2026-11-05)

Final full synthetic suite: **370 passed in 10.02 s**, outside sandbox for Qt audio initialization. `git diff --check` passed.

User screenshot showed 16:9 blurred-side fill and requested framing/size work. Measured installed Beetle core before/after loading the copied File City inspection: reports aspect **1.2190476655960083**, despite 640×480 frame pixels. `graphics.screen_rects` previously used that ratio even for the control named 4:3, explaining the narrower centre image. This is a display policy mismatch, not evidence that the core's pixel-aspect correction is invalid.

Explicit **4:3** and the inner game in **16:9 fill** now use 4:3. Added **Core aspect** to retain the emulator-reported correction; intentional **16:9 stretch** unchanged. `PlayPanel` adds **640 × 480 game view**, available while playing: selects 4:3 and resizes the game's presentation area, accounting for window chrome. Refuses single-view sizing during split screen. **No additional world scenery or camera zoom is implemented**, and no game-coordinate/projection changes. World zoom stays disabled. Restart settings to access the new controls; current screen preference isn't changed behind the user's back.

New tests: non-4:3 core aspect cannot override named 4:3/fill; Core aspect preserves it; real offscreen Qt layout has exact 640×480 viewport/target and no started core/camera; split sizing guarded. Added `--view-640 --capture` to `tools/profile_player.py`. Real-core offscreen run with copied File City inspection, 2×, four seconds, reports both view and game size 640×480. Inspected capture at `/home/drdark/dev/DW1-coop-data/p0-runtime/framing-640/view-95870102968806.png`; telemetry alongside. Proprietary capture outside Git, normal saves untouched. Desktop user confirmation pending. All prior P0/checkbox/concurrent test edits preserved. No commit/push.

Next camera task remains distinct: reproduce prior small-zoom misalignment on copied states, establish safe memory/consistent background, foreground and character transforms before considering any fixed wider-world framing. Do not confuse this display-only sizing with that unfinished camera work.

## Fixed ×2 checkbox, zoom slider removed (2026-11-05)

**Superseded:** the user asked for 4× back, so the resolution menu (Native, 2x, 4x) is restored on `play/resolution`. `saved_resolution` carries over a `play/fixed_x2` choice once, then removes the key. The zoom slider stays removed.

Validation: full synthetic suite **368 passed in 10.34 s**, including two new checkbox/persistence/legacy-migration tests; `git diff --check` passes. Restart DW1-settings to see the new control; resolution applies on the next game launch. No desktop visual acceptance claimed for the new checkbox yet.

`play_panel.py`: removed the World zoom slider/status/connection. Replaced the resolution dropdown with **Fixed ×2 rendering (640 × 480)**, using `play/fixed_x2`. On selects core `2x`, off selects `1x(native)`; next launch only, including DW1-play's shared launch path. Existing native preference defaults off; previous 2×/4× defaults on without overwriting the legacy resolution key. Smooth colours/PGXP remain independent. No zoom patch re-enabled, camera/framing/positions unchanged. This does **not** implement the wider scenery discussed with the user; the label/tooltip explicitly distinguish rendering from camera zoom. Existing experimental camera code retained, no new in-game camera code. Tests: `tests/test_fixed_x2.py` covers options, persistence, legacy settings and slider absence. All prior P0 changes and the concurrent save-note test edit preserved; no commit/push.

## P0 runtime stability (2026-11-05, Codex, Linux)

Read [P0_RUNTIME.md](P0_RUNTIME.md) first for exact evidence, baseline/current timings, private report paths, commands and pending acceptance matrix. Starting HEAD remains `f51a7c56fa0d53dd78a64c0893bb9ed2e487709d` on `python-player`; no commit, push, merge or change to `main`.

Implemented bounded opt-in telemetry, same-map environment snapshot retention across battle-clobbered metadata, correct byte-width GAME_STATE reads, transition-native fallback, generation-tagged asynchronous jobs with stale-result rejection, 128 MiB picture LRU/one latest-only loader/known-layout successor preload, 64 MiB tint budget, same-call VRAM serialization reuse and bounds checking, and one low-priority avatar rendering subprocess with cancellation/cleanup. World zoom stays **off**. Core integration, gameplay/progression/battle rules and save formats are preserved.

External data: `/home/drdark/dev/DW1-coop-data/p0-runtime/` contains matched native/2×/4× File City measurements, battle replay reports, final post-battle native/HD PNGs, avatar contention comparisons and real Qt smoke reports. No proprietary data was added to Git. Old scripts in `DW1-coop-data/dev-tools` have hard-coded obsolete disc/settings paths: inspect before use. Use the new tracked `tools/profile_runtime.py` and `tools/profile_player.py` with explicit arguments and temporary save stores instead.

Measured: no material steady-state compositor speedup (about 7/11/13 ms medians at native/2×/4×). Isolating avatars reduced core p95 from 3.872 to 2.249 ms in the matched cold-avatar test; one sample pair, not a universal FPS guarantee. One real battle replay returned to exploration with a visible, aligned HD environment; 11 native-frame hashes match baseline. Offscreen Qt tests exercise real worker/compositor, HD toggling, cold avatar generation, disposable slot-9 save/load and clean shutdown. Desktop/vsync/audio, natural rapid zone round trips, consecutive win/defeat fights and split-screen remain pending. Do not mark P0 complete.

A separate edit to `tests/test_player.py` appeared during this run (save-note test updated to shared settings instead of removed UI). Preserved, not reverted or claimed as this batch's implementation. Keep all current uncommitted changes. Final suite results are recorded in P0_RUNTIME.md/PROGRESS.md.

Final validation: **366 tests passed in 10.45 s**; full suite outside sandbox for Qt audio. `git diff --check` and `compileall` passed. Original BIN SHA-256 unchanged. Both 2× and 4× offscreen real Qt smoke completed; see the detailed report for actual timing/RSS and limitations.

Next task: instrument **natural** A → B → A exits on a copied store and the correct patched disc at 2×/4×, verify picture cache reuse and transition screenshot pairs; then consecutive battles, defeat/recovery and split-screen. No user originals may be overwritten. Do not re-enable zoom or publish without authorization.

## Characters, title screens and shared data (2026-11-04, Claude, Windows 11, branch `python-player`)

User asked for: a playable Tai, full detail instead of a reduced model, 2 re-rendered zone bundles imported, frame rate input, title screen editing at full quality, a smaller game window, no resume on exit, and the two ways of starting the app made the same.

**The trap that cost the most time.** Claude Code here runs inside the Claude desktop app's MSIX package, so everything it starts sees a private HKCU and `%LOCALAPPDATA%`. "The user's settings" and "the user's saves" as read from these tools were for a long time that private copy, and the user's own shortcut was running with imports off on the original disc. See the section below. Now that data is in `~/.dw1-coop` the two agree, but keep test runs on a copy: set `DW1_COOP_HOME`.

What changed, beyond the two sections that follow:

- `zone_import.py`: a picture counts as repainted when it isn't what the TFS renders, unless the TFS itself was hand-edited. `MapImport.skipped` keeps a damaged map out without blocking the package, and without dropping that map's earlier import.
- `title_screens.py` (TIM encoding and `overrides/title`) and `title_hd.py` (`TitleLayer`). A frame is a title when 60% of it matches the TIM at one brightness, since there's no map in RAM. The game's fade is a multiply, its 255-to-256 texel stretch needs one column of tolerance, and only the first 240 rows show: `TITLE256` rows 241-250 hold the Press Start text the game tints green. A picture within 1% of the file's whole shape keeps every row; before that, 1402 x 1122 was cropped 8 rows at each end.
- `player.py`: `framerate` in `PlayConfig` paces the loop and `stretched_audio` keeps the sound in step. `_is_repeat` skips all image work on a picture identical to the last, since the game draws about 20 a second out of 60. It always counts when a workbench menu or a visitor is drawn over the picture, and at least every 15 frames for the HUD. `_read_hud` now assigns `_hud` in one step: the compositor's thread reads it, and an empty moment made the bars flicker in about 5% of pictures.
- `play_panel.py`: Game screen, Quick saves, During a game and Title screens groups. `PlayWindow` keeps the behaviour; `quick_save_changed` and `zoom_reported` bring its changes back to Settings. The slot and note are in settings as `play/slot` and `play/save_note`.
- `launch_directly` always powers on. `play/resume` now defaults to off. An old Resume slot isn't deleted and can be opened from the Quick saves list.

Outside Git: `D:/DW1-coop-data/tai` (`convert_tai.py`, `install_tai.py`, `check_full.py`, `check_zones2.py`) and `D:/DW1-coop-data/title` (`check_hud_walk.py`, `check_speed.py`, `check_noresume.py`). Backups of the overrides from before each bundle are in `D:/DW1-coop-data/backup-overrides-before-bundle-2` and `-3`. On the first move to the shared folder, Claude's private saves went to slots 8 and 9 and `play/<disc>/private-copy`; the user's real saves kept their own slots.

Open:

1. The user reports the HP/MP bars missing while walking on a map. They draw on every picture in `check_hud_walk.py` on the same save and settings, standing and in all four directions, without a real window. A screenshot and the map name were requested. Besides the flicker fix, nothing here explains it yet.
2. The speed fix hasn't been confirmed in the user's own window. Composing still takes about 38 ms at 4x (27 ms at 2x) on its own thread. If it still feels slow: run the compositor in another process (the machine has 32 logical CPUs, and this code holds the GIL part of the time), or use 2x. The core's software renderer is single-threaded; a hardware renderer would need a GL context in this front end.
3. Re-render the 4 damaged pictures. The 8 MB core is still unbuilt.
4. Tai's joints: skinning would need a PC-side renderer, and there's no depth information from the core to composite it with.

## Where settings and saves live (`shared_data.py`)

Settings are in `~/.dw1-coop/settings.ini`, saves in `~/.dw1-coop/play`. They were in the registry and AppData, but the Claude desktop app on Windows is an MSIX package: anything started from it, Bash and PowerShell tools included, gets a private copy of HKCU and of `%LOCALAPPDATA%` under `Packages\Claude_*\LocalCache`. The same command then had different settings and saves depending on who ran it, and test output described the private copy, not the user's. `D:` and the home folder aren't redirected. Set `DW1_COOP_HOME` to run against another folder; `tests/conftest.py` does.

## Playing as another character (`tamer_skin.py`)

The Tamer is replaced in RAM and VRAM while the game runs, because he is loaded only at a new game or memory-card load and a quick save holds its copy. Writes must match what the game did at load: `as_loaded` makes offsets absolute, sets the texture page, and writes each polygon run's length into its first packet. A wrong run length hangs the renderer.

Memory, measured on map 168 with `_stack_addr` 0x801FFF00 and `_stack_size` 64 KB:

- The Tamer slot holds 15,048 bytes of model before the animations, which must stay where they are.
- 0x801BFC00-0x801D5F00 looks empty but is free heap. The next Digimon a map loads can land there, so do not use it.
- 0x801EFF00 up is the bottom of the reserved stack, out of the heap's reach. `detail.bin` goes there, up to 48 KB. Marker bytes from 0x801FBF00 to 0x801FE000 stayed untouched through 6 map changes and a full battle.
- Draw buffer is 80 KB a frame. Full Tai (2,228 triangles, 894 pairs as quads) peaked at 40 KB in the world against 29 KB for the default Tamer, and 55 KB in a battle with Terra Force. Busier scenes have not been measured; nothing guards an overflow.

The title pictures work the same way as HD maps but keyed by frame content, in `title_hd.py`: a frame is a title when 60% of it matches the TIM at one brightness, since there is no map in RAM to read. The game's fade is a multiply, and its 255-to-256 texel stretch needs one column of tolerance.

Each body part still moves rigidly, since a TMD object has one bone. Tai's converter and data live outside Git in `D:/DW1-coop-data/tai`, and his files are installed in the project's `overrides/characters/tai`.

## Controllers and the Play tab (2026-11-04, Claude, Windows 11)

User: controller mapping for both players, reorganise the UI; WarGreymon hidden from the egg list or a toggle.

- `controls.py`: `WindowsPads` (winmm `joyGetPosEx`) and `LinuxPads` (`/dev/input/js*`), no new dependency. Inputs are named `b0`, `a1-`, `h0up`; `PlayerControls` maps each game button to a list of inputs, saved as JSON in `controls/players`. `controls_view.py`: the Controls page, with Set capturing the next new input, ignoring what was already held (a trigger axis at rest).
- `PlayWindow._poll_pads` reads on the GUI thread every 8 ms while the window is active; `_pad_button` applies the same routing as the keys in split screen and shared fights. Scan: 107 ms once, then 0.2 ms every 2 s. Mapping is read when a game window opens.
- Play tab split into Play, Settings, Controls and Log; it opens on Settings when the core or BIOS isn't ready. `play/offer_forms` (off) controls the egg list only.
- Only a fake controller was tested, plus a real scan with none connected. Not tested: a physical pad, on either system, or player 2's pad with real split screen. Keyboard keys stay fixed.

## WarGreymon from DW3: first test (2026-11-04, Claude, Windows 11)

User's tool: `D:\DW3\dw3tool` (local, not on GitHub; European disc SLES-03936, battle model `DAT/FIGHT/MODEL/M213WGRE.BIN`, 23 bones). Scripts and output outside Git: `D:/DW1-coop-data/dw3-wargreymon/` (`convert.py`, `check_game.py`, `out/`, `game*/`, `patched/`, 365 MB). No project code changed.

- **Result:** WarGreymon as ID 40 on a patched disc: in the real core it loads in the native evolution scene, stands, and follows the Tamer through Andromon's walk, with no crash or freeze (2× and swap from the user's Resume, read-only, map 168). Original disc and user store unchanged.
- **Conversion:** 21 body meshes baked into Andromon's idle pose (scale 414/6027, same axes in both games), objects mapped by hand in `PARTS`; a forearm is split at 62% of its height for the hand bone. The sphere (mesh 22) and ground quad (mesh 21) are left out; what the game uses them for is unknown. Uses Andromon's skeleton and clips: parts turn around Andromon's joints, not WarGreymon's. 576 vertices, 666 polygons.
- **Fit:** the file must stay in the original 44 sectors (90,112 bytes); it is 89,474. Room came from coarser normals (`NORMAL_STEP` 0.34), removing all 197 of Andromon's texture copies (they would overwrite the texture; the clips are otherwise identical, checked) and pointing slots 13, 22-25, 27, 28 at Idle. Texture: 15 palettes; 57 polygons past row 127 get their own copy of their texels in free space.
- **Own idle:** DW3 motions are in entry 0 of `M213WGRE.BIN` (archive of `MotionStep` lists: index, count, frame, end frame; 0x7FFF ends; end frame 0 is a blend), and keys per bone in pos/rot/scale tracks with global frame numbers ending at 30000 (`wgr_pose.py`). Motion 1 (frames 10-169) is the idle. `convert.py` writes it as one looping DW1 clip for slots 0 and 33: 6 keys, 32 ticks apart, closing the loop exactly. Any rigid motion fits Andromon's skeleton: helper bones stay still and each object's bone gets the exact rotation (Rx*Ry*Rz, verified against `_local_matrix`) and translation. Through `AnimationPlayer` it stays within 8 units of the target. Real game: clip 0 played for all 360 samples.
- **No DW3 walk exists:** the battle model has only battle moves (`motion-strips.png`, rows 1-25); motion 16 looks like a forward flying dash, 10 frames looping, not converted. Walking stays Andromon's, whose root motion moves the partner; another clip would have to keep that. Hunger and other condition clips are Andromon's too. Stance at a shallow DW3 crouch.
- **In the apps:** `install.py` writes the result into the project's `DigimonOverrides` (`D:/DW1/overrides/digimon`, record `form_name`), bypassing the import checks, which require the original layout. Removable in Characters and Digimon. With imported resources on, `Workspace._imported_forms` -> `PlayConfig.forms` -> `EvolutionController.forms`: the egg list offers it, `bank.imported_form` makes it level 45 (`IMPORTED_FORM_LEVEL`, a test choice) with that level's stats, and `_name_forms` replaces the name in RAM's `DIGIMON_DATA`, so any Andromon in that game shows as WarGreymon with this model. The real path passed (`check_real_app.py`: project, patched disc with File City, hidden Workspace, menu by button presses; stats 4500/450, bank saved; user saves are copies). Converter and data stay outside Git, so a new machine needs `convert.py` and `install.py` again.
- **Sounds and skills:** the record's `form` (`install.py`) holds name, `sounds_from` 12, `special` [Fire, Battle, none] and 16 moves; `EvolutionController._apply_forms` keeps them in RAM's tables (`DIGIMON_DATA` special +30, moves +35; `DIGIMON_VLALL/VBALL_SOUND_ID`) and, while the form is the partner, sets its techniques in the shared `learnedMoves`. So other partners that can use those moves have them too once it has been active. Voice bank is the same for both (10); effect bank 0x24 -> 0x0c. Andromon's clips call cries 53-55 in bank 4, which MetalGreymon's also has; its steps (21, 22 in bank 1) become MetalGreymon's (13, 15) in `convert.py`. Heard by nobody yet: the tests run without sound output.
- **List:** slots 0-4 Megaton Punch, Counter, Buster Dive, Tremar, Dynamite Kick; 5-9 Prominence Beam, Fire Tower, Magma Bomb, Spit Fire, Heat Laser; 10-13 Red Inferno, Infinity Burn, Meltdown, Muscle Charge; 14 finisher Mega Flame (Greymon's). Each slot plays Andromon's clip for what was there, so they are matched by range. Three of 17 don't fit: War Cry, Sonic Jab, the weaker Dynamite Kick (44).
- **Effect whitelist (root cause of attacks that did nothing):** each EFE file has a table of `{type, bone, x, y, z}` for the species allowed to use it; `BTL_checkTechCompatibility` stops the script for any other, so no effect and no damage. An earlier `check_battle.py` run changed moves mid-battle, when effects are loaded at the start, so it proved clips and stability only: its "14 techniques played" says nothing about effects or damage. The species comes from `getOriginalType`, a RAM table (`ORIGINAL_DIGIMON_TYPES` `0x8012A84C`). `_apply_effect_species` sets it during battle to the form's `effect_species`: default Greymon (allowed on 12 of the 15, `efe_tables2.py`), MetalGreymon while Megaton Punch, Buster Dive or Tremar is seen queued. A ranged technique queues and starts within one frame, hence a default. Bones come from Greymon's entries, on Andromon's skeleton; `calculateBoneMatrix` falls back to bone 0 past the bone count.
- **Terra Force:** DW3's sphere (mesh 22, bone 23, parent bone 22) is the energy ball. Motions: 51 charge, 52 hold, 53 throw (grows to 3x, flies). DW3's particles run on its own effect system and don't transfer. Finisher clip (slot 60, 45 frames as Andromon's, `TERRA_FRAMES`): the game holds frame 1 through the 240-tick charge-up, so that is the orb overhead; then the throw and recovery. The orb is an icosahedron (20 faces, the disc has no room for 216), object 15 on helper node 4, set in RAM's skeleton table (`skeleton_objects`) and hidden in the chest (radius 30) in every other clip. The clip scales node 4 up to 7.9x (int16 limit 8), and its child arm gets the inverse. After the throw the node keeps that scale and goes 7000 under the ground: changing the scale put parent and child out of step for a moment, a huge arm on screen. Texture: DW3's fire quadrant shrunk to 20 x 20, the room left after moved polygons share copies. `finisher` shows Mega Flame's effect as "Terra Force" at power 500 only while the form is the partner (name kept in move 110's unused text; pointer and power restored otherwise). `model_view` skips objects no node names.
- **Full fights** (`check_fight.py`, map 109, the project's patched disc, ModokiBetamon raised to 3000-4000 HP, level 45, techniques equipped before the battle, AI choosing them, gauge filled once, MP topped up): five runs. Terra Force landed each time, 1085-1207. Won with +10 EXP and returned to the map in four; `45,40,48` ran out of test time at low enemy HP. Damage seen: Prominence Beam, Magma Bomb, Spit Fire, Red Inferno, Meltdown, Megaton Punch, Buster Dive, Tremar. **No damage:** Infinity Burn (24 casts), Heat Laser (5), Dynamite Kick (31), Fire Tower (1 cast only). Counter needs an attacker and Muscle Charge is support; neither judged. No crash or freeze. Control: native Greymon's Prominence Beam and Mega Flame in the same scene. The camera stays with the Tamer the test teleports, so many frames don't show the fighters.
- **Open:** the four techniques above; walking was only checked at low zoom; pink specks on the shields not compared with DW3; enemy Andromon uses this model and skills too; the evolution chart has no route to it; sound not heard. Terra Force's look is one low-poly orb with a 20 x 20 texture and DW1's fire trail.
- **Noise after the sequence was a test mistake, not a game bug.** The user's Resume on map 168 was made with their imported File City zone (`play/use_imports` is on), but the test disc didn't include it. `readMapTFS` then correctly loaded the disc's original tiles, drawn with the edited palette still in VRAM. On a disc with the imports (`probe_override.py`, `patched-zones/`) RAM equals the imported TFS before and after the swap, and scrolling is clean. Test harnesses must load a state on the disc it was made with. The same noise is possible for a player who turns off imported resources and then loads such a save, until the map is reloaded; no code was changed for this.
- **Found, not caused by the conversion (control with the real Andromon on the original disc):**
  A swap into an Ultimate shows "Tamer level went up!!!": `EVL_applyEvolution` calls `addTamerLevel` (100% Ultimate, 20% Champion) unless `HAS_USED_EVOITEM`. Digi-Bank swaps can therefore farm Tamer level.

## DW1-settings and DW1-play (2026-11-04, Claude, Windows 11)

User: remember the mounted game; the current app is DW1-settings; another app runs the game directly from its configuration; Agumon icon.

- `MainWindow(settings)` saves `app/last_project` in `show_workspace`; `app.main` calls `open_last_project` (a failure leaves the welcome page, a status message and the remembered path). `MainWindow()` remembers nothing, so tests can't touch the real settings. New **Game** menu.
- `play_app.py`: builds a hidden `Workspace` and calls `PlayPanel.launch_directly` (Resume, else power on), so it uses exactly the Play tab's settings, patched disc and names. Quits on `PlayPanel.player_closed`.
- `player.lock` (`QLockFile`, stale only when the holder died) in the store: added on my initiative, since the apps share saves and two games would overwrite each other's memory card. Split screen's player 2 uses its own store.
- `app_icon.py`: `avatars.render_character` at 256 px to `<data>/DW1-coop/icons/agumon.png` and `.ico`, not in Git; `set_app_id` for the Windows taskbar; `create_shortcuts` writes both `.lnk` files with PowerShell.
- `pyproject`: gui-scripts `dw1-settings`, `dw1-play` replace `dw1-workbench`; rerun `pip install -e .`.
- Real check (`D:/DW1-coop-data/windows-validation/check_play_app.py`, project `D:/DW1`, saves and settings copied): window in 3.3 s, frames running, rules on, second game refused, lock released, exit 0, Resume written to the copy, user store unchanged. Offscreen, so the taskbar icon and shortcuts' look are for the user to confirm. `app/last_project` was seeded to `D:\\DW1` and the shortcuts created on the user's desktop. Tests: `tests/test_apps.py`.

## Windows check of the CachyOS work (2026-11-04, Claude, Windows 11, branch `python-player`)

The six commits from HD backgrounds to the shared world were made on Linux (CachyOS). Run here on the real core with copies of the user's saves (user store hashes unchanged). Scripts and captures outside Git: `D:/DW1-coop-data/windows-validation/` (`check_split.py run|same|kill|nocache|hd|hd4`, `check_hd.py`). `dev-tools` and `saves-before-8mb` are not on this PC.

- **Fixed: player 2 outlived player 1.** `os.getppid()` keeps its value on Windows after the parent dies, and player 2's parent is the venv's `python.exe` launcher, not player 1. Player 1 now passes its id and `split_screen.parent_watch` waits on a process handle. Hard-killing player 1 left player 2 running before; it now stops itself (seen gone on a later check; the exit time was not measured).
- **Fixed: about 9 fps while avatars render** on a store with no cache. Rendering holds the GIL; `sys.setswitchinterval(0.0002)` while it runs gives about 58 fps.
- **Fixed: HD tint tables truncated** instead of rounding (`b6bb995`); the failing test showed it here.
- **Works:** split screen with both games at 2× (player 1 p99 24 ms, player 2 59.8 fps), world link, clean stop (0.17 s, exit 0); HUD; HD backgrounds in the right places.
- **Open: HD backgrounds display at about 30 fps at 2× and 22 at 4×** on the indoor map 218 with a flat test picture; the game keeps 60. Compose takes about 27 ms alone with the emulator stopped, so it is plain numpy cost, not timing or the GIL. Linux recorded about 57 at 4× on another map; no same-map comparison exists.
- **Open: the 8 MB core has no Windows build.** MSYS2 with gcc and make is installed and the source is at `D:/DW1-coop-data/core-8mb/beetle-psx-libretro` (`71ee81e`, the installed core's revision; the patch's `git apply --check` passes, not applied). The tool's safety check blocked compiling downloaded code from the session, so the user builds it (`make -j8 HAVE_LIGHTREC=0 platform=win` in the MSYS2 MinGW64 shell) or adds a permission rule. On the standard core map 218 refuses the visitor with the expected memory message, so a visitor walking in the other game is unverified on Windows. Back up the Windows saves first.
- Not run on Windows: a shared fight, the co-op battle test, gamepads.

## Next stages (2026-11-04, agreed with the user)

The user tested split screen: it runs, but the two games are separate and the players can't see each other. Next, in order:

1. **Shared world.** Keep two emulators. Each publishes zone, position and facing over the split-screen shared memory. In the same zone, each game spawns the other's Digimon in a free NPC slot and moves it; in different zones it is removed. First test done (`visitor.py`, `dev-tools/shared_check.py`, see FORMATS.md): player 2's Digimon appears and walks in player 1's game, and the other way round, for an Agumon. The console's heap has only 40-64 KB free with an Agumon visitor, so the user asked for more memory: the extended 8 MB core (`docs/core-8mb.patch`, FORMATS.md) adds about 6 MB to the game's heap, and Greymon now fits. The standard core refuses with a message. The user's original saves were copied to `DW1-coop-data/saves-before-8mb` before switching, because saves made on the new core don't load on the standard one. The Tamer shows too. User's test (mark 20261104-084304, slot0 in player2): both games fought separately on map 109, each with its own ModokiBetamon, so the user chose "one game runs it" (FORMATS.md, Shared fight), now built. The user then asked for one session: beaten enemies, enemy positions and the clock are now shared on the same map. Story flags, items and Bits aren't; the trigger table is the next piece, and joining two saves at different story points needs a decision. That run also had the Co-op battle test on, so player 1's ally was the Greymon from that setting, not player 2's partner.
2. **Co-op battles and shared progress.** A fight in a shared zone brings in the other's Digimon as the ally. Then sync Bits, items and story flags. Item throwing: either player to either Digimon, from the shared inventory (decided, not built).
3. **LAN Co-op** (menu option 11 is a placeholder): to be discussed.
4. **Digimon World 3 import.** Merge the evolution charts into: in-training-1 > in-training-2 > Rookie > Champion > Ultimate > Mega > Mega+, for example Botamon > Koromon > Agumon > Greymon > MetalGreymon > WarGreymon > Omnimon. WarGreymon and Omnimon come from DW3 with their animations and signature moves. First test: WarGreymon alone, standing and walking, on Andromon's base (the user's choice of closest form), possibly with Andromon's animations at first. Nothing about DW3 has been examined: disc formats, skeletons, move effects and table limits are all open. DW3 data stays outside Git.

## Co-op: ally battle test (2026-11-04, Claude, Linux, branch `python-player`)

User's plan: two players in the same town with shared progress; same zone means a co-op battle using the game's multi-fighter battles, otherwise each plays normally. Design so far: two emulators side by side (one core per process); a co-op battle runs in one game, the other pauses and shows it, and results go back to both. No project code exists yet.

Test (`DW1-coop-data/dev-tools/coop_ally.py`, throwaway run, real battle code, the user's "near end battle" inspection, MAYO00): added the map's other ModokiBetamon to the running battle as slot 2 (`entityIds[2]`, `ENEMY_COUNT = 2`, fighter record copied from slot 1 with flags 0, target 0xFF), and each frame set `FIGHTER_FLAG_10` plus `targetId` from outside; no game code patched. Results: three fighters run without a crash; enemy aimed at slot 0 hits the partner (3000 to 2689), aimed at slot 2 hits the ally (3000 to 2924). With only the ally having MP the ally spent 240 MP, the enemy lost HP and the ally landed the kill. The battle does not end while the ally stands; setting the ally's HP to 0 gives a normal win (`wins` +1) and a working map afterwards.

Second test, a different species (`DW1-coop-data/dev-tools/coop_species.py`, throwaway run from slot 2, Native Forest, BIOS folder with only `SCPH1001.BIN`): no existing game code changed. A one-shot `WORLD_OBJECTS` (`0x80137418`) entry points at a small routine in unused RAM (`0x801C0000`) that calls a game function on the game's own thread, then removes itself. Order matters: `loadNPCModel` (`0x800B6110`) first, about 70 frames, or `scriptSetDigimon` (`0x800B6118`) crashes on a missing model; before it, a copy of a `MAP_DIGIMON_TABLE` entry with the species, follow mode 0 and the post beside the Tamer. A real battle then starts through `callScriptSection` (`0x80105B14`) with `TALKED_TO_ENTITY` set and the hostile's script section; its dialogue needs Cross. With `FLEE_DISABLED[0]` set during the start, `handleBattleStart` brings the on-screen ally in by itself, so effects and sounds load natively (`ids [1, 5, 2, 0]`, the real BATTLE banner). Greymon: with the partner and enemy out of MP it spent 192 MP and the enemy lost 273 HP; win when dropped, map and Tamer normal afterwards. Meramon, enemy aimed at it: lost 543 HP. Limits: NPC model slots are 5; the world-object routine is the first code this work adds to the console's RAM.

Third test, orders (`DW1-coop-data/dev-tools/coop_control.py`, a scripted player 2, not real input): Greymon against two ModokiBetamon, ally plus both enemies brought in by `handleBattleStart` (`ids [1, 5, 2, 6]`; `FLEE_CHANCES` `0x8012BB14` set to 100 for the start, off-screen NPCs need isOnMap and a position held until then). Written between frames: move by weights in `movesPrio` (+64: 100 for the chosen, 1 for the others), target by `FIGHTER_FLAG_10` plus `targetId`, hold by charge mode 2 (+86), `FIGHTER_FLAG_ON_CHARGEUP` and cooldown 0x50 (+40 in the fighter record). 1,500 frames per order: move A at enemy 1, 3 attacks of 3 with A, enemy 2 untouched; move B at enemy 1, 2 with B after 1 A queued before the order; move A at enemy 2, enemy 1 untouched; hold, no attacks and no MP spent (enemy 2 lost 24 HP then, thought to be the tail of the previous hit, not confirmed); move B at enemy 2, 2 of 2. All 9 attacks went to the ordered target. An order applies from the next decision, so it can lag one attack; about 3 attacks per 1,500 frames with these NPC stats. Win and map normal afterwards.

Built from these: `coop.py`, Play tab **Co-op battle test** (see FORMATS.md). User decided player 2 gains the same EXP, coins and Digi-Dex. User asked about item throwing; recommended either player to either Digimon from the shared inventory, not yet decided or built; native items go through `startThrowingItem` and their effects write `PARTNER_ENTITY` directly (`handleHPHealingItem`, `item.c`), so items on the ally need their own effect handling.

Then, from the user: player 2 has player 1's controls, AI chooses techniques (J/L, K, U replace the earlier number keys). Split screen is menu option 10, LAN Co-op option 11 as a placeholder "to be discussed later" (see FORMATS.md). Item throwing decided: either player can throw to either Digimon, from the shared inventory; on hold, not built. Split screen is two separate games: joining them into shared progress and same-zone battles is the next design step. Dev checks: `dev-tools/split_check.py`, `dev-tools/coop_play.py`.

Open: natural contact on the desktop (the test script triggers the battle); finishers; gamepads; a player 2 command path; enemies should pick targets sensibly; ally defeat rules. An attempt to start a real two-enemy fight by moving NPCs failed (`coop_setup.py`; touching an enemy is not reproduced by writing positions). Note: `slot0`–`slot2` were made under `SCPH1001.BIN` and freeze when `scph5501.bin` is found, so some earlier scripts measured a frozen game there; the HUD walking and menu checks used the File City inspection and are unaffected.

## On-screen map and HP/MP (2026-11-04, Claude, Linux, branch `python-player`)

User (AFK): map at the top right as part of the game screen, not the sidebar; HP and MP above the happiness and discipline bars; match the theme. `hud_view.py`, `EmulatorWorker._read_hud` and `_publish`, toolbar **Map and HP/MP**, `play/hud`. Scripts outside Git: `DW1-coop-data/dev-tools/hud_check.py`, `hud_check2.py`, `hud_menu.py`. The map can cover something at the top right (Koromon in Green Gym).

## HD backgrounds (2026-11-04, Claude, Linux, branch `python-player`)

User asked to force 16-bit backgrounds; after the numbers (FORMATS.md) and a before/after they said the target is PC only with plenty of resources, so the full-size layer was built instead. `hd_background.py` (MapBackground, compose, BackgroundLayer, Compositor, HdPictures); `PlayConfig.hd_pictures`; toolbar **HD backgrounds**; Maps and Zones **Set HD picture…** / **Remove HD picture**; zone import keeps an edited picture in `overrides/hd/`. New dependency: numpy. Scripts outside Git in `DW1-coop-data/dev-tools/hd_*.py`; `hd_gate.py`, `hd_e2e.py`, `hd_pace.py` are the useful ones. dw_decomp is now at `DW1-coop-data/references/dw_decomp` (aaf69a4).

The user tried it on the desktop with a rebuilt picture for File City `TWNA02` (map 168) and reports much better results. Their inspection there, noted "check it", has not been looked at.

Follow-up the same day: per-block compare (4× from 23 to about 57 fps) and a foreground model (`read_foreground`) fixing the streaks on File City's roof. Not committed when written.

Next: effects, animated pieces and semi-transparent boxes stay low-res; only `MAYO00` and `TWNA02` were measured, indoor and small maps untested; other video modes pass through. Avatar rendering (`avatars.py`) holds the GIL on a store with no cache (first launch on this Linux store): frame rate about 30 for about a minute, unrelated to HD.

## Claude P6 entry point (2026-11-03)

Read [CLAUDE_HANDOVER.md](CLAUDE_HANDOVER.md) first for the consolidated current Windows/P6 guide, setup, settled decisions, verification and remaining work. P6_HANDOVER.md is chronological evidence. Old Claude/P4 notes are preserved in CLAUDE_P4_HANDOVER_HISTORY.md. No commit/push.

## Native-style focused Tree, sounds and EXP repair (2026-11-03, Codex)

Tree now follows the user’s screenshot, with native portraits and colored previous/next branches. Added opening/navigation/choose cues. Confirmed victory rewards survive map reloads before the late win-counter update; real battle +10 EXP and level-up retained through Native Forest→Green Gym in a separate test store. Latest details/evidence in P6_HANDOVER.md. User saves unchanged; no commit/push.

## P6 menu repair and relation view (2026-11-03, Codex)

Fixed the reported menu exit: USA native cancel is Triangle, so both overlay back buttons now forward it. Replaced the list with an original-icon grid and Evolution with a staged family relation chart; level-21 Champion eligibility remains. Native exit/Dex/chart paths and Greymon evolution verified separately, user saves unchanged. 184 tests passed, 14 skipped. See the latest P6_HANDOVER entry.

## Digi-Dex (2026-11-03, Codex, Windows)

Added battle-only discovery, in-game option 8 and Windows F4, with stats/stage, drops, linked/equipped skills, possible source locations and observed battle hours. Real ModokiBetamon battle/UI verified without changing user saves. See the opening of P6_HANDOVER.md and FORMATS.md for the persistence and schedule limits. Full suite: 180 passed, 14 skipped. No commit/push.

## P6 manual evolution (2026-11-03, Codex, Windows)

Implemented and real-game checked: Evolution is option 7 in the in-game menu with P6 on; Rookie → linked Champion at level 21, player choice with confirmation. Automatic natural/random/virus evolution is guarded. Agumon → Greymon completed through the native sequence in a separate test store; original save hashes unchanged. See the opening of P6_HANDOVER.md and the manual-evolution section of FORMATS.md for implementation/evidence/limits. Restart the app for the new code. No commit/push; world zoom remains off.

## P6 game rules (2026-11-03, Claude, branch `python-player`)

- User's rules: no aging/death, start at level 20 with matching stats, Rookie 10 / Champion 30 / Ultimate 90 EXP, combo ×1/×1.5/×2/×3 for 1–4 enemies, and a leveling equation (chosen: (L+1)(L+20)/2 to next level). Implemented as RAM rules in the Python player (`progression.py`, `GameRules.tick` every frame; `PlayConfig.game_rules`; Play-tab checkbox; level in the play window's status bar; `slotN.progress.json` with quick saves). Details and addresses in FORMATS.md.
- Verified on the real game from slot 2 with `DW1-coop-data/dev-tools/p6_rules_check.py` (level floor, held life/lives/age, replayed win and level-up), and with a real fight: the user's inspections `20261103-093656-777038` ("near end battle") and `…093709-657079` ("end of battle") finished with `p6_real_battle.py` give +10 EXP for one Rookie. The user's own desktop session ran with the checkbox off (`play/game_rules` never set), so they have not seen it in the window yet.
- Still to check on the desktop: the status-bar level, a level-up, a lost battle (lives back to 3), quick save/load keeping the level.
- Open decisions: EXP for Fresh/In-Training enemies (0 now), evolution by level (roadmap table), whether stats below the level floor should also apply after the partner is reborn (they do now), storage.

## P4 in-game check and stage bundles (2026-11-03, Claude, branch `python-player`)

- In-game gate passed: Koromon (ID 2) with palette 0 inverted, imported through `DigimonOverrides`, played from the patched disc; from the Native Forest inspection, walking into Green Gym loaded Koromon from disc and VRAM held only the inverted palette (original disc: only the original). Screenshot comparison differs only on Koromon's face. The partner was Agumon, so it did not keep Koromon resident. The game loads textures per model load (`loadDigimonTexture`), so changes show on entering a map that uses the Digimon. Harness: `DW1-coop-data/dev-tools/digimon_ingame.py` (uses a BIOS folder holding only `SCPH1001.BIN`, since the inspections were made with it); outputs in `DW1-coop-data/digimon-import-validation/koromon/`.
- Stage bundles: `export_bundle`, `read_bundle`, `prepare_bundle_import`, `BundleImport`, `DigimonOverrides.apply_many` (single `apply` now calls it); `_package_bytes`/`_publish` split out of `export_package`. GUI: class filter (Newborn = Fresh) in Characters and Digimon and in Animations, each with **Export class bundle…** for the filtered class, **Import Digimon package…** accepts bundles, review table, re-validation before apply. Tests: two package tests and one GUI test (164 pass).
- Next for P4: the 24 skipped models need the MMD parser to accept their primitive modes (`0x3e`, `0x36`, `0x2c`, `0x2e`, `0x2d`, one bad index on Tanemon). A model edit (not only a texture) has not been checked in game.

## World zoom switched off (2026-11-03)

After committing `74b09ff`, the user played with the zoom on the desktop: it **crashed the game and misplaced the player and NPCs**. At their request it is disabled (`player.WORLD_ZOOM_ENABLED = False`: slider disabled, `_ensure_camera` never installs). `camera.py`, `camera_engine.c` and their tests are kept. Not yet investigated; likely areas: entity culling/positions computed from screen bounds or `DRAWING_OFFSET` (e.g. `isOffScreen`, NPC AI using screen coordinates), the `VIEWPORT_DISTANCE` change affecting game logic beyond projection, state changes during map transitions or battles while patched, and RAM assumed free (`0x801C0000`–`0x801D3100`) being used in situations not sampled. Reproduce with the user's description before re-enabling.

## World zoom working (2026-11-03, Claude, branch `python-player`)

The user handed camera settings back from Codex ("codex couldnt just do it"). Codex's state: hook running, coverage cap falling back to 100% outdoors. Now working at 50–100% indoors and outdoors:

- Root causes found: (1) the game draws behind ordering-table entry 32 with `DRAWING_OFFSET` and in front with the screen centre (`applyDrawOffset`), and GTE OFX/OFY are 0 — so 2D layers must scale around primitive (160 − DO); Codex's centre (DO) and my first (0) were both wrong for the background; (2) the 4×3 tile ring cannot be widened at full size (VRAM full), so `camera_engine.c` stores tiles downscaled to the drawn size; (3) older saves "freeze" because the BIOS folder now contains `scph5501.bin` and those states were made under `SCPH1001.BIN` (not a camera problem).
- `src/dw1_workbench/camera_engine.c` + `tools/build_camera_engine.py` (clang/ld.lld, checks MIPS-I-only and R3000 HI/LO hazards) → generated `camera_engine_code.py` (test checks the source hash). `camera.py`: `ring_layout`, `engine_params`, `CameraZoom.install(scale)` (below 100% only), `tick`, `uninstall` (restores code, projection and native VRAM ring via the save state). `covered_scale` removed. Player installs with the requested scale; saves/inspections restore native first (Codex's flow kept).
- Verified (dev-tools style scripts in scratch, not in Git): 99.6% engine frame equals native apart from resampling; slot 3 indoor at 75/67/50%; Native Forest walk at 75% with streaming (old inspection run with a SCPH1001-only BIOS folder); player: zoom → save (native code in state) → load → zoom resumes → 100% native. 161 tests pass.
- Limits: black beyond map edges (camera clamps assume 320×240), object/entity culling at native bounds (not yet seen as a problem), 75% uses 84-px tiles (slight softening), battles/menus untested, user desktop confirmation pending. The P4 Digimon work below is Codex's and unchanged.

## Claude entry point (2026-11-03)

Read [CLAUDE_HANDOVER.md](CLAUDE_HANDOVER.md) first. It captures branch/HEAD, all uncommitted P4 and camera work, the recorded 160-test result, private validation paths, supported import boundaries, and the next task: verify the controlled Agumon replacement in normal gameplay. Earlier entries below are history and may describe superseded camera states or priorities. This handover update makes no code changes and does not commit or push.

## P4 native Digimon export/import (2026-11-03, latest)

The user first asked for P4 import, then confirmed that the assumed Digimon exporter did not exist. Implemented the smallest paired native package workflow rather than claiming a pre-existing bundle/GLB exporter. Camera work and its dirty files remain preserved. No commit or push.

### Implemented

- `digimon_package.py`: version-1 `.dw1digimon` ZIP, original disc/resource hashes and offsets, native MMD including animations, complete ALLTIM slot, palette-0 PNG/provenance, executable skeleton reference and animation-slot reference JSON. Export validates existing decoder support and refuses overwrite/source-tree destinations. No conversion to/from glTF is provided.
- Import checks exact source disc/release, ID/key and resource provenance; bounds and CRCs; duplicate/unsafe/encrypted/symlink/oversized members; native geometry, skeleton binding, material addresses and animation data. MMD byte length/header/object layout, clip IDs/presence and sound/texture-copy references remain fixed. TIM dimensions/mode/VRAM placement/palette count/size remain fixed. Editing both native TIM and PNG is rejected. Reference JSON edits are rejected.
- PNG edits map through the existing palette with approximation counts; native alpha classes, unchanged duplicate indices, all CLUT rows and slot padding are preserved. Palette-colour edits belong in native `texture.TIM`. No arbitrary new skeleton, enlarged mesh, character retargeting, stats/stage change or stage bundle.
- Content-addressed MMD/TIM overrides under `<project>/overrides/digimon/`, index `imports.json` committed last. Cancelled/failed staging cleans new blobs and preserves the prior index. Reopen re-hashes assets and checks source identity; restore drops the selected active record. Older unreferenced blobs are retained for reader safety. Source catalogue rows stay original; import actions are logged in SQLite and full provenance is in the override index.
- Characters and Digimon: Export/Import package/Restore buttons, background validation → texture/change review → background apply. All worker UI delivery uses bound slots; Event cancellation and close-time draining are covered. Texture and model/animation previews consume overrides. Play's checkbox is now **Play with imported resources** and combines zone + character replacements. Only changed ALLTIM slots are merged. Sector patch writes now check cancellation; animation parsing also checks cancellation inside clips.

### Validation

Automated: **160 tests pass**: 156 via `QT_QPA_PLATFORM=offscreen PYTHONPATH=src /home/drdark/.venv/bin/python -m pytest -q --ignore=tests/test_player.py`, plus 4 via the same command targeting `tests/test_player.py` outside the sandbox (Qt audio stalls inside it). Added 17 synthetic tests covering byte-identical package round trip, native vertex edit, PNG palette/alpha/index preservation, wrong source/ID/version, missing/duplicate/traversal members, malformed model, conflicting edits, dimensions, slot isolation, reopen/stale source/hash checks, cancellation and failed-write cleanup, GUI export/review/apply delivery on the GUI thread, active-worker shutdown and mid-clip animation cancellation. `git diff --check` passes.

Real supplied USA source, read-only: copied the catalogue using SQLite backup into `/home/drdark/dev/DW1-coop-data/digimon-import-validation/project/`; did not modify `/home/drdark/Documents/dw` or its overrides. Native ID 3/AGUM round trip preserved MMD SHA-256 `60d5e920b6cf621da8d2f67f703a2fe75f384000cbcdefd9398c76c1477c7c7d` and all 18,432 texture-slot bytes. Edited palette-0 entry 3 to `0x7c00`, imported/reopened, previewed 16 objects/690 triangles/54 animation slots. Patched ALLTIM equals the expected bytes with all other 179 slots untouched; patched inventory has 818 files (890 entries including directories); changed sector EDC/ECC checks. Original BIN SHA-256 before/after is `06084490ea0db34099b31495ccba4ec18fb0ff25c7683c508e9d2db93d4495f4`.

External script: `/home/drdark/dev/DW1-coop-data/dev-tools/digimon_roundtrip.py`. External results: `/home/drdark/dev/DW1-coop-data/digimon-import-validation/report.json`, `AGUM-original.dw1digimon`, `AGUM-blue-palette.dw1digimon`, `AGUM-imported-preview.png`, imported texture PNG and patched CUE/BIN. All contain or reference proprietary data and remain outside Git. `.gitignore` now excludes `.dw1digimon` packages.

### Launch, use and next task

Launch from `/home/drdark/dev/DW1-coop`: `PYTHONPATH=src /home/drdark/.venv/bin/python -m dw1_workbench.app`. Open the user's project, select Agumon in Characters and Digimon, export a package outside the repository, edit one supported representation and rezip its top-level files without changing provenance, then import into that same selected ID, review and apply. Use the restore button to remove active replacements. Enable **Play with imported resources** to launch the patched copy.

The native round trip, structural validation, imported workbench preview and patched bytes are verified. **Normal gameplay reloading of edited character assets is not yet verified.** Old emulator save states contain prior RAM/VRAM assets; start normal play/load a memory-card save or demonstrate an actual asset reload. Next concrete task: verify a controlled Agumon texture edit in normal gameplay, then consider native model edits, separate GLB/glTF export/conversion and stage bundles. P4 is partial, not complete arbitrary Digimon replacement. Camera streaming remains a separate unfinished task; 16-bit-colour work remains parked.

## Outdoor coverage correction (2026-11-03, latest)

The user's desktop screenshot at 50% in File City/TWNA01 (map 204) shows most of the frame black. Indoor transform verification did not establish outdoor support. Source investigation confirms `renderMap` draws at most 4 columns × 3 rows and `uploadMapTileImages` uploads the same ring. Tile VRAM slots alias modulo 4 columns/3 rows (setupMap); merely increasing draw-loop bounds would read reused textures. Wider rendering needs a revised background cache/upload path, not stretching the framebuffer or a loop-count-only patch.

Added `covered_scale`: choose the strongest requested zoom whose transformed loaded rectangle covers native 320×240 pixels, using current MapTiles ring indices, tile positions, camera offsets and moving origin. Invalid/unloaded state or native art that does not cover the frame falls back to 100%. Re-evaluated after each rendered frame; this prevents sustained uncovered views but is not an expanded renderer. The toolbar now shows effective zoom; its tooltip explains when tile coverage limits the requested zoom. Consequently some maps/positions will stay at 100% even with the slider at 50%. This is an interim containment, not completion of the user's wider-camera objective. A fast transition can still expose a transient frame before the next coverage check.

Fresh slot 3 recheck at requested 75% now correctly falls back to 100%: output equals native frame pixel-for-pixel, projection stays 686 and tile stays (46,59). The current report/captures in `camera-slot3/` reflect that capped result (the previous uncapped captures were regenerated). An attempted scripted Down exit from slot 3 did not leave map 218 and is not an outdoor validation. Desktop map 204 coverage still needs a source-state matched check. Automated: 139 non-player tests plus 4 player tests = 143 passing, including coverage bounds, negative/offscreen origins, full restoration and emitted geometry tests. Player tests run outside the sandbox for Qt audio.

Next concrete task: implement and validate a bounded extra-tile upload/render strategy with explicit VRAM allocation and foreground/character texture preservation; then match outdoor 100%/75% captures and map transitions. Do not describe the present coverage-limited slider as working unrestricted zoom or widescreen. User saves/disc/BIOS/memory card remain untouched by diagnostics; no commit/push.

## Slot 3 camera verification (2026-11-03, latest)

User supplied a fresh slot 3 (`ebff4c0693d9e84eb16c482751ab2d25dd1672c1a7e62e3b266aa2cde3cdb539`), which resumes correctly. The previous states' kernel stalls were specific to that diagnostic baseline; they do not demonstrate a camera-cache failure. The latest status supersedes the disabled-control notes below.

Matched comparison from slot 3, USA map 218, tamer tile (46,59), Beetle PSX `0.9.44.1 ea14d6a8`, original CUE read-only:

- Baseline changes 56,055 RAM bytes over 60 frames; hook executes 360 calls over the following 60 frames.
- At 100%, patched frame at frame 120 equals the unmodified frame 120 pixel-for-pixel.
- At 75%, projection changes 686 → 514; world background, foreground and characters visually shrink together. The moving drawing origin correction resolves the inconsistent transform. Tamer tile stays (46,59).
- Opaque clock-face and status-bar-fill samples equal native frame pixels. Whole HUD bounding rectangles differ because they include background/transparent areas; this is not a full mask-based HUD comparison.
- Uninstall restores projection 686, restores call instructions, clears temporary code/counter, and game frames continue. Synthetic test verifies complete original RAM equality and reinstall.

The USA slider is re-enabled **experimentally**, applied on demand below 100%; native play no longer installs anything by default. Quick saves, Resume, and F9 inspection states restore native camera code/projection before serialization, and selected zoom resumes on the next frame. Old saved states containing the previous camera patch are rejected with an actionable message. Tile streaming/culling/clamps remain native; this is zoom in a native frame, not completed widescreen support. Outdoor/map-transition verification and user desktop confirmation remain outstanding.

Validation source/captures/report outside Git: `/home/drdark/dev/DW1-coop-data/dev-tools/camera_slot3.py`, `/home/drdark/dev/DW1-coop-data/camera-slot3/`. No user save, BIOS, disc, memory card or inspection was overwritten by the diagnostic. Automated results: 138 tests excluding player pass; all 4 player tests pass outside the sandbox (inside it Qt audio stalls); total 142. `git diff --check` passes.

Launch: `cd /home/drdark/dev/DW1-coop` then `PYTHONPATH=src /home/drdark/.venv/bin/python -m dw1_workbench.app`. Open the user's project, Play, load slot 3, use World zoom. Next concrete task: confirm on desktop, repeat matched captures in an outdoor map with foreground occlusion and at a map transition, then address 4×3 tile streaming before describing this as a wider viewport. Do not commit/push until asked.

## Camera continuation (2026-11-03)

The emitted quad routine now applies `origin + (coordinate - origin) * scale / 256` using USA `DRAWING_OFFSET_X/Y` (`0x80134EC0/4`). Evidence: dw_decomp `src/main/main.c` calls `GsSetOrign(DRAWING_OFFSET_X, DRAWING_OFFSET_Y)`; `map.c:setPosDataMapTile` already includes that origin in its coordinates. Scaling those absolute coordinates around zero was inconsistent with changing GTE H. A RAM counter at `0x801D0200` records hook execution, and `CameraZoom.tick` refuses projection changes until that counter advances. Code remains below the counter address. The slider remains disabled pending real verification.

Regression tests execute the emitted integer instructions against independent affine expectations at three scales and three origins, including negative coordinates; they also check that an inactive hook cannot alter projection. Camera tests: 6 passed. Suite excluding `tests/test_player.py`: 137 passed. `git diff --check` passed.

Diagnostic correction: previous zero-call probes were inconclusive. In this run, restoring the existing slot/inspection states produced no ongoing world RAM updates even before patching; the CPU stayed in kernel/event code. Low kernel PCs alone are not evidence of invalid code. Tried matching GUI defaults, original CUE, both locally available BIOS inputs, memory card, loading before/after boot frames, paced frames, and execution outside the sandbox. No originals/saves were overwritten. A fresh slot 3 from normal play was requested for a working comparison. Diagnostic source outside Git: `/home/drdark/dev/DW1-coop-data/dev-tools/camera_diagnose.py`. It contains exploratory branches; use an unmodified-running baseline before interpreting hook counts.

Next: use the fresh state, prove world updates before patching, confirm the hook counter advances, then capture the identical saved state at 100% and 75%. Check projected feet against scenery, foreground occlusion, HUD pixel equality, and unchanged live world coordinates. Re-enable the slider only after that comparison succeeds. Tile streaming/culling still require separate work. No working wider camera is claimed yet.

## Camera regression correction (2026-11-03)

User screenshots confirmed that the zoom slider shrank/displaced characters against an unscaled background. A real USA capture run reproduced the mismatch. The Play worker no longer installs or ticks the prototype automatically, and the slider is disabled with an explanation. Normal play uses the original camera. Restart the player and load a save made before the zoom experiment; a state saved while zoom was active may retain modified RAM/projection. Original disc and memory card data were not modified by the zoom patch.

The prior claims below about an available world-only slider are superseded. Cache tag interpretation was also corrected against Beetle `mednafen/psx/cpu.c`, `ReadInstruction`: TV equals the complete virtual word address; bit 1 indicates invalidity. The earlier low-nibble validity assumption was wrong. This correction alone does not establish a working zoom.

Next task: verify execution of the background and foreground hooks (prefer a pre-boot patched executable), then apply the same screen-space transform to background, foreground, and projected characters, including drawing offsets. Compare the same paused game state at both scales, with ground contact and HUD pixels checked. Keep the control disabled until that succeeds. Captures: `/tmp/dw1-zoom-fix/zoom_1x_{256,205,171,128}.png` (temporary, proprietary; outside Git).

## Handover for Codex (2026-11-03, paused by the user mid-task)

### Where things stand

- Repository `/home/drdark/dev/DW1-coop`, branch `python-player` (pushed up to `220c776`; it sits on `animations`, which sits on PR #1 `agumon-preview-verification`; `main` has only `docs/ROADMAP.md` on top of the old base). Python 3.14 venv at `/home/drdark/.venv`.
- Run tests: `QT_QPA_PLATFORM=offscreen PYTHONPATH=src /home/drdark/.venv/bin/python -m pytest -q` (135 pass; the fake libretro core in `tests/fake_core.c` is compiled by a fixture). Run the app: `env PYTHONPATH=src /home/drdark/.venv/bin/python -m dw1_workbench.app`.
- The user's real project is `/home/drdark/Documents/dw` (disc: `~/Downloads/Digimon World (USA)/Digimon World (USA).cue`, BIOS `~/Downloads/SCPH1001.BIN`, core `/usr/lib/libretro/mednafen_psx_libretro.so`, saves `~/.local/share/DW1-coop/play/06084490ea0db340/`). The validation project used by scripts is `/home/drdark/dev/DW1-coop-data/maps-validation/project`. Never commit game data; packages, patched discs and `slus.bin` stay outside Git.
- The user only wants commits/pushes when they ask, and confirms features on the desktop before saying "commit it".

### Uncommitted work (verified, waiting for the user's "commit it")

Everything in `git status` after `220c776`: observations removed from zone export; zone import (`zone_import.py`, `disc_patch.py`, `background_import.py`, GUI dialogs, Play tab "Play with imported zones"); background repaint with the foreground layer (all kinds); `LibretroCore.write_memory`. The sections below describe and verify each. The user confirmed the import/background fix ("much better") and then asked for all foreground kinds, which is done and checked across all 241 maps but not yet confirmed by them on the desktop.

### In progress: camera zoom prototype (`src/dw1_workbench/camera.py`, not wired into the UI)

Goal from the user: a wider camera / aspect ratio that only changes the zoom level, without stretching images.

Design (from dw_decomp `aaf69a4`): the overworld draws a 320×240 window with the drawing origin at the screen centre (`GsInitGraph(320, 240, 4, ...)`), background tiles via `renderMap` → `setPosDataMapTile` → `setPosDataPolyFT4`, foreground pieces via `buildMapOverlayPrim` → `setPosDataPolyFT4`, and 3D through GTE H = `VIEWPORT_DISTANCE` (0x80134ED4, set per map by `GsSetProjection` in `loadMapSetup`). `camera.routine(scale)` is a 28-instruction scaled copy of `setPosDataPolyFT4` (corners × scale/256, then `ctc2` of `VIEWPORT_DISTANCE` into GTE H); encodings were checked against `llvm-mc` (27/28; `ctc2` by hand, `0x48CED000`). `CameraZoom` writes it at `ROUTINE = 0x801D0000` (inside 96 KB that stayed zero in all sampled saves) and points the three `jal setPosDataPolyFT4` call sites (`0x800D71F8`, `0x800B5074`, `0x800B509C`) at it, and `tick()` keeps `VIEWPORT_DISTANCE` at the map's value × scale. Beetle's `beetle_psx_internal_resolution` (2x, 4x, …) is the plan for keeping a smaller-drawn background sharp.

Status: **world-only zoom is wired into the Play toolbar, but live visual effect remains unverified.** The Play window now exposes a 50–100% `World zoom` slider. It changes only the patched USA overworld background/foreground quad calls and the projection-distance value; the libretro frame stays at its native geometry, so HUD/menu pixels and captured images are not stretched. The emulated CPU has an instruction cache (Beetle PSX save state field `ICache_Bulk`: 1024 × (u32 tag, u32 word)); writing code into RAM from outside does not invalidate it. The earlier save-state-wide invalidation froze the game, so `CameraZoom` now refreshes only matching resident cache words and leaves cache misses to normal refill. The targeted path has synthetic coverage, but no reliable real-game visual confirmation yet. Next steps:

1. Establish a reliable real-game execution path for the live patch. The targeted cache synchronisation avoids the known freeze, but the USA probe still records zero calls to the patched routine, so this may require patching `SLUS_010.32` before first execution (via `disc_patch`) rather than writing code into a running save state.
2. Re-run the call probe (`dev-tools/probe2.py`) and zoom capture (`dev-tools/zoom_try.py`) against a cold boot or pre-patched executable. Keep the feature labelled experimental until a real screenshot demonstrates that world geometry changes while HUD pixels remain unchanged.
3. Expected follow-ups once quads scale: only 4×3 background tiles are streamed (`uploadMapTileImages`), so the edges of a zoomed-out view will show unloaded tiles; the camera clamps and object culling still assume 320×240; HUD must stay unscaled (other `setPosDataPolyFT4` callers are untouched); battles reuse the overworld map. 16:9 would be the same mechanism with separate X/Y factors plus Beetle's `beetle_psx_widescreen_hack` for 3D, or the player displaying anamorphically.

### Development scripts (outside Git)

`/home/drdark/dev/DW1-coop-data/dev-tools/`: `cam/dis.py` (disassembles `cam/slus.bin`, the USA executable, at virtual addresses; file offset = VA − 0x80090000, load address 0x80090800) via `llvm-mc --disassemble`; `nav.py`/`walk.py`/`chase.py` (drive the real game headless along walk-through exits from an inspection state); `edit_test.py`, `bg_game*.py`, `import_try.py`, `export_try.py` (import/patched-disc checks); `fg_check.py`/`fg_match.py` (all-map foreground checks); `probe2.py`, `zoom_try.py`, `zoom_probe.py` (camera work). They expect `PYTHONPATH=src` from the repository and `QT_QPA_PLATFORM=offscreen`.

## Zone import (2026-11-03, branch `python-player`)

User: exports must not include observation files; add import for a single zone or a bundle. Observations were removed from export (option, code and test guard).

- `zone_import.py`: `read_package`/`check_package` (manifest hashes, release and disc, per-map parse, warps, sector room) and `OverrideStore` (`<project>/overrides/`, changed files only; an unchanged import restores the disc's files).
- `disc_patch.py`: Mode 2/Form 1 EDC/ECC encoder (matches 198/198 real sectors), directory-record lookup, `build_patched_image` (copy, patch only differing sectors, size field, CUE copied; one cached copy, about 365 MB).
- GUI: Maps and Zones "Import zone or bundle…" (`ZoneImportDialog` shows changed/unchanged/problems), "Imported zones (N)…" (remove), previews/pathing/export read overrides; Play tab "Play with imported zones" builds the patched disc on a `PatchWorker` (bound slots only) and launches it.
- Verified: unchanged 48-map bundle → nothing stored, patched BIN byte-identical; MAYO00 tile 52,30 blocked in a package → imported → live grid on the patched disc equals the imported MAP. Full GUI flow run offscreen on a copy of the project. 132 tests pass.

Follow-up (user's `~/Documents/native-forest.dw1zone`): they repainted `maps/109-MAYO00/background-palette0.png` at 1145×1374 and import ignored it (only MAP/TFS were read, so it looked identical). `background_import.encode_background` now converts an edited palette-0 picture into the TFS; `read_package` reports edited files instead of failing on manifest mismatches; other generated files are reported as ignored. Their package imports as one changed TFS and the new background was seen in the game. The user then saw black stepped blocks and old-coloured bushes in front of the characters: MAYO00's foreground layer (32 sprite objects from two embedded 8-bit TIMs) uses the background palette, whose transparent entry (index 255) had become opaque. `encode_background` now also repaints those TIM pixels from the picture at each instance's position, keeping transparency, and returns the new MAP; import marks MAP and TFS changed. Seen fixed in the game. The user must re-import the package (the earlier override has the old TFS only). 135 tests pass.

Then all foreground kinds (user: "for the other zones should be the same right?"): survey of 241 maps — 170 with background-palette pieces, 127 with own-palette pieces (104 with 4-bit images), 39 animated, 13 Freezeland particle maps, 27 without a foreground. `background_import._Vram` models the image loads; `_repaint_foreground` repaints opaque, static, single-place cut-outs (measured against the original background) and keeps effects, separate art, animation, particles and repeated pieces, reporting each reason in the import notes. Identity conversion of all 241 maps: no failures, median foreground colour change 0, worst 13.5/255; the scratch harness lives outside Git. For the user's MAYO00: 104,704 texels repainted, 8 semi-transparent effects and 4 right-edge foliage pieces kept. A possible next step is exporting the foreground layer as its own editable PNG.

Limits: files cannot grow past their original sectors; there is no in-workbench zone editor yet.

## Zone pathing and export (2026-11-03, branch `python-player`)

User request: "zone exporting + pathing, single zone or bundle zone" (roadmap P2's export half; import is not started).

- `pathing.py`: walkability (`value & 0x80` blocks, from dw_decomp `checkMapCollisionX/Z`), regions, Dijkstra with 8-way steps (no diagonal squeeze), `map_places` (deduplicated spawns, blocked flag, walk-through exits, toilets), `map_pathing`, `routes_from`/`plan_route` across maps over walk-through exits.
- `zone_export.py`: `.dw1zone` ZIP (see FORMATS "Zone packages"), written to a temporary file then renamed; original bytes are re-hashed against the catalogue; cancellable.
- GUI: Maps and Zones "Pathing" preview tab (route highlight, region sizes, route planner backed by a `ZoneWorker` pathing pass over all maps) and "Export zone or bundle…" (`ZoneExportDialog`: zone checklist, observations, all palettes; refuses destinations inside the repository).
- Checked: all 241 maps (8 s), routes like File City → Green Gym → Native Forest (15.9 tiles); Native Forest and a three-zone bundle exported with every MAP/TFS equal to its catalogue hash. 125 tests pass.

Next: zone import (unchanged round trip, then one controlled edit) needs the execution target decided — the player could load edited MAP/TFS files through a patched disc image or file overrides.

## Live zone inspection (2026-11-03, branch `python-player`)

Next milestone after the player, finishing the roadmap's P0 inspection requirement. The user had confirmed boot, quick save/load, Resume and the memory card with Beetle PSX and `SCPH1001.BIN` (Beetle logs `scph5501.bin` missing but boots; the BIOS check now accepts any North American BIOS).

- `live.py`: USA RAM addresses from dw_decomp (`CURRENT_SCREEN`, `TAMER_ENTITY`, `PARTNER_ENTITY`, `MAP_COLLISION_DATA`), `getModelTile` rounding, `grid_differences`. Enabled only when the project's executable is `SLUS_010.32`. `LibretroCore.read_memory` reads ranges of the core's system RAM.
- Worker reads live state every 10 frames and emits on change; `inspect` captures state, screenshot and live state at the keypress. `PlayWindow`: status-bar summary (map label, tile, trigger, grid vs decoded MAP), F10 live-map dock, F9 `InspectDialog`; the game pauses while the dialog is open.
- `inspection.py`: per-inspection folder under `<saves>/inspections/` with JSON (written last), PNG, zlib-compressed state and the live grid. Play tab lists them (play from, show map, delete); Maps and Zones scene details list a map's inspections; `Workspace.show_map(index)` opens a map on its floor grid.
- Real-game check (offscreen, from the user's slots): maps 109 `MAYO00` and 112 `TRAI00` read correctly, tiles (54,12) and (54,82), and the live grid equals the decoded floor grid on all tiles of both. An end-to-end run from the Resume slot saved and listed an inspection. 111 tests pass.

NPCs (user report: not shown on the live map): `ENTITY_TABLE` slots 2–9 with `isOnMap` are now read (`LiveNpc`), drawn and named on the live map, listed under it and in the inspect dialog, and stored in inspections (`npcs`). Checked from the user's inspection states: live NPC IDs and tiles equal the decoded MAP placements in File City and Green Gym. 112 tests pass.

Hostile Digimon (user request "aggressive spawned digimons / enemies"): `maps.follow_kind`/`follow_description`/`tracking_box`/`floor_grid_with_digimon`; `LiveNpc` gains follow mode, tracking range, post, reacting, autotalk, script ID and HP from `NPC_ENTITIES` and `MAP_DIGIMON_TABLE`; live map colours and boxes, NPC descriptions, threat in the status bar, behaviour stored with inspections. Verified with a scripted walk (scratch harness, not in Git) from the user's Native Forest inspection: spawned ModokiBetamon reacted exactly on entering its decoded box and started its battle script on contact. Not yet: battle state (whether a battle is running) and wandering/waypoint paths. 114 tests pass.

Items (user inspection note "item spawn, digi-mushroom" in `MAYO01_2` at 59,54): `items.py` (ITEM_PARA names, `DROPPED_ITEMS`, `CHEST_ARRAY`), `LiveState.items/chests`, live-map diamonds and chests, reach hint, `Inspection.items`, `SpawnLog` (`<saves>/item_spawns.json`, counted per visit), Maps and Zones lines and floor-grid diamonds. Real-game check: the Digimushrm was at 61,54; stepping to 60,54 opened the pick-up box. Item spawn rules (timers, random choice) live in map scripts and are not decoded. 117 tests pass.

Still to do for P0: the user's own playthrough — two connected zones, a gateway and a toilet inspected with F9 — then record it here and in the roadmap. Later: compare inspections where the live grid differs (script-changed tiles), and use spawn/arrival positions after warps.

## Python player (2026-11-03, branch `python-player`)

`docs/ROADMAP.md` on `main` (`812f191`) makes a playable inspection build P0. The user asked for a Python-based player that works on Linux now and Windows later, with quick saves, rather than an external emulator. Branched from `animations`.

- `libretro.py`: a ctypes libretro frontend (API 1). Answers the environment calls PS1 cores use (system/save directories, pixel format, legacy core options with overrides, input bitmasks, messages, geometry/timing changes), converts video/audio/input callbacks, and wraps serialize/unserialize and save RAM. One running core per process; libraries are never unloaded.
- `quicksave.py`: slots 1–9 plus Resume (0); state + PNG + JSON written atomically, JSON last; loads check size, SHA-256 and core name. The memory card is `memory_card_1.srm` (the core's SAVE_RAM).
- `player.py`: `EmulatorWorker` runs the core on a QThread (clock pacing at the core's fps, fast-forward ×4, command queue for save/load/reset, newest-frame hand-off, memory card flushed every 600 frames and on close). `PlayWindow` shows frames, plays audio via QAudioSink and maps keys. `worker.finished → thread.quit` must be a direct connection: `stop()` waits on the GUI thread, so a queued quit deadlocked for the full 10 s timeout (found by the tests).
- `play_panel.py` and the Workspace "Play" tab: core/BIOS settings (QSettings `DW1-coop/workbench`), automatic Beetle PSX discovery, BIOS check (`scph5501.bin` for USA), saves folder per disc SHA-256 under the generic data location, slot table with thumbnails, launch log (also `launch.log` in the saves folder). Closing the workbench closes the game first so the Resume slot and memory card are written.
- Tests: `tests/fake_core.c` is compiled by a session fixture (skipped without a C compiler) and drives `test_libretro.py`, `test_player.py`; `test_quicksave.py` is pure. 104 tests pass.

Not yet done: running the real game. The user has to install a core (`sudo pacman -S libretro-beetle-psx`) and point the tab at their own BIOS; neither may be downloaded for them. Then the roadmap P0 gate: two connected zones, a gateway and a toilet, save/relaunch/load, and the inspection checklist. Next player work: gamepad input (Qt 6 has no gamepad module; SDL2 via `pysdl2` is the likely route), key remapping, core options UI, Windows check.

## Animations milestone (2026-11-02, branch `animations`)

Branched from `agumon-preview-verification` (open as pull request #1). `src/dw1_workbench/animation.py` parses MMD animation tables and steps clips exactly like the decompiled game (`jype0/dw_decomp` `src/main/anim.c`; SydPatches double-steps the root remainder, the original does not). The model preview (`model_view.set_pose`, `gui.py` animation controls) plays clips at an assumed 20 ticks/s with optional root motion and texture copies; sound cues are counted, not played. `tests/test_animation.py` adds 16 synthetic tests (80 total pass).

Converter comparison, outside Git in `/home/drdark/dev/DW1-coop-data/converter-run/`: `compare_animations.py` (`--float` runs the diagnostic real-valued stepper) and `compare_animations.log`. Result: all 4,843 clips of 178 models match (see `docs/FORMATS.md#animation-clips`).

The user then asked for a real Animations tab: `animation_view.py` now holds the shared `ModelAnimationPanel`; the tab lists all modelled Digimon with stage/partner filters and auto-loads the selection (a newer selection cancels and queues behind the running load; the busy check waits for `_model_finished`, which fixed an orphaned-thread crash found in testing). Stages come from the `DigimonPara` level byte and match the user's evolution-stage list (9 partner Rookies). The user confirmed the tab on the desktop. 8 partner-range models have unsupported geometry (Poyomon, Tanemon, Gabumon, Devimon, Kabuterimon, Kuwagamon, H-Kabuterimon, Gigadramon). 83 tests pass. Clip names: `clip_names.py` maps animation IDs to names from dw_decomp call sites (Digimon vs player sets, confirmed/likely, evidence strings) and technique clips 46+ to `MOVE_NAMES` via each Digimon's `moves[]`; the Animations tab shows Name/Confidence columns with evidence tooltips. IDs 21 and 23–26 are unnamed (presumably script-driven). 88 tests pass. Next candidates are decoding map/event scripts (would name the remaining clips), supporting the translucent primitives those models use, clip naming from evidence (game call sites start IDs 0, 1, 2, 3, 6, 7, 10–14 for the tamer), the per-type texture cycling, and GLB/glTF export with animations.

## Maps and zones milestone (2026-11-02, branch `maps-zones`)

Worked in a separate worktree at `/home/drdark/dev/DW1-coop-maps` (branched from `873829a`) while another session verified the remaining MMD models on `agumon-preview-verification`. `maps-zones` was then merged into `agumon-preview-verification`.

- `src/dw1_workbench/maps.py`: USA map table, area names, toilets, doors; TFS backgrounds; MAP scene data; background rendering and export. `project.py` catalogues maps on import when the disc has `MAP/` files. `gui.py` replaces the Maps and Zones placeholder with a browser.
- `tests/test_maps.py`: synthetic map tests (see test count below).
- Real data outside Git: extracted inputs in `/home/drdark/dev/DW1-coop-data/converter-input-maps/`, converter output and `compare_maps.py` in `/home/drdark/dev/DW1-coop-data/converter-run-maps/`, and a fresh project with maps at `/home/drdark/dev/DW1-coop-data/maps-validation/project`.
- Result: all 241 file-backed maps and 512 backgrounds match the converter (see `docs/FORMATS.md#maps-and-zones`).

First desktop check: the user could not find zones linked south of Green Gym. The warp list showed only Green Gym's one walk-through exit; its other routes are script-driven (step-script tile 51 on its east edge) or inbound from File City. Added: tree search, "linked from" inbound list, exit direction and walk-through vs table-only per warp, the floor grid decoded as the tile-trigger map (SydPatches evidence) with a colour legend, and a Relation map tab (`relation_view.py`, worker in `gui.py`). 64 tests pass.

The user rechecked search, inbound links, and the relation map on the desktop and confirmed them correct. Next candidates are decoding map scripts (the missing transitions), sprite-object rendering, door models, and an overlay of spawns/exits/Digimon on the background (needs the camera projection).

## All-model converter comparison (2026-11-02)

All 178 USA MMDs were extracted from the catalogue (SHA-256 equal to catalogue hashes) into `/home/drdark/dev/DW1-coop-data/converter-input/CHDAT/MMD{0..5}/` and converted by the pinned `DW1ModelConverter` (`17d1a19`), which wrote 178 glTF files and skipped IDs 62/114 (no MMD). Scripts and results live outside Git in `/home/drdark/dev/DW1-coop-data/converter-run/`: `compare_all.py` (writes `results.json`), `overlap_check.py`, and `degenerate_check.py`.

Result: 151 accepted models all match on skeleton, ordered corners (compared per converter material group), materials, initial pose, and world-space assembly (max 0.00014). 135 also match every centroid texel; the 180 differing samples in 16 models are all zero-area-UV faces (13 models) or animation texture-copy regions (3 models) in the converter's single-palette texture map, not workbench errors. 27 models are rejected: 26 for unsupported primitive types (`0x3e` ×17, `0x36` ×3, `0x2c` ×3, `0x2e`, `0x38`, `0x2d` flag 1) and `TANE` for 75 out-of-range normal indices. No parser bug was found, so `mmd.py` is unchanged. Full lists are in `docs/FORMATS.md` ("All-model comparison").

Possible next steps: support translucent Gouraud textured primitives (`0x36`/`0x3e`, 20 models) with their own evidence and tests; GLB/glTF export with provenance; visual spot checks of a few more accepted models.

## Converter comparison (2026-11-02, resumed)

Step 1 of the resume plan is complete. The pinned `DW1ModelConverter` (`17d1a19`) was built and run against `converter-input/`.

Build note: configuration had picked up the system `zlib-ng` header (`1.3.1.zlib-ng`) for libpng's generated `pnglibconf.h` while compiling against the bundled zlib `1.3.2`, which failed with `The include path of <zlib.h> is incorrect`. Fixed by deleting the generated `pnglibconf.*` files and reconfiguring with `-DZLIB_INCLUDE_DIR=/tmp/DW1ModelConverter-build/_deps/zlib-src -DZLIB_LIBRARY_RELEASE=/tmp/DW1ModelConverter-build/_deps/zlib-build/libz.a`. No upstream source was changed.

Output: `/home/drdark/dev/DW1-coop-data/converter-run/output/digimon/AGUM.gltf`, SHA-256 `a26aaaf20d0ac098f94b5a0983e73080dff9189a3b84004b5473820374a33099`. Comparison script (no proprietary data): `/home/drdark/dev/DW1-coop-data/converter-run/compare_agum.py`.

Result — all checks passed:

- 22 nodes; every parent and mesh/object assignment equal.
- 16 objects, 690 triangles; every de-indexed corner equal in order (position, normal, UV as `u/256+0.0001`, `v/128+0.0001`) within 1e-4. This confirms winding and quad split independently of the earlier source reading.
- One material in both; opaque, single-sided, lit — consistent with all parsed faces.
- Initial pose from animation record 0 equal on every node (scale, translation, and rotation compared as quaternions).
- World-space vertices from the glTF TRS chain vs. the workbench renderer's `_local_matrix` chain (before its Y flip): max error 0.000032.
- Texel colours at all 690 face centroids, under each face's own CLUT (palette 0: 523 faces, 1: 21, 2: 146), equal at 5-bit source precision including transparency. The converter expands colour as `c<<3` (max 248) while the workbench uses `c*255/31`; this is a convention difference, not a palette error.

Desktop launch (Wayland) then exposed two GUI defects, both fixed with regression tests (33 passed):

- Segfault: worker `progress`/`failed`/`cancelled`/`succeeded` signals were connected to lambdas, which PySide6 runs in the emitting worker thread, so progress dialogs and labels were repainted off the GUI thread. They now connect to bound `@Slot` methods. Offscreen tests had masked this.
- Missing texture: 30 of AGUM's 690 triangles (objects 1, 3, 10) have zero-area UVs (one texel, or one texel row/column). The renderer dropped them because the UV-to-screen transform is singular; they are now drawn from their texels (corners spread inside each texel, centroid-colour fallback). Antialiased clipping also left background cracks between faces; textured faces are now drawn aliased. Interior background pixels in a back view fell from 261 to 1.

Resume step 2 (visual inspection on a real display) is done: after both fixes the user inspected Agumon in the desktop GUI and confirmed it looks correct and fully textured.

Not covered: full per-texel palette coverage (the converter's CLUT map also block-fills unassigned texels, so it is not an exact reference there). Model export remains disabled. `README.md`, `docs/FORMATS.md`, and `docs/PROGRESS.md` now describe the settled boundary (Agumon only). Next: decide on GLB/glTF export with provenance as a separate step, and/or extend the converter comparison to the other 177 MMDs.

## Paused static Agumon milestone (2026-11-02)

The user requested a pause after the bounded parser, background GUI loader, and experimental renderer were implemented, but before independent converter comparison and final documentation were completed. The work is intentionally committed as a reviewable work in progress. It must not yet be described as a fully verified textured-model milestone, and model export remains disabled.

### Preserved external data

The former temporary validation project and useful exports were copied out of `/tmp` to:

```text
/home/drdark/dev/DW1-coop-data/asset-validation-v3/
```

That directory contains the catalogue, prior texture export, source-verified `AGUM.MMD`, a source-verified `SLUS_010.32`, and experimental preview screenshots. The model SHA-256 matches its catalogue entry:

```text
60d5e920b6cf621da8d2f67f703a2fe75f384000cbcdefd9398c76c1477c7c7d
```

A minimal input tree for the independent upstream converter is outside Git at:

```text
/home/drdark/dev/DW1-coop-data/converter-input/
```

It contains proprietary data and must never be committed. The copied `CHDAT/ALLTIM.TIM` SHA-256 is `b7233629dd4d3fa9fa23118cf7ffa8b2a6fe1a2988b8f9e1c377533ed8aa5ee2`.

Catalogue reopen succeeded from the durable location. ID 3 was rechecked as display name `Agumon`, internal key `AGUM`, model `CHDAT/MMD0/AGUM.MMD`, and texture `CHDAT/ALLTIM.TIM#003`. The streamed model export hash equals the catalogue hash.

### Implemented work in progress

- `src/dw1_workbench/mmd.py` adds bounded parsing for the relative-pointer MMD/TMD layout used by AGUM: object table, signed 16-bit vertices, 12-bit fixed-point normals, modes `0x34` and `0x3c`, packet lengths, vertex/normal indices, UVs, texture page, color mode, blend mode, and CLUT coordinates.
- It rejects unsupported modes/flags, nonzero object scale exponents, malformed ranges/counts/packet sizes, invalid skeleton parents, out-of-range indices, mismatched texture pages/color modes/CLUTs, and out-of-bounds UVs.
- It parses the USA executable skeleton table and only the bounded initial-transform prefix of the first available MMD animation record. It does not parse instructions or play animation.
- `src/dw1_workbench/model_view.py` provides a PySide6/Qt raster preview with orbit, zoom, reset, textured/untextured rendering, and wireframe overlay. It adds no dependency beyond PySide6. Source coordinates stay unchanged; PS1 screen-down Y is inverted only in the rendering layer.
- `gui.py` loads executable, MMD, and TIM data in a cancellable background worker. Cancellation uses a direct `threading.Event` callback rather than a queued worker slot. Closing the workspace includes this worker in the existing safe shutdown path.
- Model export is deliberately absent.
- `tests/test_mmd.py` covers a synthetic valid triangle, stable winding, initial transform, invalid indices, unsupported packets, and invalid skeleton parents.

### Real Agumon observations

The current parser reports:

- 22 skeleton nodes;
- 16 TMD objects;
- 390 vertices;
- 412 source primitives: 134 triangles (`0x34`) and 278 quads (`0x3c`);
- 690 rendered triangles after deterministic quad splitting;
- all primitive flags are zero and all object scale exponents are zero;
- texture page 5, which matches TIM image X `320 / 64`;
- 4-bpp material mode, matching the associated TIM;
- CLUT Y 488, 489, and 490, selecting verified palette indices 0, 1, and 2 from the TIM whose CLUT starts at Y 488;
- UV bounds X 0..255 and Y 0..123, within the 256×128 texture;
- no out-of-range vertex or normal indices.

The winding and quad split match pinned `DW1ModelConverter` revision `17d1a19`: `(2,1,0)` and, for quads, `(1,2,3)`. Its skeleton and initial-pose interpretation also match the current implementation. A manually inspected external screenshot after the renderer-only Y inversion is recognizably upright Agumon with assembled head, torso, limbs, claws, legs, and tail. This is encouraging visual evidence, but independent converter output has not yet been compared side by side, so the GUI labels the preview experimental.

### Validation state at pause

```bash
QT_QPA_PLATFORM=offscreen PYTHONPATH=src /home/drdark/.venv/bin/python -m pytest -q
```

Final pause-point result: `30 passed in 0.29s`.

The offscreen real-data worker completed and produced the counts above. Manual orbit/zoom interaction on a desktop display has not been tested. Offscreen rendering alone is not considered proof of final visual correctness.

The pinned upstream converter source remains at `/tmp/DW1ModelConverter-research` (`17d1a19`). Its CMake configuration completed in `/tmp/DW1ModelConverter-build` after downloading open-source dependencies, but the executable had not been built or run when the pause was requested.

### Resume commands and next task

From the repository:

```bash
cmake --build /tmp/DW1ModelConverter-build -j2
mkdir -p /home/drdark/dev/DW1-coop-data/converter-run
cd /home/drdark/dev/DW1-coop-data/converter-run
/tmp/DW1ModelConverter-build/DW1ModelConverter /home/drdark/dev/DW1-coop-data/converter-input
```

On resume:

1. run the pinned converter and compare its AGUM glTF object, vertex, primitive, UV, material, palette, skeleton, and initial-pose results with this parser;
2. inspect converter output and workbench preview side by side on a real display, including winding, object placement, texture orientation, and all three used palettes;
3. fix any concrete discrepancies and add regression coverage;
4. update `README.md`, `docs/FORMATS.md`, and `docs/PROGRESS.md` only after the verification boundary is settled;
5. keep model export disabled unless that comparison confirms geometry/material correctness; if confirmed, implement GLB/glTF plus provenance separately;
6. do not implement animation playback, gameplay, networking, or co-op in this milestone.

## Completed in this pass

- Replaced queued worker-slot cancellation with `threading.Event`; import cancellation now works while the worker is busy.
- Recorded `cancelled` separately from `failed`, added safe close-time cancellation/waiting, and avoided forced termination.
- Added logical extent/range streaming across cooked and raw-sector layouts, incremental hashes, bounded prefix reads, and atomic cancellable exports.
- Added a background GUI export worker; incomplete `.partial` files are removed.
- Added and verified a narrow USA `CHDAT/ALLTIM.TIM` decoder: 180 fixed `0x4800`-byte TIM slots with catalogue provenance and parent associations.
- Parsed the USA executable's 180-entry internal resource-name table and 52-byte parameter records. The catalogue now records 179 non-empty display names with exact offsets; ID 114's empty field remains unavailable.
- Proved the numeric ID relationship to ALLTIM and MMD paths using upstream source and the supplied disc. All 180 IDs have texture associations; 178 present MMDs pass bounded header/TMD-table/animation-boundary validation.
- Added a real Characters and Digimon browser. Selecting ID 3 (`AGUM`, verified display name `Agumon`) previews its decoded 256×128, 4-bpp, 16-palette TIM and can export PNG plus provenance. MMD geometry/animation is not presented as decoded.

## Exact validation commands and results

From `/home/drdark/dev/DW1-coop`:

```bash
QT_QPA_PLATFORM=offscreen PYTHONPATH=src /home/drdark/.venv/bin/python -m pytest -q
```

Result: `26 passed in 0.19s`. The suite additionally covers character-table IDs/paths/display labels, empty display-name preservation, malformed table data, and valid/malformed MMD boundaries. It still uses only synthetic fixtures.

The real input was imported read-only through:

```text
/home/drdark/Downloads/Digimon World (USA)/Digimon World (USA).cue
```

The latest analysis project and exports were kept outside Git at:

```text
/tmp/dw1-asset-validation-v3/
```

Result: complete USA `SLUS_010.32` import, raw Mode 2/Form 1, 818 disc files, 180 ALLTIM children, 180 character records, and 1,178 total catalogue resources. Reopen, streamed `SYSTEM.CNF` export/catalogue-hash equality, 179 display labels, 178 MMD boundary checks, and an offscreen Agumon texture preview passed. The earlier timed real-BIN cancellation recorded `import_status=cancelled` at `/tmp/dw1-real-cancel.Bb0CUj/`. `/tmp` is not durable.

Upstream source revisions inspected in `/tmp`:

- `jype0/dw_decomp`: `aaf69a4491cbf275ffd24df19fe78010151c10f2`
- `SydMontague/DW1-SydPatches`: `b9671fad99513467dd7ae229ad2f13606f2852dc`
- `Operation-Decoded/DW1ModelConverter`: `17d1a19` (MIT)

## Remaining limitations

- GUI behavior was exercised offscreen for cancellation/shutdown, but interactive dialogs, visual TIM rendering, resizing, and desktop close behavior were not manually inspected on a display.
- ISO traversal still follows directory records, not the path table; hidden/path-table-only content is not ruled out.
- Product code plus PS-X EXE header identifies the supplied release, but no curated known-good hash registry is implemented.
- Resource IDs, internal names, display labels, ALLTIM slots, and MMD container paths are confirmed for the USA table. This does not establish animation names, gameplay roles, or geometry correctness. ID 114 has no display label; IDs 62 and 114 have no matching MMD.
- `ETCTIM.BIN` and `STDTIM.BIN` begin with TIM data but their container boundaries are not inferred from magic scans.
- MMD geometry/UVs/animation, standalone TMD rendering, TFS, MAP, audio/video, and executable disassembly remain unsupported.

## Next concrete task

Implement a bounded embedded-TMD primitive parser for one known MMD, starting with ID 3 `CHDAT/MMD0/AGUM.MMD`:

1. document and test each primitive type actually present, including packet sizes, vertex/normal/UV indices, and out-of-range rejection;
2. compare parsed counts and coordinates with `DW1ModelConverter` without copying its implementation;
3. preserve MMD source path, embedded offsets, hashes, parser version, and any preview transform;
4. add a static model preview/export only after geometry and UVs are structurally and visually verified; otherwise retain the current texture-only preview;
5. leave animation data unsupported until its bone/channel structure is separately proven.
