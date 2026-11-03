> Historical P4 handover. Superseded by CLAUDE_HANDOVER.md for current P6 work.

# Handover for Claude

Prepared 2026-11-03 (Asia/Muscat). This is the current entry point. Older entries in `docs/HANDOVER.md` are historical and sometimes contradict later work.

## Start here

- Repository: `/home/drdark/dev/DW1-coop`.
- Branch: `python-player`.
- HEAD: `c350ead852f90443ff2277bbacc78be2d5ef94c8` — zone import/background repaint and prior handover.
- Remote repository: `https://github.com/Dr-Dark92/DW1-coop.git`.
- **Camera work and the new P4 native Digimon export/import are uncommitted. Preserve all existing changes.** No commit/push is authorized by this handover request; the user requests those explicitly.
- Do not initialize `/home/drdark` as a repository or modify `/home/drdark/.git`.
- No applicable `AGENTS.md` was found in the repository or its ancestor locations during this handover; check again if the environment changes.
- Read `README.md`, `docs/FORMATS.md`, `docs/PROGRESS.md`, then the top P4 entry in `docs/HANDOVER.md`. `docs/ROADMAP.md` is absent on this branch; the copy in the locally cached `origin/main` is older than this implementation.

The user moved focus from camera zoom to P4 Digimon import. They initially believed export was ready, then checked and corrected that assumption. A paired native exporter/importer was built. The next task is **in-game verification of a controlled Digimon replacement**, not restarting the project, completing the camera first, or implementing co-op. The proposed 16-bit-colour enhancement is explicitly parked.

## What is already working

The committed baseline includes USA disc import/cataloguing, streaming exports, TIM/ALLTIM decoding, verified character associations, model/animation previews, map/background/pathing tools, native zone package export/import, background/foreground repaint, patched-disc play, and the Python libretro player with quick saves and live inspections. This runs the original game through a user-supplied core; it is not a native runtime or co-op implementation.

The current uncommitted P4 addition provides one-character `.dw1digimon` ZIP packages:

- `model.MMD`: original native geometry **and animation data**.
- `texture.TIM`: the complete `0x4800`-byte ALLTIM slot, all palettes and padding.
- `texture.png` and provenance JSON: palette-0 texture inspection/editing.
- `skeleton.json` and `animations.json`: read-only reference metadata.
- `manifest.json`: format/version, original disc identity, character ID/key, parser versions, source paths/extents/offsets/sizes/hashes and member hashes.

The GUI in **Characters and Digimon** now has **Export Digimon package…**, **Import Digimon package…**, and **Restore selected Digimon from disc**. Export and import validation/apply run in cancellable workers. Import shows changed resources and the decoded texture before applying. Previews read imported resources. **Play with imported resources** replaces the old zone-only checkbox wording and builds a patched copy containing both zone and Digimon overrides.

This is a constrained native round trip, not arbitrary 3D-model import:

- Same USA disc identity and selected character resource ID/key only.
- Existing executable-owned skeleton, IDs, names, stats and evolution stage are retained.
- MMD byte length, header/object layout, animation slot presence/IDs and sound/texture-copy references remain fixed.
- TIM dimensions, colour mode, VRAM placement, palette count and block size remain fixed.
- PNG import uses the existing palette, reports nearest-colour approximation, preserves unchanged native indices (including duplicate palette colours), all CLUT rows and padding, and requires compatible 0/128/255 alpha. Use native TIM edits to change palette colours. Editing both TIM and PNG is rejected.
- Unsupported geometry is rejected explicitly. No new skeletons, expanded model allocations, cross-character retargeting, stage bundles or GLB/glTF conversion/export.

Changed assets live in `<project>/overrides/digimon/`. Content-addressed blobs are staged first; `imports.json` is replaced atomically last. Cancellation/failure cleans newly staged blobs while preserving the prior index. Original catalogue rows remain unchanged; the index retains provenance and SQLite logs record applied imports. Assets are re-hashed on reopen/use. Restore removes the active record; old unreferenced blobs are intentionally retained for reader safety.

## Files to review

| File | Relevant work |
|---|---|
| `src/dw1_workbench/digimon_package.py` (new) | `load_source`, native package export/read/validation, PNG index conversion, `DigimonOverrides`, ALLTIM slot merging. |
| `src/dw1_workbench/gui.py` | `DigimonWorker`, export/import/review/restore handlers; override-aware texture and model previews; combined patched-disc builder; cancellation/shutdown integration. |
| `src/dw1_workbench/animation.py` | Optional cancellation callback propagated through clip/instruction parsing. |
| `src/dw1_workbench/disc_patch.py` | Cancellation checks between replacement sectors. |
| `src/dw1_workbench/play_panel.py` | Generic imported-resource checkbox/log wording and combined override count. |
| `tests/test_digimon_package.py` (new) | Native round trip, compatible edits, malformed packages, provenance, isolation, persistence, cancellation and failed-write recovery. |
| `tests/test_digimon_gui.py` (new) | Background export → review → apply, GUI-thread delivery, and closing during active work. |
| `src/dw1_workbench/camera.py`, `player.py`, `tests/test_camera.py` (new test file) | Earlier uncommitted camera work; preserve it. |
| `.gitignore`, `README.md`, `docs/FORMATS.md`, `docs/PROGRESS.md`, `docs/HANDOVER.md` | Package exclusion and current documentation, plus earlier camera changes. |

Run `git status --short` and `git diff` yourself. `git diff --stat` does **not** include the new untracked module/test files until staged; do not miss them in a later user-authorized commit.

## Validation already completed

Latest implementation checks: **160 tests passed**, split into 156 non-player tests and 4 player tests. `git diff --check` and Python compilation passed. These are recorded implementation results; this handover-only update did not rerun the suite.

```bash
cd /home/drdark/dev/DW1-coop
QT_QPA_PLATFORM=offscreen PYTHONPATH=src /home/drdark/.venv/bin/python -m pytest -q --ignore=tests/test_player.py
QT_QPA_PLATFORM=offscreen PYTHONPATH=src /home/drdark/.venv/bin/python -m pytest -q tests/test_player.py
```

Qt audio initialization stalls in this sandbox. The four player tests passed when run outside it through the supported approval mechanism. Do not claim a new passing result from a stalled/terminated invocation. Synthetic fixtures contain no commercial game data. Existing environment: Python 3.14 at `/home/drdark/.venv`; dependencies are pinned in `constraints.lock`, and no dependency was added for P4.

Focused P4 checks:

```bash
QT_QPA_PLATFORM=offscreen PYTHONPATH=src /home/drdark/.venv/bin/python -m pytest -q tests/test_digimon_package.py tests/test_digimon_gui.py tests/test_gui_workers.py tests/test_animation.py
```

Real supplied USA Agumon validation passed:

- Resource ID 3, key `AGUM`, display name Agumon; model `CHDAT/MMD0/AGUM.MMD`, texture `CHDAT/ALLTIM.TIM#003`.
- Export/import preserves the native model and every one of the 18,432 texture-slot bytes unchanged.
- Original MMD SHA-256: `60d5e920b6cf621da8d2f67f703a2fe75f384000cbcdefd9398c76c1477c7c7d`.
- Controlled native palette-0 entry-3 change to blue `0x7c00` imported and reopened correctly. The imported workbench preview visibly shows the changed colours: 16 objects, 690 triangles, 54 animation slots.
- Patched ALLTIM matches expected bytes with the other **179 slots unchanged**; patched inventory has 818 files / 890 entries including directories. The changed raw sector's EDC/ECC checks.
- Original BIN SHA-256 before and after: `06084490ea0db34099b31495ccba4ec18fb0ff25c7683c508e9d2db93d4495f4`.
- **Not verified:** normal gameplay actually reloading the edited Digimon. A workbench preview and correct disc bytes do not establish that runtime result.

## External data and reproduction

All proprietary inputs/outputs stay outside Git. Do not repeat successful disc extraction or converter analysis without changed inputs/parsers.

| Location | Contents / use |
|---|---|
| `/home/drdark/Downloads/Digimon World (USA)/` | Original CUE and BIN; read-only. Prefer the CUE, quoting paths. |
| `/home/drdark/Documents/dw/` | User's working analysis project and existing zone overrides. P4 diagnostics did not modify it. |
| `/home/drdark/dev/DW1-coop-data/digimon-import-validation/` | Separate P4 project copied via SQLite backup, packages, edited texture/model preview, patched image and `report.json`. |
| `/home/drdark/dev/DW1-coop-data/digimon-import-validation/AGUM-original.dw1digimon` | Original native package. |
| `/home/drdark/dev/DW1-coop-data/digimon-import-validation/AGUM-blue-palette.dw1digimon` | Controlled palette-edit package; target ID 3. |
| `/home/drdark/dev/DW1-coop-data/digimon-import-validation/patched/patched-2d4cf0a4f428a507.cue` | Already-built test patched disc; no need to make another full copy just to inspect it. |
| `/home/drdark/dev/DW1-coop-data/dev-tools/digimon_roundtrip.py` | P4 validation script. It rebuilds the copied catalogue/overrides and regenerates test exports/report under the validation directory, never the user's project. Inspect before rerunning. |
| `/home/drdark/.local/share/DW1-coop/play/06084490ea0db340/` | User memory card, quick saves and inspections. Preserve these. Slot 3 was supplied for earlier camera testing. |
| `/home/drdark/Downloads/` | User BIOS files. Current settings select this folder; tests have used `scph5501.bin`, and earlier user play used `SCPH1001.BIN`. Neither is bundled. |
| `/usr/lib/libretro/mednafen_psx_libretro.so` | Installed Beetle PSX `0.9.44.1 ea14d6a8`. |
| `/home/drdark/dev/DW1-coop-data/camera-slot3/` | Current camera captures/report, now showing coverage-limited fallback to native 100%. Older uncapped captures were regenerated. |
| `/home/drdark/dev/DW1-coop-data/dev-tools/` | Other local camera, navigation, map import and foreground investigation scripts; some camera probes are historical/inconclusive. |

Launch the desktop app:

```bash
cd /home/drdark/dev/DW1-coop
PYTHONPATH=src /home/drdark/.venv/bin/python -m dw1_workbench.app
```

Open the **separate validation project**, select ID 3 in Characters and Digimon, inspect or import the controlled package, and enable Play with imported resources. Repacking a package means keeping the fixed files at ZIP root, with no enclosing folder and no changed provenance. Original package hashes are deliberately retained so imports can identify edited members.

## Next concrete task

1. Review the uncommitted implementation and run relevant tests before editing.
2. Verify the controlled blue-palette Agumon in **normal gameplay using the patched disc**. Use separate test save storage. If needed, copy the user's memory-card data into that storage; do not overwrite their original saves/cards.
3. Start normal play/load a memory-card save or demonstrate an actual character-resource reload. An emulator quick save can restore old model/texture data directly into RAM/VRAM and hide a correct disc replacement.
4. Record the actual loaded resource ID, a reproducible reload route, screenshot/runtime evidence and any discrepancy. Keep original/edited packages and source hashes traceable.
5. After texture reload is demonstrated, validate one controlled native model edit. Larger/native-layout-changing models, GLB/glTF conversion and stage bundles remain separate next milestones.

User desktop confirmation of the new P4 buttons/replacement is still pending. Report verified outcomes and remaining unknowns; do not mark all of P4 complete from the native round trip alone.

## Camera context to preserve

The user wanted zoom/aspect changes without stretching the HUD or artwork. Initial projection-only behaviour shrank/displaced the character against unscaled scenery. The corrected quad transform uses the game's moving drawing origin, and projection changes are guarded by evidence that the hook executed. At 100%, the tested patch matches native pixels exactly.

Outdoor testing then exposed large black areas because the game uploads/draws only a 4×3 background-tile ring with modulo-reused VRAM slots. `covered_scale` now caps zoom at loaded coverage and displays the effective percentage; requested 75% in the current slot-3 test falls back to 100%. This prevents sustained uncovered views but **does not implement expanded tile streaming, wider culling/clamps or working unrestricted widescreen**. A transition can still show a transient frame before the next coverage check. Saves/inspections restore native camera RAM before serialization. Old states containing earlier camera patches are rejected with an explanation.

Do not revive old claims that the outdoor camera is complete, or interpret zero hook counts from stalled old save-state probes as conclusive cache evidence. The user chose P4 as the current focus; preserve camera work and leave colour-depth work parked.
