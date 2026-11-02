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
- Maps and Zones browser: zone tree, background preview per palette with PNG + provenance export, scene data, followable warps, and floor grid.
- All 241 file-backed maps verified against `DW1ModelConverter` `17d1a19` map output.
- Catalogue-backed empty/unsupported category views.
- Synthetic tests for valid/invalid layouts, missing CUE tracks, malformed/truncated input, traversal rejection, persistence, cancellation recovery, safe export, and known TIM output.

## Partial

- CUE: one data track only; audio is described but not processed.
- ISO 9660: common primary-directory traversal only.
- Release validation: product-code and executable-header evidence, not a published full-disc hash match.
- TIM: standard standalone images only; no automatic embedded/container scan.
- Executable analysis: identity/header, character resource tables, and skeleton tables only.
- MMD static geometry: one model (Agumon) verified; the other 177 are not yet compared and may be rejected if they use other primitive modes.
- DW1 animation data: initial pose only.
- Maps: sprite objects are parsed but not rendered; embedded map TIMs are located, not previewed; door models are not shown; the floor grid and palette time-of-day meanings are unverified.

## Not implemented

- General game archive unpacking beyond the fixed ALLTIM layout.
- Model export (GLB/glTF with provenance).
- Standalone TMD decoding.
- DW1 animation keyframes and playback.
- Map scripts, 3D map geometry, and collision semantics.
- Audio/video decoding.
- Disassembly/decompilation integration.
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

Maps and zones, checked from the same image on 2026-11-02:

- a fresh import catalogued 244 map records (241 decoded, 3 without files) in 65 zones, 449 warp links, and 709 Digimon-placement links; resource count 1,422;
- all 241 file-backed maps and 512 background images matched the converter's map export (details in [FORMATS.md](FORMATS.md#maps-and-zones));
- the Maps and Zones tab was exercised offscreen on that project (zone tree, background, palettes, scene data, warp navigation, floor grid, missing-file entries). It has not yet been inspected on a desktop display.

This validates this supplied USA image and implemented paths, not every retail revision, region, malformed disc, path-table-only entry, other MMD models, or animation. Automated coverage remains synthetic and contains no proprietary data; the converter comparison is a manual check kept outside Git.
