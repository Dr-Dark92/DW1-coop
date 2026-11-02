# Milestone progress

## Complete

- PySide6 first-launch import/open-project experience.
- Cancellable worker thread with progress and actionable failure reporting.
- ISO/BIN/CUE inspection with explicit supported sector layouts.
- Bounded ISO 9660 inventory and USA DW1 release validation.
- SQLite catalogue with source identity, parser versions, hashes, statuses, logs, and association-ready schema.
- Reopen workflow and stale/missing source protection.
- Disc browser, selected-file export, and 256 KiB hex view.
- Standard standalone TIM preview and palette/STP-aware PNG + provenance JSON export.
- Catalogue-backed empty/unsupported category views.
- Synthetic tests for valid/invalid layouts, missing CUE tracks, malformed/truncated input, traversal rejection, persistence, cancellation recovery, safe export, and known TIM output.

## Partial

- CUE: one data track only; audio is described but not processed.
- ISO 9660: common primary-directory traversal only.
- Release validation: product-code and executable-header evidence, not a published full-disc hash match.
- TIM: standard standalone images only; no automatic embedded/container scan.
- Executable analysis: identity/header only.

## Not implemented

- Game archive unpacking.
- MMD/TMD model decoding and character associations.
- DW1 animation decoding.
- TFS/map/background/collision/script decoding.
- Audio/video decoding.
- Disassembly/decompilation integration.
- Co-op gameplay, native runtime, or networking.

## Validation disclosure

The test suite uses generated ISO 9660, raw-sector, CUE, and TIM fixtures. No user-owned real DW1 image was used during implementation, so actual retail-disc import, its complete filesystem inventory, and DW1-specific resource detection remain unverified. The GUI must not present synthetic-test success as successful retail extraction.
