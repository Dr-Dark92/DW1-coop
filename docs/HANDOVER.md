# Handover

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
