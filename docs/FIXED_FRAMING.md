# ×2 camera frame

**Experimental, off by default.** Tick **×2 camera frame (experimental)** under Play > Settings > Graphics (`play/double_frame`, `PlayConfig.double_frame`); it applies next launch. While walking around, the view shows twice the world across and down in the same frame, with smaller characters. It needs the USA game, live RAM and the 8 MB core.

It's separate from quality: the resolution menu (Native, 2x, 4x) sets how sharply the frame is drawn, and this sets how much of the world is in it. The older adjustable World zoom (`camera.py`'s `CameraZoom` in the player, `WORLD_ZOOM_ENABLED = False`) stays off.

## When it's on

`fixed_framing.FrameController.tick()` runs between emulator frames.

| Situation | Camera |
|---|---|
| Walking, talking, pause, the main and Item menus | ×2 |
| Fight (`GAME_STATE` not 0) | The game's own, back at ×2 one frame after |
| Map change (`SCRIPT_MAP_CHANGE_STATE`, or the map number changes) | The game's own, back at ×2 two frames after |
| Map under 5 × 4 tiles, or too small to fill the frame | The game's own, with a log message. Tried again on the next map |
| Quick save, inspection, console reset | The game's own code is restored first, and ×2 returns on the next frame |
| Loading a state | Starts clean. Back in two frames if the state holds the memory, otherwise after 60 frames |
| Stock 2 MB core | Never; a log message says why |

The first start in a session waits `SETTLE_FRAMES` (60) because `extend_heap` must have run; later ones wait `RETURN_FRAMES` (2). A failed early try isn't final until 60 calm frames have passed, since a map may still be loading.

## How it works

The transform for a native screen point p, with common anchor A and scale s (128/256), is `s*p + A*(1-s)`. `fixed_anchor` in `camera_engine.c` keeps A at the screen centre, or clamps it so the view stays over the map's art. Game logic never sees it: `VIEWPORT_DISTANCE` has its real value whenever logic runs.

`FixedFramingProof.install()` checks the exact native code first, and refuses on any difference. It then makes these patches in RAM, all undone by `uninstall()`:

| Address | Native | Patched to | Why |
|---|---|---|---|
| `gameLoop`'s `GsSetProjection` call | Native H | `fixed_set_projection` | Scaled H, and a GTE offset in 16.16 for half pixels |
| `gameLoop`'s `renderObjects` call | | `fixed_render_objects` | `VIEWPORT_DISTANCE` is scaled only while drawing, which sizes sprites and effects. A frame can end mid-draw, so `tick()` accepts either value |
| `GsSortSprite` 0x80096F90 | | `fixed_sort_sprite` | Rotated or scaled sprites go through `RotTransPers4`, which adds the GTE offset. Without this the clock hand drifts and bubbles are misplaced |
| `tickCameraFollowPlayer` 0x800D6F64, `initializeDrawingOffsets` 0x800D6A4C, `tickCameraMovement` 0x800D80A0, `moveCameraByDiff` 0x800D892C, `isTileOffScreen` 0x800D3078 | | `fixed_native_0..4` | They measure through the GTE to move the camera or route the partner. At ×2 the reading is half, and the camera falls behind until the player leaves the frame. They run with native H and a zero offset |
| `handleBattleStart` 0x800E847C | | `fixed_native_5` | A fight brings in everyone marked on screen, and the fight is on the game's camera. On-screen is re-tested at the native view first |
| `renderMap`'s tile loop 0x800D590C | | `zoom_render_tiles` | Tiles are drawn from a ring of half-size copies, with `Params.pad == 1` |
| `uploadMapTileImages`, `updateTileRow`, `updateTileColumn` | | Return at once | They would overwrite the ring |
| `buildMapOverlayPrim`'s call 0x800B5074 | `setPosDataPolyFT4` | `fixed_set_foreground` | Scales foreground pieces |
| `renderMapOverlays` limits 0x800B4460, 68, 74, 7C | -40, 360, -60, 300 | -360, 680, -300, 540 | The game skips pieces outside its own view |

Displaced first instructions run from stubs in `FixedState` (`camera_engine.c`), after a check that they aren't branches or jumps.

**Why tiles use `DRAWING_OFFSET_*_COPY`:** `GsSetOrign` runs before the camera moves, so the GPU's offset is a step behind the variable. This cancels at the game's scale and not at ours, where the background would jump by each step's size. Tiles and foreground draw from the camera at the start of the frame.

**Why H is scaled but RAM isn't, outside drawing:** the old prototype changed `VIEWPORT_DISTANCE` for good, and that reached game logic.

## Memory

The engine lives at 0x80210000-0x80223200: tile buffer, code at 0x80221000, parameters at 0x80223000. That's low in the extra RAM because `malloc3` hands out a free block's far end first, so a played game fills the top of the heap long before this.

`camera_memory.CameraReservation` takes the range out of the game's free list and **keeps it, saves included**. It costs about 77 KB of about 6 MB, and once given back the game may use it. A later `reserve` recognises a saved hold (header `0x46524350`), rejects leftover header bytes inside a free block, and copes with a free block starting right at the hold, as saves from before the hold was permanent can have.

## Backgrounds

The ring stores texels 2i + 1 of each tile, so the ×2 frame is exactly a half-size map. With `BackgroundLayer.wide` set, HD composition works over `indices[1::2, 1::2]` at `wide_camera`, mirroring the engine's rounding.

- **The map has an HD picture:** it's loaded at half the scale (`PictureCache` keeps both sizes) and composed as usual.
- **No HD picture:** `_tiles_as_picture` builds one from the game's own full-size tiles, which restores the detail video memory can't hold. Cut-out foreground pieces stay half-size here, because the picture doesn't contain them.
- **HD backgrounds turned off:** the half-size tiles show.

## Tools

Every tool here uses a disposable core and temporary storage, and refuses an output folder inside the checkout.

```bash
QT_QPA_PLATFORM=offscreen PYTHONPATH=src /home/drdark/.venv/bin/python tools/compare_framing.py \
  --core /home/drdark/dev/DW1-coop-data/core-8mb/mednafen_psx_8mb_libretro.so \
  --bios /home/drdark/Downloads \
  --disc /home/drdark/Documents/dw/overrides/patched/patched-966c5f9da2cd861b.cue \
  --inspections /home/drdark/.dw1-coop/play/06084490ea0db340/inspections \
  --inspection 20261105-053603-355650 --scale 128 --direction Down --frames 300 \
  --output /home/drdark/dev/DW1-coop-data/framing-next
```

The disc's name is a hash that changes when your imports change, so check `launch.log` for the current one. `--scale 230` is the original 90%; the report and image call the framed run `fixed90` at any scale. `tools/try_fixed_framing.py DISC` is the older interactive 90% trial, and `tools/check_camera_reservation.py` exercises the memory hold alone. To rebuild the engines, which needs clang and lld:

```bash
PYTHONPATH=src /home/drdark/.venv/bin/python tools/build_camera_engine.py
```

```bash
PYTHONPATH=src /home/drdark/.venv/bin/python tools/build_camera_engine.py --fixed
```

Run both: `tests/test_camera.py` checks each generated file against the C source's hash.

## What's been checked

On the real 8 MB core with disposable copies, captures in `DW1-coop-data/framing-x2-*`: walking on Native Forest and File City, with world positions equal to normal play at every shared clock tick in the two Native Forest comparison walks; a full fight and return; Native Forest to Green Gym; pause, main and Item menus; quick save, load and reset in the real window; HD at 4× on File City and on a map with no picture, about 81% and 82% of the frame replaced; frame-to-frame scrolling with no reversals, against ±2 px before the fix.

In the user's desktop sessions on 2026-11-05: over an hour in total, with wins including back-to-back fights and a two-enemy fight, and no crash.

## Known issues

- **Menu stays up for about a second after closing** (inspection `20261105-085422-266154`, File City). In a core-only test the game's menu box closed in 11 frames at the normal camera and 16 at ×2, so the rest of the second isn't explained by that. The player and HD path haven't been timed.
- **Floating Digimon** (inspection `20261105-085627-443205`, Native Forest MAYO01). The screenshot shows a very large Digimon and two emote bubbles in the top right corner, with the player out of sight. Loading the state lands in a fight at the normal camera, so this didn't reproduce. Cause unknown.
- **HD turning itself off** with `index 320 is out of bounds`: the fix (`_current_foreground`) was reasoned from the code. It didn't recur in the one session since, which proves little.
- **On-screen differs from normal play.** Entities count as on screen across the wider frame, except for the checks listed above. Behaviour keyed to `isOnScreen` elsewhere can differ.
- **Saves from before the follow fix** (the first ×2 sessions on 2026-11-05) can hold a drifted camera, in normal play too. A map change should reset it, from reading `initializeDrawingOffsets`; not tested on those saves.

**Not tested:** defeat, groups of three or more since the battle fix, the Digimon stats screen's portrait, Steady 3D, split screen and co-op (not blocked, so leave ×2 off there), evolution, memory across very long sessions, Windows.

## History: the original fixed 90% proof

**Experimental, isolated comparison only. Not a normal-play option.** World zoom remains disabled in `player.py`. No slider, no user save writes, no edits to the original or patched disc. This completes the first standing-frame proof, not the walking/battle/transition acceptance gate.

### Baseline and reproduced defect

User inspection **check screen size 1**, `20261105-053603-355650`, Native Forest/MAYO00 (map 109), source state SHA-256 `ab727c429c153ea6c537227439cba1ae28282bb6806df68a6105e3b12efcea9a`. Loaded read-only into a disposable instance of the user's 8 MB Beetle core, with the corresponding patched disc, scph5501 BIOS, 2× rendering and dithering disabled. Tests here do not establish PGXP correctness.

The old prototype at 230/256 (89.84375%) reproduces the visible displacement: sampled world positions stay the same but the rendered characters shift against the background. Projection RAM changed 1511 → 1357. Old capture/report: `/home/drdark/dev/DW1-coop-data/fixed-framing-baseline-1/`.

Two separate render issues were investigated:

1. Models may be emitted **before** `renderMap`; setting GTE H inside the tile engine is too late. Keeping RAM H native while changing H only there produced another incorrect candidate. `main.c:gameLoop` sets projection before `renderObjects`; the proof hooks that specific, validated call instead.
2. Lower H alone does not supply the translation needed to make 3D and background corners share an affine anchor. The proof also sets GTE OFX/OFY. Independently, if a native point is `p = projected + D`, the corrected result is `s*(p-D) + D + (A-D)*(1-s) = s*p + A*(1-s)`, where D is the drawing offset and A is the common framing anchor. Both axes use this rule. Integer rounding is bounded/tested, not corrected with guessed per-character offsets.

Changing the game's `VIEWPORT_DISTANCE` variable also changes a value read by `vecmath.c:mapToWorldPosition`, bubbles, items and other game code. The new proof **does not write that RAM variable**; it stays 1511. Only the render projection becomes 1357. These are demonstrated projection/alignment issues; the historical crash's cause is still unproven.

### Implemented

- `fixed_framing.py:FixedFramingProof`: fixed scale 230, no adjustable zoom, 8 MB diagnostic-core requirement, exploration/same-scene checks, finite-art coverage check, exact original call validation, native RAM projection invariant. Restores the native GTE origin/projection and original hooks on uninstall. Still uses the legacy zero-checked camera scratch area, so it is **not certified as heap-owned/safe**.
- `camera_engine.c`: diagnostic `Params.pad == 1` mode, `fixed_set_projection` and `fixed_set_foreground`. Shared edge-clamped affine anchor prevents exposing black beyond available art in this baseline. World coordinates are not shifted to conceal a rendering error.
- `tools/build_camera_engine.py` emits the two extra entry symbols and checks MIPS-I/HI-LO hazards; regenerated machine code fits the existing reserved code range. Legacy mode remains separate and disabled in normal play.
- `tools/compare_framing.py`: explicit paths, refuses source-checkout output and pre-existing output directories, reads the source inspection, starts a separate core with temporary writable storage, captures native/fixed 640×480 images and evidence. `--legacy` reproduces the previous implementation. No full game-image copies or user memory-card writes.

### Result and limits

Latest standing comparison: `/home/drdark/dev/DW1-coop-data/fixed-framing-clamped-2/`:

- `native-matched-clock.png`: native framing at the corresponding game-clock tick.
- `fixed90.png`: candidate expanded view, same 640×480 output.
- `report.json`: exact source state identity, world/GTE samples and comparison results.
- Visually inspected: the prior large vertical displacement is gone; characters and background use the same small-scale transform. The top black strip from the first candidates is gone after edge clamping. More lower/side scenery is visible. This is visual evidence for this standing scene, **not subpixel foot-placement certification for all maps**.
- All player/partner/NPC world positions match at **37 shared complete game-clock ticks**. Equal *emulator-frame* samples do not all match: the tile upload work costs time (39 engine frames during 120 emulator frames rather than the expected ~40). The report retains that false result and the matched-clock result rather than hiding the timing difference. Clock fields come from `clock.c`: hour `0x80134EBC`, minute `0x80134EBE`, current-frame `0x80134F08`. First/last partial ticks are excluded.
- The opaque status-bar-fill probe matches; a clock-face probe does not, so **full HUD pixel equality is not established**. Timing/hand-animation and rendering causes are not yet separated. HUD placement looks unchanged, but that is not a full mask test.
- This uses native textures, not the HD background compositor. The legacy tile ring at this scale uses downsampled tiles; no improved background-detail claim. HD matching must be extended to understand the fixed affine view before combining them.
- Finite artwork cannot supply scenery beyond its edges. Small maps are rejected; newly exposed objects can still be culled by native visibility rules. No added scenery is invented.
- Walking, foreground occlusion while moving, menus, battles, zone changes, save/load during the experiment, PGXP, split screen and extended play remain unverified. The class stops when it observes a scene change; that is **not** a pre-frame guarantee that a battle cannot overwrite scratch space. Hence it must not be exposed as a playable toggle yet.

### Reproduce

Choose a **new**, external output directory each run:

```bash
cd /home/drdark/dev/DW1-coop
QT_QPA_PLATFORM=offscreen PYTHONPATH=src /home/drdark/.venv/bin/python tools/compare_framing.py \
  --core /home/drdark/dev/DW1-coop-data/core-8mb/mednafen_psx_8mb_libretro.so \
  --bios /home/drdark/Downloads \
  --disc /home/drdark/Documents/dw/overrides/patched/patched-<your disc's hash>.cue \
  --inspections /home/drdark/.dw1-coop/play/06084490ea0db340/inspections \
  --inspection 20261105-053603-355650 \
  --output /home/drdark/dev/DW1-coop-data/fixed-framing-next
```

Synthetic regressions in `tests/test_fixed_framing.py` cover unique/truncated GTE fields, exact render hooks, unchanged RAM projection, complete RAM/GTE restoration, rejected scenes/small maps/cores and a compiled-host execution of the actual C projection wrapper with stubbed hardware calls, checked against independent affine geometry and viewport coverage. Real MIPS build checks supplement these tests.

### Next gate

Validation for this batch: **376 synthetic tests passed in 11.15 s** (six new fixed-proof cases); `git diff --check` and compilation checks passed. C-to-MIPS generation passed instruction/hazard checks. No commit or push.

First have the user inspect the paired standing images. Then establish reserved scratch ownership, moving-camera alignment and full HUD masks in a disposable store. Profile startup versus steady-state tile uploads, combine with affine-aware HD matching, and validate natural transitions/battles with conservative native fallback before adding any normal-play checkbox. Do not enable the old global world-zoom switch to bypass these gates.
