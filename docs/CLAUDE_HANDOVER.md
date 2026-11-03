# Claude handover — P6 on Windows

Updated 2026-11-03 (Asia/Muscat). **Current Claude entry point.** P6 is **PARTIAL**. Read this first, then [P6_HANDOVER.md](P6_HANDOVER.md), [FORMATS.md](FORMATS.md), [PROGRESS.md](PROGRESS.md), [ROADMAP.md](ROADMAP.md). Earlier chronological entries can be superseded. The old P4 guide is preserved in [CLAUDE_P4_HANDOVER_HISTORY.md](CLAUDE_P4_HANDOVER_HISTORY.md).

## Repository and safeguards

- Actual repo: D:\DW1-coop, not the C-drive environment default.
- Branch: python-player; committed HEAD f7f7dc2, “Add P6 handover: current state, risks and remaining work”. Original P6 rules are in ancestor a7b0804.
- **Windows continuation is uncommitted, with new untracked modules/tests/docs. Inspect git status as well as git diff.**
- No commit/push authorized. Preserve dirty changes. Never stage Digimon World.7z, disc/BIOS/core/saves or private validation outputs.
- World zoom stays OFF: user reported crashes/misplaced entities. Camera C content was not changed/rebuilt; .gitattributes preserves LF for its exact source-hash test. The file can appear modified from Windows line endings/stat cache.
- ROADMAP.md was restored from local main and updated; its older P0–P5 inventory is historical. Do not merge main just to retrieve it.
- App/player was launched for the user during this handover. Do not kill an active game, overwrite the user's card, or grant test EXP to the real store.

## Settled user choices

1. No aging/death; battle defeat must remain meaningful. Start level20, matching stats; +100 max HP/MP and +10 Offense/Defense/Speed/Brains per level. Caps 9999/999; trained/evolved values above the floor stay.
2. Rookie10 / Champion30 / Ultimate90 EXP; combo x1/x1.5/x2/x3 for 1–4 enemies (native maximum is 3). Fresh/In-training EXP still zero pending a decision. Curve: (L+1)(L+20)//2; level20→21 needs420.
3. **Manual player-selected linked evolution**, with **Champion at level21 for testing**. Current eligibility is active Rookie + native link + level, without original care/stat scoring. Do not re-ask automatic versus manual.
4. Evolution is in-game item7, Digi-Dex item8; original-style icon/name grid. Final Tree follows the user's native screenshot: center portrait, previous forms left, colored next-form branches right.
5. **Only battling unlocks the Digi-Dex.** Seeing/proximity/map entry/bystanders do not. Victory is not required. Include stats/rank, locations/times, drops and linked/equipped skills.
6. Opening/navigation/choosing sounds requested.

## Files and final behavior

| File | Responsibility |
|---|---|
| progression.py | Existing levels/stat/life rules plus confirmed-victory retention across map reload and stale flee/loss protection. |
| evolution.py (new) | Reversible USA evolution guards, native-link confirmation, menu/Tree/Dex input models and sound intents. |
| game_menu_view.py (new) | Original icon grid; focused colored Tree; resident VRAM icons/portraits. |
| digidex.py (new) | Battle-only knowledge, native metadata/skills, possible MAP placements, observed hours, atomic JSON. |
| digidex_view.py (new) | Windows search dialog with anonymous locked entries. |
| menu_audio.py (new) | Generated short stereo UI tones, saturating mix into core PCM. |
| player.py | Worker, presentation, persistence/signals, serialization, asset caches, audio and F4 integration. |

All source files above are under src/dw1_workbench. Tests: test_progression.py plus new test_evolution.py, test_digidex.py, test_battle_result.py, test_menu_audio.py. pyproject.toml/constraints.lock add missing PySide6-Addons/QtMultimedia.

### Menu / Tree / evolution

- Three-column original icon grid; native first six icons plus fishing decoded from user's VRAM, new Evolution/Dex icons are vector. No game art bundled. Fishing follows Dex.
- Up/Down keep columns; Left/Right move items; Cross selects. **Circle and Triangle both close main menu.** Previous bug forwarded Circle but USA native CANCEL_BUTTON is Triangle (0x10); translated handoff now works. Nested back returns one level.
- Final focused Tree **replaces earlier six-column rendering**. Up/Down choose branch; Left/Right choose a side and explore it on repeated presses. Cross selects/confirms eligible Champion twice; other forms can be browsed without evolving.
- All source-proven forward partner links are browsable, including shared ancestors. Fresh links are hardcoded natively: 1→2,15→16,29→30,43→44. Mega pending; item/script-only routes are not invented.
- Native portrait atlas covers original 61 chart entries, with text fallback. Native confirmation sets EVOLUTION_TARGET, tamer6/0, partner13/0; partnerTickEvolving/EVL handles model/animation/bonuses/map restoration.
- Guard sites: tickConditions call at 0x800A90C8 forced to return zero (bypasses natural/virus evolution); handleSpecialEvolutions at 0x800E3234 returns -1. Validate original bytes; synchronize Beetle I-cache; no scratch RAM. Restore instructions before quick-save/Resume/F9 serialization. Explicit evolution items/scripts remain native.
- Manual evolution is USA/Beetle PSX only. Unsupported cores or code mismatch report unavailable. Full words/layout/source evidence: FORMATS.md.

### EXP / saves

- Visible post-win stat panel is composited as EXP gained, resulting level, next-level progress. Native rendering remains underneath because it applies stat rewards; Bits/drops/learning/confirm remain native.
- Reported EXP loss: GAME_STATE returns to0 during map reload before the win counter increments; clearing enemies then discarded the reward previewed in results.
- Confirmed victory now retains its enemy/result snapshot through the gap and temporarily invalid partner RAM, awards once on first valid world frame after native combat-stat restoration, and does not duplicate on late counter. Flee/loss clear unconfirmed encounters; explicit load/reset clears transient context.
- EXP is global per player/disc, not per Digimon. Format1 progression.json and slotN.progress.json carry EXP; ordinary memory-card saves do not. Quick-load intentionally restores its sidecar.
- **Audit quick-save/load during results/map transitions:** pending victory is transient GameRules context, not format1 Progress JSON. Normal battle, area transition and persisted total are verified; this exact save edge is not.

### Dex / audio

- Per-disc digidex.json format1, intentionally independent of quick-save rewinds. Atomic writes for new entry/profile/hour; bad versions/records preserve file and report unavailable.
- Read initialized battle opponents, never all NPCs. Ignore stale early state1 until setup state3; newly loaded valid mid-battle state1/2 works. Runtime reset preserves knowledge.
- Native stage/rank, base stats, drops/chance, species-linked moves and enemy-equipped animation slots mapped to named techniques; distinct stat/skill encounter variants.
- Day06–15 / Evening16–19 / Night20–05 are observed battle hours, **not exclusive spawn schedules**. Static placements are story/script conditional; unknown schedules stay unknown. Index:713 placements/125 species, not all dynamic spawns.
- F4/toolbar dialog pauses and restores prior pause state; USA live RAM works even with P6 off. In-game item8 uses P6 menu.
- Generated opening/navigation/choose/back tones mix into existing stereo game audio with bounded samples and no navigation backlog. Delegated actions retain native SFX. Physical device volume still needs desktop acceptance.

### Dex avatars and reached-forms Tree (2026-11-03, later session)

User requests: Digi-Dex entries show the Digimon's avatar "like the Digimon tab"; the evolution chart must not show forms the partner never evolved into.

- avatars.py (new): load_avatars reads the played disc (patched copy included), renders each model with model_view.ModelSnapshot (the Characters tab renderer without a widget, so it runs off the GUI thread), caches avatars/NNN-<sha16 of model+texture slot>.png in the player store. EmulatorWorker starts it on a daemon thread after the Dex loads (discovered first); _shutdown sets _stop and joins it.
- Shown: in-game Dex list (17 px, "?" for locked rows), Dex pages (48 px, wider panel), F4 dialog (list icons + 128 px panel). Locked entries never get an avatar.
- Real disc: 150/179 render (~70 s first run, 0.2 s cached); 29 have unsupported geometry (e.g. 6, 29, 44, 62) and show none. Contact sheet checked visually.
- evolution.EvolutionHistory: evolution_history.json format 1 {forms}, per disc, independent of quick-save rewinds, observed in _read_live. relation_graph keeps recorded forms + current partner, plus the current partner's linked forms as anonymous "???" (graph["hidden"]). Hidden nodes have no portrait/name, cannot be focused/explored, but stay selectable so manual evolution still works; they are revealed once reached. History starts empty, so earlier forms appear only once reached again. No history (file error) keeps the old full chart.
- Tests: tests/test_dex_avatars.py (8). Full suite 195 passed, 14 skipped. **Not yet seen on the user's desktop in the running game.**
- Then (user: "add some colors… match the game theme for the skills, add skill name and its icon"): digidex.read_moves reads MOVE_DATA 0x8012623C (Move[122], 16 bytes; special = element 0 Fire 1 Battle 2 Air 3 Earth 4 Ice 5 Mech 6 Filth, verified on the USA disc; status 1–4 Poison/Confuse/Stun/Flat per FIGHTER_FLAG order; range 1 short/2 long/3 wide/4 support; MP shown = mpCost×3 as the game does). DigiDex.moves is RAM-only (snapshot carries it, save() does not). entry_rows gives (style, text, skill id) rows; entry_pages stays its text. Menu rows carry styles/skills; game_menu_view draws a themed panel, gold header bands, label/value and stat columns, and skill rows with workbench-drawn element badges (draw_element_icon; no game art) plus power/MP and a status dot. F4 dialog: themed stylesheet, title + avatar header, HTML details with element icons as document resources. Rendered with the real move table and checked visually; 196 passed, 14 skipped.

### Play window size fix and P7 (2026-11-03, later)

- User: the map size kept changing and resized the game screen. Cause: the Live map dock's word-wrapped NPC label set the dock width (and long status texts the window minimum). Fix in player.py: live panel fixed at 320 px, NPC list in a 180 px scroll area, status and live labels use an Ignored horizontal size policy (live label min 220 px, full text in its tooltip). Offscreen check: window/view/dock sizes identical with short and very long texts. 196 passed, 14 skipped.
- P7 was started then **postponed by the user**. Decisions (MetalEtemon 65, Etemon → MetalEtemon at level 45) and the resume step are recorded under P7 in ROADMAP.md. No P7 code exists.

### Digi-Bank and early-stage evolution (2026-11-03, later)

User request: storage as a 9th menu icon, 4 slots; list shows name + icon; Cross shows status; Cross sets active; "Select new egg" hatches an egg into the bank, then set it active.

- bank.py (new): capture/apply of a partner record (Stats 32 bytes + PartnerPara 0x84 + EXP), egg records (stats/para null, native reincarnation stats and care), status pages in Dex row styles. learnedMoves stay shared.
- progression.Progress: `bank` (4 entries, record or None), JSON format 2; format 1 loads with an empty bank; `valid_stored` validates records. The bank therefore travels with progression.json and every slot sidecar.
- evolution.EvolutionController: menu options built by name (… Evolution, Digi-Dex, Digi-Bank, Fishing); grid pitch shrinks for a 4th row. `_bank_frame` (list → status (2 pages) / eggs, double-Cross confirms), `hatch`, `start_swap` (captures active, starts native sequence, updates bank/EXP at once), `_finish_swap` (after state leaves 1 and returns with the new kind, writes stored stats/para). `reset()` drops a pending swap (state load). `notices`/`events` are taken by EmulatorWorker._bank_events (messages, rules.reset, write progression.json).
- Manual evolution generalised: STAGE_LEVELS {Fresh 6, In-Training 12, Rookie 21(test)}; FRESH_TARGETS from getFreshEvolutionTarget; `required_level()` feeds Tree footer and checks. Ultimate stage still has no manual step.
- Real check (isolated store, user slot 0 read-only, test EXP in memory only): menu shows Digi-Bank; Botamon hatched into slot 1; swap Agumon → Botamon finished in 1814 frames (native wireframe sequence; HP 90, Off 10, age 0, level 0; Agumon stored with its EXP); swap back finished in 1837 frames with Stats bytes identical to the stored record and EXP restored. User store hashes unchanged. Script/captures: D:\DW1-coop-data\bank-validation\ (check_bank.py, runtime\*.png, report.json). Field captures do not clearly show the partner (camera); RAM and the sequence's wireframes do.
- Tests: tests/test_bank.py (8). Full suite 204 passed, 14 skipped. **Not yet seen on the user's desktop.**

### Regen and training EXP (2026-11-03, later)

User chose 1%/s regen (over 2%) and +5 EXP per training, to revisit with the EXP plan.

- progression.py: REGEN_PERCENT 1, REGEN_FRAMES 60 (game time), out of battle only, never from 0 HP. TRAINING_EXP 5 via `_training_tick` (TRAINING_STATE nonzero→0 edge + HOUR = TRN_CURRENT_HOUR+1/+2). `award` now goes through `gain(amount, source)`, shared with training (level-ups add stats the same way).
- Real check (isolated, user slot 0 read-only): HP 500→700 and MP 300→500 over 600 frames at max 2000 (exactly +20/s); TRAINING_STATE stayed 0 outside a gym, no EXP given. Script: D:\DW1-coop-data\regen-validation\check_regen.py.
- **A real gym session is not yet verified** (no automated gym path). Ask the user to train once and look for "Training: +5 EXP". Tests: 2 new in test_progression.py; full suite 206 passed, 14 skipped.

### Graphics options and frame pacing (2026-11-03, later)

User: 16:9, less pixelated/grainy, options in the interface, occasional lag.

- Lag: the worker paced frames with `threading.Event.wait`, which on Windows rounds to the 15.6 ms tick: 17% of frame intervals were 31–36 ms with catch-up frames after (profile of the real EmulatorWorker, 40 s, isolated copy of slot 0). Now `time.sleep` (high-resolution on Python 3.11+): 0 intervals over 25 ms, p99 20.7 ms. Emulation itself is 3.4 ms a frame; rules/live/memory card work is under 1 ms. One-off costs remain on first menu/Tree open (VRAM decode) and GUI-side work was not profiled.
- graphics.py: RESOLUTIONS (1x/2x/4x), core_options (internal resolution, dither off, optional PGXP memory only + texture), SCREENS and screen_rects. Play tab Graphics row (QSettings play/resolution, smooth_colours, steady_3d) feeds PlayConfig.options; game window toolbar Screen combo + Smooth (play/screen, play/smooth, live).
- Measured emulated frame time: native 3.4 ms, 2x 5.0, 2x+widescreen 5.1, 4x 10.2 (p99 23, 461/1383 intervals over 25 ms), 4x+PGXP 13.0. 2x is the default. Widescreen hack rejected: pre-rendered backgrounds stretch and the tamer drew ~50 px off (captures in D:\DW1-coop-data\graphics-validation\runtime). Menu icons/portraits decode at 2x (save-state VRAM stays native); a 2x state loads at 1x. Scripts: profile.py, check_2x.py, list_options.py there.
- Tests: tests/test_graphics.py (2). Full suite 208 passed, 14 skipped. **Not yet seen on the user's desktop.**

## Windows setup and commands

Repo: D:\DW1-coop. Local .venv is Python3.13 with editable project, PySide6 Essentials+Addons and Pillow. Use it instead of system Python.

~~~powershell
Set-Location D:\DW1-coop
.\.venv\Scripts\pythonw.exe -m dw1_workbench.app
$env:QT_QPA_PLATFORM='offscreen'
.\.venv\Scripts\python -m pytest -q
git diff --check
~~~

If dependencies are needed: .\.venv\Scripts\python -m pip install -e .

- Core: C:\RetroArch-Win64\cores\mednafen_psx_libretro.dll; Beetle PSX0.9.44.1 71ee81e.
- BIOS: D:\Digimon World\PSX_BIOS; validation selected scph7003.bin. Do not rename/replace firmware; states depend on BIOS identity.
- Disc: D:\Digimon World\Digimon World.cue; source hash prefix06084490ea0db340.
- User store: C:\Users\Dr.Dark92\AppData\Local\DW1-coop\play\06084490ea0db340.
- Saved play/game_rules=true.
- Latest full run: **187 passed,14 skipped** (compiler-dependent mock-core tests), 2026-11-03. Diff check passes. Camera hash passes with LF; zoom off.
- Offscreen Windows QA scripts need QFontDatabase.addApplicationFont('C:/Windows/Fonts/arial.ttf'); that Qt plugin otherwise discovers no fonts. Normal desktop fonts work.

## Real evidence — isolated test stores

- Native icon grid and final portrait/color Tree visually inspected. Both main back buttons, nested Dex/Tree returns restore walking.
- Agumon→Greymon at test-only Lv21 completed native sequence and returned normally.
- Natural timer, virus threshold and special/Nanimon trigger each evolved unguarded, stayed Agumon for600frames guarded.
- NPC map entry unlocked nothing; ModokiBetamon battle unlocked ID83. Verified Rookie, stats, sm.recovery40%, five source placements, linked Static Elect/Electric Cloud/Water Blit/Aqua Magic/Electric Shock and three equipped moves. Older state retained discovery; locked/search UI checked.
- Real Rookie victory +10EXP crossed test threshold to total3855 / Lv21 EXP5/451. Native map109 Native Forest→map112 Green Gym retained3855, including reopening private JSON.
- Menu sound events/mixed PCM WAV recorded. Physical loudness still a desktop check.
- User save/card hashes unchanged; diagnostic position setup was isolated RAM only. No test EXP or discovery written to user store.

Private scripts/captures/reports, **outside Git**:
- D:\DW1-coop-data\menu-validation\check_menu.py; Tree/grid/portrait PNGs, menu-audio.wav.
- D:\DW1-coop-data\digidex-validation\check_dex.py, check_exp_area.py, area-exp-report.json, private states/JSON/screenshots.
- D:\DW1-coop-data\evolution-validation\check_evolution.py, check_guards.py.
- Source: D:\DW1-coop-data\references\dw_decomp, revision aaf69a4.

Harnesses assume paths/Resume/maps and were iteratively adapted. Inspect before reuse; never use user store for outputs.

## Remaining P6 work

1. User desktop acceptance: sounds, Tree/readability, all native actions, ordinary area/EXP play, quick-save/load.
2. Real defeat/no-death check outstanding. Synthetic lives/lifetime checks pass; every same-frame death/reincarnation route is not proven.
3. Fresh (6) and In-Training (12) thresholds now implemented; Champion 21 is a test rule; Champion → Ultimate has no manual step yet. Mega needs P7. Historical roadmap schedule is not implemented behavior.
4. Digi-Bank first version done (see above). Open: deposit without a replacement (not offered: always one active partner), whether stored partners train/age (they do not), scripted native rebirth still keeps the active EXP, desktop acceptance.
5. Savev2/migration, storage round trips, remaining evolution boundaries, configurable curve, recovery/training/cap/enemy balance; Fresh/In-training EXP still0.
6. Audit pending-victory mid-transition saves as noted above.
7. More species/skills/bosses and exact script spawn schedules need verification; keep unknown/conditional labels.
8. Commit only when requested, include untracked source/tests/docs, exclude proprietary files. No co-op or native runtime implemented.

Preserve the working user setup. Ask only for genuinely unresolved gameplay choices.
