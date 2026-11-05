"""Encounter-gated knowledge, separate from rewindable quick saves (USA RAM).

Locations/times are observations, never assertions of exclusive spawn schedules.
"""
from __future__ import annotations

import copy
import json
import struct
from pathlib import Path

from .characters import STAGES, MOVE_NAME_COUNT
from .live import ENTITY_TABLE, ram_offset, ram_end
from .progression import DIGIMON_DATA, DIGIMON_PARA_SIZE, PARTNER_ENTITY, PARTNER_STATS, GAME_STATE, ENEMY_COUNT, COMBAT_DATA_PTR, COMBAT_ENTITY_IDS
from .quicksave import _atomic_write

HOUR = 0x80134EBC
ITEM_PARA = 0x801269DC
STATS = ("offense", "defense", "speed", "brains", "max_hp", "max_mp")
# MOVE_DATA (dw_decomp include/dw/move.h): Move[122] of 16 bytes. `special` is the element; battle code compares
# it with DIGIMON_DATA.special (TYPE_FACTORS). Checked on the USA disc: Fire Tower 0, Tremar 1, Static Elect 2,
# Poison Powder 3, Ice Needle 4, Power Crane 5, Odor Spray 6; Agumon's specialty is Fire, Betamon's Ice.
MOVE_DATA = 0x8012623C
ELEMENTS = ("Fire", "Battle", "Air", "Earth", "Ice", "Mech", "Filth")
STATUSES = {1: "Poison", 2: "Confuse", 3: "Stun", 4: "Flat"}  # FIGHTER_FLAG_* order
RANGES = {1: "Short", 2: "Long", 3: "Wide", 4: "Support"}  # 1 is blockable melee, 3 hits all, 4 buffs


def read_moves(read):
    """Element, power, MP (the game shows mpCost × 3), range and status of every technique, from RAM."""
    raw = read(ram_offset(MOVE_DATA), MOVE_NAME_COUNT * 16)
    moves = {}
    for move in range(MOVE_NAME_COUNT):
        _, power, mp, _, reach, element, status = struct.unpack_from("<ihBBBBB", raw, move * 16)
        if element < len(ELEMENTS) and 0 <= power <= 9999:
            moves[move] = {"element": element, "power": power, "mp": mp * 3, "range": reach, "status": status}
    return moves


def period(hour):
    # map.c lighting boundaries; evening is kept distinct from night.
    return "Night" if hour < 6 or hour >= 20 else "Evening" if hour >= 16 else "Day"


class DigiDex:
    excluded_slot = None  # an entity slot in the battle that is not an opponent (coop.py)

    def __init__(self, path: Path):
        self.path = Path(path)
        self.entries = {}
        self.catalog = []
        self.moves = {}  # technique id -> element/power/mp/range/status, read once a game is running
        self.dirty = False
        self.placements = {}
        self.map_names = {}
        self.reset_runtime()
        if self.path.exists():
            record = json.loads(self.path.read_bytes())
            if not isinstance(record, dict) or record.get("format") != 1:
                raise ValueError("Unsupported Digi-Dex record")
            entries = record.get("entries")
            catalog = record.get("catalog", [])
            if (not isinstance(entries, dict) or not isinstance(catalog, list)
                    or any(type(k) is not int or not 0 < k < 180 for k in catalog)):
                raise ValueError("Invalid Digi-Dex record")
            for key, value in entries.items():
                if not key.isdigit() or not 0 < int(key) < 180 or not self._valid_entry(value):
                    raise ValueError("Invalid Digi-Dex entry")
            self.entries, self.catalog = entries, sorted(set(catalog))

    def reset_runtime(self):
        self._last_state = None
        self._battle_ready = False

    @staticmethod
    def _valid_skills(skills):
        return isinstance(skills, list) and all(isinstance(s, dict) and type(s.get("id")) is int
            and 0 <= s["id"] < MOVE_NAME_COUNT and isinstance(s.get("name"), str) for s in skills)

    @staticmethod
    def _valid_entry(e):
        if (not isinstance(e, dict) or not isinstance(e.get("name"), str)
                or type(e.get("stage")) is not int or e["stage"] not in STAGES
                or not isinstance(e.get("drop"), dict) or not isinstance(e.get("profiles"), list)):
            return False
        if not DigiDex._valid_skills(e.get("skills", [])):
            return False
        drop = e["drop"]
        if (not isinstance(drop.get("name"), str) or type(drop.get("chance")) is not int
                or not 0 <= drop["chance"] <= 100):
            return False
        for p in e["profiles"]:
            if (not isinstance(p, dict) or type(p.get("map")) is not int or not 0 <= p["map"] <= 255
                    or not isinstance(p.get("location", ""), str)
                    or not isinstance(p.get("hours"), list) or not p["hours"]
                    or any(type(h) is not int or not 0 <= h < 24 for h in p["hours"])
                    or not isinstance(p.get("stats"), dict)
                    or set(p["stats"]) != set(STATS)
                    or any(type(v) is not int or not 0 <= v <= 32767 for v in p["stats"].values())):
                return False
        if any(not DigiDex._valid_skills(p.get("skills", [])) for p in e["profiles"]):
            return False
        placements = e.get("placements", [])
        if not isinstance(placements, list):
            return False
        for p in placements:
            if (not isinstance(p, dict) or not isinstance(p.get("name"), str)
                    or type(p.get("map")) is not int or not 0 <= p["map"] <= 255
                    or not isinstance(p.get("stats"), dict) or set(p["stats"]) != set(STATS)
                    or any(type(v) is not int or not 0 <= v <= 32767 for v in p["stats"].values())):
                return False
        return True

    def snapshot(self):
        """Persisted knowledge plus the game's technique table (`moves`, read from RAM, never saved)."""
        return copy.deepcopy({"format": 1, "catalog": self.catalog, "entries": self.entries, "moves": self.moves})

    def save(self):
        if self.dirty:
            record = {"format": 1, "catalog": self.catalog, "entries": self.entries}
            _atomic_write(self.path, json.dumps(record, indent=2).encode())
            self.dirty = False

    def observe(self, read, live):
        def number(address, fmt="<h"):
            return struct.unpack(fmt, read(ram_offset(address), struct.calcsize(fmt)))[0]
        # Never turn title-screen/stale entity RAM into knowledge.
        if (live.tamer is None or not 0 < number(PARTNER_ENTITY, "<i") < 180
                or number(PARTNER_STATS + 16) <= 0):
            return []
        hour = number(HOUR)
        if not 0 <= hour < 24:
            return []
        table = read(ram_offset(DIGIMON_DATA), 180 * DIGIMON_PARA_SIZE)
        catalog = [kind for kind in range(1, 180) if table[kind * 52 + 29] in STAGES]
        changed = catalog != self.catalog
        if not self.moves:
            self.moves = read_moves(read)
            changed |= bool(self.moves)  # lets the player publish a snapshot carrying the table
        self.catalog = catalog
        unlocked = []
        # Only combatants count; on-map/on-screen bystanders never grant knowledge.
        game_state = number(GAME_STATE)
        if game_state == 0:
            self._battle_ready = False
        elif game_state == 3 or (self._last_state is None and game_state in (1, 2)):
            # handleBattleStart sets state 3 only after the opponent list is built.
            # A session/state loaded mid-battle can begin directly in state 1/2.
            self._battle_ready = True
        self._last_state = game_state
        if not self._battle_ready:
            self.dirty |= changed
            return []
        count, combat = number(ENEMY_COUNT), number(COMBAT_DATA_PTR, "<I")
        if not 1 <= count <= 3 or not 0x80000000 <= combat <= ram_end() - COMBAT_ENTITY_IDS - 4:
            self.dirty |= changed
            return []
        slots = [slot for slot in read(ram_offset(combat + COMBAT_ENTITY_IDS + 1), count) if slot != self.excluded_slot]
        if len(set(slots)) != len(slots) or any(not 2 <= slot < 10 for slot in slots):
            self.dirty |= changed
            return []
        for slot in slots:
            pointer = number(ENTITY_TABLE + slot * 4, "<I")
            if not 0x80000000 <= pointer <= ram_end() - 104:
                continue
            kind = number(pointer, "<i")
            if not 0 < kind < 180 or kind not in catalog:
                continue
            meta = table[kind * 52:(kind + 1) * 52]
            name = meta[:20].split(b"\0", 1)[0].decode("ascii", errors="replace") or f"Digimon {kind}"
            values = struct.unpack("<4h", read(ram_offset(pointer + 56), 8))
            hp, mp = struct.unpack("<2h", read(ram_offset(pointer + 72), 4))
            stats = dict(zip(STATS, (*values, hp, mp)))
            if any(v < 0 for v in stats.values()) or hp <= 0:
                continue
            drop_id, chance = meta[33], meta[34]
            if chance > 100:
                continue
            drop_name = "None"
            if chance and drop_id < 128:
                raw = read(ram_offset(ITEM_PARA + drop_id * 32), 20)
                drop_name = raw.split(b"\0", 1)[0].decode("ascii", errors="replace") or f"Item {drop_id}"
            elif chance:
                continue
            linked = [read_skill(read, move) for move in dict.fromkeys(meta[35:51]) if move < MOVE_NAME_COUNT]
            animations = read(ram_offset(pointer + 68), 4)
            equipped = [read_skill(read, meta[35 + move - 46]) for move in dict.fromkeys(animations)
                        if 46 <= move < 62 and meta[35 + move - 46] < MOVE_NAME_COUNT]
            key = str(kind)
            if key not in self.entries:
                self.entries[key] = {"name": name, "stage": meta[29],
                    "drop": {"name": drop_name, "chance": chance, "item": drop_id}, "profiles": [],
                    "placements": copy.deepcopy(self.placements.get(key, [])), "skills": linked}
                unlocked.append(name)
                changed = True
            entry = self.entries[key]
            profile = next((p for p in entry["profiles"] if p["map"] == live.map_index and p["stats"] == stats and p.get("skills", []) == equipped), None)
            if profile is None:
                profile = {"map": live.map_index, "location": self.map_names.get(live.map_index, f"Map {live.map_index}"),
                           "stats": stats, "hours": [], "skills": equipped}
                entry["profiles"].append(profile)
                changed = True
            if hour not in profile["hours"]:
                profile["hours"] = sorted([*profile["hours"], hour])
                changed = True
        self.dirty |= changed
        return unlocked


def entry_sections(entry, map_names=None):
    """Compact in-game pages of (style, text, skill id) rows. Only a persisted unlocked entry produces data.

    Styles: "header" (page title), "info", "stat", "skill" (third value is the technique id), "note".
    """
    map_names = map_names or {}
    drop = entry["drop"]

    def stat_rows(stats):
        return [("stat", f"HP {stats['max_hp']}   MP {stats['max_mp']}", None),
                ("stat", f"Off {stats['offense']}   Def {stats['defense']}", None),
                ("stat", f"Speed {stats['speed']}   Brains {stats['brains']}", None)]

    def skill_rows(skills):
        return [("skill", skill["name"], skill["id"]) for skill in skills]

    overview = [[("header", "Overview", None), ("info", f"Rank: {STAGES[entry['stage']]}", None),
              ("info", f"Drop: {drop['name']}", None), ("info", f"Drop chance: {drop['chance']}%", None),
              ("note", "Battle stats vary by encounter.", None), ("note", "Times are observed sightings,", None),
              ("note", "not an exclusive schedule.", None)]]
    pages = []
    skills = entry.get("skills", [])
    if skills:
        for offset in range(0, len(skills), 6):
            pages.append([("header", "Linked skills (species)", None), *skill_rows(skills[offset:offset + 6])])
    else:
        pages.append([("header", "Linked skills: none recorded.", None)])
    locations = []
    for placement in entry.get("placements", []):
        locations.append([("header", placement["name"], None), ("info", "Evidence: Decoded placement", None),
                      ("info", "When: schedule unknown", None), *stat_rows(placement["stats"]),
                      ("note", "May depend on story/scripts.", None)])
    encounters = []
    for number, p in enumerate(entry["profiles"], 1):
        periods = "/".join(dict.fromkeys(period(h) for h in p["hours"]))
        hours = ", ".join(f"{h:02d}" for h in p["hours"])
        location = map_names.get(p["map"], p.get("location", f"Map {p['map']}"))
        encounters.append([("header", location, None), ("info", f"Observed battle: {number}", None), ("info", f"Seen: {periods}", None),
                      ("info", f"Hours: {hours}", None), *stat_rows(p["stats"]),
                      ("note", "Availability not yet established.", None)])
        equipped = p.get("skills", [])
        if equipped:
            for offset in range(0, len(equipped), 6):
                encounters.append([("header", "Equipped in encountered battle", None),
                                   ("info", f"Battle {number}: {location}", None), *skill_rows(equipped[offset:offset + 6])])
        else:
            encounters.append([("header", "Encounter skills: none recorded.", None),
                               ("info", f"Battle {number}: {location}", None)])
    return {"Overview": overview, "Skills": pages, "Decoded locations": locations, "Observed encounters": encounters}


def entry_rows(entry, map_names=None):
    """At most nine rows per 320×240 page, grouped by evidence type; keep existing row/skill metadata API."""
    return [page for pages in entry_sections(entry, map_names).values() for page in pages]


def entry_pages(entry, map_names=None):
    """The text of entry_rows."""
    return [[text for _, text, _ in page] for page in entry_rows(entry, map_names)]


def skill_summary(move):
    """'Fire · Pow 155 · MP 81 · Long · Stun' from a read_moves record."""
    parts = [ELEMENTS[move["element"]], f"Pow {move['power']}", f"MP {move['mp']}",
             RANGES.get(move["range"], "")]
    if move["status"] in STATUSES:
        parts.append(STATUSES[move["status"]])
    return " · ".join(part for part in parts if part)


def read_skill(read, move):
    pointer = struct.unpack("<I", read(ram_offset(0x80126054 + move * 4), 4))[0]
    name = ""
    if 0x80000000 <= pointer <= ram_end() - 64:
        raw = read(ram_offset(pointer), 64)
        if b"\0" in raw:
            name = raw.split(b"\0", 1)[0].decode("ascii", errors="replace")
    return {"id": move, "name": name if name and name.isprintable() else f"Skill {move}"}


def load_placements(disc, cancelled=lambda: False, map_names=None):
    """Decode possible placements, without asserting runtime/script availability."""
    from .disc import inspect_image
    from .iso9660 import read_inventory
    from .maps import MAX_MAP_FILE, parse_map, parse_map_table
    from .errors import Cancelled

    result = {}
    with inspect_image(Path(disc)).open() as reader:
        inventory = {entry.path: entry for entry in read_inventory(reader, cancelled)}
        exe = inventory["SLUS_010.32"]
        table = parse_map_table(reader.read_extent(exe.extent, exe.size, 2 * 1024 * 1024))
        for entry in table:
            if map_names is not None and entry.name:
                map_names.setdefault(entry.index, f"{entry.area_name} ({entry.name})")
            if cancelled():
                raise Cancelled("Digi-Dex location indexing cancelled")
            resource = inventory.get(entry.map_path)
            if not entry.has_digimon or resource is None:
                continue
            scene = parse_map(reader.read_extent(resource.extent, resource.size, MAX_MAP_FILE), entry)
            for npc in scene.digimon:
                if not 0 < npc.character_id < 180:
                    continue
                stats = {key: npc.stats[key] for key in STATS}
                if any(not 0 <= v <= 32767 for v in stats.values()) or stats["max_hp"] <= 0:
                    continue
                placement = {"map": entry.index, "name": f"{entry.area_name} ({entry.name})", "stats": stats}
                rows = result.setdefault(str(npc.character_id), [])
                if placement not in rows:
                    rows.append(placement)
    return result
