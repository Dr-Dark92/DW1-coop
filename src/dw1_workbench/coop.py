"""A prototype co-op battle in one game (USA, Beetle PSX): player 2's Digimon fights beside the partner.

Nothing in the game's code is changed. When a battle-on-contact script starts, a small routine in unused RAM, run once
from the game's own object list, loads the ally's model and places it (loadNPCModel, then scriptSetDigimon, as the
game's scripts do). It stands beside the partner in the highest free NPC slot, so handleBattleStart brings it into the
fight last, which also loads its effects and sounds. Between frames the player then writes who fights whom:

- FIGHTER_FLAG_10 makes the AI keep a target written into the fighter record, so the ally and the enemies can aim at
  any slot; the partner is turned away from the ally when it picks it.
- Player 2 has player 1's battle controls: Left and Right along the same commands, built by the same Brains rule
  (BTL_initializeCombat), a confirm button, and a finisher button when it is ready. The game's AI picks the techniques,
  steered as the partner's is: movesPrio weights and the charge mode follow the command, with Defensive standing by.
- Every loop over fighters runs to ENEMY_COUNT, so once the real enemies are down, the partner falls or it flees, the
  count drops by one. The ally is no longer in the battle and the game ends it normally, with rewards counted for the
  real enemies alone.

Player 2 gains what the main player gains: the same EXP and Bits go to their own record (coop_player2.json), which
sets their level and so their Digimon's stats. The Digi-Dex is shared because there is one per disc.

Addresses are from jype0/dw_decomp aaf69a4 (src/main/overworld.c, model.c, btl.c, world_object.c, src/btl/) and apply
only to SLUS_010.32.
"""
from __future__ import annotations

import json
import struct
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from .live import ram_offset
from .progression import LEVEL_MAX, level_for, stats_for, total_exp, exp_to_next
from .quicksave import _atomic_write

ENTITY_TABLE, GAME_STATE, ENEMY_COUNT, COMBAT_DATA_PTR = 0x8012F344, 0x80134F0A, 0x80134D6C, 0x80134D4C
WORLD_OBJECTS, OBJECT_COUNT = 0x80137418, 128
MAP_DIGIMON_TABLE, MAP_DIGIMON_SIZE = 0x8013CB50, 196
NPC_ENTITIES, NPC_SIZE, AUTOTALK = 0x80155828, 104, 102
NPC_MODEL_TAKEN, NPC_MODEL_SLOTS = 0x801371CC, 5
DIGIMON_DATA, DIGIMON_SIZE, DIGIMON_MOVES = 0x8012CEB4, 52, 35
MOVE_DATA = 0x8012623C  # Move[122]
MOVE_NAMES = 0x80126054  # char *[122]
MOVE_NAME_COUNT = 122
MONEY = 0x80134EB8  # the player's Bits
FLEE_CHANCES = 0x8012BB14
IS_SCRIPT_PAUSED, TALKED_TO_ENTITY = 0x80134FF4, 0x80134C9C
LOAD_NPC_MODEL, SCRIPT_SET_DIGIMON, REMOVE_OBJECT = 0x800B6110, 0x800B6118, 0x800A3008
PARTNER_SLOT, FIRST_NPC, LAST_NPC = 1, 2, 9
# Unused RAM (zero in every save sampled): the routine, then function, a0, a1, a2 and its result.
ROUTINE, CALL_DATA = 0x801C0000, 0x801C0200
NOT_DONE = 0xFFFFFFFF
TASK_ID = 0x7A10
ALLY_MARK = 0x7A7A  # MapDigimonEntity.padding0 of the entry placing the ally, so a loaded state finds it again
# Entity fields (DigimonEntity), and FighterData.
IS_ON_MAP, IS_ON_SCREEN, POSITION, LOCATION = 52, 53, 4, 120
OFFENSE, MOVE_WEIGHTS, MOVES, MAX_HP, MAX_MP, HP, MP, CHARGE_MODE = 56, 64, 68, 72, 74, 76, 78, 86
FIGHTER_SIZE, COOLDOWN, FLAGS, TARGET, ENTITY_IDS = 360, 40, 52, 55, 1644
FLAG_ATTACKING, FLAG_KEEP_TARGET, FLAG_CHARGING, FLAG_DEAD = 1 << 5, 1 << 10, 1 << 11, 1 << 15
FOLLOW_MODE, POST = 188, 168
RETARGET_FRAMES = 300  # how often an enemy looks again for the nearer of the two Digimon
FAR = 1 << 62
RUN, ATTACK, YOUR_CALL, MODERATE, DISTANCE, DEFENSIVE, CHANGE, FINISHER = 1, 2, 3, 4, 5, 6, 7, 11  # the game's numbering
COMMAND_NAMES = {RUN: "Run", ATTACK: "Attack", YOUR_CALL: "Your Call", MODERATE: "Moderate", DISTANCE: "Distance",
                 DEFENSIVE: "Defensive", CHANGE: "Change"}
BRAIN_TO_COMMANDS = (3, 4, 5, 7, 8)  # BRAIN_TO_COMMAND_MAP
LEFT, RIGHT, CONFIRM, SQUARE = "left", "right", "confirm", "finisher"  # player 2's buttons
FINISHER_GOAL, FINISHER_PROGRESS = 24, 26  # in FighterData
MOVE_SIZE, MOVE_POWER, MOVE_MP, MOVE_RANGE = 16, 4, 6, 8
FINISHER_TECHS = range(0x3A, 0x71)


def _lui(reg, value):
    return 0x3C000000 | reg << 16 | (value >> 16) & 0xFFFF


def _ori(reg, value):
    return 0x34000000 | reg << 21 | reg << 16 | value & 0xFFFF


def routine() -> bytes:
    """One-shot object tick: result = function(a0, a1, a2), then remove this object. R3000: loads have a delay slot."""
    t0, t9, a0, a1, a2, v0, sp, ra = 8, 25, 4, 5, 6, 2, 29, 31
    load = lambda reg, offset: 0x8C000000 | t0 << 21 | reg << 16 | offset
    code = [0x27BDFFE0, 0xAFBF0018, _lui(t0, CALL_DATA), _ori(t0, CALL_DATA),
            load(t9, 0), load(a0, 4), load(a1, 8), load(a2, 12), 0,
            0x0320F809, 0,  # jalr t9
            _lui(t0, CALL_DATA), _ori(t0, CALL_DATA), 0xAC000000 | t0 << 21 | v0 << 16 | 16,  # sw v0,16(t0)
            _lui(a0, TASK_ID), _ori(a0, TASK_ID), 0x34000000 | a1 << 16,  # ori a1,zero,0
            0x0C000000 | (REMOVE_OBJECT >> 2) & 0x3FFFFFF, 0,
            0x8C000000 | sp << 21 | ra << 16 | 24, 0x27BD0020, 0x03E00008, 0]
    return struct.pack(f"<{len(code)}I", *code)


def species_moves(read, species: int) -> list[int]:
    """Animation IDs for a species' techniques (DigimonPara.moves[i] plays animation 0x2E + i): its first three, and
    its finisher in the last place, where the partner's goes and the battle AI looks for one."""
    table = read(ram_offset(DIGIMON_DATA + species * DIGIMON_SIZE + DIGIMON_MOVES), 16)
    finishers = [0x2E + index for index, tech in enumerate(table) if tech in FINISHER_TECHS]
    normal = [0x2E + index for index, tech in enumerate(table) if tech != 0xFF and tech not in FINISHER_TECHS]
    moves = (normal[:3] + [0xFF] * 3)[:3]
    return moves + [finishers[0] if finishers else (normal[3] if len(normal) > 3 else 0xFF)]


def read_text(read, pointer: int, limit: int = 32) -> str:
    if not 0x80000000 <= pointer < 0x80200000 - limit:
        return ""
    return read(ram_offset(pointer), limit).split(b"\0")[0].decode("latin-1")


def read_names(read) -> tuple[dict[int, str], dict[int, str]]:
    """Digimon and technique names, from the game's own tables in RAM."""
    digimon = {kind: read_text(read, DIGIMON_DATA + kind * DIGIMON_SIZE, 20) for kind in range(180)}
    pointers = struct.unpack(f"<{MOVE_NAME_COUNT}I", read(ram_offset(MOVE_NAMES), MOVE_NAME_COUNT * 4))
    return digimon, {move: read_text(read, pointer) for move, pointer in enumerate(pointers)}


@dataclass
class PlayerTwo:
    """Player 2's own progress: gains the same EXP and Bits as the main player."""
    species: int
    exp: int
    bits: int = 0

    @property
    def level(self) -> int:
        return level_for(self.exp)

    def summary(self) -> str:
        level = self.level
        if level >= LEVEL_MAX:
            return f"Lv {level} · max"
        return f"Lv {level} · EXP {self.exp - total_exp(level)}/{exp_to_next(level)}"

    @classmethod
    def load(cls, path: Path, species: int, start_level: int) -> "PlayerTwo":
        """The saved record for this species, or a new one at the starting level."""
        try:
            record = json.loads(Path(path).read_text())
            if record.get("format") == 1 and record.get("species") == species and type(record.get("exp")) is int \
                    and type(record.get("bits")) is int and record["exp"] >= 0 and record["bits"] >= 0:
                return cls(species, min(record["exp"], total_exp(LEVEL_MAX)), record["bits"])
        except (OSError, ValueError):
            pass
        return cls(species, total_exp(start_level))

    def save(self, path: Path):
        _atomic_write(Path(path), json.dumps({"format": 1, "species": self.species, "exp": self.exp, "level": self.level,
                                              "bits": self.bits}, indent=2).encode())


def available_commands(brains: int) -> tuple[int, ...]:
    """Player 2's menu by the partner's rule; slot 0 is the finisher, which is not on the menu. Techniques stay the AI's."""
    entries = 9 if brains >= 500 else BRAIN_TO_COMMANDS[brains // 100]
    return {3: (FINISHER, RUN, YOUR_CALL), 4: (FINISHER, RUN, ATTACK, YOUR_CALL),
            5: (FINISHER, RUN, MODERATE, ATTACK, YOUR_CALL),
            7: (FINISHER, RUN, CHANGE, DISTANCE, MODERATE, ATTACK, YOUR_CALL),
            8: (FINISHER, RUN, CHANGE, DEFENSIVE, DISTANCE, MODERATE, ATTACK, YOUR_CALL),
            9: (FINISHER, RUN, CHANGE, DEFENSIVE, DISTANCE, YOUR_CALL)}[entries]


@dataclass(frozen=True)
class AllyPanel:
    name: str
    progress: str
    hp: int
    max_hp: int
    mp: int
    max_mp: int
    commands: tuple[int, ...]  # without the finisher
    hovered: int  # index into commands
    current: int  # the command in force
    target: str
    finisher_ready: bool
    down: bool
    left: bool


class CoopBattle:
    """Applied once per frame by the emulator worker, between frames."""

    def __init__(self, read, write, player: PlayerTwo, path: Path | None = None):
        self.read, self.write, self.player, self.path = read, write, player, path
        self.species = player.species
        self.names, self.move_names = {}, {}
        self._presses: deque[str] = deque()
        self.sound_events: list[str] = []
        self.panel: AllyPanel | None = None
        self.messages: list[str] = []
        self.reset()

    def reset(self):
        """After a state load or console reset: find an ally already placed, and forget the rest."""
        self._state = "field"
        self._slot = self._find_ally()
        self._step = None
        self._chances = None
        self._frames = 0
        self._targets = {}
        self._money_before = None
        self._commands = ()
        self._hovered = 1
        self._current = YOUR_CALL
        self._target = 0
        self._finisher_armed = False
        self._was_holding = False
        self._after_battle = False
        self.panel = None
        self.excluded_slot = None

    # -- memory --------------------------------------------------------------------------------------------------
    def _s16(self, address):
        return struct.unpack("<h", self.read(ram_offset(address), 2))[0]

    def _u32(self, address):
        return struct.unpack("<I", self.read(ram_offset(address), 4))[0]

    def _i32(self, address):
        return struct.unpack("<i", self.read(ram_offset(address), 4))[0]

    def _put16(self, address, value):
        self.write(ram_offset(address), struct.pack("<h", value))

    def _put8(self, address, value):
        self.write(ram_offset(address), bytes([value]))

    def _entity(self, slot):
        pointer = self._u32(ENTITY_TABLE + slot * 4)
        return pointer if 0x80000000 <= pointer < 0x80200000 - NPC_SIZE else None

    def _location(self, entity):
        return self._u32(entity + POSITION) + LOCATION

    def _position(self, entity):
        return struct.unpack("<3i", self.read(ram_offset(self._location(entity)), 12))

    def _name(self, entity) -> str:
        return self.names.get(self._i32(entity), "Digimon")

    def _table_entry(self, slot):
        return MAP_DIGIMON_TABLE + (slot - FIRST_NPC) * MAP_DIGIMON_SIZE

    def _find_ally(self):
        for slot in range(FIRST_NPC, LAST_NPC + 1):
            entity = self._entity(slot)
            if (entity is not None and self._s16(self._table_entry(slot) + 2) == ALLY_MARK
                    and self._i32(entity) == self.species):
                return slot
        return None

    def _free_slot(self):
        """The highest NPC slot with no entity and no Digimon the map could still place: fighting last, in no script's way."""
        for slot in range(LAST_NPC, FIRST_NPC - 1, -1):
            if self._entity(slot) is None and self._s16(self._table_entry(slot)) in (-1, 0xFF):
                return slot
        return None

    def press(self, button: str):
        """A player 2 button, from the GUI thread; acted on between frames."""
        self._presses.append(button)

    def _take_presses(self, foes_left: int, finisher_ready: bool):
        """The partner's input rules (tickTamerBattle): Left and Right move along the menu, confirm gives the order."""
        last = len(self._commands) - 1
        while self._presses:
            button = self._presses.popleft()
            if button == LEFT:
                self._hovered = self._hovered + 1 if self._hovered < last else 1
                self.sound_events.append("navigate")
            elif button == RIGHT:
                self._hovered = self._hovered - 1 if self._hovered > 1 else last
                self.sound_events.append("navigate")
            elif button == CONFIRM:
                command = self._commands[self._hovered]
                self.sound_events.append("choose")
                if command == CHANGE:
                    self._target = (self._target + 1) % max(foes_left, 1)
                else:
                    self._current = command
            elif button == SQUARE and finisher_ready:
                self._finisher_armed = True
                self.sound_events.append("choose")

    # -- calling the game ---------------------------------------------------------------------------------------
    def _call(self, function, a0=0, a1=0, a2=0):
        code = routine()
        if self.read(ram_offset(ROUTINE), len(code)) != code:  # never rewritten once there, so no stale cached code
            self.write(ram_offset(ROUTINE), code)
        self.write(ram_offset(CALL_DATA), struct.pack("<5I", function, a0, a1, a2, NOT_DONE))
        for index in range(OBJECT_COUNT):
            if self._s16(WORLD_OBJECTS + index * 12) == -1:
                self.write(ram_offset(WORLD_OBJECTS + index * 12), struct.pack("<hhII", TASK_ID, 0, ROUTINE, 0))
                return True
        return False

    def _call_done(self):
        return self._u32(CALL_DATA + 16) != NOT_DONE

    # -- the frame ------------------------------------------------------------------------------------------------
    def tick(self) -> list[str]:
        self.messages = []
        if not self.names.get(self.species):  # the game's tables are only in RAM once it has started
            self.names, self.move_names = read_names(self.read)
        in_battle = self._s16(GAME_STATE) != 0
        placing = self._step is not None
        if not placing and self._slot is not None and (self._entity(self._slot) is None
                                                       or self._i32(self._entity(self._slot)) != self.species):
            self._slot = None  # the map changed
        if in_battle:
            self._tick_battle()
        else:
            self._tick_field()
        messages, self.messages = self.messages, []
        return messages

    def _script_contact(self):
        """A battle-on-contact Digimon's script has just begun: the entity it belongs to, or None."""
        if struct.unpack("<i", self.read(ram_offset(IS_SCRIPT_PAUSED), 4))[0] != 0:
            return None
        talked = struct.unpack("<b", self.read(ram_offset(TALKED_TO_ENTITY), 1))[0]
        if not FIRST_NPC <= talked <= LAST_NPC or self._entity(talked) is None or talked == self._slot:
            return None
        if self.read(ram_offset(NPC_ENTITIES + (talked - FIRST_NPC) * NPC_SIZE + AUTOTALK), 1)[0] != 1:
            return None
        return talked

    def _tick_field(self):
        if self._state in ("battle", "ending", "left", "solo"):
            self._leave_battle()
        talked = self._script_contact()
        if self._after_battle:
            if struct.unpack("<i", self.read(ram_offset(IS_SCRIPT_PAUSED), 4))[0] == 0:  # 0 while a script runs
                return
            self._after_battle = False
        if self._step == "model":
            if self._call_done():
                self._step = "place"
                slot = self._free_slot()
                if slot is None:
                    self._no_room("no free Digimon slot on this map")
                    return
                self._slot = slot
                self._write_entry(slot, self._source)
                self._call(SCRIPT_SET_DIGIMON, self.species, slot - FIRST_NPC, 0)
            return
        if self._step == "place":
            if self._call_done():
                self._step = None
                if self._u32(CALL_DATA + 16) != 1 or self._entity(self._slot) is None:
                    self._no_room("the game would not place it")
                    return
                self._apply_stats(self._entity(self._slot))
                self._hide()
            return
        if talked is None:
            if self._state == "joining":
                self._hide()
                self._restore_chances()
                self._state = "field"
            return
        if self._slot is None:
            if self._state == "no room":
                return
            if self._free_slot() is None or sum(self.read(ram_offset(NPC_MODEL_TAKEN), 20)[::4]) >= NPC_MODEL_SLOTS:
                self._no_room("no room for its model on this map")
                return
            self._source = talked
            self._step = "model"
            self._call(LOAD_NPC_MODEL, self.species)
            return
        self._state = "joining"
        self._show_beside_partner()
        self._hold_join_chance()

    def _no_room(self, why):
        self.messages.append(f"Player 2's {self.names.get(self.species, 'Digimon')} can't join: {why}")
        self._step = None
        self._slot = None
        self._state = "no room"

    def _write_entry(self, slot, source):
        entry = bytearray(self.read(ram_offset(self._table_entry(source)), MAP_DIGIMON_SIZE))
        struct.pack_into("<hH", entry, 0, self.species, ALLY_MARK)
        entry[FOLLOW_MODE] = 0  # ignores the Tamer
        x, y, z = self._position(self._entity(PARTNER_SLOT))
        struct.pack_into("<3h", entry, POST, x - 120, y, z + 60)
        self.write(ram_offset(self._table_entry(slot)), bytes(entry))

    def _apply_stats(self, entity):
        stats = stats_for(self.player.level)
        self.write(ram_offset(entity + OFFENSE), struct.pack("<4h", stats["offense"], stats["defense"], stats["speed"], stats["brains"]))
        moves = species_moves(self.read, self.species)
        self.write(ram_offset(entity + MOVE_WEIGHTS), bytes(25 if move != 0xFF else 0 for move in moves))
        self.write(ram_offset(entity + MOVES), bytes(moves))
        self.write(ram_offset(entity + MAX_HP), struct.pack("<4h", stats["hp"], stats["mp"], stats["hp"], stats["mp"]))

    def _hide(self):
        if self._slot is not None and self._entity(self._slot) is not None:
            self.write(ram_offset(self._entity(self._slot) + IS_ON_MAP), bytes([0, 0]))

    def _show_beside_partner(self):
        entity = self._entity(self._slot)
        x, y, z = self._position(self._entity(PARTNER_SLOT))
        self.write(ram_offset(self._location(entity)), struct.pack("<3i", x - 120, y, z + 60))
        self.write(ram_offset(entity + IS_ON_MAP), bytes([1, 1]))

    def _hold_join_chance(self):
        """handleBattleStart rolls against FLEE_CHANCES for everyone on screen; the ally's column always joins."""
        if self._chances is None:
            self._chances = self.read(ram_offset(FLEE_CHANCES), 12)
            self.write(ram_offset(FLEE_CHANCES), bytes([100]) * 12)

    def _restore_chances(self):
        if self._chances is not None:
            self.write(ram_offset(FLEE_CHANCES), self._chances)
            self._chances = None

    # -- the battle -----------------------------------------------------------------------------------------------
    def _fighter(self, index):
        return self._u32(COMBAT_DATA_PTR) + index * FIGHTER_SIZE

    def _ids(self):
        return list(self.read(ram_offset(self._u32(COMBAT_DATA_PTR) + ENTITY_IDS), 4))

    def _fighter_flags(self, index):
        return struct.unpack("<H", self.read(ram_offset(self._fighter(index) + FLAGS), 2))[0]

    def _tick_battle(self):
        self._restore_chances()
        count = self._s16(ENEMY_COUNT)
        combat = self._u32(COMBAT_DATA_PTR)
        if not 1 <= count <= 3 or not 0x80000000 <= combat < 0x80200000:
            return
        if self._state not in ("battle", "ending", "left", "solo"):
            ids = self._ids()[:count + 1]
            if self._slot is None or self._slot not in ids:
                if self._state == "joining":
                    self.messages.append("No room for player 2's Digimon in this fight")
                    self._hide()
                # Player 2 still gains what this fight pays, as after loading a save made once the ally had left it.
                self._state = "solo"
                self._money_before = self._i32(MONEY)
                return
            self._ally_index = ids.index(self._slot)
            if self._ally_index != count:  # the ending relies on it being last
                self.messages.append("Player 2's Digimon joined out of order; it sits this one out")
                self._state = "solo"
                return
            self._state = "battle"
            self.excluded_slot = self._slot
            self._frames = 0
            self._targets = {}
            self._money_before = self._i32(MONEY)
            self._presses.clear()
            self._commands = available_commands(stats_for(self.player.level)["brains"])
            self._hovered = len(self._commands) - 1  # Your Call, as the partner starts
            self._current = YOUR_CALL
            self._target = 0
            self._finisher_armed = False
        if self._state == "solo":
            return
        if self._state == "battle":
            self._frames += 1
            self._run_sides(count)
        elif self._state == "left":
            self._presses.clear()

    def _alive(self, index):
        entity = self._entity(self._ids()[index])
        return entity is not None and self._s16(entity + HP) > 0 and not self._fighter_flags(index) & FLAG_DEAD

    def _nearest(self, entity, slots):
        x, _, z = self._position(entity)
        best, best_distance = None, FAR
        for index in slots:
            other = self._entity(self._ids()[index])
            ox, _, oz = self._position(other)
            distance = (ox - x) ** 2 + (oz - z) ** 2
            if distance < best_distance:
                best, best_distance = index, distance
        return best

    def _run_sides(self, count):
        ally = self._ally_index
        foes = [index for index in range(1, count + 1) if index != ally and self._alive(index)]
        partner_entity = self._entity(PARTNER_SLOT)
        current_command = self.read(ram_offset(self._u32(COMBAT_DATA_PTR) + 1440 + 176), 1)[0]
        if not foes or self._s16(partner_entity + HP) <= 0 or current_command == 1 or self._current == RUN:
            # Won, lost, fled or player 2 ran: the ally leaves the battle, so the game goes on or ends it for the real
            # enemies alone. Running leaves the partner fighting and player 2 still gains what it wins.
            ran = bool(foes) and self._current == RUN and self._s16(partner_entity + HP) > 0 and current_command != 1
            self._put16(ENEMY_COUNT, count - 1)
            self._state = "left" if ran else "ending"
            self.panel = None
            self.excluded_slot = self._slot
            self.write(ram_offset(self._entity(self._slot) + IS_ON_MAP), bytes([0, 0]))
            if ran:
                self.messages.append("Player 2 ran from the fight")
            return
        ally_entity = self._entity(self._slot)
        ally_alive = self._alive(ally)
        if ally_alive:
            self._order_ally(ally, ally_entity, foes)
        else:
            self._presses.clear()
        if self._fighter_flags(0) & FLAG_DEAD == 0 and self.read(ram_offset(self._fighter(0) + TARGET), 1)[0] == ally:
            self._put8(self._fighter(0) + TARGET, foes[0])
        if self._frames % RETARGET_FRAMES == 1:
            self._targets = {}
        for foe in foes:
            flags = self._fighter_flags(foe)
            if foe not in self._targets:
                others = [0, ally] if ally_alive else [0]
                self._targets[foe] = self._nearest(self._entity(self._ids()[foe]), others)
            if self._targets[foe] == ally and not ally_alive:
                self._targets[foe] = 0
            self.write(ram_offset(self._fighter(foe) + FLAGS), struct.pack("<H", flags | FLAG_KEEP_TARGET))
            self._put8(self._fighter(foe) + TARGET, self._targets[foe])
        self._update_panel(ally_entity, foes, not ally_alive)

    def _order_ally(self, ally, entity, foes):
        fighter = self._fighter(ally)
        flags = self._fighter_flags(ally)
        goal = self._s16(fighter + FINISHER_GOAL)
        ready = goal > 0 and self._s16(fighter + FINISHER_PROGRESS) >= goal - 1
        self._take_presses(len(foes), ready)
        # The AI fires a finisher as soon as the bar is full; here it waits for player 2's button, as the partner's does.
        if self._finisher_armed:
            if not ready:
                self._finisher_armed = False  # the AI took it and reset the bar
            else:
                self._put16(fighter + FINISHER_PROGRESS, goal)
        elif ready:
            self._put16(fighter + FINISHER_PROGRESS, goal - 1)
        self._put8(fighter + TARGET, foes[self._target % len(foes)])
        new = flags | FLAG_KEEP_TARGET
        command = self._current
        moves = self.read(ram_offset(entity + MOVES), 4)
        if command == DEFENSIVE:  # stands by and attacks nothing, like the partner on this command
            self._put8(entity + CHARGE_MODE, 2)
            if not flags & FLAG_ATTACKING:
                new |= FLAG_CHARGING
                self._put16(fighter + COOLDOWN, 0x50)
            self._was_holding = True
        else:
            if self._was_holding:
                self._was_holding = False
                self._put16(fighter + COOLDOWN, 1)
            self.write(ram_offset(entity + MOVE_WEIGHTS), bytes(self._weights(command, entity, moves)))
            if command == ATTACK:
                self._put8(entity + CHARGE_MODE, 0)
            elif command in (MODERATE, DISTANCE):
                self._put8(entity + CHARGE_MODE, 2)
        self.write(ram_offset(fighter + FLAGS), struct.pack("<H", new))

    def _weights(self, command, entity, moves):
        """What the partner's commands favour: Attack the strongest, Moderate the cheapest, Distance the longest reach."""
        table = self.read(ram_offset(DIGIMON_DATA + self.species * DIGIMON_SIZE + DIGIMON_MOVES), 16)
        usable = {}
        mp = self._s16(entity + MP)
        for slot in range(4):
            if moves[slot] == 0xFF:
                continue
            tech = table[moves[slot] - 0x2E]
            if tech in FINISHER_TECHS or tech == 0xFF:
                continue
            record = self.read(ram_offset(MOVE_DATA + tech * MOVE_SIZE), MOVE_SIZE)
            power, cost, reach = struct.unpack_from("<h", record, MOVE_POWER)[0], record[MOVE_MP] * 3, record[MOVE_RANGE]
            if cost <= mp:
                usable[slot] = (power, cost, reach)
        chosen = None
        if usable and command == ATTACK:
            chosen = max(usable, key=lambda slot: usable[slot][0])
        elif usable and command == MODERATE:
            chosen = min(usable, key=lambda slot: usable[slot][1])
        elif usable and command == DISTANCE:
            chosen = max(usable, key=lambda slot: usable[slot][2])
        # A finisher is only ever player 2's button (the AI weighs the last slot like any other unless it is left out).
        return [0 if slot not in usable else 100 if slot == chosen else 1 if chosen is not None else 25 for slot in range(4)]

    def _update_panel(self, entity, foes, down):
        fighter = self._fighter(self._ally_index)
        target = self._entity(self._ids()[foes[self._target % len(foes)]])
        goal = self._s16(fighter + FINISHER_GOAL)
        ready = goal > 0 and self._s16(fighter + FINISHER_PROGRESS) >= goal - 1 and not self._finisher_armed
        self.panel = AllyPanel(self.names.get(self.species, "Digimon"), self.player.summary(), max(self._s16(entity + HP), 0),
                               self._s16(entity + MAX_HP), max(self._s16(entity + MP), 0), self._s16(entity + MAX_MP),
                               self._commands[1:], self._hovered - 1, self._current, self._name(target), ready, down, False)

    def gain_exp(self, amount: int) -> list[str]:
        """The EXP the main player just gained goes to player 2 as well."""
        before = self.player.level
        self.player.exp = min(self.player.exp + amount, total_exp(LEVEL_MAX))
        after = self.player.level
        messages = [f"Player 2: +{amount} EXP — {self.player.summary()}"]
        if after > before:
            messages.append(f"Player 2 level up! Lv {after}")
            entity = self._entity(self._slot) if self._slot is not None else None
            if entity is not None and self._s16(GAME_STATE) == 0:
                self._apply_stats(entity)
        self._save()
        return messages

    def _save(self):
        if self.path is not None:
            try:
                self.player.save(self.path)
            except OSError as exc:
                self.messages.append(f"Player 2's progress could not be written: {exc}")

    def _leave_battle(self):
        if self._money_before is not None:
            gained = self._i32(MONEY) - self._money_before
            self._money_before = None
            if gained > 0:
                self.player.bits += gained
                self.messages.append(f"Player 2: +{gained} Bits — {self.player.bits} Bits")
                self._save()
        self._hide()
        if self._slot is not None and self._entity(self._slot) is not None:
            self._apply_stats(self._entity(self._slot))
        self._after_battle = True  # the script that started this fight is still finishing
        self.panel = None
        self.excluded_slot = None
        self._state = "field"
