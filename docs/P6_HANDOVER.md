# P6 handover — progression, evolution, death policy and storage

Updated 2026-11-03 (Asia/Muscat), Windows continuation. Start with [CLAUDE_HANDOVER.md](CLAUDE_HANDOVER.md), the consolidated current P6 resume guide. This file preserves chronological evidence; older paths, open decisions and implementation claims below can be superseded. Branch: `python-player`, committed HEAD `f7f7dc2`; Windows continuation uncommitted. P6 remains PARTIAL.

## Reference-style Tree, menu audio and EXP reload fix (2026-11-03, Codex)

User supplied the original native chart screenshot and requested that layout, opening/selection/navigation sounds, and reported EXP resetting on area change. The Tree is now local/focused: centered native portrait/name/stage, previous forms in the left pane, colored bent branches into next forms in the right pane. Up/Down cycle a branch; Left/Right select previous/next side and explore it on repeated presses; Cross confirms eligible Champions twice, or browses ineligible forms. All native forward partner links are available for browsing, including shared ancestors outside the current partner family; evolution still only allows the active Rookie's immediate linked Champions at level 21. Mega remains pending. This replaces the six-column rendering described below.

Portraits are decoded from the resident UI atlas (`EVO_CHART_BOXES` at `0x80124544`, 61 eight-byte entries; `EVO_CHART_DETAIL_COLORS` at `0x80123E6C`; texture page `0x18`). No game art is bundled. The supplied native reference was matched with real Agumon/Koromon and Champion portraits; native menu exit and chart→Greymon evolution still pass in the isolated harness.

`menu_audio.py` generates distinct short opening, navigation, choose and back tones; the worker mixes them into the existing stereo game PCM at the core's sample rate, clipping safely and avoiding queued input bleeps. Native delegated actions keep their native sounds. Windows F4 Dex opening also plays the open cue. Real core harness recorded open/navigation/choose/back events and a mixed PCM WAV outside Git; physical device loudness remains a desktop check.

EXP issue: `GAME_STATE` can return to 0 while the game reloads the map, before `PARTNER_PARA.battles` increments. Clearing enemies at that boundary could discard a won reward even though the result panel had previewed it. `GameRules` now retains the confirmed victory panel's enemy snapshot/result across invalid-partner/map reload frames, awards once on the first valid world frame (after native combat stat restoration), and suppresses a duplicate when the late win counter changes. Explicit load/reset still clears the transient victory; loss/flee snapshots still clear. Regression covers invalid partner during reload, delayed counter, level crossing, area change, persistence and no duplicate award.

Real Windows check from the user's latest Resume loaded read-only into an isolated store: one ModokiBetamon victory gave +10 EXP, crossing test-only level 20→21, ending at total 3855 / Lv21 EXP5/451; a native gateway transition from map109 to map112 (Green Gym) retained exactly 3855, and the private progression JSON reopened with that value. No test EXP was written to the user store. User save/memory-card hashes unchanged. Harness/report: `D:/DW1-coop-data/digidex-validation/check_exp_area.py`, `area-exp-report.json`. Tree/audio captures: `D:/DW1-coop-data/menu-validation/`. Full suite: 187 passed, 14 skipped; targeted follow-up checks pass. No commit/push; world zoom remains off. Restart for new code.

## Menu exit fix, icon grid and relation chart (2026-11-03, Codex)

User reported the new menu would not exit and requested original-style icons/names and an evolution relation view. Root cause: the USA game's `CANCEL_BUTTON` is **Triangle** (`0x10`, `include/dw/input.h`), but the overlay forwarded Circle. Both Circle and Triangle now forward native Triangle when closing the main menu, preserving nested Dex/chart back navigation. Real isolated checks closed from both buttons and confirmed `IS_IN_MENU=0`, tamer state 0; Dex → main → gameplay also passed. The earlier desktop-check claims did not cover this exit path.

`game_menu_view.py` renders a three-column grid, with Up/Down retaining columns and Left/Right moving between items. The first six original icons (plus fishing) are decoded from the user's live VRAM once; new Evolution/Dex icons are small vector drawings. No game art is bundled. Main menu fields remain eight options (plus optional fishing) and native actions still delegate to the game.

Evolution is now a six-column relation chart: Newborn, In-training, Rookie, Champion, Ultimate, Mega pending. Family = current partner's native ancestors and descendants, from `EVOLUTION_PATHS` and hardcoded Fresh links 1→2, 15→16, 29→30, 43→44 (`getFreshEvolutionTarget`). Only forward stage links are displayed; item/script-only routes are not invented. Long columns scroll around the selected form. Selected links are highlighted; current partner is green. Left/Right browse stages, Up/Down browse forms. Browsing ancestors/future forms never grants evolution: only the existing immediate linked Champion choices, level ≥21, idle/menu/fade revalidation and double confirmation may trigger the native sequence.

Real isolated Windows check: original icon grid and full Botamon → Koromon → Agumon → Champion/Ultimate family visually inspected; both exit buttons, Dex returns, chart ancestor browsing and Agumon → Greymon through the chart passed (native evolution completed, normal gameplay restored). Test store only; user save/memory-card hashes unchanged. Harness and captures at `D:/DW1-coop-data/menu-validation/` outside Git. Full suite **184 passed, 14 skipped**; diff check passes. No commit/push; world zoom remains off. Restart the app for new code.

## Digi-Dex continuation (2026-11-03, Codex)

User requested information gated on encounters, then chose **only after battling** and added linked enemy skills. Implemented `digidex.py` / `digidex_view.py`, worker observation/persistence/signals, in-game **8. Digi-Dex** after Evolution and Windows **F4 / Digi-Dex**. The original six actions and Evolution remain; fishing follows the Dex. USA live RAM required; in-game integration uses P6/Beetle, Windows reader also works with P6 off.

Battle participants only, after the initialized opponent list is ready; visibility/bystanders alone never unlock. Victory is not required. Anonymous locked entries cannot expose names/details through selection/search. Unlocked data: native stage/rank, drop/chance, possible decoded map placements, actual battle day/evening/night hours, variant base stats, native species-linked skills and opponent-equipped skills. Source-dependent schedules are explicitly unknown and placements may depend on scripts/story. See FORMATS.md for fields, addresses, persistence and coverage.

Knowledge is atomic JSON format 1 in `digidex.json` per player/disc store, independent of quick-save rewinds. New entry/profile/hour updates only; failed writes keep the old file, bad versions/records preserve the file and report unavailable. No migration from older progression records is required. Startup indexing is cancellable; closing before a frame runs cannot replace Resume with an unstarted core state.

Real isolated Windows check passed: map entry with two NPCs did not unlock; native ModokiBetamon battle unlocked ID 83 with correct stats, item/chance, five possible source placements and linked/equipped techniques. Knowledge survived loading an older state. In-game menu/detail pages and Windows dialog were visually inspected. User save/memory-card hashes unchanged. Harness/captures/report: `D:/DW1-coop-data/digidex-validation/` outside Git. Known placement index: 713 placements / 125 species; script-only spawns/schedules are not fully decoded. No user discovery file was populated by diagnostics. Full Windows suite: **180 passed, 14 skipped** (compiler-dependent mock-core tests). `git diff --check` passes. No commit/push; world zoom stays off.

## Manual evolution continuation (2026-11-03, Codex)

User chose **manual linked evolution**, with Champion eligibility at **level 21** for testing and Evolution as the **seventh in-game menu item**. Implemented in `evolution.py`, integrated with the emulator worker and an in-viewport main-menu/choice presentation. Original six actions remain native; fishing remains available as an additional option. All native natural/random/virus evolution branches are reversibly guarded in RAM while P6 is on. Explicit items/scripts are still native. Current manual stage support is Rookie → Champion only, using native links and level eligibility; no original care/stat requirements. Full address/patch/flow evidence is in FORMATS.md.

Real Windows checks passed with the user's current Resume loaded read-only into an isolated emulator/test store: Agumon → Greymon through option 7 at test-only level 21, native evolution cutscene completed, returned to map 110 in normal walking states with native bonuses and level retained. Native/guarded comparisons proved the natural, virus and special-trigger branches start evolution unguarded and stay Agumon for 600 frames guarded. User saves and memory-card hashes were unchanged. Nothing was persisted to the user's level. Scripts/captures/reports: `D:/DW1-coop-data/evolution-validation/` (outside Git). Other targets and long play remain unverified.

Quick saves/Resume/F9 serialize original instructions; load resets host menu state; per-slot EXP still restores normally. Supported core: Beetle PSX. Source mismatches/other cores report that manual evolution is unavailable. World zoom remains off. Restart the app to load this code, enable P6, open Triangle, choose Evolution, select a linked Champion and press Cross twice; Circle returns. Level 20 is still the user's saved level; reaching 21 needs 420 EXP.

The Windows full suite passed 171 tests, 14 skipped (compiler-dependent mock-core tests). An existing generated-camera-source hash failed initially due to Windows CRLF checkout; `.gitattributes` now preserves LF for that generated source input, and the suite passes without rebuilding/changing the camera code. No commit/push.

Next P6 work: storage/ownership/versioned saves and remaining stage thresholds. Earlier notes below about evolution being entirely unimplemented, and automatic-versus-choice being undecided, are superseded by this entry.

## Battle results replacement (2026-11-03, Codex)

User requested replacing the post-win gained-stat menu with EXP and next-level progress. With P6 enabled, the player now composites a replacement over the native stats panel: EXP gained, resulting level, EXP within that level and EXP remaining. This is a player presentation change, not an executable patch. Native stat rewards, Bits, drops, move learning and confirmation remain active underneath. Native rendering must continue because `renderPostBattleStatsBox` also applies stat gains.

Detection uses USA `UI_BOX_DATA` at `0x8013D390`, 36-byte `UIBoxData`, state +18 and renderer +32 equal to `0x800EE1FC`. Source: dw_decomp `aaf69a4`, `include/dw/ui.h`, `src/main/battle_ui.c`, `src/main/ui.c`, `config/us/symbols.txt`. Native final rectangle (-88,-78,176,96) maps to (72,42,176,96) in 320x240; replacement includes its border and scales with output resolution. The native panel precedes the won-counter update, so displayed EXP is a preview; persistence still happens at the existing win check. Max-level handling is explicit. No instruction or save-state patch is installed.

Validation: 15 progression/quick-save/panel tests passed, including repeated preview frames without awarding EXP, level crossing, unrelated boxes, clearing, maximum level and image region checks at 320x240/640x480. Rendered preview visually inspected at `D:/DW1-coop-data/validation/p6/battle-result-preview.png`. Real-game panel/input verification remains pending. Added missing PySide6-Addons dependency for the player's QtMultimedia import. No commit/push.

## Windows continuation (2026-11-03, Codex)

- Working tree: `D:\DW1-coop`, branch `python-player`, baseline `f7f7dc2`. No commit or push.
- Restored `docs/ROADMAP.md` from local `main`, marked P6 PARTIAL, and labelled the older review/P0–P5 inventory as historical.
- Fixed risk 4 below: after processing any win, out-of-battle frames clear the encounter enemy snapshot. Flee/loss cannot carry enemies into a later unreadable encounter.
- Added parameterized regression checks for win/flee/loss followed by an unreadable battle, checking EXP and repeat-frame behavior. Synthetic life restoration is checked; real defeat remains unverified.
- Created ignored `.venv` with pytest. With `PYTHONPATH=src`, `.venv\Scripts\python -m pytest -q tests/test_progression.py tests/test_quicksave.py`: **11 passed**. `git diff --check` passed. Full GUI suite and real-game checks were not run.
- Next: desktop checks and evolution timing investigation; evolution/ownership/storage choices below remain open. User disc/archive files were left untouched; world zoom remains disabled.

## Where the work is

| Item | Value |
|---|---|
| Branch | `python-player` (all P6 work lives here; **not** on `main`) |
| P6 commit | `a7b0804` — "Add P6 game rules: levels and EXP from battles, no aging or death" (HEAD of `origin/python-player`) |
| Parent | `3626987` — P4 in-game Digimon import verification, class bundles and filters |
| `main` | `812f191`, diverged from this branch at `d028ce2`. `main` has two docs-only commits (`eb48a43`, `812f191`) adding `docs/ROADMAP.md`; this branch has no `ROADMAP.md`. Bring that file across before treating the roadmap as current. |
| Other remote branches | `agumon-preview-verification` and `animations` are ancestors of `python-player`; nothing to merge from them. |

The original workstation (Arch Linux) used these paths. They are not in Git:

- Repository: `/home/drdark/dev/DW1-coop`
- Out-of-Git data and scripts: `/home/drdark/dev/DW1-coop-data/` (the P6 checks are `dev-tools/p6_rules_check.py` and `dev-tools/p6_real_battle.py`)
- User project (catalogue/overrides): `/home/drdark/Documents/dw`
- Python: `/home/drdark/.venv/bin/python`; core: Beetle PSX 0.9.44.1; BIOS `SCPH1001.BIN`

This clone is on a Windows machine at `D:\DW1-coop`. The proprietary disc, BIOS, core, saves, inspections and
`dev-tools` scripts are **not** here. Copy them over, or resume on the Linux workstation, before doing anything that needs the real game.

## What P6 asks for (roadmap, user items 11–14, 16–17)

From `docs/ROADMAP.md` on `main`:

- Persistent level/EXP with a configurable curve.
- Per level: +100 max HP, +100 max MP, +10 Offense, Defense, Speed and Brains.
- Starting level/stats, stat caps, recovery on level-up, training interaction, evolution bonuses and enemy balance.
- Configurable evolution thresholds and branch eligibility (automatic evolution or player choice is undecided).
- Digimon death disabled, without silently removing battle defeat.
- Persistent **Digimon storage**: deposit/withdraw, active-partner rules, ownership, and whether stored Digimon age or train.
- Versioned saves, with tests for migration, storage round trips, evolution and level boundaries.
- Requested stage schedule: 0–5 Fresh, 6–11 In-training, 12–19 Rookie, 20–34 Champion, 35–44 Ultimate, 45–60 Mega, 61–99 unspecified (proposal: stay Mega).
- Exit gate: one saved partner progresses through the supported stages, with the chosen death policy and reliable storage. Mega depends on P7 content.

Decisions the user made later, recorded in commit `a7b0804` and `docs/HANDOVER.md`:

- No aging and no death.
- Start at level 20 with that level's stats.
- EXP per enemy: Rookie 10, Champion 30, Ultimate 90.
- Combo multiplier ×1 / ×1.5 / ×2 / ×3 for 1 / 2 / 3 / 4 enemies.
- Levelling equation: the user asked for one; Claude picked **(L+1)(L+20)/2 EXP from level L to L+1**.

## What is implemented (commit `a7b0804`)

There is no native runtime. P6 rules are applied to **console RAM once per emulated frame** by the Python libretro player,
for the USA release (`SLUS_010.32`) only. The disc and memory card are never changed.

### Files

| File | Change |
|---|---|
| `src/dw1_workbench/progression.py` (new, 241 lines) | The rules (`exp_to_next`, `total_exp`, `level_for`, `battle_exp`, `stats_for`), the `Progress` record (JSON format 1, holds only `exp`), and `GameRules.tick()`, which reads and writes RAM. |
| `src/dw1_workbench/player.py` | `PlayConfig.game_rules`; `EmulatorWorker` builds `GameRules` when `game_rules and live_ram`, calls `_apply_rules()` after every `run_frame()`, writes `progression.json` atomically when EXP changes, resets on console reset/state load, restores a slot's progress on quick load, and emits `progress_changed`. `PlayWindow` shows `Lv N · EXP a/b` in the status bar. |
| `src/dw1_workbench/quicksave.py` | `save(..., progress=)` writes `slotN.progress.json` next to the slot (and removes it when no progress is passed). Adds `progress_path()`/`progress()`; `delete()` removes it too. |
| `src/dw1_workbench/play_panel.py` | **Game rules (P6)** checkbox, persisted as `play/game_rules`, enabled only when live RAM is available (USA). Off by default. |
| `tests/test_progression.py` (new) | 4 synthetic tests (see Verification). |
| `README.md`, `docs/FORMATS.md`, `docs/HANDOVER.md`, `docs/PROGRESS.md` | User-facing rules, RAM addresses and verification notes. |

### Rules as coded

- Levels 0–99. `exp_to_next(L) = (L+1)(L+20)//2`; `total_exp(L) = L(L+1)(L+29)/6`. Level 20 = 3,430 total; 20→21 needs 420; 98→99 needs 5,841; level 99 = 211,200.
- Level floor: outside battle, every stat is raised to at least `stats_for(level)` (level × gain, capped at 9,999 HP/MP and 999 others). Higher trained or evolved values are kept. Current HP/MP rise by the same amount as their maximum.
- Level-up: `_add_stats` adds the full gain on top of the current stats (not just up to the floor), capped.
- EXP: enemy class comes from `DIGIMON_DATA[kind].level` (byte +29): 3 → 10, 4 → 30, 5 → 90. Fresh (1) and In-Training (2) give 0 EXP. The sum is multiplied by `COMBO[count]` and rounded half-up.
- Death/aging: every frame, `remainingLifetime` is raised to ≥ 360 h, `lives` is set to 3, and `age` is held at the first value seen (it may drop, for a newly hatched partner).
- Win detection: `PARTNER_PARA.battles` goes up by exactly 1 while enemies were captured during that battle. Enemies are captured while `GAME_STATE != 0`, because their entity slots are cleared after the battle.

### RAM map used (from jype0/dw_decomp `aaf69a4`, USA only)

| Symbol | Address / offset |
|---|---|
| `PARTNER_ENTITY` | `0x801557A8`; Stats at +56 (off, def, speed, brain at 0/2/4/6; max HP/MP at 16/18; HP/MP at 20/22); lives at +124 |
| `PARTNER_PARA` | `0x80138460`; remainingLifetime +72 (hours), age +74 (days), battles won +84 |
| `GAME_STATE` | `0x80134F0A` (0 = not in battle) |
| Enemy count | `0x80134D6C` |
| `CombatData*` | `0x80134D4C`; entity IDs at `4*360 + 204` (slot 0 = partner) |
| Entity table | `0x8012F344` |
| `DIGIMON_DATA` | `0x8012CEB4`, 180 × 52 bytes, level byte +29 |

## Verification state

| Check | Result |
|---|---|
| Synthetic tests (this clone, Windows, Python 3.13, temporary venv with pytest only) | `tests/test_progression.py` + `tests/test_quicksave.py`: **8 passed in 0.17s** (2026-11-03). The full suite was not run here: PySide6 is not installed on this machine. |
| Full suite (earlier, workstation, from handover) | 164 passed after P4 bundles. `tests/test_player.py` has to run outside the sandbox (Qt audio stalls inside it). The count after `a7b0804` was not recorded. |
| Real game, scripted (`p6_rules_check.py`, slot 2, Native Forest) | Agumon was raised from 81/61/71/71, 800/601 to level-20 values. Lifetime 1, lives 1 and age +5 were restored. A replayed win over two ModokiBetamon gave 30 EXP. A win crossing into level 21 added +100/+10. |
| Real battle (`p6_real_battle.py`) | The user's inspections `20261103-093656-777038` and `20261103-093709-657079`, finished with the rules on: one Rookie read during `GAME_STATE` 1–2, won-counter 1 → 2, **+10 EXP** (`Lv 20 · EXP 10/420`). |
| User desktop | **Not seen yet.** In the user's session the checkbox was off (`play/game_rules` never set). |

Still unverified on the desktop (from the branch handover):

1. The status-bar level is visible and updates.
2. A real level-up in normal play.
3. A **lost** battle: lives stay at 3 and the partner does not die. Not tested in the real game at all.
4. Quick save → quick load keeps the level, and loading another slot switches it.

## Review notes and risks found while preparing this handover

These are from reading the code. None is confirmed as a bug in the game.

1. **Evolution may be affected by holding age/lifetime.** DW1's natural evolution is time-driven. Find out which `PARTNER_PARA` field drives the evolution timers in dw_decomp (`partner_impl.c`/`clock.c`), and whether holding `age` or pinning `remainingLifetime` stops or changes evolution. Do this before the level-based evolution work, because it may decide the design.
2. **Level is global, not per partner.** One `progression.json` serves the whole player store. After rebirth or evolution, the new partner inherits the level, and the floor lifts a Fresh newborn to level-20 stats straight away. The handover already lists "floor after rebirth" as an open decision. With death off, rebirth only happens through scripts or old saves.
3. **Memory-card saves carry no level.** Loading an in-game memory-card save applies the player's current `progression.json` level. Loading an F9 inspection does the same: it calls `rules.reset()` but restores no progress. A pre-P6 quick slot with no `.progress.json` keeps the current level.
4. **Stale enemies after flee/loss.** `_enemies` is cleared only when the won counter changes. After a fled or lost battle, if the next battle's enemy read fails validation (returns `[]`), a win there would award the previous battle's enemies. Low probability; to fix, clear `_enemies` on the transition from in-battle to out-of-battle when there was no win.
5. **The ×3 combo can never apply.** The game puts at most 3 enemies in a battle. The 4-enemy entry is dead unless P8 changes battles.
6. **Same-frame ordering.** Rules run after `run_frame()`. If the game zeroes the lifetime and checks death within a single frame, the write comes too late. Lives held at 3 should stop the "last life" path, but only the lost-battle test (item 3 above) will prove it.
7. **Save format.** `Progress` is JSON `format: 1` with only `exp`. Roadmap P6 asks for versioned saves with migration tests. Storage, evolution and per-partner data will need format 2 and a migration from 1.
8. **The word "storage" means two things.** The commit's "Storage" means where level/EXP is persisted. The roadmap's storage is a **Digimon bank** (deposit/withdraw/active partner), which is not started.

## Remaining P6 work, in suggested order

1. **Desktop confirmation of what exists** (the 4 unverified checks above). Tick **Game rules (P6)** in the Play tab, use a test save store, and do not overwrite the user's memory card.
2. **Fix the small issues**: stale enemies (risk 4), then decide and implement per-partner vs global level (risk 2) and the post-rebirth floor.
3. **Investigate evolution timing** against held age/lifetime (risk 1). Record the evidence in `docs/FORMATS.md`.
4. **Level-based evolution** using the roadmap stage table. Decide: automatic or player choice; branch eligibility (the game's own requirement tables or level only); what happens at 45+ without Mega content (P7). Proposal: stay Ultimate and keep levelling until P7 provides Mega. Evolution in RAM must set the partner's type, model and stat bonuses the way the game's own evolution routine does. Prefer triggering the game's routine over writing fields by hand, and cite the dw_decomp function used.
5. **Fresh/In-Training enemy EXP** (currently 0): get values from the user.
6. **Digimon storage**: data model (per-slot `progress` format 2 holding a list of owned Digimon with type, stats, level/EXP and care fields), deposit/withdraw UI in the Play window, rules for swapping the active partner in RAM, and whether stored Digimon age or train (with aging off, "no" is simplest).
7. **Save versioning**: `Progress` format 2 plus a migration from 1; tests for migration, storage round trip, level 0/20/99 boundaries and evolution thresholds.
8. **Balance**: stat caps (999 cap vs +10/level on top of training: trained partners saturate early), enemy scaling. Needs user input.
9. Update `README.md`, `docs/FORMATS.md`, `docs/PROGRESS.md` and bring `docs/ROADMAP.md` over from `main`, marking P6 PARTIAL.

## Open decisions for the user

- Should level/EXP belong to each partner or to the player?
- Should the level floor apply to a freshly reborn/evolved partner?
- Evolution: automatic at the threshold, or player choice? Level only, or level plus the game's own requirements?
- What happens at level 45+ before Mega content exists?
- EXP values for Fresh and In-Training enemies.
- Storage: capacity, where to access it (anywhere or a specific place), whether stored Digimon change over time.
- Keep the 999 stat cap, or raise it (that needs patching the game's cap checks, not just RAM)?

## How to resume

On the Linux workstation, from the repository:

```bash
git checkout python-player && git pull
QT_QPA_PLATFORM=offscreen PYTHONPATH=src /home/drdark/.venv/bin/python -m pytest -q --ignore=tests/test_player.py
PYTHONPATH=src /home/drdark/.venv/bin/python -m dw1_workbench.app
```

On this Windows clone, only the GUI-free tests run until PySide6 is installed (`pip install -e .` with `constraints.lock`):

```bash
PYTHONPATH=src python -m pytest -q tests/test_progression.py tests/test_quicksave.py
```

Then go to Play, set the core/BIOS, tick **Game rules (P6)**, and start from a quick slot near Native Forest to repeat the checks above.

Rules for whoever picks this up, carried over from the earlier handovers: do not commit disc data, BIOS or saves. Do not overwrite the user's saves or memory card (use a separate test store). Keep world zoom switched off. Commit or push only when the user asks.
