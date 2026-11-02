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
| DW1 animation clips | Confirmed | MMD animation pointer table, initial pose per bone, and the instruction stream: keyframes (per-bone, per-axis velocities), loop start/end, texture copy, and sound cue. Unknown opcodes, zero step counts, out-of-range bones, unmatched loop ends, and unterminated streams are rejected. Played with the game's integer stepping. See [Animation clips](#animation-clips). |
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

### Animation clips

Each MMD's animation data starts with a table of 32-bit offsets (the first offset gives the table size; zero entries are empty slots). A clip is a frame count (bit 15: initial pose includes scale), the initial scale/rotation/translation of bones 1…n−1 (bone 0 starts at identity), and 16-bit instructions terminated by `0x0000`:

| Opcode | Meaning |
|---|---|
| `0x0fff` frame | Keyframe: entries `0x8000 \| axisMask<<6 \| bone`, a step count, then one value per set axis (bit 14 = scale X … bit 6 = translation Z). Each value becomes a per-frame velocity of `value / steps` plus a ±1 remainder spread so that `steps` frames add exactly `value`. Velocities persist until changed. |
| `0x10nn` | Loop start; `nn` is the repeat count (`0xff` or `0` loop forever). |
| `0x2fff` frame, jump | Loop end: decrement and jump back to `jump` frame, re-running that frame's instructions in the same tick. |
| `0x3fff` frame + 3 words | Texture copy: VRAM rectangle (x/width in 16-bit units, so 4 texels at 4 bpp) moved within the model's texture. |
| `0x4fff` frame + word | Sound cue: sound id (low byte) and VAB id (high byte). |

Evidence and behaviour follow the matching decompilation [jype0/dw_decomp `src/main/anim.c`](https://github.com/jype0/dw_decomp/blob/aaf69a4491cbf275ffd24df19fe78010151c10f2/src/main/anim.c) (`tickAnimation`, `tickMomentum`, `animReadMomentumInstruction`, `applyRootMomentum`), cross-checked with DW1-SydPatches `src/Model.cpp`. Velocities are applied before each frame's instructions, so a keyframe at frame *f* first moves bones on frame *f*+1. Bone 0's translation channels are root motion: the game rotates them by the root bone's matrix and moves the entity, not the bone, and it zeroes the sideways (X) component for animation IDs `0x23` and `0x24`. SydPatches additionally advances the root-translation remainder counter in `tickMomentum`; the decompiled original does not, and the workbench follows the original.

Verification against `DW1ModelConverter` `17d1a19` on the supplied disc, all 178 MMDs (4,843 clips, 5,686,560 sampled channel values), comparing the pose after each sampled frame's motion and before its instructions, with every sound and texture event:

- the instruction stream, timing, loops, events, initial poses, and channel layout are identical: replacing the game's integer stepping with exact real-valued velocities (as the converter models them) reproduces every clip to within 0.0005° and 0.0004 units, with all events equal;
- with the game's integer stepping, all clips stay within the expected rounding (rotation ≤ 0.172°, i.e. under one 1/4096-turn unit per axis; translation < 1 unit; scale < 1/4096), except root motion in the 162 clips with animation ID 35 or 36, where the game zeroes sideways root motion and the converter does not;
- comparisons stop where an endless loop first jumps back, because the converter continues linearly past an endless loop's end instead of repeating it.

Not established: the playback rate (20 frames per second is the converter's time base), clip names or purposes (clips are shown by index), and sound playback (cues are listed, not played). The game also cycles texture frames for a few Digimon outside the animation data (`animateEntityTexture`); the preview does not.

## Resource association

The catalogue contains 180 confirmed numeric character-resource records. Each is associated with its same-index ALLTIM texture; 178 are associated with a path-present, boundary-validated MMD container. Display names are stored as separate labels with executable offsets and evidence, not derived from appearance. This proves resource-table relationships, not playable-character semantics or animation labels. Static model correctness is compared for the 151 models the parser accepts, as described above. Empty/unsupported categories state their limits explicitly.

The catalogue also holds 244 map-table records with an `area_name` label each. Each file-backed map is associated with its MAP (`scene`) and TFS (`background`) files, with every distinct warp-target map (`warp`, 449 links), and with every character resource it places (`digimon_placement`, 709 links). Projects imported before map support must be re-imported to gain these records.

## Executable analysis/decompilation

PS-X EXE magic and the boot executable are identified. Load fields, sections, disassembly, and decompilation are not yet implemented. No external decompiler is integrated, and approximate pseudocode is never labelled recovered source.

## Research license notes

`dw_decomp` is MIT licensed (copyright jype, 2026). `DW1ModelConverter` is MIT licensed. No code was copied. DW1-SydPatches was used as research context only; no code or bundled data was incorporated. Links and attribution are retained here and in the README.
