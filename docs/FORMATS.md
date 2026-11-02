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

`CHDAT/ALLTIM.TIM` is split only by its verified fixed-slot rule. No general-purpose DW1 archive format is claimed. Filename extensions and file listing are not treated as archive support.

## Asset decoding

| Format | Status | Notes |
|---|---|---|
| Standard PS1 TIM | Partial | 4/8-bit indexed and 16/24-bit direct-color TIM data at resource start; palettes, VRAM coordinates, zero transparency, and STP indication retained. PNG plus JSON provenance export. Arbitrary embedded scans and containers other than ALLTIM are not implemented. |
| USA `CHDAT/ALLTIM.TIM` | Confirmed | Exactly 180 fixed `0x4800`-byte (nine-sector) slots. Each slot begins with and structurally decodes as TIM; child offset, slot size, SHA-256, decoder version, and parent association are catalogued. |
| USA character resource tables | Confirmed | 180 internal names at executable offset `0xA3B44`, indexed to `MMD{id//30}/{name}.MMD` and ALLTIM slot `id`; 179 non-empty display names from the 52-byte records at `0x9CEB4`. ID 114's empty display field is retained as unavailable. |
| MMD container boundary | Partial | The leading TMD offset (`8`), embedded TMD id/flags/object count/table bound, and animation-data offset are validated. 178 supplied MMD files passed. |
| Embedded MMD/TMD static geometry | Partial | Relative-pointer TMD (id `0x41`, flags 0): object table, signed 16-bit vertices, 12-bit fixed-point normals (4096 = 1.0), and primitive modes `0x34` (textured Gouraud triangle) and `0x3c` (textured Gouraud quad) with flags 0 and object scale exponent 0. Packet lengths, indices, texture page, colour mode, CLUT, and UV bounds are validated; anything else is rejected. 151 of the 178 USA models are accepted and match the reference converter; the other 27 use primitive types outside this subset and are rejected (see below). Rendered in a static preview; not exported. |
| USA skeleton table | Partial | Per-ID bone count from the 52-byte parameter record and signed `(object, parent)` byte pairs reached through the per-ID pointer table at executable offset `0x8CE60`; parents must precede children. Matches the reference converter for all 151 accepted models. |
| Standalone TMD model | Unsupported | Not decoded or exported. |
| DW1 animation data | Partial | Only the initial-transform prefix (scale, rotation, translation per bone) of the first non-empty animation record is read, to assemble the static pose. Keyframe instructions, clips, and playback are unsupported. |
| TFS/backgrounds | Unsupported | No background or scene reconstruction. |
| XA/STR/VAG | Unsupported | Extension detection only where applicable; no decode. |

TIM structure and PS1 transparency semantics are based on [psx-spx file-format documentation](https://psx-spx.consoledev.net/cdromfileformats/). PNG alpha is an interchange approximation: zero color words map to alpha 0, STP-set words to alpha 128, and other words to alpha 255. The JSON sidecar records that policy so it is not mistaken for native eight-bit alpha.

The ALLTIM slot rule is verified in both named upstream codebases: [`dw_decomp` reads `digiType * 9` for 9 sectors into a `0x4800` allocation](https://github.com/jype0/dw_decomp/blob/aaf69a4491cbf275ffd24df19fe78010151c10f2/src/main/model.c#L3287-L3303), while [DW1-SydPatches uses the same nine-sector rule](https://github.com/SydMontague/DW1-SydPatches/blob/b9671fad99513467dd7ae229ad2f13606f2852dc/src/Model.cpp#L332-L338). The user-provided USA file was `0x32a000` bytes (`180 × 0x4800`), and all 180 slots decoded structurally. This evidence applies only to that named container/layout; `ETCTIM.BIN`, `STDTIM.BIN`, and arbitrary concatenated TIM data remain unsupported as containers.

The character association and MMD boundary were independently checked against `Operation-Decoded/DW1ModelConverter` revision `17d1a19` (MIT) and the supplied executable/disc. The upstream converter selects a resource table entry by numeric ID, forms the six `MMD0`–`MMD5` groups in blocks of 30, uses the same ID for ALLTIM, and reads an embedded standard TMD after the two-word MMD header. No converter code is copied.

### Static Agumon verification

The static parse of ID 3 (`CHDAT/MMD0/AGUM.MMD`, SHA-256 `60d5e920…7c7d`, texture `CHDAT/ALLTIM.TIM#003`) was compared with the glTF output of the pinned converter run on the same files:

- 22 skeleton nodes with identical parents and object assignments; 16 objects, 390 vertices, 412 source primitives (134 triangles, 278 quads), 690 triangles after splitting;
- every triangle corner identical and in the same order (position, normal, and UV), confirming triangle winding `(2,1,0)` and quad split `(1,2,3)`;
- one opaque, single-sided, lit material in both;
- initial pose from animation record 0 identical on every node; world-space vertices from the converter's node transforms match the workbench preview to within 0.00004 units;
- texture page 5 and 4-bpp mode match the TIM; faces use CLUT rows 488–490 (palettes 0, 1, 2), and texel colours at all 690 face centroids match under each face's own palette at 5-bit source precision. The two tools expand 5-bit colour differently (`c<<3` versus `c*255/31`); this is a convention difference, not a palette error.

Thirty `AGUM` triangles have zero-area UVs (all corners on one texel, or on one texel row or column). The PS1 fills these from the referenced texels; the preview does the same rather than dropping them. The preview was then inspected on a desktop display and confirmed visually correct. It inverts PS1 screen-down Y only when rendering; decoded coordinates are unchanged.

### All-model comparison

The same comparison was then run for every USA MMD (178 files, all hash-verified against the catalogue; IDs 62 and 114 have no MMD). Per model it checks parser acceptance, node parents and mesh mapping, every ordered triangle corner, material grouping (the converter groups a mesh's faces by double-sided, blend mode, and translucency, so corners are compared per group in that order), the initial pose, world-space assembly, and the centroid texel of every face.

| Outcome | Models |
|---|---|
| Accepted; geometry, skeleton, materials, pose, assembly, and all centroid texels match | 135 |
| Accepted; geometry, skeleton, materials, pose, and assembly match; some centroid texels differ for converter-side reasons (below) | 16 |
| Rejected: a primitive type outside the supported subset | 26 |
| Rejected: out-of-range normal indices | 1 |

Across the 151 accepted models (93,686 triangles) there were no geometry, skeleton, material, or pose differences, and the largest world-space difference was 0.00014 units.

180 of the 93,686 centroid texel samples differ, in 16 models. Every one is explained by how the converter builds its single combined texture, which assigns one palette per texel:

- 13 models (`BETA`, `PUNI`, `ELEC`, `GIRO`, `YURA`, `PALM`, `MODO`, `ARUR`, `TKKA`, `EBET`, `EELE`, `EGIR`, `EPAL`): the differing samples belong to zero-area-UV faces, for which the converter's palette map has no reliable entry of the face's own and is filled from neighbouring texels or from animation texture copies (its copy crop is inclusive, so a `height` 24 copy also writes row 24).
- 3 models (`NUME`, `GERE`, `ENUM`): the differing samples lie inside regions that animation texture-copy instructions overwrite in the converter's palette map.

The workbench samples each face under its own CLUT directly from the TIM, as the PS1 GPU does, so these are not workbench errors. Animated texture copies are not applied by the static preview.

Rejected models, by the first unsupported primitive found (mode meanings follow the converter's own mode table):

| Mode, flags | Primitive | Models |
|---|---|---|
| `0x3e`, `0x00` | Gouraud textured quad, translucent | 17: `DEVI`, `GABU`, `KABU`, `KUWA`, `HKAB`, `GGDR`, `YANM`, `GOBR`, `PSYC`, `SNDY`, `GOLE`, `TENT`, `EGOB`, `EDEV`, `EGAB`, `EKAB`, `EKUW` |
| `0x36`, `0x00` | Gouraud textured triangle, translucent | 3: `POYO`, `BLMR`, `OTAM` |
| `0x2c`, `0x00` | Flat textured quad | 3: `TOYA`, `HAGU`, `BRIK` |
| `0x2e`, `0x00` | Flat textured quad, translucent | 1: `CLEA` |
| `0x38`, `0x00` | Gouraud untextured quad | 1: `TONO` |
| `0x2d`, `0x01` | Flat textured quad, unlit | 1: `HYOG` |
| — | 75 faces with out-of-range normal indices (the converter substitutes a fallback normal) | 1: `TANE` |

Rejected models stay unsupported rather than approximated; adding these primitive types needs its own evidence and tests. The comparison scripts are kept outside Git with the proprietary inputs. Model export is not implemented.

## Resource association

The catalogue contains 180 confirmed numeric character-resource records. Each is associated with its same-index ALLTIM texture; 178 are associated with a path-present, boundary-validated MMD container. Display names are stored as separate labels with executable offsets and evidence, not derived from appearance. This proves resource-table relationships, not playable-character semantics or animation labels. Static model correctness is compared for the 151 models the parser accepts, as described above. Empty/unsupported categories state their limits explicitly.

## Executable analysis/decompilation

PS-X EXE magic and the boot executable are identified. Load fields, sections, disassembly, and decompilation are not yet implemented. No external decompiler is integrated, and approximate pseudocode is never labelled recovered source.

## Research license notes

`dw_decomp` is MIT licensed (copyright jype, 2026). `DW1ModelConverter` is MIT licensed. No code was copied. DW1-SydPatches was used as research context only; no code or bundled data was incorporated. Links and attribution are retained here and in the README.
