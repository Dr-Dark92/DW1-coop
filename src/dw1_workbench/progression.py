"""P6 game rules for the USA release, applied to console RAM while the player runs the original game.

The game has no levels or EXP. These rules add them outside the game: the partner gets a level (0-99) that
grows from EXP won in battle, and each level is worth +100 max HP, +100 max MP and +10 Offense, Defense, Speed
and Brains. Aging and death are turned off. Nothing on the disc or memory card changes; level and EXP are kept
by the player (progression.json, and with each quick save), together with the Digi-Bank (bank.py): up to
BANK_SLOTS stored partners, each with its own EXP.

Addresses and layouts are from jype0/dw_decomp aaf69a4 (config/us/symbols.txt, include/dw/entity.h,
include/dw/params.h, include/dw/combat.h, src/main/tamer.c, src/main/clock.c, src/main/partner_impl.c,
src/main/script_interp.c) and apply only to SLUS_010.32.

Death has three routes in the game: remainingLifetime reaching 0 (tickDeathCondition), a lost battle taking the
last of the partner's lives (startBattle's caller sets remainingLifetime to 0), and 12 hours of sickness taking
a life. Keeping the lifetime topped up and the lives at 3 every frame closes all of them; age is held still.
Out of battle the partner regains REGEN_PERCENT of its max HP and MP every second of game time (60 frames), and
each finished gym training session is worth TRAINING_EXP. A session (src/trn/*.c) runs with TRAINING_STATE above 0,
returns it to 0 when it ends, and TRN_advanceTrainingTime sets HOUR to the hour it started (TRN_CURRENT_HOUR) + 1.

A battle is won when PARTNER_PARA.battles goes up (only startBattle's win branch increments it); the enemies are
read while the battle runs, since their entity slots are cleared afterwards.
"""
from __future__ import annotations

import json
import struct
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .live import ram_offset

# -- the rules ----------------------------------------------------------------------------------------------------
LEVEL_MAX = 99
START_LEVEL = 20
STAT_GAINS = {"hp": 100, "mp": 100, "offense": 10, "defense": 10, "speed": 10, "brains": 10}  # per level
STAT_CAPS = {"hp": 9999, "mp": 9999, "offense": 999, "defense": 999, "speed": 999, "brains": 999}  # the game's caps
CLASS_EXP = {3: 10, 4: 30, 5: 90}  # DigimonPara.level: Rookie, Champion, Ultimate. Fresh/In-Training: not set (0).
COMBO = {1: 1.0, 2: 1.5, 3: 2.0, 4: 3.0}  # EXP multiplier by number of enemies in the battle
CLASS_NAMES = {1: "Fresh", 2: "In-Training", 3: "Rookie", 4: "Champion", 5: "Ultimate"}
FULL_LIFETIME = 360  # hours; the game's own value after hatching or evolving
LIVES = 3
REGEN_PERCENT = 1  # of max HP and MP, per second of game time out of battle
REGEN_FRAMES = 60
TRAINING_EXP = 5  # per finished training session; to revisit with the EXP plan
BANK_SLOTS = 4


def exp_to_next(level: int) -> int:
    """EXP needed to go from `level` to `level + 1`: (L + 1)(L + 20) / 2."""
    return (level + 1) * (level + 20) // 2


def total_exp(level: int) -> int:
    """EXP needed to reach `level` from 0: L(L + 1)(L + 29) / 6, the sum of exp_to_next below it."""
    return level * (level + 1) * (level + 29) // 6


def level_for(exp: int) -> int:
    level = 0
    while level < LEVEL_MAX and total_exp(level + 1) <= exp:
        level += 1
    return level


def battle_exp(enemy_classes: list[int]) -> int:
    """Sum of each enemy's class EXP times the multiplier for how many enemies there were."""
    if not enemy_classes:
        return 0
    multiplier = COMBO.get(len(enemy_classes), COMBO[max(COMBO)])
    return int(sum(CLASS_EXP.get(level, 0) for level in enemy_classes) * multiplier + 0.5)


def stats_for(level: int) -> dict[str, int]:
    """The stats a level stands for; the partner never has less (training and evolution may add more)."""
    return {name: min(gain * level, STAT_CAPS[name]) for name, gain in STAT_GAINS.items()}


def valid_stored(record) -> bool:
    """A bank record: {"kind", "exp", "stats": 32-byte Stats hex or None (new egg), "para": PartnerPara hex or None}."""
    if not isinstance(record, dict) or set(record) != {"kind", "exp", "stats", "para"}:
        return False
    kind, exp, stats, para = record["kind"], record["exp"], record["stats"], record["para"]
    if type(kind) is not int or not 0 < kind < 66 or type(exp) is not int or not 0 <= exp <= total_exp(LEVEL_MAX):
        return False
    for value, size in ((stats, 32), (para, 0x84)):
        if value is not None:
            try:
                if not isinstance(value, str) or len(bytes.fromhex(value)) != size:
                    return False
            except ValueError:
                return False
    return True


@dataclass
class Progress:
    """The active partner's EXP, and the stored partners (format 2; format 1 records had no bank)."""
    exp: int = total_exp(START_LEVEL)
    bank: list = field(default_factory=list)  # BANK_SLOTS entries: a valid_stored record, or None (empty slot)

    def __post_init__(self):
        self.bank = (list(self.bank) + [None] * BANK_SLOTS)[:BANK_SLOTS]

    @property
    def level(self) -> int:
        return level_for(self.exp)

    @property
    def into_level(self) -> tuple[int, int]:
        """EXP gained in this level, and EXP this level needs (0 at the maximum)."""
        level = self.level
        return self.exp - total_exp(level), exp_to_next(level) if level < LEVEL_MAX else 0

    def summary(self) -> str:
        done, need = self.into_level
        return f"Lv {self.level}" + (f" · EXP {done}/{need}" if need else " · max")

    def to_json(self) -> bytes:
        return json.dumps({"format": 2, **asdict(self), "level": self.level}, indent=2).encode()

    @classmethod
    def from_json(cls, data: bytes) -> "Progress":
        record = json.loads(data)
        if (not isinstance(record, dict) or record.get("format") not in (1, 2)
                or type(record.get("exp")) is not int or record["exp"] < 0):
            raise ValueError("Unsupported progression record")
        bank = record.get("bank", []) if record["format"] == 2 else []
        if (not isinstance(bank, list) or len(bank) > BANK_SLOTS
                or any(entry is not None and not valid_stored(entry) for entry in bank)):
            raise ValueError("Unsupported Digi-Bank record")
        return cls(min(record["exp"], total_exp(LEVEL_MAX)), bank)

    @classmethod
    def load(cls, path: Path) -> "Progress":
        try:
            return cls.from_json(Path(path).read_bytes())
        except (OSError, ValueError):
            return cls()


# -- the game's memory --------------------------------------------------------------------------------------------
PARTNER_ENTITY = 0x801557A8
PARTNER_STATS = PARTNER_ENTITY + 56  # Stats: off, def, speed, brain, movesPrio[4], moves[4], max HP, max MP, HP, MP
PARTNER_LIVES = PARTNER_ENTITY + 124
PARTNER_PARA = 0x80138460
LIFETIME = PARTNER_PARA + 72  # remainingLifetime (hours)
AGE = PARTNER_PARA + 74  # days
BATTLES_WON = PARTNER_PARA + 84
GAME_STATE = 0x80134F0A  # 0 outside battle
ENEMY_COUNT = 0x80134D6C
COMBAT_DATA_PTR = 0x80134D4C
COMBAT_ENTITY_IDS = 4 * 360 + 204  # CombatData: FighterData[4] (360 bytes each), then PlayerData.entityIds[4]
ENTITY_TABLE = 0x8012F344
DIGIMON_DATA = 0x8012CEB4  # DigimonPara[180], 52 bytes each; level byte at +29
DIGIMON_PARA_SIZE, DIGIMON_LEVEL = 52, 29
STAT_FIELDS = (("offense", 0), ("defense", 2), ("speed", 4), ("brains", 6), ("hp", 16), ("mp", 18))
CURRENT_HP, CURRENT_MP = 20, 22
TRAINING_STATE = 0x80135371  # u8, 0 between training sessions
TRN_CURRENT_HOUR = 0x8013538E  # hour a training session started
HOUR = 0x80134EBC
# dw_decomp aaf69a4: UIBoxData is 36 bytes; state +18, render +32.
UI_BOX_DATA = 0x8013D390
POST_BATTLE_RENDER = 0x800EE1FC


@dataclass(frozen=True)
class BattleResult:
    gained: int
    exp: int

    @property
    def lines(self) -> tuple[str, ...]:
        progress = Progress(self.exp)
        done, need = progress.into_level
        return ("VICTORY", f"EXP gained  +{self.gained}", f"Level {progress.level}",
                f"EXP  {done}/{need}" if need else "Maximum level",
                f"Next level {need - done} EXP" if need else "EXP complete")


class GameRules:
    """Applied once per frame by the emulator worker. `read(offset, size)` / `write(offset, data)` use main RAM."""

    def __init__(self, read, write, progress: Progress | None = None):
        self.read, self.write = read, write
        self.progress = progress or Progress()
        self.reset()

    def reset(self, keep_victory=False):
        """Forget what was seen in RAM: after loading a state, resetting, or returning to the title screen."""
        if not keep_victory:
            self._victory_enemies = None
            self._victory_result = None
        self._age = None
        self._battles = None
        self._regen_frames = 0
        self._training = False
        self._enemies: list[int] = []
        self.battle_result: BattleResult | None = None

    def _s16(self, address):
        return struct.unpack("<h", self.read(ram_offset(address), 2))[0]

    def _put16(self, address, value):
        self.write(ram_offset(address), struct.pack("<h", value))

    def _u32(self, address):
        return struct.unpack("<I", self.read(ram_offset(address), 4))[0]

    def partner_valid(self) -> bool:
        partner = struct.unpack("<i", self.read(ram_offset(PARTNER_ENTITY), 4))[0]
        return 0 < partner < 180 and self._s16(PARTNER_STATS + 16) > 0

    def tick(self) -> list[str]:
        """Apply the rules for this frame; returns messages for the player (wins and level-ups)."""
        if not self.partner_valid():
            self.reset(keep_victory=True)
            return []
        messages = []
        in_battle = self._s16(GAME_STATE) != 0
        self._hold_life()
        if in_battle:
            enemies = self._battle_enemies()
            if enemies:
                self._enemies = enemies
        won = self._s16(BATTLES_WON)
        awarded = False
        # Victory UI is authoritative. Preserve its encounter across the game's
        # out-of-battle map reload, which can precede the won-counter increment.
        if not in_battle and self._victory_enemies is not None:
            messages += self.award(self._victory_enemies)
            self._victory_enemies = self._victory_result = None
            self._enemies = []
            awarded = True
        if (not awarded and self._victory_enemies is None and self._battles is not None
                and won == self._battles + 1 and self._enemies):
            messages += self.award(self._enemies)
            awarded = True
        if won != self._battles:
            if self._battles is not None:
                self._enemies = []
            self._battles = won
        box = self.read(ram_offset(UI_BOX_DATA), 36)
        state = struct.unpack_from("<h", box, 18)[0]
        renderer = struct.unpack_from("<I", box, 32)[0]
        if in_battle and state in (1, 2, 3) and renderer == POST_BATTLE_RENDER:
            if self._victory_enemies is None and self._enemies and not awarded:
                self._victory_enemies = list(self._enemies)
                gained = battle_exp(self._victory_enemies)
                self._victory_result = BattleResult(gained, min(self.progress.exp + gained, total_exp(LEVEL_MAX)))
            self.battle_result = self._victory_result
        else:
            self.battle_result = None
        if not in_battle:
            # Resolve a win first, then discard the encounter even after flee/loss.
            # A later unreadable battle must never reuse these enemies.
            self._enemies = []
            self._apply_floor()
            messages += self._training_tick()
            self._regen()
        else:
            self._regen_frames = 0
        return messages

    def _regen(self):
        self._regen_frames += 1
        if self._regen_frames < REGEN_FRAMES:
            return
        self._regen_frames = 0
        stats = self._stats()
        for name, current in (("hp", CURRENT_HP), ("mp", CURRENT_MP)):
            value, maximum = self._s16(PARTNER_STATS + current), stats[name]
            if 0 < value < maximum:  # 0 HP is the game's own business (fainting); never revive
                self._put16(PARTNER_STATS + current, min(value + max(1, maximum * REGEN_PERCENT // 100), maximum))

    def _training_tick(self) -> list[str]:
        if self.read(ram_offset(TRAINING_STATE), 1)[0] != 0:
            self._training = True
            return []
        if not self._training:
            return []
        self._training = False
        if (self._s16(HOUR) - self._s16(TRN_CURRENT_HOUR)) % 24 not in (1, 2):  # 2: the minute ran over the hour
            return []  # not a finished session (e.g. state loaded mid-session)
        return self.gain(TRAINING_EXP, "Training")

    def _hold_life(self):
        if self._s16(LIFETIME) < FULL_LIFETIME:
            self._put16(LIFETIME, FULL_LIFETIME)
        if self.read(ram_offset(PARTNER_LIVES), 1)[0] != LIVES:
            self.write(ram_offset(PARTNER_LIVES), bytes([LIVES]))
        age = self._s16(AGE)
        if self._age is None or age < self._age:  # first sight, or a newly hatched partner
            self._age = age
        elif age > self._age:
            self._put16(AGE, self._age)

    def _battle_enemies(self) -> list[int]:
        """DigimonPara.level of each enemy in the running battle."""
        count = self._s16(ENEMY_COUNT)
        combat = self._u32(COMBAT_DATA_PTR)
        if not 1 <= count <= 3 or not 0x80000000 <= combat < 0x80200000:
            return []
        ids = self.read(ram_offset(combat + COMBAT_ENTITY_IDS), 4)
        levels = []
        for entity_id in ids[1:1 + count]:
            if not 2 <= entity_id < 10:
                return []
            entity = self._u32(ENTITY_TABLE + entity_id * 4)
            if not 0x80000000 <= entity < 0x80200000:
                return []
            kind = struct.unpack("<i", self.read(ram_offset(entity), 4))[0]
            if not 0 <= kind < 180:
                return []
            levels.append(self.read(ram_offset(DIGIMON_DATA + kind * DIGIMON_PARA_SIZE + DIGIMON_LEVEL), 1)[0])
        return levels

    def award(self, enemy_classes: list[int]) -> list[str]:
        names = ", ".join(CLASS_NAMES.get(level, f"class {level}") for level in enemy_classes)
        multiplier = COMBO.get(len(enemy_classes), COMBO[max(COMBO)])
        return self.gain(battle_exp(enemy_classes), f"Victory over {len(enemy_classes)} ({names})",
                         f" (×{multiplier:g})" if multiplier != 1 else "")

    def gain(self, gained: int, source: str, detail: str = "") -> list[str]:
        before = self.progress.level
        self.progress.exp = min(self.progress.exp + gained, total_exp(LEVEL_MAX))
        after = self.progress.level
        messages = [f"{source}: +{gained} EXP{detail} — {self.progress.summary()}"]
        if after > before:
            self._add_stats(after - before)
            messages.append(f"Level up! Lv {after}: +{100 * (after - before)} HP/MP, "
                            f"+{10 * (after - before)} Offense, Defense, Speed and Brains")
        return messages

    def _stats(self) -> dict[str, int]:
        block = self.read(ram_offset(PARTNER_STATS), 24)
        return {name: struct.unpack_from("<h", block, offset)[0] for name, offset in STAT_FIELDS}

    def _set(self, name, value):
        self._put16(PARTNER_STATS + dict(STAT_FIELDS)[name], value)

    def _raise_current(self, name, delta):
        if name in ("hp", "mp") and delta > 0:
            address = PARTNER_STATS + (CURRENT_HP if name == "hp" else CURRENT_MP)
            self._put16(address, min(self._s16(address) + delta, STAT_CAPS[name]))

    def _add_stats(self, levels: int):
        stats = self._stats()
        for name, gain in STAT_GAINS.items():
            value = min(stats[name] + gain * levels, STAT_CAPS[name])
            self._set(name, value); self._raise_current(name, value - stats[name])

    def _apply_floor(self):
        stats, floor = self._stats(), stats_for(self.progress.level)
        for name, minimum in floor.items():
            if stats[name] < minimum:
                self._set(name, minimum); self._raise_current(name, minimum - stats[name])
