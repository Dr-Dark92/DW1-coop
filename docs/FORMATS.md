# Format support and evidence

Support labels mean:

- **Confirmed**: implemented with structural validation and synthetic tests.
- **Partial**: a bounded subset is implemented; important variants remain.
- **Unsupported**: inventoried as bytes only; no semantic claims.

## Disc and filesystem extraction

| Format | Status | Implemented boundary |
|---|---|---|
| Cooked ISO, 2048-byte sectors | Confirmed | ISO 9660 PVD at LBA 16; one data track. |
| Raw BIN, Mode 2/Form 1, 2352 bytes | Confirmed | Sync/header/subheader checked; 2048-byte payload at `0x18`. |
| Raw BIN, Mode 1, 2352 bytes | Confirmed | Sync/header checked; payload at `0x10`. This is supported structurally but is not the expected PS1 CD-XA layout. |
| CUE | Partial | `FILE`, `TRACK`, `INDEX`; one data track; audio tracks accepted but not decoded. BINARY/MOTOROLA references are validated, with no multi-session support. |
| ISO 9660 | Partial | Primary descriptor and directory-record traversal; bounds/endian validation. Rock Ridge, Joliet, interleaving, multi-extent files, and hidden path-table-only entries are not supported. |
| Mode 2/Form 2, 2336/2448 sectors, ECM/CHD/MDS | Unsupported | Rejected with an actionable layout error. |

Sector locations follow [psx-spx CD-ROM sector encoding](https://psx-spx.consoledev.net/cdromformat/). PS1 ISO 9660 context follows [psx-spx CD-ROM file formats](https://psx-spx.consoledev.net/cdromfileformats/).

## Release identification

Only the USA release with boot executable `SLUS_010.32` is accepted. Identification requires all of:

1. a valid ISO 9660 filesystem;
2. `SYSTEM.CNF` naming that product-code executable;
3. the named file present in the filesystem;
4. a `PS-X EXE` header in that file.

This establishes release/product-code evidence, not a byte-perfect known-good dump. The complete source image SHA-256 is recorded so a future verified hash registry can strengthen identification. Unknown or non-USA releases are reported honestly and rejected for this milestone.

The USA focus is corroborated by [dw_decomp](https://github.com/jype0/dw_decomp), whose build instructions dump the original USA disc with `dumpsxiso`, and [DW1-SydPatches](https://github.com/SydMontague/DW1-SydPatches), whose documented extraction step targets the US version.

## Game archive unpacking

No DW1-specific game archive is unpacked. Filename extensions and file listing are not treated as archive support.

## Asset decoding

| Format | Status | Notes |
|---|---|---|
| Standard PS1 TIM | Partial | 4/8-bit indexed and 16/24-bit direct-color standalone TIM files; palettes, VRAM coordinates, zero transparency, and STP indication retained. PNG plus JSON provenance export. Embedded scans and DW1 container splitting are not implemented. |
| TMD / MMD | Unsupported | No model or model/archive association claims. |
| DW1 animation data | Unsupported | No clip parsing or preview. |
| TFS/backgrounds | Unsupported | No background or scene reconstruction. |
| XA/STR/VAG | Unsupported | Extension detection only where applicable; no decode. |

TIM structure and PS1 transparency semantics are based on [psx-spx file-format documentation](https://psx-spx.consoledev.net/cdromfileformats/). PNG alpha is an interchange approximation: zero color words map to alpha 0, STP-set words to alpha 128, and other words to alpha 255. The JSON sidecar records that policy so it is not mistaken for native eight-bit alpha.

## Resource association

The schema supports directional associations, evidence, and confidence. No character/model/texture/animation or map-resource associations are seeded without verified data. Empty category views state this explicitly.

## Executable analysis/decompilation

PS-X EXE magic and the boot executable are identified. Load fields, sections, disassembly, and decompilation are not yet implemented. No external decompiler is integrated, and approximate pseudocode is never labelled recovered source.

## Research license notes

`dw_decomp` is MIT licensed (copyright jype, 2026). No code was copied. DW1-SydPatches was used as research context only; no code or bundled data was incorporated. Links and attribution are retained here and in the README.
