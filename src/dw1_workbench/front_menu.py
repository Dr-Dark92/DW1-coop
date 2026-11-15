"""USA title/settings adapters. Policy is callbacks; rendering uses EvolutionMenu.

Source: dw_decomp aaf69a4 main_menu.c/new_game.c/shop/script_menu.c.
World-object callbacks gate each scene; a shared script-state number alone is not sufficient.
"""
import struct
from dataclasses import replace

from .evolution import EvolutionMenu, bit
from .live import ram_offset, ram_end
from .camera import sync_instruction_cache
from .progression import PARTNER_ENTITY, PARTNER_STATS, LIFETIME, DIGIMON_DATA, DIGIMON_PARA_SIZE

WORLD_OBJECTS = 0x80137418
CURRENT_MENU, TARGET_MENU, MENU_CURSOR = 0x8013504C, 0x80135068, 0x801316B8
SCRIPT_STATE = 0x80134FFA
SCRIPT_POINTER = 0x80134FB8
# Confirmed native partner IDs; Evening uses partner Kunemon32, not NPC duplicate152.
TIMES = (("Late Night", 17), ("Dawn", 31), ("Morning", 46), ("Midday", 3),
         ("Late Afternoon", 45), ("Dusk", 57), ("Evening", 32))
STARTER_WORDS = {0x800EE9D0: 0x24100011, 0x800EE9D8: 0x24100003}


class SettingsMenu:
    def __init__(self, get, apply):
        self.get, self.apply = get, apply
        self.open()

    def open(self):
        self.selected = 0
        self.title_context = False

    def frame(self, pressed):
        if pressed & (bit("A") | bit("X")):
            return None
        if pressed & (bit("Up") | bit("Down")):
            self.selected = (self.selected + (-1 if pressed & bit("Up") else 1)) % 7
        options, lifespan, exp = self.get()
        if pressed & (bit("B") | bit("Left") | bit("Right")):
            if self.selected < 5:
                key = ("hp_mp", "lifespan", "hunger", "poop", "fatigue")[self.selected]
                options = replace(options, **{key: not getattr(options, key)})
            elif self.selected == 5:
                lifespan = True if lifespan is None else not lifespan
            else:
                exp = not exp  # next launch, never mutate progression half-way through a frame
            self.apply(options, lifespan, exp)
        rows = tuple(f"{label}: {'ON' if getattr(options, key) else 'OFF'}" for key, label in
                     (("hp_mp", "HP / MP"), ("lifespan", "Lifespan bar"), ("hunger", "Hunger"),
                      ("poop", "Toilet meter"), ("fatigue", "Fatigue")))
        rows += (f"Lifespan: {'LEGACY' if lifespan is None else 'ON' if lifespan else 'PAUSED*'}",
                 f"{'Gameplay' if self.title_context else 'Restart app'}: {'EXP-based' if exp else 'Classic'}",
                 "*Hourly pause; sleep/training not covered.", "Visibility never disables care needs.")
        return EvolutionMenu("Settings", rows, self.selected, "Cross: change   Circle: back", False)


class FrontMenu:
    def __init__(self, core, settings):
        self.core, self.settings = core, settings
        self.previous = 0
        self.selection = 0
        self.menu = None
        self.in_settings = self.handoff = self.question = False
        self.time_index = 0
        self.patched = None
        self.wait_stage = None
        self.at_title = False
        self.starting = None

    def read(self, address, size):
        return self.core.read_memory(ram_offset(address), size)

    def reset(self):
        self.restore()
        self.menu = None
        self.previous = 0
        self.in_settings = self.handoff = False
        self.starting = None

    def number(self, address, fmt="<i"):
        return struct.unpack(fmt, self.read(address, struct.calcsize(fmt)))[0]

    def put(self, address, fmt, value):
        self.core.write_memory(ram_offset(address), struct.pack(fmt, value))

    def restore(self):
        if self.question and self.wait_stage is not None and self.number(SCRIPT_STATE, "<h") == 1:
            self.put(SCRIPT_STATE, "<h", self.wait_stage)
        self.question = False
        if self.patched is None:
            return
        for address, original in STARTER_WORDS.items():
            if self.number(address, "<I") == 0x24100000 | self.patched:
                self.put(address, "<I", original)
        sync_instruction_cache(self.core, list(STARTER_WORDS))
        self.patched = None

    def _patch_starter(self, kind):
        if type(kind) is not int or kind not in {kind for _, kind in TIMES}:
            raise ValueError("Unsupported native starter ID")
        if "beetle psx" not in self.core.info.name.lower():
            raise ValueError("Starter choice needs verified Beetle instruction-cache support")
        if any(self.number(a, "<I") != original for a, original in STARTER_WORDS.items()):
            raise ValueError("Starter initialization code differs from the verified USA executable")
        # Patch BOTH branches' immediate arguments, not the partner entity or any care table.
        self.patched = kind
        self.starting = kind
        try:
            for address in STARTER_WORDS:
                self.put(address, "<I", 0x24100000 | kind)
            sync_instruction_cache(self.core, list(STARTER_WORDS))
        except Exception:
            self.restore()
            self.starting = None
            raise

    def _starter_skill(self, kind):
        if kind in (3, 17):
            return  # native initializePartner already equips/learns their original starting attack
        moves = self.read(DIGIMON_DATA + kind * DIGIMON_PARA_SIZE + 35, 16)
        regular = next(((index, move) for index, move in enumerate(moves) if move < 58), None)
        if regular is None:
            raise ValueError("Selected starter has no source-linked regular technique")
        index, move = regular
        self.put(PARTNER_STATS + 12, "B", 0x2E + index)
        learned = self.number(PARTNER_ENTITY + 88, "<Q")
        paired = {44: (44, 48), 48: (44, 48), 55: (55, 57), 57: (55, 57)}
        for tech in paired.get(move, (move,)):
            learned |= 1 << tech
        self.put(PARTNER_ENTITY + 88, "<Q", learned)

    def before_frame(self, buttons):
        self.menu = None
        pressed = buttons & ~self.previous
        self.previous = buttons
        objects = self.read(WORLD_OBJECTS, 128 * 12)
        callbacks = [struct.unpack_from("<hhII", objects, index * 12) for index in range(128)]
        title = any(obj == 5000 and tick == 0x8010FF1C and render == 0x80113458
                    for obj, instance, tick, render in callbacks)
        self.at_title = title and self.number(CURRENT_MENU) == self.number(TARGET_MENU) == 0
        jijimon = any(obj == 117 and instance == 2 and tick == 0x800F1498
                      for obj, instance, tick, render in callbacks)
        if self.at_title:
            self.restore()
            self.starting = None
            self.question = False
            if self.handoff:
                if buttons: return buttons
                self.handoff = False
            if self.in_settings:
                self.menu = self.settings.frame(pressed)
                if self.menu is not None: return 0
                self.in_settings = False
                pressed = 0
            if pressed & (bit("Up") | bit("Down")):
                self.selection = (self.selection + (-1 if pressed & bit("Up") else 1)) % 5
            if pressed & bit("B"):
                if self.selection == 4:
                    self.settings.open(); self.settings.title_context = True; self.in_settings = True
                    self.menu = self.settings.frame(0)
                    return 0
                self.put(MENU_CURSOR, "b", self.selection)
                self.handoff = True
                return buttons
            self.menu = EvolutionMenu("Digimon World", ("New Game", "Load Game", "Delete Game", "Battle Mode", "Settings"),
                                      self.selection, "Cross: select   Up/Down: move", False)
            return 0
        if jijimon:
            stage = self.number(SCRIPT_STATE, "<h")
            if not self.question and stage in (6, 10):
                self.question = True
                self.wait_stage = stage
                self.time_index = 0
                self.put(SCRIPT_STATE, "<h", 1)  # native wait state; do not display original day/night question
            if self.question:
                if pressed & (bit("Up") | bit("Down")):
                    self.time_index = (self.time_index + (-1 if pressed & bit("Up") else 1)) % len(TIMES)
                selected = TIMES[self.time_index][1]
                if pressed & bit("B") and selected is not None:
                    pointer = self.number(SCRIPT_POINTER, "<I")
                    if not 0x80000000 <= pointer < ram_end() - 601:
                        raise ValueError("Invalid new-game script state pointer")
                    self._patch_starter(selected)
                    self.put(pointer + 345 + 254, "B", 0 if selected == 3 else 1)
                    self.put(SCRIPT_STATE, "<h", 0x11)  # resume native acknowledgement and naming
                    self.question = False
                    return 0
                footer = "Not available: partner conversion required" if selected is None else "Cross: choose a time of day"
                self.menu = EvolutionMenu("Jijimon", ("Which time of day do you like best?",) + tuple(t[0] for t in TIMES),
                                          self.time_index + 1, footer, False, layout="dialogue")
                return 0
        else:
            self.question = False
            if self.patched is not None and any(obj == self.patched and instance == 1 for obj, instance, tick, render in callbacks):
                self.restore()
            if (self.starting is not None and self.number(PARTNER_ENTITY) == self.starting
                    and any(obj == self.starting and instance == 1 for obj, instance, tick, render in callbacks)
                    and self.number(PARTNER_STATS + 16, "<h") > 0 and self.number(LIFETIME, "<h") > 0):
                self._starter_skill(self.starting)
                self.starting = None
        return buttons
