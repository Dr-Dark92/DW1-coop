"""The other player in this game's world (USA, Beetle PSX): their Tamer and Digimon appear when both games are on the same map.

Each game publishes where its partner is. The other game shows that Digimon as an NPC it places through the game's own
functions (loadNPCModel, scriptSetDigimon, startAnimation), run once from the object list as in coop.py. stopAnim in its
MapDigimonEntity keeps the NPC out of the map AI and contact checks, so only this class moves it, and 0 HP keeps it from
being solid. It hides during this game's scripts and battles, so it joins neither, and before a save.

Many maps list a Digimon in all eight rows of the table for their scripts to place later, so a row with no entity on the
map is borrowed. The map's row goes back whenever the visitor hides, and scriptSetDigimon replaces a hidden visitor if a
script wants that place. A script starting takes one frame to notice, so one placing that row at once would find it taken.

Addresses are from jype0/dw_decomp aaf69a4 and apply only to SLUS_010.32.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

from .coop import (AUTOTALK, ENTITY_TABLE, FIRST_NPC, FOLLOW_MODE, GAME_STATE, IS_ON_MAP, IS_SCRIPT_PAUSED, LAST_NPC, LOAD_NPC_MODEL,
                   LOCATION, MAP_DIGIMON_SIZE, MAP_DIGIMON_TABLE, NOT_DONE, NPC_ENTITIES, NPC_MODEL_SLOTS, NPC_MODEL_TAKEN, NPC_SIZE,
                   OBJECT_COUNT, PARTNER_SLOT, POSITION, POST, SCRIPT_SET_DIGIMON, WORLD_OBJECTS, routine, work_area)
from .live import CURRENT_SCREEN, ram_offset, ram_end

START_ANIMATION = 0x800C1A04
HOUR, YEAR, CURRENT_FRAME = 0x80134EBC, 0x80134F02, 0x80134F08  # HOUR is followed by MINUTE, YEAR by DAY
MALLOC, FREE, LOOKUP_FILE_SIZE = 0x80090F84, 0x80091034, 0x800A32CC
DIGIMON_FILE_NAMES, MODEL_FOLDER, MODEL_SUFFIX = 0x8011D19C, 0x8011D484, 0x801340E4  # the path loadMMD builds
# loadMMD doesn't check its allocation: without room it reads the file over low memory and the game freezes. So the model
# plus this much (its texture buffer, bones, and something for the map's own needs) must fit before it is called.
HEADROOM = 0x8000
SETTLE_TICKS = 120
LOADED_DIGIMON_MODELS, LOADED_MODEL_COUNT = 0x801BE7EC, 8  # the map change unloads what is listed here
TASK_ID = 0x7A11  # plus the actor
PARTNER, TAMER = 0, 1  # the actors one game shows of the other's
ACTOR_SLOTS = {PARTNER: PARTNER_SLOT, TAMER: 0}  # their places in ENTITY_TABLE
VISITOR_MARK = 0x7A7B  # MapDigimonEntity.padding0 of the entry placing the visitor, so a loaded state finds it again
ROTATION, ANIM_ID, CURRENT_HP = 112, 46, 76  # PositionData.rotation, Entity.anim.animId, DigimonEntity.stats.current
AI_SECTIONS, STOP_ANIM = 132, 193  # in MapDigimonEntity


def routine_address(actor: int = PARTNER) -> int:  # after coop.py's routine in the same area
    return work_area() + 0x100 + actor * 0x200


def call_data(actor: int = PARTNER) -> int:  # function, a0, a1, a2, result
    return work_area() + 0x220 + actor * 0x200


def path_buffer(actor: int = PARTNER) -> int:
    return work_area() + 0x240 + actor * 0x200


def read_partner_stats(read) -> dict | None:
    """What the partner would fight with beside the other player: its own stats, as trained."""
    entity = struct.unpack("<I", read(ram_offset(ENTITY_TABLE + PARTNER_SLOT * 4), 4))[0]
    if not 0x80000000 <= entity < ram_end() - NPC_SIZE:
        return None
    offense, defense, speed, brains = struct.unpack("<4h", read(ram_offset(entity + 56), 8))
    hp, mp = struct.unpack("<2h", read(ram_offset(entity + 72), 4))
    return {"offense": offense, "defense": defense, "speed": speed, "brains": brains, "hp": hp, "mp": mp}


@dataclass(frozen=True)
class NpcState:
    """One map Digimon, as one game has it. species is -1 where there is nothing to share."""
    species: int = -1
    on_map: bool = False
    x: int = 0
    y: int = 0
    z: int = 0
    rotation: int = 0


@dataclass(frozen=True)
class WorldState:
    """What one game's map looks like, for the other to match."""
    map: int
    hour: int
    minute: int
    year: int
    day: int
    frame: int
    npcs: tuple[NpcState, ...]


def on_the_field(read) -> bool:
    return (struct.unpack("<h", read(ram_offset(GAME_STATE), 2))[0] == 0
            and struct.unpack("<i", read(ram_offset(IS_SCRIPT_PAUSED), 4))[0] != 0)  # 0 while a script runs


def _npc_entity(read, slot):
    pointer = struct.unpack("<I", read(ram_offset(ENTITY_TABLE + slot * 4), 4))[0]
    return pointer if 0x80000000 <= pointer < ram_end() - NPC_SIZE else None


def read_world(read, skip=()) -> WorldState:
    """This game's map Digimon, leaving out the visitors and ally in `skip`."""
    npcs = []
    for slot in range(FIRST_NPC, LAST_NPC + 1):
        entity = _npc_entity(read, slot)
        position = struct.unpack("<I", read(ram_offset(entity + POSITION), 4))[0] if entity else 0
        if slot in skip or entity is None or not 0x80000000 <= position < ram_end() - 136:
            npcs.append(NpcState())
            continue
        x, y, z = struct.unpack("<3i", read(ram_offset(position + LOCATION), 12))
        npcs.append(NpcState(struct.unpack("<i", read(ram_offset(entity), 4))[0], read(ram_offset(entity + IS_ON_MAP), 1)[0] != 0,
                             x, y, z, struct.unpack("<h", read(ram_offset(position + ROTATION + 2), 2))[0]))
    hour, minute = struct.unpack("<2h", read(ram_offset(HOUR), 4))
    year, day = struct.unpack("<2h", read(ram_offset(YEAR), 4))
    return WorldState(read(ram_offset(CURRENT_SCREEN), 1)[0], hour, minute, year, day,
                      struct.unpack("<H", read(ram_offset(CURRENT_FRAME), 2))[0], tuple(npcs))


def match_world(read, write, theirs: WorldState, leader: bool, skip=()) -> None:
    """Make this game's map agree with the other's: an enemy gone there is gone here, whichever game beat it. Otherwise
    one game leads: the other takes its clock, and its Digimon stand where the leader's do."""
    if not leader:
        write(ram_offset(HOUR), struct.pack("<2h", theirs.hour, theirs.minute))
        write(ram_offset(YEAR), struct.pack("<2h", theirs.year, theirs.day))
        write(ram_offset(CURRENT_FRAME), struct.pack("<H", theirs.frame & 0xFFFF))
    if theirs.map != read(ram_offset(CURRENT_SCREEN), 1)[0]:
        return
    for slot, npc in zip(range(FIRST_NPC, LAST_NPC + 1), theirs.npcs):
        entity = _npc_entity(read, slot)
        if (slot in skip or entity is None or npc.species == -1
                or struct.unpack("<i", read(ram_offset(entity), 4))[0] != npc.species
                or not read(ram_offset(entity + IS_ON_MAP), 1)[0]):
            continue
        if not npc.on_map:
            if read(ram_offset(NPC_ENTITIES + (slot - FIRST_NPC) * NPC_SIZE + AUTOTALK), 1)[0] == 1:  # only enemies: scripts place the rest
                write(ram_offset(entity + IS_ON_MAP), bytes([0, 0]))
        elif not leader:
            position = struct.unpack("<I", read(ram_offset(entity + POSITION), 4))[0]
            write(ram_offset(position + LOCATION), struct.pack("<3i", npc.x, npc.y, npc.z))
            write(ram_offset(position + ROTATION + 2), struct.pack("<h", npc.rotation))


@dataclass(frozen=True)
class WorldRecord:
    """What one game tells the other about its partner."""
    map: int
    x: int
    y: int
    z: int
    rotation: int
    species: int
    animation: int
    visible: bool


def read_record(read, actor: int = PARTNER) -> WorldRecord | None:
    """This game's partner or Tamer, or None before it has one."""
    entity = struct.unpack("<I", read(ram_offset(ENTITY_TABLE + ACTOR_SLOTS[actor] * 4), 4))[0]
    if not 0x80000000 <= entity < ram_end() - NPC_SIZE:
        return None
    species, position = struct.unpack("<iI", read(ram_offset(entity), 8))
    if not (actor == TAMER or species > 0) or not 0 <= species < 180 or not 0x80000000 <= position < ram_end() - 136:
        return None
    x, y, z = struct.unpack("<3i", read(ram_offset(position + LOCATION), 12))
    rotation = struct.unpack("<h", read(ram_offset(position + ROTATION + 2), 2))[0]
    field = struct.unpack("<h", read(ram_offset(GAME_STATE), 2))[0] == 0
    return WorldRecord(read(ram_offset(CURRENT_SCREEN), 1)[0], x, y, z, rotation, species,
                       read(ram_offset(entity + ANIM_ID), 1)[0], field and read(ram_offset(entity + IS_ON_MAP), 1)[0] != 0)


class Visitor:
    """Shows one of the other player's actors in this game. tick() runs between frames."""

    def __init__(self, read, write, actor: int = PARTNER):
        self.read, self.write = read, write
        self.actor = actor
        self.messages: list[str] = []
        self.reset()

    def reset(self):
        """After a load: the visitor, if that state has one, is found again by its mark."""
        self._step = None
        self._step_map = None
        self._species = None
        self._slot = None
        self._shown = False
        self._animation = None
        self._no_room_map = None
        self._map_row = None  # the map's own table row, while the visitor is using it
        self._map = None
        self._wait = 0

    @property
    def _task(self) -> int:
        return TASK_ID + self.actor

    # -- memory --------------------------------------------------------------------------------------------------
    def _u32(self, address):
        return struct.unpack("<I", self.read(ram_offset(address), 4))[0]

    def _s16(self, address):
        return struct.unpack("<h", self.read(ram_offset(address), 2))[0]

    def _entity(self, slot):
        pointer = self._u32(ENTITY_TABLE + slot * 4)
        return pointer if 0x80000000 <= pointer < ram_end() - NPC_SIZE else None

    def _table_entry(self, slot):
        return MAP_DIGIMON_TABLE + (slot - FIRST_NPC) * MAP_DIGIMON_SIZE

    def _call(self, function, a0=0, a1=0, a2=0) -> bool:
        code = routine(call_data(self.actor), self._task)
        if self.read(ram_offset(routine_address(self.actor)), len(code)) != code:
            self.write(ram_offset(routine_address(self.actor)), code)
        self.write(ram_offset(call_data(self.actor)), struct.pack("<5I", function, a0, a1, a2, NOT_DONE))
        for index in range(OBJECT_COUNT):
            if self._s16(WORLD_OBJECTS + index * 12) == -1:
                self.write(ram_offset(WORLD_OBJECTS + index * 12), struct.pack("<hhII", self._task, 0, routine_address(self.actor), 0))
                return True
        self.write(ram_offset(call_data(self.actor) + 16), struct.pack("<I", 0))
        return False

    def _call_done(self) -> bool:
        """Finished, or lost: a map change clears the object list, and the call with it."""
        if self._u32(call_data(self.actor) + 16) != NOT_DONE:
            return True
        table = self.read(ram_offset(WORLD_OBJECTS), OBJECT_COUNT * 12)
        return not any(struct.unpack_from("<h", table, index * 12)[0] == self._task for index in range(OBJECT_COUNT))

    def _find(self, species):
        for slot in range(FIRST_NPC, LAST_NPC + 1):
            entity = self._entity(slot)
            if entity is None or struct.unpack("<i", self.read(ram_offset(entity), 4))[0] != species:
                continue
            row = self._table_entry(slot)
            if self._s16(row + 2) == VISITOR_MARK or self._s16(row) != species:  # a hidden visitor has the map's row
                return slot
        return None

    def _free_slot(self):
        """The highest NPC slot the map doesn't use, or failing that the highest with no Digimon on the map yet."""
        empty = [slot for slot in range(LAST_NPC, FIRST_NPC - 1, -1) if self._entity(slot) is None]
        unused = [slot for slot in empty if self._s16(self._table_entry(slot)) in (-1, 0xFF)]
        return (unused or empty or [None])[0]

    def _listed_models(self):
        return struct.unpack(f"<{LOADED_MODEL_COUNT}i", self.read(ram_offset(LOADED_DIGIMON_MODELS), LOADED_MODEL_COUNT * 4))

    def _list_model(self, species) -> None:
        models = self._listed_models()
        if species not in models and -1 in models:
            self.write(ram_offset(LOADED_DIGIMON_MODELS + models.index(-1) * 4), struct.pack("<i", species))

    # -- the frame ------------------------------------------------------------------------------------------------
    def hide(self):
        """Off the map (in this game) until the next tick that wants it shown."""
        if self._slot is not None and self._entity(self._slot) is not None:
            self.write(ram_offset(self._entity(self._slot) + IS_ON_MAP), bytes([0, 0]))
        if self._slot is not None and self._map_row is not None:
            self.write(ram_offset(self._table_entry(self._slot)), self._map_row)
        self._map_row = None
        self._shown = False

    def tick(self, other: WorldRecord | None, busy: bool = False) -> list[str]:
        """busy: the other actor is being placed; one at a time, so they don't choose the same place."""
        self.messages = []
        here = self.read(ram_offset(CURRENT_SCREEN), 1)[0]
        if here != self._map:  # let the new map place its own Digimon first
            self._map = here
            self._wait = SETTLE_TICKS
            self._slot = self._map_row = None  # the table and entities are the new map's now
            self._shown = False
        if self._step is not None:
            self._continue_placing(here)
            return self._take_messages()
        if self._slot is not None and (self._entity(self._slot) is None
                                       or struct.unpack("<i", self.read(ram_offset(self._entity(self._slot)), 4))[0] != self._species):
            self._slot = self._map_row = None  # a script took its place
            self._shown = False
        field = (self._s16(GAME_STATE) == 0 and self._entity(PARTNER_SLOT) is not None
                 and struct.unpack("<i", self.read(ram_offset(IS_SCRIPT_PAUSED), 4))[0] != 0)  # 0 while a script runs
        if other is None or not other.visible or other.map != here or not field:
            self.hide()
            return self._take_messages()
        if other.species != self._species:  # scriptSetDigimon replaces the old form in its slot
            self.hide()
            self._species, self._animation = other.species, None
            self._no_room_map = None
            found = self._find(other.species)
            if found is None:
                self._start_placing(other, here, busy)
                return self._take_messages()
            self._slot = found
        if self._slot is None:
            self._slot = self._find(other.species)
            if self._slot is None:
                self._start_placing(other, here, busy)
                return self._take_messages()
        self._follow(other)
        return self._take_messages()

    def _take_messages(self):
        messages, self.messages = self.messages, []
        return messages

    def _no_room(self, why, here):
        if self._no_room_map != here:
            self.messages.append(f"The other player's {'Tamer' if self.actor == TAMER else 'Digimon'} can't appear here: {why}")
        self._no_room_map = here
        self._wait = SETTLE_TICKS  # something may leave: look again later
        self._step = None
        self._slot = None

    def _start_placing(self, other, here, busy=False):
        if busy:
            return
        if self._wait > 0:
            self._wait -= 1
            return
        if self._slot is None and self._free_slot() is None:
            self._no_room("no free Digimon slot on this map", here)
            return
        self._step_map = here
        if other.species in self._listed_models():
            if self._call_done():
                self._step = "place"
                self._place(other.species, here)
        elif sum(self.read(ram_offset(NPC_MODEL_TAKEN), 20)[::4]) >= NPC_MODEL_SLOTS:
            self._no_room("no room for its model on this map", here)
        elif self._call_done():
            self.write(ram_offset(path_buffer(self.actor)), self._model_path(other.species))
            if self._call(LOOKUP_FILE_SIZE, path_buffer(self.actor)):
                self._step = "size"

    def _model_path(self, species) -> bytes:
        """The model file's path, as loadMMD builds it."""
        name = self._text(self._u32(DIGIMON_FILE_NAMES + species * 4))
        path = bytearray(self._text(MODEL_FOLDER) + name + self._text(MODEL_SUFFIX))
        path[9] = species // 30 + ord("0")
        return bytes(path) + b"\0"

    def _text(self, address) -> bytes:
        return self.read(ram_offset(address), 32).split(b"\0")[0]

    def _continue_placing(self, here):
        if not self._call_done():
            return
        result = self._u32(call_data(self.actor) + 16)
        if here != self._step_map or result == NOT_DONE:  # the map changed and took the model with it: start again
            self._step = None
            return
        if self._step == "size":
            if not 0 < result < 0x100000 or not self._call(MALLOC, ((result + 0x7FF) & ~0x7FF) + HEADROOM):
                self._no_room("its model can't be read", here)
            else:
                self._step = "room"
        elif self._step == "room":
            if not result:
                self._no_room("not enough game memory on this map", here)
            elif self._call(FREE, result):
                self._step = "free"
            else:
                self._step = None
        elif self._step == "free":
            if self._call(LOAD_NPC_MODEL, self._species):
                self._step = "model"
            else:
                self._step = None
        elif self._step == "model":
            self._list_model(self._species)
            self._step = "place"
            self._place(self._species, here)
        else:
            self._step = None
            placed = self._u32(call_data(self.actor) + 16) == 1 and self._entity(self._slot) is not None
            self.hide()
            if not placed:
                self._no_room("the game would not place it", here)

    def _place(self, species, here):
        slot = self._slot if self._slot is not None else self._free_slot()
        if slot is None:
            self._no_room("no free Digimon slot on this map", here)
            return
        self._slot = slot
        self._animation = None
        self._borrow_row(species)
        if not self._call(SCRIPT_SET_DIGIMON, species, slot - FIRST_NPC, 0):
            self.hide()
            self._step = None
            self._slot = None

    def _borrow_row(self, species):
        slot = self._slot
        if self._map_row is None:
            self._map_row = self.read(ram_offset(self._table_entry(slot)), MAP_DIGIMON_SIZE)
        entry = bytearray(MAP_DIGIMON_SIZE)
        struct.pack_into("<hH", entry, 0, species, VISITOR_MARK)
        struct.pack_into("<8h", entry, AI_SECTIONS, *[-1] * 8)
        x, y, z = struct.unpack("<3i", self.read(ram_offset(self._location(PARTNER_SLOT)), 12))
        struct.pack_into("<3h", entry, POST, x, y, z)
        entry[FOLLOW_MODE] = 0
        entry[STOP_ANIM] = 1
        self.write(ram_offset(self._table_entry(slot)), bytes(entry))

    def _location(self, slot):
        return self._u32(self._entity(slot) + POSITION) + LOCATION

    def _follow(self, other: WorldRecord):
        entity = self._entity(self._slot)
        position = self._u32(entity + POSITION)
        self.write(ram_offset(position + LOCATION), struct.pack("<3i", other.x, other.y, other.z))
        self.write(ram_offset(position + ROTATION + 2), struct.pack("<h", other.rotation))
        if not self._shown:
            self._borrow_row(other.species)
            self.write(ram_offset(entity + CURRENT_HP), struct.pack("<h", 0))  # entityCheckCollision ignores 0 HP, so it isn't solid
            self.write(ram_offset(entity + IS_ON_MAP), bytes([1, 1]))
            self._shown = True
        if other.animation != self._animation and self._call_done():
            if self._call(START_ANIMATION, entity, other.animation):
                self._animation = other.animation
