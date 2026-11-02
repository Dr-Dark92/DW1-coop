# Handover

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
