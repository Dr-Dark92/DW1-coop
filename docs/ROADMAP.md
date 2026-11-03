# DW1 rework roadmap and repository review

Updated: 2026-11-03 (Asia/Muscat).
Reviewed main at `d028ce2c7477d29c53ef8f06f2189ff659094bac` — "WIP add static Agumon model preview".

This document records the user's 17 requested capabilities, their implementation order, acceptance gates, and the evidence-backed current position. It is a plan, not a claim that planned features work.

## Current position

**The project is a read-only disc/asset workbench with an experimental static character preview. It is not yet a playable reimplementation or a map/Digimon replacement tool.**

### Implemented foundation

Source review confirms:

- ISO/BIN/CUE reading with explicit sector-layout validation and USA SLUS_010.32 identification.
- SQLite resource catalogue, source hashes/provenance, reopen workflow, bounded hex reads.
- Thread-safe cancellation, streamed hashing and atomic selected-resource exports.
- TIM decoding, texture preview, PNG and provenance JSON export.
- ALLTIM fixed-slot decoding and character-resource associations.
- A bounded MMD/TMD static parser, executable skeleton lookup, initial-pose prefix parsing, background model loader, and experimental Qt raster preview.

The handover records real-disc results from 2026-11-02: 818 inventoried files, 180 ALLTIM slots, 180 character-resource records, 179 nonempty display names, and 178 present MMD containers. These are recorded prior validation results, not a new disc run in this review. Resource IDs include character resources; they are not evidence of 180 playable Digimon.

### Experimental work already present

The paused Agumon milestone is implemented in:

- [mmd.py](../src/dw1_workbench/mmd.py): bounded geometry parsing for modes 0x34/0x3c, skeleton and initial static pose, texture-material checks.
- [model_view.py](../src/dw1_workbench/model_view.py): static textured/untextured preview, orbit/zoom, wireframe.
- [gui.py](../src/dw1_workbench/gui.py): cancellable model-loading worker and experimental preview status.
- [test_mmd.py](../tests/test_mmd.py): synthetic parser checks.

The handover reports Agumon as 22 skeleton nodes, 16 objects, 390 vertices, 412 source primitives and 690 rendered triangles. Independent converter-output comparison and final desktop visual verification remain pending. Model export is deliberately absent. Reading an initial transform prefix does not establish animation decoding or playback.

### Missing capabilities

- Semantic MAP/TFS/background, collision, walking-area, gateway and event decoding.
- Zone packages, zone replacement, dependency-aware bundles.
- Evolution-stage classification and complete Digimon packages.
- Model export, animation playback, replacement import and rebuild pipeline.
- A game runtime, gameplay systems, multiplayer or chat.

Raw file export is implemented; a usable zone or complete Digimon export is not. Disc import creates an analysis catalogue; it does not import replacements into the game.

### Review findings and verification boundary

1. README, FORMATS and PROGRESS describe the previous texture/boundary-only milestone. HANDOVER's opening paused milestone and current code describe the newer experimental static preview. Read those older claims as the verified baseline, not as a complete inventory of WIP code.
2. HANDOVER also retains older "next concrete task" and unsupported-geometry statements below the newer pause section. The opening pause section is the current resume point.
3. There is no in-repository CI workflow in the reviewed tree. The handover records 30 passing tests, superseding its older 26-test result.
4. In this review, Python compilation of src and tests passed. Pytest could not run because pytest is absent from both available Python environments. No fresh passing test result is claimed.
5. The original disc, preserved local converter input and desktop display were unavailable in this review. Real-disc/model counts above remain handover evidence.
6. No gameplay progress percentage is assigned: extraction, static preview, native execution and multiplayer are separate deliverables.

## Delivery order and acceptance gates

Priority revised on 2026-11-03: deliver a playable inspection build first so zones can be examined in the running original game before decoding or editing them. Static-model validation is deferred and does not block this build. This changes delivery order, not the implementation status recorded above.

### P0 — Playable inspection build first

User item: 5 (initial direct-launch experience). Status: NOT STARTED. Highest implementation priority.

Purpose: launch and play the original game so the user can inspect zone layouts, walking boundaries, gateways, toilets and other interactions before map decoding and editing.

- Investigate a maintained PS1 emulator/core with a supported local launch/integration interface. Record the selected version, dependencies, licensing, BIOS requirements and host compatibility before implementation.
- Use the user's own disc image and, where required, user-supplied BIOS. Do not bundle game data or firmware.
- Add a clearly labelled Play / Launch game workflow to the workbench with emulator, disc and firmware configuration, validation, actionable errors and launch logs.
- Start with an external emulator process if that is the smallest reliable integration. An embedded viewport is optional and must not delay the playable build.
- Provide keyboard/controller input, working video/audio, normal-speed gameplay, clean shutdown and persistent saves in a separate writable location. Preserve original disc data.
- Confirm new-game/load-game, player and partner movement, a representative battle, transitions between connected zones, and save/relaunch/load.
- Create an inspection checklist for named zones: screenshots, observed paths/boundaries, gateway destinations/arrival points, toilets and other interactions. Record observed facts separately from decoded resource IDs and hypotheses.
- Establish reproducible inspection access using an ordinary save near the target zone. Save states may supplement normal saves if supported by the chosen emulator. Teleport, zone selection and debug overlays are later optional tools requiring verified integration; they are not assumed to exist.
- Record the runtime feasibility decision here. The initial emulator-backed build supplies a playable reference and desktop launch experience; it does not complete native reimplementation or prove replacement import support.
- Treat existing HANDOVER instructions as historical asset-work resume notes. The current next task is this playable build, not completion of Agumon model validation.

Exit: on the target workstation, launch the user's game from the workbench, move through at least two connected zones, inspect a gateway and a toilet/other interactive object, and save/relaunch/load successfully. Record screenshots and the inspection checklist. No decoded-map overlay is required for this gate.

After this gate, resume P1 and P2 using the playable build as the behaviour reference. Before P3 replacement verification, document the rebuild/patch path or runtime override mechanism that will actually consume edited zone packages. A full native runtime remains a separate architectural commitment and is not a prerequisite for initial zone inspection.

### P1 — Close the existing static-model milestone

Status: IMPLEMENTED WIP; verification deferred until after the P0 playable inspection build. Preserve the current parser and experimental labels; this work must not block launching the game.

- Reproduce the 30-test baseline in the configured development environment.
- Compare AGUM output with pinned DW1ModelConverter revision 17d1a19: objects, vertices, UVs, materials/palettes, skeleton, initial pose and winding.
- Inspect the preview and converter output side by side; exercise orbit/zoom and worker cancellation/shutdown on a desktop.
- Record discrepancies and regression coverage; retain experimental labels until resolved.
- Reconcile README, FORMATS, PROGRESS and HANDOVER without promoting unverified support.
- If verification passes, implement static GLB/glTF export with source IDs, hashes, transforms and parser version. This remains separate from complete animated Digimon export.

Exit: one independently checked static Agumon model; documented supported boundaries and reproducible checks.

### P2 — One-zone discovery, reconstruction and export

User item: 1. Status: NOT STARTED beyond raw disc inventory/export.

- Identify one small named zone and prove its resource/dependency mapping. Do not assume every MAP/TFS pair equals one complete zone.
- Decode its visual layout/background representation, camera data and placements where evidenced.
- Decode walkable regions and collision with explicit coordinate units/axes.
- Locate gateway triggers, destination IDs, arrival points and conditions.
- Locate interactive objects such as toilets, and associate their trigger regions and behaviour/script references.
- Preserve undecoded bytes and identify unsupported fields; do not infer semantics from extensions alone.
- Export a versioned zone manifest plus all required resources, hashes and provenance.
- Add an inspection view with visual, walking/collision and event overlays.

Exit: one inspectable zone whose layout, walkability, gateways and at least one interactive object can be checked against the original game. Unknown fields remain explicit.

### P3 — Single-zone import, then zone bundles

User item: 2. Status: NOT STARTED.

- Define whether each package targets the original-game rebuild path, a future runtime, or both.
- Validate package version, source release, identifiers, dependencies and references before applying changes.
- Import into a separate project/override layer; keep original source data immutable.
- Prove unchanged export/import fidelity: byte equality for preserved raw data and defined semantic equality for decoded/re-encoded data.
- Make one controlled gateway/object change and verify it in the chosen execution target.
- Add multi-zone bundles, cross-zone dependency checks and atomic apply/rollback only after single-zone replacement works.

Exit: unchanged zone round trip and one verified edit, followed by a bundle containing connected zones.

### P4 — Digimon export/import, then stage bundles

User items: 3–4. Status: PARTIAL FOUNDATION ONLY.

- Build a verified stage/class mapping for Fresh, In-training, Rookie, Champion and Ultimate; leave unknown/non-Digimon entries explicit.
- Complete geometry/material export after P1; separately decode and verify animations and their skeleton/channel mappings.
- Package one Digimon with stable ID, display label, stage, model, textures/palettes, skeleton, animation and required references.
- Support single-Digimon export and validated replacement import without silently changing IDs or losing unknown fields.
- Verify unchanged round trip and one controlled replacement in the execution target.
- Add stage-filtered selection and class bundles after single-Digimon replacement works.

Exit: one complete usable Digimon package and replacement, then verified Rookie/Champion/Ultimate bundles. A PNG or raw MMD alone does not satisfy this gate.

### P5 — Resolution, aspect ratios, frame rate and game speed

User items: 6–7. Status: NOT STARTED.

- Establish source timing and separate simulation, rendering, animation, audio and input clocks where the target architecture permits.
- Add resolution options and UI scaling; handle backgrounds, camera limits and UI anchoring for each supported aspect ratio.
- Treat higher internal resolution and replacement background/detail assets as separate tasks.
- Add frame-rate options only after checking movement, battles, timers and audio at the intended game speed.
- Make speed multipliers explicit and independent from render frame rate where supported.

Exit: reproducible timing comparisons and playable rendering at declared resolutions/aspect ratios, without accidental speed changes.

### P6 — Progression, evolution, death policy and storage

User items: 11–14, 16–17. Status: NOT STARTED.

- Add persistent level/XP state and a configurable XP curve; exact curve remains undecided.
- Per level gained: +100 maximum HP, +100 maximum MP, +10 Offense, Defense, Speed and Brains.
- Specify starting level/stats, stat caps, recovery on level-up, training interaction, evolution bonuses and enemy balance.
- Implement configurable evolution thresholds and branch eligibility; automatic evolution versus player choice remains undecided.
- Disable Digimon death per user request. The scope still needs a decision: age-related death only, or also other death/reincarnation paths. Do not silently remove battle defeat.
- Add persistent storage, deposit/withdraw, active-partner rules and ownership. Decide whether stored Digimon age or train.
- Version saves and test migration, storage round trips, evolution and level boundaries.

Requested schedule:

| Levels | Stage |
|---|---|
| 0–5 | Fresh / newborn |
| 6–11 | In-training |
| 12–19 | Rookie |
| 20–34 | Champion |
| 35–44 | Ultimate |
| 45–60 | Mega |
| 61–99 | Unspecified by user |

Proposal, not an approved rule: retain Mega at 61–99 and continue stat growth.

From level 0 to 99, the requested increments add 9,900 HP, 9,900 MP and 990 to each remaining stat, before base stats/training/evolution bonuses. Validate storage limits and balance before implementation.

Exit: one saved partner progresses through supported stages with the selected death policy and reliable storage. Mega is gated by P7 content.

### P7 — Mega-stage content

User item: 15. Status: NOT STARTED.

- Add stage/roster identifiers and evolution routes.
- Supply compatible model, texture, skeleton, animation and ability content.
- Integrate names/icons, UI, encounters where intended, battle balance and storage.
- Handle missing content explicitly rather than evolving into an unusable form.

Exit: at least one complete Mega evolution that can move, fight, save/load and enter storage.

### P8 — Local two-player split screen

User item: 8. Status: NOT STARTED.

Plan ownership and world-state boundaries during runtime architecture planning and P6 even though multiplayer delivery comes later.

- Define two player identities, inputs, partners, inventories and UI.
- Render two cameras and support independent controls.
- Decide same-zone restriction versus independent zones, battle pausing, shared recruitment/quests and player-two persistence.
- Make session state serializable so LAN work can reuse it.

Initial proposal, not an approved restriction: same-zone play, one partner per player, host-owned world progression.

Exit: two players can explore, interact, battle, transition zones and save without ownership conflicts.

### P9 — LAN play

User item: 9. Status: NOT STARTED.

- Establish an authoritative host and explicit ownership of players, partners, quests, battles and inventory.
- Synchronize relevant game state/events and zone transitions, with protocol/save version checks.
- Handle join/leave, disconnect/reconnect and conflicting actions.
- Test on two machines with simulated delay/loss.

Exit: two-machine playable session with persistent, consistent state.

### P10 — Global chat

User item: 10. Status: NOT STARTED.

- Define identity, hosted service, public/private channels, authentication and privacy.
- Implement reconnect, rate limits, blocking/reporting and moderation controls.
- Keep chat service availability independent from local/offline play.

Exit: authenticated users can exchange messages across separate networks with the stated controls.

## Next implementation task

Implement P0: inspect the current launch/UI structure, select and validate an emulator integration on the target workstation, add the Play workflow, then demonstrate two connected zones and persistent saves. Do not wait for Agumon verification, model export or MAP/TFS decoding.

After the playable inspection gate passes, resume the paused static-model verification from [HANDOVER.md](HANDOVER.md) as P1, then decode one complete zone as P2. Preserve existing WIP code and its verification limits. The roadmap's revised delivery order takes precedence over older next-task text in the handover.

Recorded workstation paths from the handover, not paths accessible in this review:

- Repository: /home/drdark/dev/DW1-coop
- Preserved analysis data: /home/drdark/dev/DW1-coop-data/asset-validation-v3/
- Converter input: /home/drdark/dev/DW1-coop-data/converter-input/

The converter build/source were recorded under /tmp and may need to be recreated at the pinned revision. Keep disc images and extracted game data outside Git.

## Status maintenance

For each milestone, record commit, supported boundaries, checks run, observed results, remaining blockers and the next concrete task. Distinguish planned, implemented, independently verified and playable outcomes. Update this roadmap when implementation evidence changes.
