# P0 runtime stability — 2026-11-05

Branch `python-player`, starting at `f51a7c56fa0d53dd78a64c0893bb9ed2e487709d`. An incremental stability batch, **not completion of the gameplay acceptance matrix**. Figures and test counts below are from when it was written; the suite has grown since. World zoom remains disabled. No core, native gameplay/progression/battle logic, original disc or user save format was changed.

## Evidence and changes

- `hd_background.MapBackground.refresh`: previously parsed the map table before checking battle state, then cleared its key when dimensions were invalid. `tamer.c:startBattle` / `btl.c:loadBattleData` load battle code into the environment buffer. A cached environment is now retained for the same map throughout battle; a different map cannot inherit it. Synthetic overwritten-dimension/table regression passes. This is a demonstrated code defect, not proof that it explains every reported missing background.
- `GAME_STATE` at USA `0x80134F0A` is **int8**, not the int16 previously read here. Battle entry/loading/active states are 1/3/2; unknown state 4 is not assumed to be a battle. Sources: local `dw_decomp` `src/main/tamer.c:startBattle`, `src/main/btl.c:loadBattleData`, and `config/us/symbols.txt` (the previously pinned reference under the external data directory). No upstream code copied.
- `BackgroundLayer.observe_scene/prepare`, `BackgroundJob`, `Compositor`: generation-tagged jobs; reset/map/battle/transition identity changes invalidate results. Native fallback, HD toggles and title selection invalidate queued/in-flight replacements too. Pending jobs stay newest-only; results are checked again after composition, including shutdown. A changed same-map table increments generation even while artwork is loading. Event-barrier tests cover the races.
- `SCRIPT_MAP_CHANGE_STATE` at USA `0x80134DDE` is int16. `map.c:scriptTickChangeMap` sets it at fade-out and clears it after `changeMap`; HD replacement is suspended during it. This does not establish the beginning/end of every non-script transition.
- `PictureCache`: one loader thread, one latest pending request, 128 MiB decoded-image LRU. Previously each map miss started another daemon thread, and A → B → A discarded A's resampling. Previously observed successor maps can be prefetched, using **only dimensions actually read from the game**, not guessed from picture size. Predictions/layouts are bounded to 64 entries. Demand loading takes priority. Source pictures above 32 megapixels are rejected with native fallback. Decode errors are remembered in a bounded queue rather than retried every frame.
- Whole-map tint cache remains FIFO, now at most four entries **and** 64 MiB. Keys distinguish source pictures, avoiding old asynchronous jobs populating a new map's tint cache with the wrong image. Arrays referenced by a current/in-flight job and one active decode can temporarily sit outside the LRU budget; the budget is not a process-RSS cap.
- `BackgroundLayer._prepare/_read_vram`: one serialization reused for both tile recovery and palette in the same prepare call; truncated VRAM explicitly rejected. Palette sampling is still periodic, since no verified direct VRAM API exists in this core. It has **not** been replaced by guessed RAM palettes.
- `avatars.load_avatars_isolated`, `EmulatorWorker._start_avatars`: one spawned, low-priority renderer process with a one-way pipe, one rendering task, cooperative cancellation and bounded shutdown. Only this disposable process may be terminated if its decoder cannot stop; the emulator is never forcibly terminated. No more process-wide `sys.setswitchinterval` workaround in normal play. Cache format and visible avatar rendering are unchanged.
- `libretro.py`, `player.py`, `telemetry.py`: opt-in monotonic timings for core run/serialize/RAM reads, background RAM/VRAM/prepare/compose, resampling, avatar batch, Qt preparation/paint/delivery, queue latency; pending replacements, stale results, repeats and over-budget work counters. Bounded 2,048 timing samples per fixed metric and 256 events; lifetime counters are retained. Linux RSS is sampled for player and avatar process; unavailable platforms report null, not zero. Reports contain no RAM, images or save-state bytes. A load/reset cuts timing continuity rather than inventing battle/transition duration across a quick load.

## Measurements

External results: `/home/drdark/dev/DW1-coop-data/p0-runtime/`. These are single-run samples on Linux with user-supplied Beetle PSX `0.9.44.1 ea14d6a8`, USA game and scph5501 BIOS. No Windows performance claim. The harness reads the exact old HD module from the starting commit, using the **same current instrumentation/core harness** for both implementations. It is not an original whole-app benchmark.

File City inspection `20261103-040053-253666`, map 168; 600 emulated frames per run, standing, no avatars. This is **unpaced pipeline work**, not display FPS. Only 32–35 compositions per run; p99 is effectively the maximum. Startup/resampling included.

| Stage | Baseline p50 / p95 / p99 ms | Current p50 / p95 / p99 ms |
| --- | --- | --- |
| Compose native | 7.151 / 7.659 / 7.668 | 7.193 / 7.518 / 7.739 |
| Compose 2× | 10.598 / 10.862 / 11.035 | 10.814 / 11.049 / 11.260 |
| Compose 4× | 13.313 / 13.454 / 13.456 | 13.277 / 13.427 / 13.490 |
| Prepare 4× | 0.158 / 4.329 / 8.135 | 0.197 / 4.444 / 8.220 |
| Core run 4× | 2.902 / 3.262 / 3.548 | 2.906 / 3.371 / 5.084 |

No material steady-state compositor speedup demonstrated. First picture load/resampling took 172–213 ms on its loader; cache revisit avoids repeating it (synthetic LRU/revisit tests, not timed natural zone round trips).

Avatar impact: same map, 2×, 1,800 frames, no HD, cold cache, six requested species/five supported images. Previous **0.2 ms switch-interval mitigation** versus new isolated process:

| Stage | Thread p50 / p95 / p99 ms | Process p50 / p95 / p99 ms |
| --- | --- | --- |
| Core run | 2.041 / 3.872 / 4.363 | 2.040 / 2.249 / 3.483 |
| Frame work | 2.145 / 4.035 / 4.768 | 2.145 / 2.448 / 3.722 |

This supports lower GIL contention; it is not a claim of 60 FPS on all machines or scenes. Avatar batch was slower in isolation (1.92 s vs 1.66 s). An earlier unmitigated thread run is also retained but is not the proper production baseline.

Battle replay: inspection `20261103-093656-777038`, map 109, 2×, 2,400 frames, periodic Cross input. Both versions observed game state 1 → 2 → 0 and returned to exploration. Median coverage 94.204% for both; all 11 sampled native-frame hashes match. Compose p50/p95/p99: baseline 11.816/16.478/17.664 ms, current 11.758/16.393/17.504 ms. Over-budget **synchronous harness** work: 735 vs 734 frames; these are not GUI dropped frames (the real app composes asynchronously). Startup was already inside battle entry, so no invented complete battle-duration sample is reported.

A separate 1,800-frame replay captured native and composed post-battle images under `battle-capture/`. Inspected visually: environment, characters, clock and status bars remain present and aligned. This verifies **one replay**, not consecutive battles or defeat. The chosen inspection and patched disc match the older documented diagnostic pair; never substitute a different disc when loading states containing imported VRAM/RAM assets.

## Changes after review

`Compositor(paint=)` paints unlocked and delivers under the lock, so the emulator never waits for the HUD. `PictureCache` survives any loader error. The avatar renderer talks over a one-way pipe, so its death ends the read. World zoom's slider is gone; the [×2 camera frame](FIXED_FRAMING.md) is a separate option.

## Running diagnostics

Normal launch with opt-in telemetry (use a fresh **external** report directory):

```bash
cd /home/drdark/dev/DW1-coop
DW1_COOP_TELEMETRY=/home/drdark/dev/DW1-coop-data/p0-runtime/manual \
  PYTHONPATH=src /home/drdark/.venv/bin/python -m dw1_workbench.play_app
```

Reports are written on clean player shutdown with exclusive, unique filenames. Normal launch uses normal user saves; **for automated work use the isolated harness below**, not normal launch. Neither harness auto-migrates settings, copies a whole disc, or loads a memory card from the original save store. They use explicit selected save inputs and temporary emulator storage. Both accept `--help`.

```bash
QT_QPA_PLATFORM=offscreen PYTHONPATH=src /home/drdark/.venv/bin/python tools/profile_runtime.py \
  --core /usr/lib/libretro/mednafen_psx_libretro.so --bios /home/drdark/Downloads \
  --disc /home/drdark/Documents/dw/overrides/patched/patched-<your disc's hash>.cue \
  --store /home/drdark/.dw1-coop/play/06084490ea0db340 \
  --inspection 20261103-040053-253666 \
  --hd /home/drdark/dev/DW1-coop-data/p0-runtime/hd \
  --resolution 2x --frames 600 --output /home/drdark/dev/DW1-coop-data/p0-runtime/new-run
```

Add `--baseline` for the old HD module. Omit `--hd` for native graphics. `--avatars thread|process` measures a cold-cache avatar batch; `--press B` uses periodic Cross, other directions hold that button. `--captures` explicitly writes proprietary final native/HD images **outside Git**. Never pass a Git checkout as output. `--slot N` can replace `--inspection ID`.

For real Qt/worker/presentation preparation, substitute `tools/profile_player.py`, `--seconds 10 --roundtrip --toggle-hd --avatars` for `--frames 600`. This writes/loads **disposable slot 9 only**, uses isolated settings, disc read-only and no audio device. Offscreen paints do not measure monitor presentation or vsync.

## Acceptance status and next task

Final automated command: `QT_QPA_PLATFORM=offscreen PYTHONPATH=src /home/drdark/.venv/bin/python -m pytest -q` — **366 passed in 10.45 s**, including 18 new runtime-stability cases. Executed outside the sandbox for Qt audio initialization; test fixtures isolate settings/saves. `git diff --check` and `compileall` passed. The concurrent save-note test correction was preserved. No tests require commercial assets.

Additional 4× real Qt smoke (`qt-smoke-4x/`, 8 seconds including startup/shutdown): 474 core frames, 25 composed deliveries, 26 avatar previews, two over-budget work frames, disposable slot 9 saved/loaded, HD off/on, no reported failure. Core p50/p95/p99 3.174/3.731/4.552 ms; Qt preparation 1.469/8.894/10.133 ms; paint 0.602/0.930/1.888 ms; compose 14.753/19.174/21.938 ms. Sampled player RSS 364–376 MiB, renderer child about 71 MiB. This is short, offscreen and standing, not a memory-soak or desktop-FPS guarantee.

Original source BIN re-hash still `06084490ea0db34099b31495ccba4ec18fb0ff25c7683c508e9d2db93d4495f4`. All emulator writable paths in diagnostics were disposable directories, not user save storage.

- Synthetic: bounded telemetry, byte-width state read, battle-clobbered table retention/different-map rejection, in-flight scene/reset/native-fallback/stop rejection, single-serialization battle recovery, truncated VRAM, bounded latest-only loading/LRU, A → B → A known-successor preload, tint identity, subprocess error/cancellation cleanup, script transition/native fallback, same-map table generation and world-zoom-disabled guard.
- Real core: native/2×/4× standing pipeline comparisons, one battle-entry-through-return replay, real avatar generation and the Qt smoke runs recorded above. `qt-smoke/` used 2× for 10 seconds with cold avatars, save/load and HD off/on: 581 core frames, 31 composed deliveries, two over-budget work frames, no stale delivery/error observed. Qt preparation p50/p95/p99 0.582/8.007/9.311 ms; paint 0.634/0.790/1.101 ms; delivery latency 0.597/2.107/2.152 ms. Player RSS about 347–348 MiB during play (not including child in that first report). No claim of high unique-frame FPS in a standing scene.
- **Still pending:** multiple naturally consecutive fights, a demonstrated defeat and recovery, natural repeated/rapid A → B → A exits, saving/loading during those exits, full split-screen/shared-fight validation, long-session cache/memory soak, Windows subprocess/priority packaging and desktop/audio/vsync checks. The new scripted-transition metric only covers its verified flag, not every zone-loading route. No measured natural-zone-transition before/after improvement yet.

Next: use a copied store and the correct patched disc to traverse two connected zones repeatedly, with HD on/off and 2×/4×, while recording the transition flags, generation events, screenshot pairs and cache counters. Then run consecutive win/defeat/shared-fight cases. Preserve user originals and keep world zoom off. Do not describe the user's full graphical-glitch/FPS report as fixed until those real scenarios pass.
