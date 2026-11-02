# Handover

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
