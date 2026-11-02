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
- Catalogue-backed empty/unsupported category views.
- Synthetic tests for valid/invalid layouts, missing CUE tracks, malformed/truncated input, traversal rejection, persistence, cancellation recovery, safe export, and known TIM output.

## Partial

- CUE: one data track only; audio is described but not processed.
- ISO 9660: common primary-directory traversal only.
- Release validation: product-code and executable-header evidence, not a published full-disc hash match.
- TIM: standard standalone images only; no automatic embedded/container scan.
- Executable analysis: identity/header only.

## Not implemented

- General game archive unpacking beyond the fixed ALLTIM layout.
- MMD/TMD geometry and animation decoding.
- DW1 animation decoding.
- TFS/map/background/collision/script decoding.
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

This validates this supplied USA image and implemented paths, not every retail revision, region, malformed disc, path-table-only entry, MMD geometry, animation, or manual visual rendering. Automated coverage remains synthetic and contains no proprietary data.
