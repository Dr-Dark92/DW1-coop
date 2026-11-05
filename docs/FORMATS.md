# Format support and evidence

×2 camera frame (2026-11-05): [FIXED_FRAMING.md](FIXED_FRAMING.md) has its hooks, addresses and evidence.

Status screen (USA): `UI_BOX_DATA` `0x8013D390`, 36 bytes each, frame at +16, state at +18, render function at +32; box 1 with `renderDigimonMenu` `0x800B93D4`, frame 5, state 1 is the open Digimon menu. `MENU_STATE` `0x80134D34` is 1 once drawn, `DIGIMON_MENU_STATE` `0x80134D36` is 0 on the Status tab. The game's digits are 8 × 12 cells of 4-bit texels at VRAM (704, 496), 0 to 9 side by side. The labels AGE and WEIGHT are baked into the menu art.

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
| USA character resource tables | Confirmed | 180 internal names at executable offset `0xA3B44`, indexed to `MMD{id//30}/{name}.MMD` and ALLTIM slot `id`; 179 non-empty display names from the 52-byte records at `0x9CEB4`. ID 114's empty display field is retained as unavailable. Evolution stage from each record's `level` byte (offset 29 of the `DigimonPara` layout in dw_decomp `include/dw/params.h`; names from the SydPatches `Level` enum): 5 Fresh, 4 In-Training, 39 Rookie, 84 Champion, 36 Ultimate, 12 with no stage (humans, NPCs, WereGarurumon, `tAKA`). IDs 1–65 are the partner range (`NUM_PARTNER_DIGIMON` = 66): 4 Fresh, 4 In-Training, 9 Rookie, 30 Champion (including Numemon, Sukamon, Nanimon), 17 Ultimate. |
| MMD container boundary | Partial | The leading TMD offset (`8`), embedded TMD id/flags/object count/table bound, and animation-data offset are validated. 178 supplied MMD files passed. |
| Embedded MMD/TMD static geometry | Partial | Relative-pointer TMD (id `0x41`, flags 0): object table, signed 16-bit vertices, 12-bit fixed-point normals (4096 = 1.0), and primitive modes `0x34` (textured Gouraud triangle) and `0x3c` (textured Gouraud quad) with flags 0 and object scale exponent 0. Packet lengths, indices, texture page, colour mode, CLUT, and UV bounds are validated; anything else is rejected. 151 of the 178 USA models are accepted and match the reference converter; the other 27 use primitive types outside this subset and are rejected (see below). Rendered in a static preview; native MMD is included in Digimon packages, while GLB/glTF export remains unsupported. |
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

Rejected models stay unsupported rather than approximated; adding these primitive types needs its own evidence and tests. The comparison scripts are kept outside Git with the proprietary inputs. GLB/glTF export is not implemented; native MMD export/import is provided by Digimon packages.

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

### Animation clip names

The game stores no clip names; each animation ID's meaning comes from where the game code starts it ([dw_decomp](https://github.com/jype0/dw_decomp/tree/aaf69a4491cbf275ffd24df19fe78010151c10f2/src) call sites, cross-checked with DW1-SydPatches). *Confirmed* means the call sites leave one clear meaning; *likely* means the ID is shared by several situations and the name describes them. The table lives in `src/dw1_workbench/clip_names.py` and is the mapping to use for future work.

**Techniques (IDs 46 and up).** Animation `46 + i` plays technique `moves[i]` of the Digimon's `DigimonPara` record (offset 35, 16 bytes, `0xff` empty; SydPatches `entityGetTechFromAnim`). Names come from the executable's `MOVE_NAMES` table (122 pointers at `0x80126054`, dw_decomp `config/us/symbols.txt`), kept verbatim including the game's "Inifinity Burn"; the last pointer is unused. Agumon's 46–53 are Spit Fire, Fire Tower, Magma Bomb, Heat Laser, Red Inferno, Sonic Jab, Muscle Charge, Pepper Breath. SydPatches remaps H-Kabuterimon's animation 60 as a fix to the original game; the workbench keeps the unmodified mapping. Two models have a technique-range clip with no move behind it (`MGSD` 58, `MUGE` 53); those stay unnamed.

**Digimon (all resource IDs except 0):**

| ID | Name | Confidence | Evidence (game functions) |
|---|---|---|---|
| 0 | Idle | confirmed | partnerTickIdle, setPartnerIdle (healthy), NPCEntityTickLookingAtTamer; default reset |
| 1 | Idle (unwell) | confirmed | setPartnerIdle when sleepy, tired, injured or sick |
| 2 | Walk | confirmed | setPartnerSlowWalking (healthy), NPCEntityTickTrackingTamer, walking to food/toilet |
| 3 | Walk (unwell) | confirmed | setPartnerSlowWalking when sleepy, tired, injured or sick |
| 4 | Run | confirmed | partnerTickWalking catching up, NPCEntityTickTrackingTamer3, tickPartnerToilet |
| 5 | Happy | confirmed | updateConditionAnimation: happiness >= 31 |
| 7 | Unhappy mood | confirmed | updateConditionAnimation: happiness < -30 |
| 8 | Eat | confirmed | partnerTickFeedItem (item removed at frame 11, then consumed), partnerTickEatShit |
| 9 | Sleep | confirmed | partnerTickSleep |
| 10 | Poop | confirmed | tickPartnerToilet, tickWildPoop |
| 11 | Reaction (after eating, before sleeping, after evolving) | likely | partnerHandleFoodFeed, partnerTickSleep, EVL_tickEvoSequence end, DOOA_tickRebirth |
| 12 | Reaction (evolution climax, MP training, after wild poop, dissolving) | likely | EVL_tickEvoSequence, TRN2_tickMpTraining, tickWildPoop, DOOA_tickDissolve |
| 13 | Refuse item | confirmed | partnerTickFeedItem: item put away without being consumed |
| 14 | Injured or sick | confirmed | updateConditionAnimation: condition injured/sick |
| 15 | Hungry | confirmed | updateConditionAnimation: condition hungry |
| 16 | Needs toilet | confirmed | updateConditionAnimation: condition poopy |
| 17 | Sleepy | confirmed | updateConditionAnimation: condition sleepy |
| 18 | Tired | confirmed | updateConditionAnimation: condition tired; end of training sessions |
| 19 | Unhappy condition | confirmed | updateConditionAnimation: condition unhappy; BTL_tickSenileState |
| 22 | Brains training | likely | TRN_tickBrainsTraining |
| 27 | HP training | likely | TRN_tickHpTraining, TRN2_tickHpTraining |
| 28 | Curling aim | likely | KAR_beginAiming; also DOOA_tickRebirth |
| 29 | Curling throw | likely | KAR_beginThrow |
| 30 | Curling result | likely | KAR_tickMatchState, KAR_moveMatchCamera |
| 31 | Curling throw (second) | likely | KAR_beginThrow |
| 32 | Curling result (second) | likely | KAR_tickMatchState, KAR_moveMatchCamera |
| 33 | Battle stance | confirmed | loadBattleData, BTL_handlePartnerMoveCommand, battle start |
| 34 | Stunned | confirmed | BTL_tickStunState, STD_tickStunState, VS__tickDigimonStun |
| 35 | Recover from flattened | likely | BTL_tickEnemyAI when the flattened status ends; also curling aiming and camera chases; the game zeroes sideways root motion for this ID |
| 36 | Retreat / flee | likely | BTL_handleBattleIntro (bystanders flee), after revive in battle; the game zeroes sideways root motion |
| 37 | Block | confirmed | BTL_tickAttackHits sets FIGHTER_FLAG_BLOCKING; defense training |
| 40 | Hit from the front | confirmed | BTL_handleHitReaction: attacker within 90 degrees of facing |
| 41 | Hit from behind | confirmed | BTL_handleHitReaction: attacker behind |
| 42 | Victory | confirmed | BTL_handleVictorySequence, STD_tickChampionScene |
| 43 | Faint | confirmed | BTL_faintDigimon, STD_faintDigimon |
| 44 | Revive | confirmed | handleRestore at 0 HP, DOOA_tickDissolve, battle revive |

**Player character (resource ID 0, its own animation set):**

| ID | Name | Confidence | Evidence (game functions) |
|---|---|---|---|
| 0 | Idle | confirmed | tamerTickIdle, tamerTickWalkingState |
| 1 | Battle stance | confirmed | loadBattleData, tamerTickBattle |
| 2 | Walk | confirmed | tamerTickWalkingState |
| 3 | Run | confirmed | tamerTickWalkingState |
| 5 | Hold out item | likely | partnerTickFeedItem (tamer offers the item) |
| 6 | Throw item | confirmed | startThrowingItem |
| 7 | Praise or scold | likely | tamerTickPraiseScold |
| 8 | Put partner to sleep | likely | partnerTickSleep (tamer) |
| 10 | Training | likely | tamerTickTraining, tamerTickBattle |
| 12 | Pick up item | confirmed | tamerTickPickupItem |
| 14 | Battle command | likely | handleCommands |
| 38 | Praise | likely | tamerTickPraiseScold |
| 39 | Scold | likely | tamerTickPraiseScold |

Coverage on the USA disc: 4,543 of 4,843 clips are named (3,760 confirmed, 783 likely). The rest are mostly IDs 21 and 23–26, which no game function starts by number; they are presumably used by map or event scripts, which are not decoded, and stay unnamed rather than guessed.

### Live game state (player)

While the USA release runs in the player, the workbench reads main RAM through the core's `retro_get_memory_data(RETRO_MEMORY_SYSTEM_RAM)` (2 MiB from `0x80000000`). Addresses are from `jype0/dw_decomp` `aaf69a4` (`config/us/symbols.txt`) and are used only when the project's executable is `SLUS_010.32`:

| Symbol | Address | Use |
|---|---|---|
| `CURRENT_SCREEN` | `0x80134DA8` (u8) | map-table index; `src/main/map.c` sets it after copying the floor grid |
| `TAMER_ENTITY` | `0x8015576C` | `Entity.posData` pointer at +4; `PositionData.location` (3×i32) at +120 |
| `PARTNER_ENTITY` | `0x801557A8` | same header |
| `MAP_COLLISION_DATA` | `0x801AF398` (u8[100×100]) | the running map's tile-trigger grid |
| `ENTITY_TABLE` | `0x8012F344` (Entity*[10]) | slot 0 Tamer, 1 partner, 2–9 NPC Digimon; `Entity.type` (character ID) at +0, `isOnMap` (u8) at +52 |

World position becomes a tile with `getModelTile` (`src/main/map_collision.c`): column `x/100 + 50`, row `50 - z/100`, C truncating division, minus one when `x < 0` or `z > 0`; the grid is indexed `column + row × 100`, the same layout as the decoded MAP floor grid.

Checked on 2026-11-03 with Beetle PSX and the user's two quick saves: map indices 109 (`MAYO00`, Native Forest) and 112 (`TRAI00`, Green Gym), and in both the live grid equals the floor grid decoded from the MAP file on every one of the 10,000 tiles. NPCs read from `ENTITY_TABLE` slots 2–9 with `isOnMap` set matched the decoded MAP Digimon placements in ID and tile: Yuramon (43) at 48,73 and Tanemon (44) at 41,27 in `TWNA02`, Koromon (2) at 59,80 in `TRAI00`. The other placements in those MAP files were not on the map in the saved games (the game only places Digimon the story or recruitment state allows). Map Digimon behaviour (`src/main/overworld.c`, `NPCEntityTickOverworld`): the MAP file's `ai_type` is the game's `followMode`. Modes 2/11 turn to watch the Tamer inside a box of ±`trackingRange` world units (100 per tile) around the Digimon's post (its MAP position); 3–5, 8, 9, 12–14, 17 and 18 chase inside that box; 6, 7, 15 and 16 use the game's own radius test, `(dx + dz) < (trackingRange × 6 / 10)²` (a signed sum, not a distance); other modes ignore the Tamer. Across the 241 USA maps: mode 1 ×372, 2 ×175, 12 ×441, 16 ×1. Whether touching a Digimon starts its script (`NPCEntity.autotalk`, +102) is set by the map script (`scriptSetDigimon`), not the MAP file, and the map script also chooses which placements appear: Native Forest `MAYO00` lists Dokunemon (scripts 5, 8) and ModokiBetamon (scripts 7, 9) at the same two posts. Live, `MAP_DIGIMON_TABLE` (`0x8013CB50`, 196 bytes per slot; post at +168, `trackingRange` +180, `followMode` +188, `lookAtTamerState` +192) drives NPC slot `n` through entry `n − 2`.

Checked in the running game on 2026-11-03: re-entering `MAYO00` spawned two ModokiBetamon (ID 83, mode 12, range 1000, autotalk on, HP 300/300). The nearer one's box was rows 16–36; it began reacting the frame the Tamer reached row 17, ran to where it first saw him, then went back to its post and stopped reacting. Touching it started its script ("This is my territory!"). The other, whose box the Tamer never entered, did not react.

Items (`src/main/item.c`, `src/main/map_object.c`, `src/main/tamer.c`): names come from `ITEM_PARA` (`0x801269DC`, 128 × 32-byte `Item`, USA layout with `name[20]` first). Ground items are not in the MAP file: map scripts place them with `SCRIPT_OP_SPAWN_ITEM` → `spawnItem(item, tileX, tileY)` into `DROPPED_ITEMS` (`0x8013D4E0`, 11 × 16 bytes: sprite location, `i32 type` with `0xFF` empty, `i16 tileX`, `i16 tileY`), which battle drops share; while walking, `checkItemPickup` offers the item when the Tamer's tile is within one tile of it on both axes. Chests come from `SCRIPT_OP_SPAWN_CHEST` into `CHEST_ARRAY` (`0x80150F00`, 8 × 56 bytes: trigger u16 at +48, item +50, isTaken +51, tileX +52, tileY +53). Checked in the running game: item 44 is `Digimushrm`; the user's Native Forest `MAYO01_2` inspection had one at tile 61,54 (and `MAYO00` one at 55,71), and stepping to 60,54 opened the game's pick-up box. Because the scripts are not decoded, the player keeps a per-disc log of every ground item it sees (`item_spawns.json`: map, item, tile, visits).

Scripts can change the live grid (the game has `setImpassableRect`); the player reports any such difference instead of assuming the decoded grid.

### Pathing

Walkability follows the game's collision code (`checkMapCollisionX/Z` in `src/main/map_collision.c`): a tile blocks movement when its floor-grid value has bit 7 set (`value & 0x80`); every other value, including exits, toilets and script tiles, can be walked on. Routes use 8-way steps with diagonal moves only when both orthogonal neighbours are open (X and Z are checked separately), and distances in tiles (diagonal √2). Character radius is not modelled. Spawn points with identical positions are merged (unused slots repeat a default), and spawns on blocked tiles are marked. Across maps, routes follow walk-through exits (warp slot tiles 110–119) to the warp's target spawn; script-driven exits have no tiles and are not followed. Over the 241 USA maps: 1,237 spawn-to-exit/toilet routes, 40 unreachable (spawns in separate walkable areas joined by scripts), 78 spawns on blocked tiles.

### Zone packages (`.dw1zone`)

A ZIP holding one zone (`kind: zone`) or several (`kind: bundle`), format `dw1-zone-package` version 1:

- `manifest.json`: tool and parser versions, source release and disc SHA-256, zones, maps, count of links leaving or entering the package, limits, and every file with its size and SHA-256.
- `maps/<index>-<NAME>/`: the original `NAME.MAP` and `NAME.TFS` bytes (checked against the catalogue SHA-256 before writing), `map.json` (table entry with its raw bytes, setup, spawns with tiles, warps, entrances from any map, Digimon placements with behaviour, sprites, trigger counts and the floor grid as 100 hex rows), `pathing.json` (rules, walkable regions, places, routes with tile paths), `floor-grid.png`, `pathing.png`, and background PNGs (first palette, or all).
- `zones/<zone>.json`: member maps, entrances from outside the package and exits leading out of it.
- `routes.json`: the shortest walk-through route between every pair of maps in the package, leg by leg.

A Native Forest package (7 maps) is about 18 MB; Native Forest + Green Gym + File City (48 maps) exported with all 96 MAP/TFS files equal to their catalogue hashes. Play-time observations (inspections, item spawns) are never included. Packages contain the user's game data and must stay out of Git.

### Zone import and the patched disc

Importing checks a package before storing anything: format and version, which files were edited since export (their SHA-256 no longer matches the manifest — expected after hand edits, reported, not an error), that it was made from this release and disc image, and for each map that the MAP parses with this disc's map table, the TFS parses, warps lead to existing maps and spawns 0–9, and each changed file fits the sectors its original occupies (ISO extents are never moved). An edited `background-palette0.png` is converted into the map's TFS: scaled to the native size (grid × 128 px), fitted to a new 256-colour palette at 5-bit precision with one word kept for transparency where the original was transparent, error-diffused into the existing tiles, and each other palette (other times of day) re-tinted by the per-channel linear map that best turns the original first palette into it; the TFS keeps its size. Maps also have a foreground layer drawn in front of characters: sprite objects cut from the MAP's embedded 4- and 8-bit images (`loadMapImage1/2`, `buildMapOverlayPrim`). Images load in order (8-bit, then 4-bit) at page `384 + 128·(texX/256)` (8-bit) or `384 + 64·(texX/256)` (4-bit); clut −1 is the background palette (row 480), clut < 16 a 16-colour row of the first 4-bit image's CLUT (row 486), clut ≥ 16 the 8-bit CLUT at row `clut + 468`; transparency 4 is opaque, 0–3 are blend modes. A piece is repainted from the picture only when it is opaque, shown by one instance at one place, not animated, not a snow/mist particle (maps 0x58–0x60 and 0x84–0x87, first 0x23 instances), and a cut-out of the background — at least half its texels within a mean 12/255 of the original background where it is drawn (opaque cut-outs measured 79% on `MAYO00` and 100% on `MAYO06`; Toy Mansion's separate art 0%). Background-palette pieces get the new palette, own-palette pieces their nearest colour in their unchanged palette; transparency is kept; other background-palette texels get their nearest new colour; texels also read through an own palette are never rewritten. Converting each of the 241 maps from its own unchanged background reproduced the foreground with a median colour change of 0 and at most 13.5/255 (Lava Cave `TUNN08`); 43 texels on Trash Mountain `GOMI01`, read through both kinds of palette, cannot keep both meanings. Edits to other generated files (other palettes' PNGs, `floor-grid.png`, `pathing.*`, `map.json`) are reported and ignored. Files identical to the disc are not stored; changed ones go to `<project>/overrides/` with `overrides.json` (package, time, size, SHA-256, re-checked on use). Maps and Zones then reads those files (scene, background, floor grid, pathing, exports).

To play them, a copy of the BIN is written to `<project>/overrides/patched/` (one at a time, reused while the imports are unchanged): each changed sector's 2048 data bytes are replaced and, for raw Mode 2/Form 1, its EDC (CRC over subheader and data) and Reed–Solomon P/Q ECC (computed with the header zeroed) are regenerated; the directory record's size is updated if it changed; the CUE is copied with the data file renamed. The encoder reproduces 198 of 198 sampled Form 1 sectors of the USA disc exactly. Checked on 2026-11-03: re-importing an unchanged three-zone bundle (48 maps) stored nothing and its patched image was byte-identical to the original BIN; blocking tile 52,30 of `MAYO00` in an exported package, importing it and re-entering the map on the patched disc gave a live grid equal to the imported MAP (tile value 128). The user's repainted `MAYO00` background (1145×1374) was converted (palettes 1 and 2 re-tinted within 1.01 and 0.32 of 31 per channel) and appeared in the game after re-entering the map; the first version left the foreground bushes in their old colours with black blocks where their transparent index had become opaque, fixed by repainting the foreground layer.

### Native Digimon packages (`.dw1digimon`)

Application-defined ZIP format `dw1-digimon-package`, version 1, implemented in `digimon_package.py`. It contains one verified USA resource ID, not a newly invented character or a glTF conversion. Underlying MMD, ALLTIM, executable skeleton and animation structures use the previously documented source research and parsers.

| Member | Meaning and import policy |
|---|---|
| `manifest.json` | Tool/parser versions, original release/disc SHA-256, character ID/key/name/stage, catalogue resource IDs/paths/extents/offsets/sizes/hashes, and exported member hashes. Must retain source identity and provenance. |
| `model.MMD` | Original native geometry and animation bytes. Edited bytes are parsed against this ID's executable skeleton. Length and header/object layout must stay unchanged. |
| `texture.TIM` | Complete `0x4800`-byte ALLTIM slot, including all palettes and original padding. Colour mode, dimensions, CLUT/image placement and block sizes are validated. |
| `texture.png` / `texture.png.json` | Palette-0 preview and source/palette provenance. An edited PNG is converted to native indices through that existing palette; nearest colours are reported, alpha must have a compatible palette entry, unchanged texels retain even duplicate native indices. All palettes/padding are preserved. Editing both TIM and PNG is rejected. |
| `skeleton.json` | Executable-owned node/object/parent references; read-only. The package cannot replace the skeleton or stats. |
| `animations.json` | Animation slot IDs, offsets, lengths and frame counts for inspection; read-only. Actual animation bytes are inside the MMD. |

Package member hash differences identify intentional edits, as in zone packages; they do not bypass native parsing. Imports require exact disc identity, resource ID/key and source resource provenance. Existing relative-pointer MMD modes `0x34/0x3c` only; unsupported geometry is rejected rather than imported partially. Animation slot count/presence and sound/texture-copy payload references must remain unchanged. Changes to native vertex positions, UVs, normals, initial transforms and animation data within these constraints are structurally validated, not a guarantee of good artistic/gameplay results. There is no arbitrary GLB/glTF import, new skeleton, ID reassignment or expanded model allocation.

Limits: 7 fixed members including the manifest, 16 MiB per binary member, 64 KiB per JSON member and 20 MiB decompressed total. Duplicate/unrecognized/path-traversal members, encrypted files and symbolic links are rejected; members are read in cancellable chunks without extracting paths. Export refuses overwrite and publishes a complete ZIP atomically. Imports validate in a worker, show a texture review, then stage content-addressed blobs under `<project>/overrides/digimon/` and atomically commit `imports.json`. Cancellation/failure before commit cleans newly staged blobs and preserves previous records; unreferenced older blobs are retained so concurrent readers remain safe. The index records original provenance, new hashes/sizes, importer version and time; assets are re-hashed on use. Restore removes the active record. Preview reads overrides; patched-disc builds merge only changed fixed slots into original ALLTIM and include changed MMDs together with existing zone overrides. Shared patch writes now check cancellation between sectors.

Real supplied USA validation (2026-11-03): Agumon ID 3/`AGUM`, `CHDAT/MMD0/AGUM.MMD` (`60d5e920…7c7d`) and ALLTIM slot 3 export/import byte-identically. A controlled palette-0 entry-3 edit to `0x7c00` persisted on reopen and appeared in the imported textured preview (16 objects, 690 triangles, 54 animation slots). Patched disc ALLTIM equals the expected merged bytes, all other 179 slots are unchanged, 818 files remain inventoried, and the changed raw sector's EDC/ECC checks. Original BIN SHA-256 before/after equals `06084490ea0db34099b31495ccba4ec18fb0ff25c7683c508e9d2db93d4495f4`. The game reads a Digimon's texture from `ALLTIM.TIM` (9 sectors at `id · 9`) whenever its model is loaded (`loadDigimonTexture`, dw_decomp `src/main/model.c`), and the file table's sector addresses are fixed, which in-place patching keeps. In game (2026-11-03): Koromon (ID 2) with palette 0 inverted was imported and played from the patched disc. Starting from a Native Forest inspection and walking into Green Gym, where a Koromon stands, VRAM held only the inverted palette (only the original on the unpatched disc), and Koromon's face showed in the inverted colours while its body (another palette) and everything else on screen were unchanged. The validation project, packages, patched image, report and preview remain outside Git at `/home/drdark/dev/DW1-coop-data/digimon-import-validation/`.

### Digimon stage bundles (`.dw1digimons`)

Application-defined ZIP format `dw1-digimon-bundle`, version 1 (`digimon_package.py`). Members are `bundle.json` and one complete `.dw1digimon` package per Digimon at `packages/NNN-KEY.dw1digimon` (stored, as the packages are already compressed). `bundle.json` has the source release/disc SHA-256, the stages, each package's path, size, SHA-256, resource ID, key, name and stage, and a `skipped` list with the reason for every Digimon of those stages that could not be packaged (unsupported primitives or invalid geometry). Stages come from the executable's level byte (Fresh 1 … Ultimate 5).

Reading rejects duplicates, more than 180 packages, members other than `bundle.json` and `packages/NNN-KEY.dw1digimon`, encrypted or symlinked members, members over 20 MiB, a total over 512 MiB, a package list that does not match the members, and a Digimon listed twice. Import requires the same disc identity, then validates every member exactly as a single package import against its own catalogue entry; one failure rejects the bundle, naming the member. Applying stages every changed asset and then writes `imports.json` once (`DigimonOverrides.apply_many`), so a bundle is applied completely or not at all; members identical to the disc remove any earlier import of that Digimon. The GUI validates the bundle again before applying and refuses if it or the disc changed since review.

Real USA disc (2026-11-03): Rookie bundle 28 packaged and 11 skipped (1.2 MB, 1.3 s); all stages 144 packaged and 24 skipped (6.4 MB, 6.5 s); both re-imported with no changes. An In-Training bundle with Koromon's member replaced by the in-game-verified inverted palette imported only that change, and the resulting `ALLTIM.TIM` equals the one played in game.

### HD background layer (USA, in the player)

Runtime safeguards added 2026-11-05: USA `GAME_STATE` is an **int8** at `0x80134F0A` (`tamer.c:startBattle`, `btl.c:loadBattleData`: states 1/3/2); `SCRIPT_MAP_CHANGE_STATE` is int16 at `0x80134DDE` (`map.c:scriptTickChangeMap`). These are evidenced runtime fields, not new asset formats. The layer retains an established same-map environment across battle initialization, falls back to native during flagged transitions/unknown scenes, and rejects asynchronous results from superseded generations. Pictures/tints/load queues are bounded and avatar rendering isolated; details, measurements and unverified scenarios are in [P0_RUNTIME.md](P0_RUNTIME.md). Historical timings below are not a new P0 acceptance claim.

Why not 16-bit in the game: a 16-bit tile keeps the same 128×128 detail, its 4×3 ring needs 512×384 of VRAM where 256×384 plus a 64×256 gap are free, a 35-tile map needs 1.12 MB of the 2 MB of RAM, and a TFS twice the size no longer fits its sectors. `hd_background.py` works on finished frames instead and patches nothing.

- **Game's background:** `MapTileData.imagePtr` tiles in RAM (equal to the VRAM ring on every tile measured), palette from VRAM row 480 (read from the save state four times a second, 2–5 ms). Screen = map − camera (`cameraX/Y`, `MAP_TILE_DATA + 0x1BB4`). The state reads do not disturb the game: RAM hashes over 1,000 frames of a battle were identical with and without them.
- **Tile rows:** tiles with `texV % 256 != 0` are drawn with a V span of `0x7F` over 128 lines (`renderMap`). At 1× the source row is floor(line × 127/128 + ½). Above 1× the rounding varies with the camera, so those lines accept the row above as well.
- **Camera:** the frame on screen was drawn with an earlier camera than the one in RAM, so the last 4 values are scored on a sample of the frame. This took walking from about 50% to 93% coverage.
- **Battles:** the battle code loads over the tiles in RAM (`startBattle` re-reads the TFS after a win). Tiles read earlier stay in use; a state loaded mid-battle takes the visible tiles from VRAM; outside battle they are re-read twice a second.
- **Foreground pieces:** an opaque, static piece (`LOCAL_MAP_OBJECTS` `0x8013C060`, instances at `MAP_TILE_DATA + 700`) that is a cut-out of the background (at least half its texels within a mean 12/255 of the background under it, decided once per map against the first palette) is modelled from VRAM, so where the frame shows it the picture shows, whichever palette it uses. Effects, animated pieces and separate art stay the game's. A piece reaching the end of its 256×256 page spans one texel less in both directions (`buildMapOverlayPrim`), which is what left streaks on File City's roof until modelled. Pieces are re-read about once a second outside battles.
- **Per-block compare:** every game texel is tested once on a sample of the frame; only texels whose class differs from a neighbour are tested at full detail. Darkened pixels with under half of their 5×5 neighbourhood background are chance matches and are dropped; single-texel holes are filled.
- **Merge:** the commonest difference from the background is a screen-wide shift (some battle saves have −4 per channel; the game's fades clamp at black). A pixel equal to background + shift, or the same pixel with all channels darkened alike (at most 16 levels), takes the picture with that darkening; anything else stays the game's. Below 25% coverage the frame is shown unchanged (menus, cutscenes, map changes).
- **Time of day:** a per-channel linear fit from the TFS's first palette to the live one tints the picture. Whole-map tinted copies are cached per look; a look that changes every frame is applied to the screen part only.
- **Threads:** the picture is resampled in the background (the frame shows normally until it is ready), composing runs on its own thread at 1280×960, and only the newest frame waits.

Pictures live in `<project>/overrides/hd/NNN-MAPNAME.png`, stored by **Set HD picture…** or by importing an edited `background-palette0.png`. They are not part of the patched disc.

Checked on the real game, `MAYO00` (640×768 native, picture 1145×1374), 2× internal resolution, Linux, 12 threads: coverage 94.1% standing, 92.7% walking over 259 camera positions, 93.8% in a real battle, 94% at night after the game's own day-to-night transition (a brief 60% during it), 94% after the win and map reload. File City `TWNA02` (picture 1374×1145) at 4×: 94.1% standing, 94.2% walking over 153 camera positions. About 57–60 frames per second in the play window at 1×, 2× and 4× with the layer on (no avatar rendering running); composing takes 8 / 11 / 14 ms on its own thread and preparation under 1 ms on the emulator thread (it was 46 and 10 ms at 4× before the per-block compare, about 23 frames per second). If composing fails, the player falls back to plain frames.

### On-screen HUD (USA, in the player)

`hud_view.py` draws over the finished frame, at the frame's own size, in 320 × 240 units. The game's status bars are at x = `STATUS_UI_OFFSET_X` (`0x80134C50`, i16) + 160, y = 205 and 217, 75 × 8, and that offset moves between 75 and 240 by 50 per frame with `IS_GAMETIME_RUNNING` (`renderStatusBars`, `renderGameClock`). The HP and MP bars are at y = 181 and 193 with the same x, and the map moves by the same amount, so they appear and hide with the native HUD. The bar frame is the game's own, decoded once from VRAM (4-bit page at x 320, u 0, v 236, CLUT at (96, 500)); a hand-drawn copy stands in if it is not resident. The fill follows the game's: 25 blocks of 2 × 2 from x + 18, y + 3. The heart, drop and digits are the workbench's own pixel art. The map is the live 100 × 100 tile grid cropped to the tiles in use; the Tamer and partner markers use their exact positions each frame, and NPCs update six times a second.

Quick saves and inspections keep the plain frame. Checked in the real play window at 1×, 2× and 4×, with and without an HD picture: File City, Native Forest and Green Gym in the field; a real battle (nothing drawn, native HUD alone), the win (returns with live values, 484/802); regen visibly raising the numbers; the in-game menu (hidden, back on closing); about 56 frames per second at 4× with HD.

### Co-op battle prototype (USA, in the player)

`coop.py`, run between frames by the emulator worker. No existing game code changes. A one-shot `WORLD_OBJECTS` entry (`0x80137418`, 12 bytes each) runs a routine in unused RAM (`0x801C0000`, written once, arguments at `0x801C0200`), which calls a game function on the game's own thread and removes itself.

When a battle-on-contact script starts (`IS_SCRIPT_PAUSED` `0x80134FF4` is 0, `TALKED_TO_ENTITY` `0x80134C9C` set, that NPC's autotalk 1), it calls `loadNPCModel` (`0x800B6110`, about 70 frames of CD reading) and then `scriptSetDigimon` (`0x800B6118`), which needs a `MAP_DIGIMON_TABLE` entry for the species in a slot whose type is -1, so no map script needs that slot. The highest such slot is used, so `handleBattleStart` appends the ally last. The entry's `padding0` is marked `0x7A7A` so a loaded state finds it again. The ally is off the map (`isOnMap` 0) except during such a script and the fight, and `FLEE_CHANCES` (`0x8012BB14`) is set to 100 until the battle starts; two other on-screen enemies can still take the room, in which case it sits that fight out.

In battle: `FIGHTER_FLAG_10` keeps a written `targetId`; enemies aim at the nearer of the partner and ally, re-chosen every 300 frames; the partner is turned away from the ally; `movesPrio` (+64) selects its technique; charge mode 2 (+86) with `FIGHTER_FLAG_ON_CHARGEUP` and cooldown 0x50 holds it back. Stats are the level's values (`stats_for`), and techniques the species' first four (`DigimonPara.moves[i]` plays animation `0x2E + i`). When all enemies are down, the partner falls or it flees, `ENEMY_COUNT` drops by one, so the game ends the battle for the real enemies alone: no defeat sequence for the ally, and stat rewards, drops and Bits count the real enemies. `GameRules` and `DigiDex` skip its slot (`excluded_slot`). Player 2 then receives the same EXP and the change in `MONEY` (`0x80134EB8`).

Checked in the real play window from a copy of slot 2 (Native Forest, Greymon level 20, 2x), with player 2's keys through the window's key handler and only the battle trigger scripted: a fight won with both players at +10 EXP and +100 Bits; "Victory over 1 (Rookie)"; Digi-Dex unlocking ModokiBetamon only; keys 2, 6 and 0 applied, no MP spent while holding; quick save and load mid-battle; a second fight without a second model load; a loss ending normally with the ally gone; the Tamer walking afterwards.

### Split screen (in the player)

`split_screen.py`. The core allows one copy per process, so player 2's game is a second process (`python -m dw1_workbench.split_screen`) running the same `EmulatorWorker` with Qt's offscreen platform. `multiprocessing.shared_memory` carries one block: a header `<IIIII` (frame sequence, width, height, buttons, quit) and one RGB32 frame up to 1280x960. The sequence is odd while a frame is being written. The child opens the block untracked, because Python's resource tracker would otherwise delete it when the child exits. The child also quits if its parent changes.

The chosen save is copied to `<store>/player2` (state, info, picture, progress and, once, the memory card); the originals are never written. Player 1 can't choose or load player 2's slot, and player 2 can't choose player 1's. The child doesn't render Digi-Dex avatars: with a new store this held the interpreter for a minute and cut its game to 22 fps; without it both games measured 60.5 fps.

Checked in the real window, offscreen (slot 3 and a copy as slot 4): chooser and refusal, independent input, player 1's load of player 2's save refused, stop from the menu, close, child exit, no shared-memory block left behind, and slot files byte-identical afterward. Not synchronised: time, progress and battles are separate.

**Visitor (`visitor.py`).** After the frame, the block holds two records `<IiiihHBBB` (sequence, x, y, z, rotation Y, species, map, animation, visible), one per game, read from the partner entity. On the same map the other game places an NPC with the partner's species through `loadNPCModel` and `scriptSetDigimon`, in the highest free slot, with `padding0` = 0x7A7B so a loaded state finds it. `stopAnim` (+193) keeps it out of the map AI and contact checks, while `tickAnimation` still runs. Each frame writes location and rotation; a changed animation calls `startAnimation` (`0x800C1A04`). The species goes in `LOADED_DIGIMON_MODELS` (`0x801BE7EC`), so the map change unloads it. It hides (`isOnMap` 0) during this game's scripts and battles and before a save. `entityCheckCollision` skips entities at 0 HP, so setting `currentHP` to 0 stops it blocking the Tamer. Maps often list a Digimon in all eight rows for scripts to place later, so a row with no entity is borrowed while the visitor is shown, and the map's row is written back when it hides.

The Tamer is a second visitor: entity type 0, using its own routine, data and task (`TASK_ID + 1`). Only one visitor is placed at a time, so they don't take the same row. Records are per player and actor.

**Shared fight.** During split screen `worker.coop` is a `CoopBattle` whose `LinkedPlayer` holds the other game's published level and six partner stats, with `can_join` requiring that player on this map, on the field, and already placed as a visitor. Meeting an enemy sets this game's hosting flag in the block (`coop.engaged`, until the script after the fight ends). A game whose other player hosts runs no frames, and the window draws the host's picture on both halves. If both meet an enemy at once, player 1's game runs first and player 2's waits inside its own encounter. The species' model is already listed in `LOADED_DIGIMON_MODELS` by the visitor, so it isn't loaded a second time, which would leave a model slot in use after the map change. A row the map only lists for scripts can be borrowed, and goes back when the ally hides.

The block adds, per player, level, stats, total EXP and Bits earned for them, and Digi-Dex JSON of the foes fought, plus a small press ring for player 1's keys when player 2 hosts. Host's `gain_exp` and Bits go into the `LinkedPlayer`, and the other game applies the difference through `rules.gain` and `MONEY`. The ally's HP doesn't carry back to its own game.

Checked in the real window, offscreen, on map 109: fights started by player 1 and by player 2; the other game ran 0 frames; ids `[1, 6, 7, 0]`, with the ally as Agumon, at its real 2,000 HP; the guest's keys moved the ally's commands; both ended +10 EXP and +100 Bits; ModokiBetamon reached the guest's Digi-Dex.

**Shared map.** On the field (`GAME_STATE` 0, no script), each game publishes its eight map Digimon and clock, leaving out its visitors and ally. Battles and scripts take Digimon off the map for a while, so nothing is published then. A beaten enemy is `isOnMap` 0 with its HP back to full, so HP says nothing; a Digimon with `autotalk` 1 that is off the map in the other game is taken off here, in both directions. Script Digimon (`autotalk` 0) aren't hidden this way. Player 1 leads: player 2's game takes the location and facing of its Digimon, and `HOUR`, `MINUTE`, `YEAR`, `DAY` and the u16 `CURRENT_FRAME` (`0x80134F08`, right before `GAME_STATE`). Enemy AI still runs in player 2's game, so its animations aren't the leader's, and enemies chase player 1's Tamer only. Checked offscreen on map 109: a ModokiBetamon beaten by either player is off the map in both, and the clocks agree.

**Memory limit.** `loadMMD` doesn't check `malloc3`: when it returns NULL, `handleNullModel` is empty and the file is read over low memory, which froze the game for Greymon (`GREY.MMD`) on map 204. So the visitor first calls `lookupFileSize`, then `malloc3` of the rounded size plus 0x8000, then `free3`, and loads only if that succeeded. Measured largest free block with an Agumon visitor (AGUM.MMD 72,012 bytes) in place: 0xA000 on map 204, 0x10000 on map 109. Routine at work area +0x100, data +0x220, path +0x240 (`coop.work_area()`).

Checked in the real window, offscreen, with two real processes: Agumon visitor on maps 204 and 109, following player 2's keys with animation frames advancing and correct facing; Greymon refused with a message and no freeze; another save's map removes it and returning restores it; a quick save keeps it; a real battle on player 1's side had only the real enemy (`ids [1, 6, 0, 0]`, count 1) with the visitor hidden. On the 8 MB core also: both players walking from map 204 to 180 and back (the model is unloaded with the map, and the visitor returns on map 180, whose eight rows are all listed); the visitor back after a real fight ends; player 1 walking through it. Not checked: two different species for real (species 5 was forced in the test), or a map script placing a Digimon in the borrowed row in the same frame it starts.

The battle cursor's CLUT is requested as (282,497), but the GPU keeps x in multiples of 16, so the colours come from (272,497).

### Extended core: 8 MB of RAM

`docs/core-8mb.patch`, against libretro/beetle-psx-libretro ea14d6a8 (the same revision as the Arch package `libretro-beetle-psx`), built with `make HAVE_LIGHTREC=0` to `DW1-coop-data/core-8mb/mednafen_psx_8mb_libretro.so`. Software renderer and interpreter only; the Lightrec recompiler isn't patched. The Linux core file isn't in Git, and a Windows build would need the same patch.

- **Core:** main RAM is 8 MB with no mirrors: CPU map, read and write masks, DMA masks, memory size, and the save state field. `MainRAM.data8` of 2 MB in a state loads into the first 2 MB and zeroes the rest. A state saved on this core has 8 MB and isn't readable on the standard core. The PGXP shadow memory isn't changed (PGXP not used here).
- **Game:** `malloc3` and `free3` are a K&R allocator: a circular, address-sorted free list of `{next, size in 8-byte units}`, rover at `0x80115A24`, last block a zero-size sentinel at the end of the heap. `extra_memory.py` appends the block at `0x80200000` to that list every 30 frames if it isn't there, up to 4 KB below the top. The rest of the game then allocates from it without knowing.
- **Pointers:** `live.ram_end()` gives this core's top, and every pointer check uses it, because heap pointers can now be above 2 MB. Routines called from outside go in the top 4 KB (`coop.work_area()`). On the standard core they stay at `0x801C0000`, which turned out to be inside a free heap block, so a very full heap could reach them.

Checked on the real game, offscreen: 2 MB saves load (slots 3 and 4); Greymon appears as a visitor on map 204 where it froze before, with at least 1 MB (the probe's limit) free; a fight on map 109 starts with only the real enemy (`ids [1, 6, 0, 0]`); a fresh boot reaches the new game naming screen with its model; a save made on the core loads back; the standard core still refuses Greymon cleanly. Not checked: a whole play session, or the memory card on a new game.

### Game rules (P6, USA)

`progression.py` applies these rules to console RAM every frame while the player runs the game (Play tab: **Game rules (P6)**). Nothing on the disc or memory card changes.

- **Levels 0–99.** EXP to go from level L to L+1 is (L+1)(L+20)/2. Total EXP to reach level L is L(L+1)(L+29)/6. Level 20 = 3,430 EXP, 20→21 needs 420, 98→99 needs 5,841, level 99 = 211,200.
- **Stats per level:** +100 max HP and MP, +10 Offense, Defense, Speed and Brains. A partner never has less than its level's values (level 20: 2,000 HP/MP, 200 each). Stats trained or evolved above that are kept, and a level-up adds the gains on top. Current HP/MP rise with their maximum. The game's caps are 9,999 and 999, and level 99 (9,900 / 990) fits under them.
- **Start:** level 20.
- **EXP per won battle:** each enemy's class from the game's Digimon table (`DIGIMON_DATA` `0x8012CEB4`, 52 bytes, level byte +29): Rookie 10, Champion 30, Ultimate 90. Fresh and In-Training give 0 (not set yet). The total is multiplied by the enemy count: 1 ×1, 2 ×1.5, 3 ×2, 4 ×3. The game puts at most 3 enemies in a battle (`PlayerData.entityIds[4]`, slot 0 is the partner).
- **Win detection:** `PARTNER_PARA.battles` (+84) goes up only in `startBattle`'s win branch. Enemies are read during the battle (`GAME_STATE` `0x80134F0A` ≠ 0, `ENEMY_COUNT` `0x80134D6C`, `COMBAT_DATA_PTR` → `entityIds` at +1644, `ENTITY_TABLE` types) because their slots are cleared afterwards.
- **No aging or death:** `PARTNER_PARA` `0x80138460`: `remainingLifetime` (+72) is kept at least 360 hours, `age` (+74) held, and `PartnerEntity.lives` (+124) kept at 3. This closes natural death, death from losing the last life in battle, and lives lost to sickness.
- **Storage:** level/EXP live in `progression.json` in the player's save folder and in `slotN.progress.json` beside each quick save. Loading a slot restores its level. Memory-card saves do not carry the level; the player's current level applies.
- **Regeneration:** out of battle (`GAME_STATE` 0) current HP and MP (`Stats` +20/+22) rise by 1% of their maximum (at least 1) every 60 emulated frames, up to the maximum. 0 HP is never raised. Counting stops in battle and restarts after it.
- **Training EXP:** +5 per finished gym session. Every `src/trn/*.c` session keeps `TRAINING_STATE` (`0x80135371`, u8) above 0 and sets it back to 0 at the end; `TRN_advanceTrainingTime` sets `HOUR` (`0x80134EBC`) to the session's start hour `TRN_CURRENT_HOUR` (`0x8013538E`) + 1. A 1 → 0 edge with the clock 1 (or 2, if the minute ran over) hours past the start counts once. `TRAINING_COMPLETE` (`0x80134CA0`) is not used: the script poll `isTrainingComplete` clears it.
- **Record format 2** adds the Digi-Bank: `{"format": 2, "exp", "level", "bank": [4 × record or null]}`. A bank record is `{"kind", "exp", "stats", "para"}`: `stats` is the 32-byte `Stats` at `PARTNER_ENTITY+56` as hex (base stats, `movesPrio`, equipped `moves`, max/current HP and MP), `para` the 0x84-byte `PARTNER_PARA` as hex; both are null for a new egg. Format 1 records still load, with an empty bank. Loading a quick save restores its bank with its EXP, so a rewind cannot duplicate a partner.
- **Digi-Bank swap:** the active partner is captured, the native evolution sequence is started to the stored form (`EVOLUTION_TARGET`, tamer state 6, partner state 13, as for manual evolution; `EVOLUTION_STATS_GAINS` covers all 66 partner forms), and when the partner is back in state 1 as that form, the stored `stats`/`para` are written over what `EVL_applyEvolution` set. `PartnerEntity.learnedMoves` is not swapped: like the original game across rebirths, learned techniques stay with the player. Eggs are the native rebirth babies (`REINCARNATE_BABY_TYPE`: Botamon 1, Punimon 15, Poyomon 29, Yuramon 43) with `initializeReincarnatedPartner`'s stats (e.g. Botamon 90/110 HP/MP, 10/11/9/10) and care values (discipline and happiness 50, lifetime 360, age 0); weight is the form's `RAISE_DATA.defaultWeight` (`0x801225BC`, 28 bytes, +21).
- **Stage levels (manual evolution):** Fresh → In-Training at level 6 (targets from `getFreshEvolutionTarget`: 1→2, 15→16, 29→30, 43→44), In-Training → Rookie at 12 and Rookie → Champion at 21 (test value), from `EVOLUTION_PATHS`.

Checked on the real game (slot 2, Native Forest): Agumon went from 81/61/71/71 and 800/601 to the level-20 values; lifetime set to 1, lives to 1 and age +5 were restored while the game ran; a replayed win over the map's two ModokiBetamon (Rookie) gave 30 EXP; a win crossing level 21 added +100/+10. Real battle (2026-11-03): the user's two Native Forest inspections taken during a fight against one Rookie ("near end battle", "end of battle") were finished with the rules on: the enemy was read as Rookie while `GAME_STATE` was 1–2, and the win gave +10 EXP (`Lv 20 · EXP 10/420`) as the game's win counter went from 1 to 2. In that desktop session the Play-tab checkbox had not been ticked, so the rules were not running there.

### Drawing offsets and the world zoom (USA)

Each frame the game sets the GPU drawing offset to `DRAWING_OFFSET_X/Y` (`0x80134EC0/4`, `GsSetOrign`) and, after rendering, inserts `DR_OFFSETS[buffer]` (`0x8015DB90`, an `E5` command with the screen centre 160,120 or 160,360) at ordering-table entry 32 (`applyDrawOffset`, `src/main/main.c`). Everything deeper than entry 32 — background tiles (entry 4095) and foreground pieces (depth ≥ 35, `calcMapObjectOrder`) — is drawn at primitive + drawing offset; characters (entries 9–31) and the HUD at primitive + screen centre. The GTE offset OFX/OFY is 0 (Beetle save state), so changing the projection distance H scales characters around the screen centre; consistent 2D scaling is therefore around primitive (160 − DRAWING_OFFSET_X, 120 − DRAWING_OFFSET_Y).

Background tiles (`setupMap`, `uploadMapTileImages`, `updateTileRow/Column`, `renderMap`): full 128×128 8-bit tiles in a 4×3 ring at VRAM x 768 + 64·(col mod 4), y 128·(row mod 3), i.e. x 768–1023, y 0–383; the rest of VRAM is used (framebuffers x 0–319, characters x 320–383 and x 320–639 lower half, foreground images x 384–639, map name x 640–767, interface below the ring; only x 640–703, y 256–511 was never used across nine saves). A zoomed-out view needs up to ⌈320/s/128⌉+1 by ⌈240/s/128⌉+1 tiles, which only fits if tiles are stored at the size they are drawn: 84×84 in a 6×4 ring for 67–90%, 64×64 in 8×6 for 50–60% (tiles may not cross a 256-row texture page). `camera_engine.c` does this: it redirects `renderMap`'s tile loop (`0x800D590C`: `jal engine; nop; j 0x800D5AEC; nop`), turns off the three native upload functions (`jr ra`), downscales tiles from the map data already in RAM (`MapTileData.imagePtr`) with `LoadImage`, keeps a resident table per slot, uploads at most 6 per frame (all at once after a map change), calls `GsSetProjection(VIEWPORT_DISTANCE)` and draws the visible tiles. The foreground relative-position call in `buildMapOverlayPrim` (`0x800B5074`) goes through a scaled `setPosDataPolyFT4`; the particle call (`0x800B509C`, screen coordinates in front of entry 32) is left alone. Code, parameters and buffer live in RAM that stayed zero in every sampled save (`0x801C0000`–`0x801D3100`); removing the zoom restores the code, the projection and the native ring (written into the save state's VRAM). Live patching works because Beetle's instruction cache only needs resident words updated (`sync_instruction_cache`).

Save states record the BIOS: saves made under `SCPH1001.BIN` hang if Beetle now finds `scph5501.bin` (added to the BIOS folder on 2026-11-03), which is why older quick saves and inspections appeared frozen in camera diagnostics.

## Resource association

The catalogue contains 180 confirmed numeric character-resource records. Each is associated with its same-index ALLTIM texture; 178 are associated with a path-present, boundary-validated MMD container. Display names are stored as separate labels with executable offsets and evidence, not derived from appearance. This proves resource-table relationships, not playable-character semantics or animation labels. Static model correctness is compared for the 151 models the parser accepts, as described above. Empty/unsupported categories state their limits explicitly.

The catalogue also holds 244 map-table records with an `area_name` label each. Each file-backed map is associated with its MAP (`scene`) and TFS (`background`) files, with every distinct warp-target map (`warp`, 449 links), and with every character resource it places (`digimon_placement`, 709 links). Projects imported before map support must be re-imported to gain these records.

## Executable analysis/decompilation

PS-X EXE magic and the boot executable are identified. Load fields, sections, disassembly, and decompilation are not yet implemented. No external decompiler is integrated, and approximate pseudocode is never labelled recovered source.

## Research license notes

`dw_decomp` is MIT licensed (copyright jype, 2026). `DW1ModelConverter` is MIT licensed. No code was copied. DW1-SydPatches was used as research context only; no code or bundled data was incorporated. Links and attribution are retained here and in the README.

### Manual Champion evolution (P6, USA / Beetle PSX)

User decision 2026-11-03: stop automatic/random evolution; add **Evolution** as option 7 of the in-game Triangle menu; allow a Rookie to choose a linked Champion at **level 21 or higher** for testing. Eligibility is level plus the native link, without the original weight/care/stat scoring. Other stage thresholds remain pending. Explicit evolution-item/script transformations are not intercepted.

`evolution.py` controls an in-viewport replacement of the native main menu while its original box is open. Original six actions delegate to their native selections; fishing, when present, remains an additional option. Up/Down (or Left/Right) select, Cross opens/selects, Cross again confirms evolution, Circle cancels. Submenus, battle input and overworld movement remain native. Detection requires `IS_IN_MENU` (`0x80134EF0`, i32) = 1, game state 0, box 0 state 1, renderer `0x800B80C0`, and `TRIANGLE_MENU_STATE` (`0x80134D30`, i32) = -1. Real menu idle uses tamer state 6, not the transient state 1 set when opening it.

Native paths: `EVOLUTION_PATHS` at `0x8012B66C`, 62 records of 11 bytes (`from[5]`, `to[6]`), indexed by partner ID minus one. Candidates come only from `to`, with nonempty IDs below 66, class 4 in `DIGIMON_DATA`, deduplicated; current partner must be class 3. The table gives Agumon: Greymon, Meramon, Birdramon, Centarumon, Monochromon, Tyrannomon. Names are read from the native Digimon records. Revalidate link, level, menu, idle partner and absence of fade before triggering.

Two guarded reversible eight-byte RAM edits disable native automatic evolution (source and original words checked against supplied SLUS_010.32):

- `tickConditions`, call at `0x800A90C8`: `jal isInDaytimeTransition; nop` (`0x0C035D86, 0`) becomes `addiu v0,zero,0; nop` (`0x24020000, 0`). The following `beq v0,zero` returns at `0x800A9358`, bypassing natural/virus evolution while preserving the earlier care-condition updates. The original function is `0x800A8F38`–`0x800A936B`.
- `handleSpecialEvolutions` at `0x800E3234`: original prologue `0x27BDFFC0, 0xAFBF0034` becomes `jr ra; addiu v0,zero,-1` (`0x03E00008, 0x2402FFFF`), preventing random sleep/praise/condition branches.

All sites validate before any write. Original instructions are restored for quick saves, Resume and F9 inspections and reapplied before the next frame; resident Beetle instruction-cache words are synchronized both ways using the existing cache helper. No scratch RAM is used and world zoom remains disabled. Unknown executable bytes or an unsupported core disable this feature with a message.

Confirmation sets `EVOLUTION_TARGET` (`0x80134E50`, i16), clears `IS_IN_MENU`, sets tamer state/substate (`0x80134C91/2`) to 6/0 and partner state/substate (`0x80134C5B/C`) to 13/0, matching the native state setters. `partnerTickEvolving` removes menus and drives `getEvoSequenceState` / `EVL_applyEvolution`, including model loading, native bonuses, parameter resets and map restoration. It does not directly replace the partner type/model. P6 EXP is retained.

Evidence: dw_decomp `aaf69a4`, `src/main/partner_impl.c:tickConditions`, `src/main/evolution.c:handleSpecialEvolutions` / `EVOLUTION_PATHS`, `src/main/partner.c:partnerTickEvolving`, `src/main/game_menu.c`, `src/evl/evl.c`, headers and USA symbols. Read-only disc verification used the supplied USA executable.

Real Windows verification (2026-11-03): isolated Beetle PSX `0.9.44.1 71ee81e`, BIOS `scph7003.bin`, copied-in-memory Resume in map 110, test-only level 21. Opened option 7, selected Greymon, completed evolution in 1,776 frames: type 3 → 5, native partner/tamer states 1/0, map 110 retained, max HP 2100 → 2300, P6 level still 21. Matched native/guarded runs with evolution timer 100, virus threshold 16, and the Nanimon special trigger: native began evolution to IDs 47/39/53 (after 3/3/159 frames); guarded stayed Agumon/normal with no target for 600 frames each. User save/memory-card hashes remained unchanged. Scripts, reports and screenshots are outside Git at `D:/DW1-coop-data/evolution-validation/`. Long play, all linked targets and other stages are not verified.

### Digi-Dex (USA, battle-gated)

User decision 2026-11-03: **only battling** unlocks a Digimon, not visibility, proximity, bystanders or map entry. `digidex.py` records knowledge separately from rewindable progression/save states in `<player store>/digidex.json`, JSON `format: 1`. Entries are keyed by native Digimon ID; `catalog` contains only IDs with native class 1–5, never locked names. Unbattled UI rows are `???`; searches only match unlocked names. Nothing backfills knowledge from the analysis catalogue or an old progress record.

The worker samples with live RAM every 10 emulated frames, including when map/position stays unchanged. Requires a valid active partner/Tamer and in-game clock hour. `GAME_STATE` 0 clears the encounter gate. `handleBattleStart` sets state 3 after building opponents; that enables reads of `ENEMY_COUNT` (1–3) and bounded `COMBAT_DATA_PTR->player.entityIds[1:count+1]`, with unique NPC slots 2–9. The early state-1 initialization phase cannot reuse the previous battle's opponent list. A newly loaded mid-battle state 1/2 is accepted; load/reset clears only this transient gate. Opponents are identified from the validated entity pointers, not all visible/map NPCs. A victory is not required; entry into the initialized battle counts.

Persisted information includes name/stage from `DIGIMON_DATA`, native drop item/chance (+33/+34), and encounter profiles keyed by map, base stats and equipped skill set. Stats use Entity+56 (`off/def/speed/brain`) and +72 (`max HP/MP`), not remaining HP. Rank means the native evolution stage; no invented difficulty rank. Profile hours are actual battle sightings, from `HOUR` at `0x80134EBC`. Day 06–15, Evening 16–19, Night 20–05 follow `map.c` clock/lighting boundaries. These sightings do not establish exclusive availability.

Species-linked skills come from DigimonPara `moves[16]` at +35; enemy equipped animation IDs at Entity+68 map slots 46–61 through that species table. Technique names come from bounded `MOVE_NAMES` pointers at `0x80126054` (122 entries). Empty/invalid IDs are omitted; unreadable valid names retain a numeric fallback. Linked skills are possible native techniques, not a claim that every enemy equips them or the partner can learn them. Equipped sets stay attached to their encounter variant.

`load_placements` reads the active disc's executable map table and bounded MAP scenes on the emulator worker, with cancellation. Only an unlocked entry receives its decoded possible placements and their source stats. These include story/script-conditional entries and are labelled accordingly; exact spawn schedules remain unknown where script conditions have not been decoded. Live battle sightings supplement the static placements. Real USA index: 713 distinct species/map/stat placements across 125 species. It is not a complete dynamic spawn catalogue.

Discovery writes are atomic and only occur for new catalogue/entry/profile/hour information. Failed writes preserve the earlier file and retain dirty state for retry. Unknown versions or malformed records disable the Dex and preserve the file; no silent reset/overwrite. Snapshots sent to the GUI are deep copies. Existing discoveries survive quick-load, reset and reopening, intentionally independent of the quick-save level. No source disc, BIOS or memory card is changed.

UI: **8. Digi-Dex** follows Evolution in the P6 in-game menu; fishing, when present, follows it. Up/Down select, Cross opens an unlocked entry, Left/Right page skills/locations/stats, Circle returns. Locked entries reveal no details. A separate searchable Windows dialog opens with **F4** or the toolbar; emulation pauses while this dialog is open and returns to its prior pause state on close. The toolbar works with USA live RAM even with P6 rules off. Other releases lack this feature. Failed location indexing leaves battle sightings available with an explanatory message.

Real Windows validation (2026-11-03), isolated Beetle PSX `0.9.44.1 71ee81e`, BIOS `scph7003.bin`: began from read-only Resume in map 110; diagnostic-only position writes reached the existing native gateway to map 109 and then a ModokiBetamon interaction. Merely loading the map with two NPCs unlocked nothing. Dismissing the native encounter dialogue and entering battle unlocked ID 83. Recorded Rookie, 300 HP/500 MP, Off 70/Def 50/Speed 50/Brains 70, `sm.recovery` 40%, hour 11 (Day), five decoded possible placements. Species links: Static Elect, Electric Cloud, Water Blit, Aqua Magic, Electric Shock. Equipped opponent skills: Static Elect, Electric Cloud, Water Blit. An older state was then loaded without losing discovery. In-game list/details and Windows dialog rendered and visually inspected; locked rows remained anonymous. User save/memory-card hashes unchanged. Harness, report and screenshots: `D:/DW1-coop-data/digidex-validation/` outside Git. This checks one enemy, not every battle/skill/schedule.

### P6 menu rendering / relation chart update

The USA native menu cancels with Triangle (`CANCEL_BUTTON=0x10`, `include/dw/input.h`). Overlay Circle and Triangle both forward libretro X (PS Triangle) at the native main-menu boundary; nested overlays consume their back action first. This fixes the previously broken exit. Grid direction handling follows three columns; optional fishing remains after Evolution and Digi-Dex.

`game_menu_view.native_icons` extracts the original seven 20×20 menu sprites from the user's resident Beetle VRAM via `GPURAM_FIELD`, once per worker. Four-bit texture page base is (896,256), UV origin y=192; icon UV/CLUT rows follow `MENU_OPTIONS` in `game_menu.c`: (0,0,12), (40,0,13), (0,20,12), (40,20,13), (0,40,13), (40,40,13), (80,0,13), palette at (256,496+row). Source zero color is transparent; native assets are not stored in the repository. Evolution and Dex use small QPainter vector icons. If a core state cannot supply the VRAM field, generic drawn icons are used.

The relation model reads staged partner IDs 1–65 and the native 62×11-byte path table, adds the four source-proven hardcoded Fresh targets, and retains forward native stage links. It displays the current partner's ancestors plus descendants, caches per runtime load/evolution, and uses six stage columns. Only existing stages 1–5 are populated; Mega is explicitly pending. Up/Down select within a stage, Left/Right follow a link into an adjacent available stage where possible; columns show six nodes and scroll to the selected form. Lines touching the selection are highlighted. Form names are elided in compact nodes, with the selected full name below. The view changes no evolution eligibility: only immediate Rookie→Champion native links at level 21 can be confirmed. Item/script-only links are not inferred.

Real Windows verification: main-menu cancel with Circle and Triangle restored native walking state; Dex back/main/exit and chart back/main/exit worked; chart showed Botamon/Koromon/Agumon and its actual Champion/Ultimate branches, and confirmed Greymon evolution completed normally. Live icon grid and chart captures visually inspected. External harness/captures: `D:/DW1-coop-data/menu-validation/`. User saves/card unchanged. Automated suite: 184 passed, 14 skipped.

### Focused evolution Tree, audio and late victory reward

Latest chart rendering matches the supplied native reference: previous forms left, focused portrait center, colored branches to next forms right. It browses the source-proven forward native partner graph rather than a six-column overview. Left/Right switch sides, and repeating that direction recenters on the selected adjacent form; Up/Down select its alternatives. Only `choices()` for the actual active Rookie at level≥21 may evolve, still through two Cross presses and native state revalidation. Cross on another browsable form only recenters. Native UI portraits use `EVO_CHART_BOXES` (`0x80124544`, 61×8 bytes, u/v/clut index at +4/+5/+6), encoded CLUTs from `EVO_CHART_DETAIL_COLORS` (`0x80123E6C`, 5×u16), and the resident 4-bpp texture page `0x18` (512,256). Missing portraits use a text fallback. Color-zero pixels are transparent; no proprietary graphics are bundled.

`EvolutionController.before_frame` produces sound intents for host-rendered opening, navigation, choosing and backing out. `MenuAudio` produces short synthesized PCM tones and mixes them into existing interleaved signed-16 stereo audio at the core rate, saturating to the valid sample range. A new cue replaces pending old cues; delegated native choices/cancel use the game's own sounds. F4 Dex opening uses the same generated cue through `AudioOutput`. The cues are generated UI feedback, not extracted original SFX.

Victory handling now distinguishes a confirmed result from an unconfirmed encounter. Seeing the USA post-battle stats renderer captures `_victory_enemies` and its display result. Native `startBattle` sets GAME_STATE=0, then `readMapTFS`, and only later increments the won counter. A victory snapshot survives this gap, including temporarily invalid partner RAM; the first valid world tick commits EXP and full level stat gains once, after native `resetStatsAfterCombat` has run. The late counter cannot award again. Explicit rules reset/state-load clears pending context; generic invalid-partner ticks retain only a confirmed victory. Loss/flee encounters remain unconfirmed and cannot leak EXP to the next fight.

Real isolated Windows result: +10 Rookie EXP crossed a test threshold into Lv21, total EXP3855 (5/451), then map109→map112 via native gateway retained the total and persisted it to private progression JSON. Original user saves/card unchanged. Real Tree capture uses native Agumon portrait and colored branches; native chart-to-Greymon evolution and menu/Dex return paths pass. Mixed sound events and PCM recorded by the external harness. Full suite 187 passed, 14 skipped; the added tests cover delayed win-counter/map reload behavior and distinct bounded stereo tones.
