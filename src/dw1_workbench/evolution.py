"""P6 manual evolution and the Digi-Bank menu, USA only; source layouts: dw_decomp aaf69a4.

Native selectors are reversibly disabled; the native evolution sequence performs
model loading, stat bonuses, care-field changes and map restoration. Making a banked
partner active uses the same sequence, then writes the stored record (bank.py).
"""
from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from pathlib import Path

from . import bank
from .camera import sync_instruction_cache
from .libretro import BUTTON
from .digidex import entry_rows
from .live import ram_offset
from .progression import (BANK_SLOTS, Progress, DIGIMON_DATA, DIGIMON_LEVEL, DIGIMON_PARA_SIZE, GAME_STATE, PARTNER_ENTITY,
                          PARTNER_STATS, UI_BOX_DATA)
from .quicksave import _atomic_write

CHAMPION_LEVEL = 21
# Level needed to leave each stage (DigimonPara.level): Fresh, In-Training, Rookie (Champion 21 is a test value).
STAGE_LEVELS = {1: 6, 2: 12, 3: CHAMPION_LEVEL}
FRESH_TARGETS = {1: 2, 15: 16, 29: 30, 43: 44}  # hardcoded in native getFreshEvolutionTarget
EVOLUTION_PATHS = 0x8012B66C  # 62 EvolutionPath {from[5], to[6]}
EVOLUTION_TARGET = 0x80134E50
TAMER_STATE, PARTNER_STATE = 0x80134C91, 0x80134C5B
TRIANGLE_MENU_STATE = 0x80134D30
MENU_OPTIONS, HAS_FISHING_ROD = 0x80123E88, 0x80134D2C
GAME_MENU_RENDER = 0x800B80C0
IS_IN_MENU, FADE_PROTECTION = 0x80134EF0, 0x80134CB8
# Original words verified read-only against the supplied SLUS_010.32.
# tickConditions calls isInDaytimeTransition and returns when v0 == 0.
# Force that result only at this call site, bypassing all natural/Sukamon
# evolution checks while preserving its preceding care-condition updates.
NO_TARGET = struct.pack("<II", 0x03E00008, 0x2402FFFF)  # jr ra; v0=-1
PATCHES = {
    0x800A90C8: (bytes.fromhex("865d030c00000000"), struct.pack("<II", 0x24020000, 0)),
    # handleSpecialEvolutions: prevent random praise/sleep/condition branches.
    0x800E3234: (bytes.fromhex("c0ffbd273400bfaf"), NO_TARGET),
}


def bit(name):
    return 1 << BUTTON[name]


@dataclass(frozen=True)
class EvolutionChoice:
    kind: int
    name: str


@dataclass(frozen=True)
class EvolutionMenu:
    title: str
    rows: tuple[str, ...]
    selected: int
    footer: str
    numbered: bool = True
    offset: int = 0
    layout: str = "list"
    relation: object = None
    icons: tuple = ()  # list rows: the Digimon whose avatar each row shows (None: locked, no avatar)
    avatar: int | None = None  # a Digi-Dex page's Digimon
    styles: tuple = ()  # Digi-Dex page rows: digidex.entry_rows styles
    skills: tuple = ()  # Digi-Dex page rows: technique record (digidex.read_moves) or None


class EvolutionHistory:
    """Forms the partner has been while the player ran (per disc). Like the Digi-Dex this is knowledge,
    so it survives quick-save rewinds. The evolution chart shows only these and the current partner's next forms."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.forms: set[int] = set()
        self.dirty = False
        if self.path.exists():
            record = json.loads(self.path.read_bytes())
            forms = record.get("forms") if isinstance(record, dict) and record.get("format") == 1 else None
            if not isinstance(forms, list) or any(type(k) is not int or not 0 < k < 180 for k in forms):
                raise ValueError("Unsupported evolution history record")
            self.forms = set(forms)

    def observe(self, read) -> bool:
        """Record the current partner; ignores title-screen/unset partner RAM."""
        kind = struct.unpack("<i", read(ram_offset(PARTNER_ENTITY), 4))[0]
        if (not 0 < kind < 180 or struct.unpack("<h", read(ram_offset(PARTNER_STATS + 16), 2))[0] <= 0
                or not 1 <= read(ram_offset(DIGIMON_DATA + kind * DIGIMON_PARA_SIZE + DIGIMON_LEVEL), 1)[0] <= 5
                or kind in self.forms):
            return False
        self.forms.add(kind)
        self.dirty = True
        return True

    def save(self):
        if self.dirty:
            _atomic_write(self.path, json.dumps({"format": 1, "forms": sorted(self.forms)}, indent=2).encode())
            self.dirty = False


class EvolutionController:
    def __init__(self, core, progress, dex=None, map_names=None, history: EvolutionHistory | None = None):
        self.core, self.progress = core, progress
        self.dex, self.map_names = dex, map_names or {}
        self.history = history  # None: the chart shows every linked form (no record available)
        self.installed = False
        self.notices: list[str] = []  # messages for the player, taken by the worker
        self.events: list[str] = []  # "bank": EXP or the bank changed; save them and re-read the partner
        self.reset()

    def reset(self):
        """Forget menu state, and a swap in progress (after loading a state, the slot's own record applies)."""
        self._swap = None
        self.previous_buttons = 0
        self.selection = 0
        self.choice_index = 0
        self.choosing = False
        self.confirming = False
        self.menu = None
        self._menu_open = False
        self._handoff = False
        self._handoff_button = 0
        self._dex_open = self._dex_detail = False
        self._dex_index = self._dex_page = 0
        self._relation = None
        self._relation_kind = None
        self._relation_focus = None
        self._relation_side = "next"
        self._bank_open = False
        self._bank_view = "list"  # list, status, eggs
        self._bank_index = self._bank_page = self._egg_index = 0
        self._bank_confirm = False

    def read(self, address, size):
        return self.core.read_memory(ram_offset(address), size)

    def put(self, address, fmt, value):
        self.core.write_memory(ram_offset(address), struct.pack(fmt, value))

    def number(self, address, fmt="<h"):
        return struct.unpack(fmt, self.read(address, struct.calcsize(fmt)))[0]

    def install(self):
        observed = {address: self.read(address, 8) for address in PATCHES}
        if self.installed and all(data == PATCHES[address][1] for address, data in observed.items()):
            return True
        if any(data == bytes(8) for data in observed.values()):
            return False  # boot ROM, executable not loaded yet
        if any(data not in PATCHES[address] for address, data in observed.items()):
            raise ValueError("Manual evolution requires the original USA evolution code; no patches applied.")
        if "beetle psx" not in self.core.info.name.lower():
            raise ValueError("Manual evolution currently requires the Beetle PSX core (instruction-cache support).")
        try:
            for address in PATCHES:
                self.core.write_memory(ram_offset(address), PATCHES[address][1])
            sync_instruction_cache(self.core, self.words)
        except Exception:
            for address, data in observed.items():
                self.core.write_memory(ram_offset(address), data)
            sync_instruction_cache(self.core, self.words)
            raise
        self.installed = True
        return True

    @property
    def words(self):
        return [address + offset for address in PATCHES for offset in (0, 4)]

    def restore(self):
        changed = False
        for address, (original, patched) in PATCHES.items():
            if self.read(address, 8) == patched:
                self.core.write_memory(ram_offset(address), original)
                changed = True
        if changed:
            sync_instruction_cache(self.core, self.words)
        self.installed = False

    def stage(self):
        kind = self.number(PARTNER_ENTITY, "<i")
        return self.read(DIGIMON_DATA + kind * DIGIMON_PARA_SIZE + DIGIMON_LEVEL, 1)[0] if 0 < kind < 180 else 0

    def required_level(self):
        """Level the partner needs for its next stage; None when manual evolution has no next stage for it."""
        return STAGE_LEVELS.get(self.stage())

    def choices(self):
        kind = self.number(PARTNER_ENTITY, "<i")
        stage = self.stage()
        if not 1 <= kind <= 62 or stage not in STAGE_LEVELS:
            return ()
        links = (FRESH_TARGETS.get(kind, 0),) if stage == 1 else self.read(EVOLUTION_PATHS + (kind - 1) * 11 + 5, 6)
        result = []
        for target in dict.fromkeys(links):
            if not 0 < target < 66:
                continue
            record = self.read(DIGIMON_DATA + target * DIGIMON_PARA_SIZE, DIGIMON_PARA_SIZE)
            if record[DIGIMON_LEVEL] != stage + 1:
                continue
            name = record[:20].split(b"\0", 1)[0].decode("ascii", errors="replace")
            result.append(EvolutionChoice(target, name or f"Digimon {target}"))
        return tuple(result)

    def main_menu_open(self):
        box = self.read(UI_BOX_DATA, 36)
        return (self.number(GAME_STATE) == 0 and self.number(IS_IN_MENU, "<i") == 1
                and struct.unpack_from("<h", box, 18)[0] == 1
                and struct.unpack_from("<I", box, 32)[0] == GAME_MENU_RENDER
                and self.number(TRIANGLE_MENU_STATE, "<i") == -1)

    def idle(self):
        return (self.main_menu_open() and self.number(PARTNER_STATE, "<b") == 1
                and self.number(FADE_PROTECTION, "<b") == 0 and self._swap is None)

    def start_evolution(self, target):
        # Revalidate immediately before writing: selection cannot grant arbitrary forms.
        required = self.required_level()
        if (not self.idle() or required is None or self.progress.level < required
                or target not in [choice.kind for choice in self.choices()]):
            raise ValueError(f"Evolution is unavailable: choose a linked form at level {required or CHAMPION_LEVEL} "
                             "or higher while idle.")
        self._sequence(target)
        self.reset()

    def _sequence(self, target):
        # Same state setters used by tickConditions/handleSpecialEvolutions.
        # partnerTickEvolving removes the menu and invokes the EVL overlay itself.
        self.put(EVOLUTION_TARGET, "<h", target)
        self.put(IS_IN_MENU, "<i", 0)
        self.put(TAMER_STATE, "<b", 6)
        self.put(TAMER_STATE + 1, "<b", 0)
        self.put(PARTNER_STATE, "<b", 13)
        self.put(PARTNER_STATE + 1, "<b", 0)

    def start_swap(self, slot):
        """Make the stored partner in `slot` active; the active one takes its slot. The bank and EXP change now;
        the stored stats and care are written once the native sequence has loaded the form (_finish_swap)."""
        record = self.progress.bank[slot] if 0 <= slot < BANK_SLOTS else None
        if record is None or not self.idle():
            raise ValueError("The Digi-Bank swap is unavailable: open it from the menu while your partner is idle.")
        outgoing = bank.capture(self.read_ram, self.progress.exp)
        self._sequence(record["kind"])
        self.reset()
        self.progress.bank[slot] = outgoing
        self.progress.exp = record["exp"]
        self._swap = {"record": record, "started": False}
        self.notices.append(f"Digi-Bank: {bank.species(self.read_ram, record['kind'])[0]} is now your partner; "
                            f"{bank.species(self.read_ram, outgoing['kind'])[0]} is stored in slot {slot + 1}")
        self.events.append("bank")

    def _finish_swap(self):
        record = self._swap["record"]
        if self.number(PARTNER_STATE, "<b") != 1:
            self._swap["started"] = True
        elif (self._swap["started"] and self.number(PARTNER_ENTITY, "<i") == record["kind"]
              and self.number(GAME_STATE) == 0 and self.number(FADE_PROTECTION, "<b") == 0):
            bank.apply(self.read_ram, self.write_ram, record)
            self._swap = None
            self.events.append("bank")

    def hatch(self, kind):
        """Hatch a new egg into the first empty bank slot; returns the slot."""
        slot = next((i for i, record in enumerate(self.progress.bank) if record is None), None)
        if slot is None:
            raise ValueError("The Digi-Bank is full.")
        self.progress.bank[slot] = bank.egg(kind)
        self.notices.append(f"Digi-Bank: {bank.species(self.read_ram, kind)[0]} hatched and waits in slot {slot + 1}")
        self.events.append("bank")
        return slot

    def read_ram(self, offset, size):
        return self.core.read_memory(offset, size)

    def write_ram(self, offset, data):
        self.core.write_memory(offset, data)

    def relation_graph(self):
        if self._relation is not None:
            return self._relation
        table = self.read(DIGIMON_DATA, 180 * DIGIMON_PARA_SIZE)
        nodes = {}
        for kind in range(1, 66):
            record = table[kind * 52:(kind + 1) * 52]
            stage = record[29]
            if 1 <= stage <= 5:
                name = record[:20].split(b"\0", 1)[0].decode("ascii", errors="replace")
                nodes[kind] = (kind, name or f"Digimon {kind}", stage)
        paths = self.read(EVOLUTION_PATHS, 62 * 11)
        edges = set()
        for kind in range(1, 63):
            if kind not in nodes:
                continue
            for target in paths[(kind - 1) * 11 + 5:kind * 11]:
                if target in nodes and nodes[target][2] > nodes[kind][2]:
                    edges.add((kind, target))
        # Fresh targets are hardcoded in native getFreshEvolutionTarget.
        edges.update((a, b) for a, b in ((1, 2), (15, 16), (29, 30), (43, 44)) if a in nodes and b in nodes)
        current = self.number(PARTNER_ENTITY, "<i")
        hidden = ()
        if self.history is not None:
            # Only forms the partner has been are shown. The current partner's next forms stay
            # selectable (so it can still evolve) but are anonymous until reached.
            known = (self.history.forms | {current}) & set(nodes)
            upcoming = {b for a, b in edges if a == current} - known
            edges = {(a, b) for a, b in edges if a in known and b in known | upcoming}
            hidden = tuple(sorted(upcoming))
            nodes = {kind: (kind, "???", stage) if kind in upcoming else node
                     for kind, node in nodes.items() if kind in known | upcoming}
        # Local focused rendering stays readable while allowing exploration of
        # shared ancestors outside the active partner's immediate family.
        self._relation = {"nodes": tuple(nodes.values()),
                          "edges": tuple(sorted(edges)), "current": current, "hidden": hidden}
        return self._relation

    def _relation_menu(self, choices, ready):
        graph = self.relation_graph()
        selected = self._relation_kind
        node = next((n for n in graph["nodes"] if n[0] == selected), None)
        eligible = selected in [choice.kind for choice in choices]
        footer = ("Cross again: evolve   Circle: back" if self.confirming else "Up/Down: branch  L/R: explore  Cross: choose") if ready and eligible else "L/R: explore   Up/Down: branch   Circle: back"
        if eligible and not ready:
            footer = f"Lv {self.required_level()} required   Arrows: browse   Circle: back"
        focus = self._relation_focus or graph["current"]
        relation = {**graph, "focus": focus, "side": self._relation_side,
                    "selected": selected, "level": self.progress.level,
                    "eligible": tuple(choice.kind for choice in choices), "ready": ready,
                    "selected_name": node[1] if node else "No linked forms"}
        rows = tuple("???" if choice.kind in graph["hidden"] else choice.name for choice in choices)
        self.menu = EvolutionMenu("Evolution", rows, self.choice_index, footer, layout="relation", relation=relation)

    def _dex_frame(self, pressed):
        ids = self.dex.catalog or sorted(int(k) for k in self.dex.entries)
        if pressed & (bit("A") | bit("X")):
            if self._dex_detail:
                self._dex_detail = False
            else:
                self._dex_open = False
                return
        if self._dex_detail and ids:
            entry = self.dex.entries.get(str(ids[self._dex_index % len(ids)]))
            if entry is None:
                self._dex_detail = False
            else:
                pages = entry_rows(entry, self.map_names)
                if pressed & (bit("Right") | bit("Down")):
                    self._dex_page = (self._dex_page + 1) % len(pages)
                elif pressed & (bit("Left") | bit("Up")):
                    self._dex_page = (self._dex_page - 1) % len(pages)
                self._dex_page %= len(pages)
                page = pages[self._dex_page]
                self.menu = EvolutionMenu(entry["name"], tuple(text for _, text, _ in page), -1,
                    f"{self._dex_page + 1}/{len(pages)}  Left/Right: pages  Circle: back", False,
                    avatar=ids[self._dex_index % len(ids)], styles=tuple(style for style, _, _ in page),
                    skills=tuple(self.dex.moves.get(skill) if skill is not None else None for _, _, skill in page))
                return
        if ids:
            if pressed & (bit("Down") | bit("Right")):
                self._dex_index = (self._dex_index + 1) % len(ids)
            elif pressed & (bit("Up") | bit("Left")):
                self._dex_index = (self._dex_index - 1) % len(ids)
            self._dex_index %= len(ids)
            if pressed & bit("B") and str(ids[self._dex_index]) in self.dex.entries:
                self._dex_detail = True
                self._dex_page = 0
                self._dex_frame(0)
                return
            start = self._dex_index // 8 * 8
            page = ids[start:start + 8]
            rows = tuple(self.dex.entries.get(str(kind), {}).get("name", "??? (not battled)") for kind in page)
            self.menu = EvolutionMenu(f"Digi-Dex {len(self.dex.entries)}/{len(ids)}", rows,
                self._dex_index - start, "Cross: info   Circle: back", True, start,
                icons=tuple(kind if str(kind) in self.dex.entries else None for kind in page))
        else:
            self.menu = EvolutionMenu("Digi-Dex", ("No encounters yet.", "Battle a Digimon to unlock it."), -1,
                                      "Circle: back", False)

    def _bank_frame(self, pressed):
        """Digi-Bank screens: the slot list (plus "Select new egg"), a partner's status, and the egg choice.
        Returns 0 when a swap started (the native sequence takes over), else None."""
        back, cross = pressed & (bit("A") | bit("X")), pressed & bit("B")
        step = (1 if pressed & (bit("Down") | bit("Right")) else -1 if pressed & (bit("Up") | bit("Left")) else 0)
        stored = self.progress.bank
        free = any(record is None for record in stored)
        if self._bank_view == "status":
            record = stored[self._bank_index]
            if back:
                if self._bank_confirm:
                    self._bank_confirm = False
                else:
                    self._bank_view = "list"
            elif cross and record is not None and self.idle():
                if self._bank_confirm:
                    self.start_swap(self._bank_index)
                    return 0
                self._bank_confirm = True
            elif step and record is not None:
                self._bank_page = (self._bank_page + step) % 2
                self._bank_confirm = False
            if self._bank_view == "status" and record is not None:
                active = bank.species(self.read_ram, self.number(PARTNER_ENTITY, "<i"))[0]
                page = bank.pages(self.read_ram, record)[self._bank_page]
                footer = (f"Cross again: swap with {active}   Circle: back" if self._bank_confirm
                          else f"{self._bank_page + 1}/2  Cross: make active  L/R: page  Circle: back" if self.idle()
                          else f"{self._bank_page + 1}/2  Partner busy   Circle: back")
                moves = self.dex.moves if self.dex is not None else {}
                self.menu = EvolutionMenu(bank.species(self.read_ram, record["kind"])[0],
                    tuple(text for _, text, _ in page), -1, footer, False, avatar=record["kind"],
                    styles=tuple(style for style, _, _ in page),
                    skills=tuple(moves.get(skill) if skill is not None else None for _, _, skill in page))
                return None
            self._bank_view = "list"
            back = cross = step = 0
        if self._bank_view == "eggs":
            eggs = list(bank.EGGS)
            if back:
                if self._bank_confirm:
                    self._bank_confirm = False
                else:
                    self._bank_view = "list"
            elif step:
                self._egg_index = (self._egg_index + step) % len(eggs)
                self._bank_confirm = False
            elif cross and free:
                if self._bank_confirm:
                    self._bank_index = self.hatch(eggs[self._egg_index])
                    self._bank_view, self._bank_confirm = "list", False
                else:
                    self._bank_confirm = True
            if self._bank_view == "eggs":
                names = tuple(bank.species(self.read_ram, kind)[0] for kind in eggs)
                footer = (f"Cross again: hatch {names[self._egg_index]}   Circle: back" if self._bank_confirm
                          else "Cross: hatch   Circle: back")
                self.menu = EvolutionMenu("Choose an egg", names, self._egg_index, footer, False,
                                          icons=tuple(eggs))
                return None
            back = cross = step = 0
        rows = BANK_SLOTS + 1  # the slots, then "Select new egg"
        if back:
            self._bank_open = False
            return None
        if step:
            self._bank_index = (self._bank_index + step) % rows
        elif cross:
            if self._bank_index == BANK_SLOTS:
                if free:
                    self._bank_view, self._bank_confirm = "eggs", False
            elif stored[self._bank_index] is not None:
                self._bank_view, self._bank_page, self._bank_confirm = "status", 0, False
            if self._bank_view != "list":
                return self._bank_frame(0)
        names = []
        for record in stored:
            if record is None:
                names.append("Empty slot")
            else:
                names.append(f"{bank.species(self.read_ram, record['kind'])[0]}   Lv {Progress(record['exp']).level}")
        names.append("Select new egg" if free else "Bank full: no new egg")
        active = bank.species(self.read_ram, self.number(PARTNER_ENTITY, "<i"))[0]
        used = sum(record is not None for record in stored)
        self.menu = EvolutionMenu(f"Digi-Bank {used}/{BANK_SLOTS} · {active} Lv {self.progress.level}",
            tuple(names), self._bank_index, "Cross: status   Circle: back", False,
            icons=tuple(record["kind"] if record else "empty" for record in stored) + ("egg",))
        return None

    def before_frame(self, buttons):
        old = self.menu
        pressed = buttons & ~self.previous_buttons
        output = self._before_frame(buttons)
        self.sound_events = []
        if self.menu is not None and old is None:
            self.sound_events.append("open")
        elif old is not None and output == 0:
            if pressed & (bit("A") | bit("X")):
                self.sound_events.append("back")
            elif pressed & bit("B"):
                self.sound_events.append("choose")
            elif pressed & (bit("Up") | bit("Down") | bit("Left") | bit("Right")) and self.menu != old:
                self.sound_events.append("navigate")
        return output

    def _before_frame(self, buttons):
        self.menu = None
        pressed = buttons & ~self.previous_buttons
        self.previous_buttons = buttons
        if not self.install():
            return buttons
        if self._swap is not None:
            self._finish_swap()
        if not self.main_menu_open():
            self._menu_open = False
            self.choosing = self.confirming = self._handoff = False
            self._dex_open = self._dex_detail = self._bank_open = False
            return buttons
        if self._handoff:
            if buttons:
                return self._handoff_button
            self._handoff = False
        if not self._menu_open:
            self.selection = max(0, min(5, self.number(MENU_OPTIONS + 4) - 1))
            self._menu_open = True
        if self._dex_open:
            self._dex_frame(pressed)
            if self._dex_open:
                return 0
            pressed = 0
        if self._bank_open:
            output = self._bank_frame(pressed)
            if self._bank_open or output is not None:
                return output or 0
            pressed = 0
        choices = self.choices()
        required = self.required_level()
        ready = required is not None and self.progress.level >= required and bool(choices)
        options = ("Items", "Digimon", "Player", "Praise", "Scold", "Sleep", "Evolution")
        options += ("Digi-Dex",) if self.dex is not None else ()
        options += ("Digi-Bank",)
        options += ("Fishing",) if self.number(HAS_FISHING_ROD, "<i") else ()
        count = len(options)
        if self.choosing:
            if pressed & (bit("A") | bit("X")):
                self.choosing = self.confirming = False
            else:
                graph = self.relation_graph()
                nodes = graph["nodes"]
                focus = self._relation_focus or graph["current"]
                parents = [n for n in nodes if (n[0], focus) in graph["edges"]]
                children = [n for n in nodes if (focus, n[0]) in graph["edges"]]
                column = children if self._relation_side == "next" else parents
                selected = next((n for n in column if n[0] == self._relation_kind), None)
                if selected is None and column:
                    selected = column[0]
                    self._relation_kind = selected[0]
                if pressed & (bit("Up") | bit("Down")) and column:
                    delta = -1 if pressed & bit("Up") else 1
                    self._relation_kind = column[(column.index(selected) + delta) % len(column)][0]
                    self.confirming = False
                elif pressed & (bit("Left") | bit("Right")):
                    side = "previous" if pressed & bit("Left") else "next"
                    if side == self._relation_side and selected and selected[0] not in graph["hidden"]:
                        self._relation_focus = selected[0]
                        focus = selected[0]
                        parents = [n for n in nodes if (n[0], focus) in graph["edges"]]
                        children = [n for n in nodes if (focus, n[0]) in graph["edges"]]
                    self._relation_side = side
                    options = children if side == "next" else parents
                    self._relation_kind = options[0][0] if options else focus
                    self.confirming = False
                elif pressed & bit("B") and selected:
                    if ready and selected[0] in [c.kind for c in choices]:
                        if self.confirming:
                            self.start_evolution(selected[0])
                            return 0
                        self.confirming = True
                    elif selected[0] not in graph["hidden"]:  # an unreached form cannot be explored
                        self._relation_focus = selected[0]
                        self._relation_side = "next"
                        children = [n for n in nodes if (selected[0], n[0]) in graph["edges"]]
                        self._relation_kind = children[0][0] if children else selected[0]
                        self.confirming = False
                self.choice_index = next((i for i,c in enumerate(choices) if c.kind == self._relation_kind),0)
        else:
            if pressed & (bit("Left") | bit("Right")):
                self.selection = (self.selection + (-1 if pressed & bit("Left") else 1)) % count
            elif pressed & (bit("Up") | bit("Down")):
                column = list(range(self.selection % 3, count, 3))
                delta = -1 if pressed & bit("Up") else 1
                self.selection = column[(column.index(self.selection) + delta) % len(column)]
            elif pressed & bit("B"):
                if options[self.selection] == "Evolution":
                    self.choosing = True
                    self.choice_index = 0
                    self._relation_focus = self.number(PARTNER_ENTITY, "<i")
                    self._relation_side = "next"
                    self._relation = None  # rebuild: the partner or its recorded forms may have changed
                    graph = self.relation_graph()
                    children = [n for n in graph["nodes"] if (self._relation_focus,n[0]) in graph["edges"]]
                    self._relation_kind = choices[0].kind if choices else children[0][0] if children else self._relation_focus
                elif options[self.selection] == "Digi-Dex":
                    self._dex_open = True
                    self._dex_frame(0)
                    return 0
                elif options[self.selection] == "Digi-Bank":
                    self._bank_open = True
                    self._bank_view, self._bank_confirm = "list", False
                    self._bank_frame(0)
                    return 0
                else:
                    native = self.selection + 1 if self.selection < 6 else 7
                    self.put(MENU_OPTIONS + 4, "<h", native)
                    self._handoff = True
                    self._handoff_button = bit("B")
                    return self._handoff_button
            elif pressed & (bit("A") | bit("X")):
                self._handoff = True
                self._handoff_button = bit("X")
                return self._handoff_button
        if self.choosing:
            self._relation_menu(choices, ready)
        else:
            self.menu = EvolutionMenu("Menu", options, self.selection, "Cross: select   Circle/Triangle: close",
                                      layout="grid")
        return 0  # consume only while this in-game menu is controlled by P6
