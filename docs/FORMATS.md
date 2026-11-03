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
| USA character resource tables | Confirmed | 180 internal names at executable offset `0xA3B44`, indexed to `MMD{id//30}/{name}.MMD` and ALLTIM slot `id`; 179 non-empty display names from the 52-byte records at `0x9CEB4`. ID 114's empty display field is retained as unavailable. Evolution stage from each record's `level` byte (offset 29 of the `DigimonPara` layout in dw_decomp `include/dw/params.h`; names from the SydPatches `Level` enum): 5 Fresh, 4 In-Training, 39 Rookie, 84 Champion, 36 Ultimate, 12 with no stage (humans, NPCs, WereGarurumon, `tAKA`). IDs 1–65 are the partner range (`NUM_PARTNER_DIGIMON` = 66): 4 Fresh, 4 In-Training, 9 Rookie, 30 Champion (including Numemon, Sukamon, Nanimon), 17 Ultimate. |
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
- `observations/` (optional): inspections, their screenshots and observed item spawns, marked as seen while playing, not decoded.

A Native Forest package (7 maps) is about 18 MB; Native Forest + Green Gym + File City (48 maps) exported with all 96 MAP/TFS files equal to their catalogue hashes. Packages contain the user's game data and must stay out of Git.

## Resource association

The catalogue contains 180 confirmed numeric character-resource records. Each is associated with its same-index ALLTIM texture; 178 are associated with a path-present, boundary-validated MMD container. Display names are stored as separate labels with executable offsets and evidence, not derived from appearance. This proves resource-table relationships, not playable-character semantics or animation labels. Static model correctness is compared for the 151 models the parser accepts, as described above. Empty/unsupported categories state their limits explicitly.

The catalogue also holds 244 map-table records with an `area_name` label each. Each file-backed map is associated with its MAP (`scene`) and TFS (`background`) files, with every distinct warp-target map (`warp`, 449 links), and with every character resource it places (`digimon_placement`, 709 links). Projects imported before map support must be re-imported to gain these records.

## Executable analysis/decompilation

PS-X EXE magic and the boot executable are identified. Load fields, sections, disassembly, and decompilation are not yet implemented. No external decompiler is integrated, and approximate pseudocode is never labelled recovered source.

## Research license notes

`dw_decomp` is MIT licensed (copyright jype, 2026). `DW1ModelConverter` is MIT licensed. No code was copied. DW1-SydPatches was used as research context only; no code or bundled data was incorporated. Links and attribution are retained here and in the README.
