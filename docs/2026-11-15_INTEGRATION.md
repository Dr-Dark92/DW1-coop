# 2026-11-15 integration: starters, settings, HUD and backgrounds

This checkpoint is on `python-player`. It describes the current Python/PySide6 player running the user's USA disc through Beetle PSX. It does **not** claim changes to a separate native PC port. No disc, BIOS, emulator binary, save, extracted artwork or generated analysis/cache belongs in this repository.

## How the pieces connect

```text
PlayPanel settings → PlayConfig → EmulatorWorker (core thread)
                                  ├─ FrontMenu: title / Jijimon / Settings
                                  ├─ EvolutionController: roaming menu and EXP evolution
                                  ├─ GameRules + LifespanPause: separate mechanics
                                  └─ HudState / RoamingOptions: read-only display

User WebP → HdPictureWorker → external HdPictures store
                             → PictureCache / BackgroundDecoder (one loader thread)
                             → BackgroundLayer.prepare (core thread)
                             → Compositor (render thread) → Qt presentation
```

| Module | Responsibility |
| --- | --- |
| `front_menu.py` | Scene-gated title menu, seven-choice question, controller-operated settings, reversible starter argument hook |
| `evolution.py` | Existing roaming menu integration; Classic/EXP action differences; native action handoff |
| `gameplay_options.py` | Immutable visibility options, persistence keys, alternate desktop HUD dialog |
| `play_panel.py` | Launch defaults, configuration creation, synchronization after in-game changes |
| `player.py` | Core-thread wiring, settings-change signal, persistence on GUI thread, clock, save guards and rendering adapters |
| `progression.py`, `lifespan.py` | EXP rules versus experimental hourly-lifespan pause |
| `hud_view.py`, `game_menu_view.py`, `menu_icons.py` | HUD, themed menus and original code-authored Settings sprite |
| `background_animation.py` | Bounded WebP metadata, timing, decoding and visible-window resampling |
| `hd_background.py` | Override storage, bounded asynchronous cache, native-background matching, tint/alpha/composition |
| `gui.py` | Cancellable background import and safe shutdown tracking |

## 1. Digimon starter selection

Jijimon's time-of-day question now presents only these labels—no names or portraits before confirmation:

| Visible answer | Hidden starter | Native partner ID |
| --- | --- | --- |
| Late Night | Gabumon | 17 |
| Dawn | Patamon | 31 |
| Morning | Palmon | 46 |
| Midday | Agumon | 3 |
| Late Afternoon | Biyomon | 45 |
| Dusk | Penguinmon | 57 |
| Evening | Kunemon | 32 |

Evening originally proposed Tentomon. Inspection found Tentomon111 is an NPC outside the native `RaiseData[66]` care table. The user replaced it with Kunemon; use partner32, not the similarly named NPC152.

`FrontMenu.before_frame()` checks exact world-object IDs, instances and callbacks. Title integration requires object5000 with native title tick/render callbacks and both current/target menu0. Jijimon integration requires object117, instance2, tick`0x800F1498`. A shared `SCRIPT_STATE_2` value alone is never sufficient.

For the time question, native states6/10 become wait-state1 while the host draws the choices. Confirmation writes PSTAT254 through a bounded script-state pointer and resumes native state`0x11`: acknowledgement, naming and the rest of the new-game flow remain native.

The native new-game branch normally selects Agumon/Gabumon before calling `initializePartner`. We verify and temporarily replace **both** `li s0` argument instructions:

- `0x800EE9D0`: original`0x24100011`.
- `0x800EE9D8`: original`0x24100003`.

Only the seven allowlisted partner IDs are accepted. Unsupported code or core support fails clearly instead of writing arbitrary memory. Beetle instruction-cache synchronization makes the RAM changes effective. The original code is restored when the native partner object is created; positive HP and lifespan establish completion. Reset, load and shutdown restore owned temporary changes. Quick-save and inspection capture are rejected during starter selection/initialization so temporary patches cannot escape into saved states.

This is **not** a raw entity-ID swap: native code loads the model, sounds and care data. Native initialization equips starting regular attacks only for3/17. For the other five, the adapter equips the first valid regular technique in that species' executable-linked list and sets its learned bit(s), preserving the native finisher and existing learned bits. This is our starting-move policy, not a claim that the original game offered these starters. Native PSTAT branch behavior supplies initial stats: verified Agumon800HP/600MP; the other selected branches700HP/700MP.

Existing baseline work remains distinct: the Digi-Dex uses stable resource IDs, including green Goburimon80 versus blue SnowGoburimon103, not a display-name merge. This checkpoint does not implement Digimon model bundle import/export or new animation reconstruction.

## 2. Settings and gameplay style

Title Settings appears beside native New Game, Load Game, Delete Game and Battle Mode. Native actions0–3 still delegate to the original cursor/action flow; the fifth action is host-rendered. Settings is also available in the roaming menu. Navigation uses the existing D-pad/Cross/Circle controller path and blue/gold rendering; the desktop toolbar dialog is optional, not required.

`SettingsMenu` gets a snapshot through a callback and sends changes back to `EmulatorWorker._change_gameplay`. That worker owns core-affecting changes. It emits `gameplay_changed`; `PlayWindow._persist_gameplay` writes QSettings on the GUI thread and notifies `PlayPanel` to update its controls. The busy worker does not depend on a queued GUI-to-worker slot to process input.

- **At the title:** changing Classic/EXP reconfigures rules and native/manual evolution selectors before New Game or Load.
- **While roaming:** a style change is staged for app restart; it does not rewrite progression halfway through play. The menu labels this distinction.
- **Classic:** native progression/evolution, no manual-evolution blocking patches. The settings/menu adapter still works.
- **EXP:** existing EXP/stat rules and manual level-based evolution. No new evolution thresholds were invented for this checkpoint.

Configuration keys are `play/game_rules`, `play/lifespan` and `play/roaming/<field>`. These remain **shared launch defaults**, not per-playthrough immutable rules. Switching to Classic does not undo stats already awarded by EXP. Per-playthrough setup/locking is unfinished.

## 3. HUD visibility is not care logic

`RoamingOptions` is a frozen snapshot with independent `hp_mp`, `lifespan`, `hunger`, `poop`, `fatigue` fields. `read_hud` reads the source fields; `HudPainter.draw` chooses visible rows. Nothing about hiding a row changes the game's care values.

USA `PartnerPara` at`0x80138460` supplies signed16-bit lifetime hours`+72`, food energy`+60`, toilet countdown`+24` and fatigue`+34`. Native `RaiseData` at`0x801225BC`,66records×28bytes, supplies energy cap`+8` and default toilet timer`+12`. The food bar is energy fullness; the toilet bar uses a native countdown, not an invented urgency percentage. The toilet timer can exceed its species default. Values are retained; only displayed bar fill clamps. Lifespan360hours is a reference, not a proven species maximum. The existing native happiness/discipline bars remain untouched.

The normal native HUD/menu visibility gate is retained. Alternate desktop settings pause the worker while modal and restore the prior pause state. In-game Settings already uses the existing game-menu input/display path.

## 4. Lifespan is independent and still experimental

The old EXP behavior freezes age, lifetime and lives. `lifespan_enabled=None` preserves that legacy behavior. Explicit Enabled or Paused disables the old immortality guard in `GameRules`.

`LifespanPause` takes frame-local snapshots around `core.run_frame()`. It compensates normal one-hour lifetime loss, protects the last positive hour from reaching zero during that clock tick, and does not restore zero lifetime, lives, age, battle losses or arbitrary lifetime changes. It does not carry stale snapshots through a load/reset/evolution.

**Limits:** this is not complete immortality or a full lifespan-off implementation. Scripted sleep/training jumps and natural death/illness/defeat combinations still require verification and work. Native age and evolution timers continue advancing. The menu marks hourly pause with an asterisk and explanation.

## 5. Item-icon extraction

`tools/export_item_icons.py` reads the original CUE/BIN filesystem, `ETCDAT/ITEM.TIM`, and the USA executable. It exports12816×16 cells using actual `ITEM_CLUT_DATA` palette assignments (`0x80127BDC`), not one arbitrary atlas palette. Output includes PNGs, contact sheet, names, item IDs, UV/CLUT coordinates and source extent/hash provenance. Output must be a new directory outside the checkout.

Inventory and ground share the atlas/palette setup; ground items are distance-scaled camera-facing sprites, not separate recovered models. Native last-column UV span15 and enlarged-ground15×15 spans are recorded; exports retain full16×16 cells. Battle-menu call-chain reuse remains unverified.

## 6. Animated background replacement

The previous HD importer converted WebP to PNG, dropping animation. It now retains original WebP bytes. `HdPictureWorker` performs validation/import off the GUI thread, checks cancellation before committing, and participates in workspace shutdown tracking. Replacement is explicit: the new file is written atomically before old override removal. Failure/cancellation leaves the old image in place. The GUI still performs a bounded header-only shape check.

`BackgroundDecoder` opens at most one animation source on the single `PictureCache` loader thread. RIFF bounds/ANIM loops/ANMF durations are checked; Pillow/libwebp handles full composited canvases, alpha and disposal. Limits:64MiB compressed,8MP animated canvas,600frames. Durations below10ms normalize to10ms; normal16/17ms frames are preserved. Finite loops hold the last frame.

The128MiB decoded-frame LRU is separate from decoder buffers, temporary output and in-flight jobs, which add bounded memory. There is only one active loader request and one latest pending request. Latest completion is tracked independently of LRU access; loop epochs reject old-loop results and frame selection cannot run backwards within a loop. Corrupt later frames invalidate the source and fall back to native graphics, rather than leaving a misleading frozen replacement.

Only a verified full-canvas opaque VP8 frame without an ALPH chunk can be treated as an independent still. RGB final output does not prove delta independence: VP8L/ALPH frames may depend on preceding frames. Those use sequential canvas decoding, with cancellation checks between frames.

Animations retain source-resolution canvases and resample only the visible camera window with bicubic filtering. Animated tints are applied to that window, not cached as whole-map images per frame. The existing native background matcher, foreground masking, scene generations and stale-job rejection remain in charge. Source alpha blends with native background only on confirmed background pixels, preserving characters and HUD.

`EmulatorWorker._visual_time` advances by`1/core.fps` only when running a core frame. App pause freezes playback; fast-forward accelerates it. Repeated-native-frame skipping is disabled while animation is active so standing still does not freeze animated art. Map/state changes restart animation phase; existing static battle-reset behavior is preserved.

This supports **external HD zone replacements only**. No animated TFS/disc encoding, title animation or animated Digimon portrait support is claimed.

## 7. Validation and measured limits

Run from the checkout, with the project virtual environment selected:

```bash
QT_QPA_PLATFORM=offscreen PYTHONPATH=src /home/drdark/.venv/bin/python -m pytest -q
PYTHONPATH=src /home/drdark/.venv/bin/python -m dw1_workbench.app
PYTHONPATH=src /home/drdark/.venv/bin/python -m dw1_workbench.play_app
```

The suite isolates settings/saves via`DW1_COOP_HOME`. Real-core diagnostics use separate output directories/private cards and read user states/discs without changing them. Each tool has`--help`:

- `tools/check_front_menu_core.py`: real title → Settings → Jijimon route.
- `tools/check_starters_core.py`: copied question state → all seven native starter initializations.
- `tools/check_roaming_core.py`: controlled native versus paused hour boundary.
- `tools/check_animated_background_core.py`: static/animated File City comparison.
- `tools/export_item_icons.py`: verified item atlas extraction/provenance.

Real starter check completed all seven expected IDs, positive HP/MP and360-hour lifetime, with regular attacks equipped/learned. Patamon and Kunemon actual game frames were visually inspected. Both native question branches have synthetic coverage; full combat/evolution acceptance for every starter is not complete.

Controlled lifespan check12:59→13:01: native life5→4 versus paused5→5, lives unchanged. This is not proof for sleep/training or death edge cases.

The supplied File City WebP is2048×1706,180frames,3000ms,infinite loop,34,810,788bytes. It matches File City204/TWNA01's6×5tile aspect ratio. At4× output in a copied slot1 test:

| Measurement | Static | Animated |
| --- | --- | --- |
| Core frame p50 / p95 | 2.264 / 4.347ms | 2.585 / 5.613ms |
| Compose p50 | 9.727ms | 49.084ms |
| Composites / observed seconds | 154 / 3.125 | 198 / 10.145 |
| Distinct animation source frames displayed | — | 7 |

It renders with actors/HUD intact and loops correctly after the epoch fix, but **stutters badly** because large delta dependencies are expensive. No smooth60FPS claim. A native renderer could remove emulator background matching, but would not automatically remove this codec bottleneck. Next work is an optimized bounded playback representation or a15–20FPS re-export, followed by measured comparison.

## 8. Local installation and rollback

After isolated testing, the user explicitly requested live replacement. Installed copy:

`/home/drdark/Documents/dw/overrides/hd/204-TWNA01.webp`

Previous PNG backup:

`/home/drdark/dev/DW1-coop-data/file-city-before-webp-rG9gre/204-TWNA01.png`

The original`/home/drdark/Downloads/background-palette0.webp` remains unchanged. HD backgrounds were enabled and the player launched. These are local user-data operations, **not Git content**. To roll back, close the player, preserve/remove the live204WebP through the override UI or a recoverable move, and restore the backed-up PNG. Never remove the only backup.

Private reports/captures are under`/home/drdark/dev/DW1-coop-data/`: `starters-20261115-3`, `front-menu-20261115-2`, `roaming-core-20261115`, `item-icons-20261115`, `animated-file-city-20261115-2`. Earlier diagnostic runs may be incomplete; use these final reports.

## 9. Preparing a native-port implementation

Keep the hidden mapping, immutable visibility options, timing/loop policy, bounds and tests. Replace the adapter boundaries:

1. Replace world-object/RAM scene detection with explicit title/new-game/gameplay states.
2. Replace MIPS argument/cache patches with a supported partner-spawn API that initializes model, sounds, care, stats and techniques together.
3. Replace raw care reads with typed partner state; retain units and separate display flags from mechanics.
4. Keep progression-mode changes at safe boundaries and add real per-playthrough persistence/migration.
5. Send prepared background frames to a native texture/render path, keeping foreground, entities and HUD as separate layers rather than emulator pixel matching.
6. Budget codec, frame/cache and GPU memory independently. Validate delta-frame preparation and performance before promising smoother playback.

Do not carry hardcoded USA RAM addresses, emulator cache tricks or the assumption of a conventional3D scene into a native implementation. The engine-specific adapter is replaceable; the source evidence, policy and regression tests are the reusable foundation.

Evidence: local MIT`dw_decomp` revision`aaf69a4` (`main.c`, `main_menu.c`, `new_game.c`, `shop/script_menu.c`, `partner_impl.c`, `utils2.c`, headers/symbols); original USA executable/CUE; [WebP container specification](https://developers.google.com/speed/webp/docs/riff_container); [Pillow WebP decoder source](https://pillow.readthedocs.io/en/stable/_modules/PIL/WebPImagePlugin.html). No upstream decoder/game code or proprietary art is bundled. See [FORMATS.md](FORMATS.md) and [HANDOVER.md](HANDOVER.md) for address-level evidence and remaining acceptance gates.
