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
| Embedded MMD/TMD static geometry | Partial | Relative-pointer TMD (id `0x41`, flags 0): object table, signed 16-bit vertices, 12-bit fixed-point normals (4096 = 1.0), and primitive modes `0x34` (textured Gouraud triangle) and `0x3c` (textured Gouraud quad) with flags 0 and object scale exponent 0. Packet lengths, indices, texture page, colour mode, CLUT, and UV bounds are validated; anything else is rejected. Verified for ID 3 `AGUM` only (see below). Rendered in a static preview; not exported. |
| USA skeleton table | Partial | Per-ID bone count from the 52-byte parameter record and signed `(object, parent)` byte pairs reached through the per-ID pointer table at executable offset `0x8CE60`; parents must precede children. Verified for `AGUM`. |
| Standalone TMD model | Unsupported | Not decoded or exported. |
| DW1 animation data | Partial | Only the initial-transform prefix (scale, rotation, translation per bone) of the first non-empty animation record is read, to assemble the static pose. Keyframe instructions, clips, and playback are unsupported. |
| USA map table | Confirmed | 255 16-byte entries at executable offset `0x992D4` (name, image/object counts, flags, door/toilet/area-name indices); 70 area-name pointers at `0x991BC`, toilet rectangles at `0x92E10`, door placements at `0x92E60`. Entry `i` maps to `MAP/MAP{1 + i//15}/{name}.MAP/.TFS`. See [Maps and zones](#maps-and-zones). |
| TFS background | Confirmed | Header, 1–16 palettes of 256 colours, and 128×128 8-bit tiles; assembled using the MAP tile grid. Every background of every file-backed map matches the converter. |
| MAP scene data | Confirmed | Header offsets, setup (camera, three lights, liked/disliked areas, background tile grid), embedded TIM offsets, sprite objects and instances, 10 spawns, 10 warps, Digimon placements, and the 100×100 tile-trigger grid (walkable, blocked, script, exit, and toilet tiles). Map scripts, sprite rendering, and 3D map geometry are not decoded. |
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

This verifies one model. The other 177 MMDs are not yet compared, and any that use other primitive modes, flags, or scale exponents are rejected rather than approximated. Model export is not implemented.

### Maps and zones

The USA map table, `.MAP` scene files, and `.TFS` backgrounds were decoded from the layouts the pinned converter uses (no code copied) and compared with its map export for every map on the supplied disc:

- 255 table entries: 244 named, 11 empty. Names may repeat across directories (`YAKA25` is entry 65 in `MAP5` and entry 232 in `MAP16`), so uniqueness is enforced per path.
- 241 named entries have both files and were compared field by field: map name, area name, sound id, day/night and Digimon flags, toilet, doors, camera, lights, ambient values, liked/disliked areas, background grid, spawns, warps, 989 Digimon placements (every field, including unknowns), 9,177 sprite objects and their instances, and the floor grid. All matched.
- 512 background images (every palette of every map), 219,807,744 pixels, matched at 5-bit precision.
- Entries 50 (`YAKA01`), 61 (`YAKA21`), and 65 (`YAKA25`) have no files on the disc and are catalogued as table entries only. The `MGEN17` files are on the disc but no table entry references them.

Grouping and semantics:

- A **zone** is the set of maps sharing an area name (the location name selected by the entry's area-name index): 65 distinct names, from File City (40 maps) down to single rooms.
- Background tiles are always referenced in sequence; the TFS header width/height disagree with the MAP grid on 13 maps, so the MAP grid is authoritative.
- Background colours use the TIM conversion except that STP is ignored: only the zero colour word is transparent, matching the converter. Which palette corresponds to which time of day is not established.
- Warps with a map index of 255 or more are unused slots. Field names for Digimon placements (stats, script id, moves, waypoints) follow the converter; `unk2`, `unk3`, and `unk5` are kept as unknown.
- The floor grid is the game's tile-trigger map, read under the player each tick ([DW1-SydPatches `Tamer.cpp`](https://github.com/SydMontague/DW1-SydPatches/blob/b9671fad99513467dd7ae229ad2f13606f2852dc/src/Tamer.cpp#L1060-L1078) and [`Map.cpp` `getTileTrigger`](https://github.com/SydMontague/DW1-SydPatches/blob/b9671fad99513467dd7ae229ad2f13606f2852dc/src/Map.cpp#L964-L973)): 0 walkable; `0x80` impassable; 50–79 runs a map-script section when stepped on; 80–109 runs one when the cross button is pressed; 110–119 starts the warp in slot `value − 110`; 120 is the toilet trigger. Tile column is `x/100 + 50` and row is `50 − z/100`, so row 0 is the far (+Z) edge, labelled north here. On the USA disc only values 0, 51–73, 81–86, 110–114, 116, 120, and 128 occur, and no exit tile points at an unused warp slot.
- Of 466 used warp-table entries, 289 have exit tiles (walk-through exits) and 177 have none: those transitions are triggered by map scripts or are unused. Many routes, including most of File City and the way north out of Green Gym, are script-driven, and map scripts are not decoded. Inbound links therefore matter: Green Gym's only exit tile leads south to Native Forest, but 40 File City maps and `MAYO00` link into it.
- The relation map groups maps into zones and draws one link per pair of zones that have any warp between their maps (68 links). Walk-through exits orient a force-directed layout by the direction of their exit tiles; the result is an approximate schematic, not a decoded world map. 13 zones (shops, arena rooms, Mt. Infinity, Back Dimension, and others) have no warp-table links in either direction.

## Resource association

The catalogue contains 180 confirmed numeric character-resource records. Each is associated with its same-index ALLTIM texture; 178 are associated with a path-present, boundary-validated MMD container. Display names are stored as separate labels with executable offsets and evidence, not derived from appearance. This proves resource-table relationships, not playable-character semantics or animation labels. Model correctness is verified only for ID 3, as described above. Empty/unsupported categories state their limits explicitly.

The catalogue also holds 244 map-table records with an `area_name` label each. Each file-backed map is associated with its MAP (`scene`) and TFS (`background`) files, with every distinct warp-target map (`warp`, 449 links), and with every character resource it places (`digimon_placement`, 709 links). Projects imported before map support must be re-imported to gain these records.

## Executable analysis/decompilation

PS-X EXE magic and the boot executable are identified. Load fields, sections, disassembly, and decompilation are not yet implemented. No external decompiler is integrated, and approximate pseudocode is never labelled recovered source.

## Research license notes

`dw_decomp` is MIT licensed (copyright jype, 2026). `DW1ModelConverter` is MIT licensed. No code was copied. DW1-SydPatches was used as research context only; no code or bundled data was incorporated. Links and attribution are retained here and in the README.
