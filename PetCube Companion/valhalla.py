"""
valhalla.py
Paradiso digitale dei mostri caduti. Salva e carica le creature morte
localmente in valhalla.json; calcola i nomi delle creature replicando la
logica del firmware (creatureName()).
"""
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

VALHALLA_PATH = Path(__file__).resolve().parent / "valhalla.json"
_DEATHS_CACHE_PATH = Path(__file__).resolve().parent / "valhalla_deaths_cache.json"

# ── Creature name tables (specchio esatto del firmware) ────────────────────
_FIRE_SHARED  = ["Kindlekin", "Emberpaw", "Pyruff"]
_FIRE_LINE0   = ["Blazebrand", "Mightforge"]          # STR
_FIRE_LINE1   = ["Shieldmane", "Fortifire"]           # ENG
_FIRE_LINE2   = ["Aurovulp", "Vulpyre"]               # INT
_FIRE_FINAL0  = ["Flameforge", "Seraphyre", "Noxfortress"]
_FIRE_FINAL1  = ["Citadellion", "Seraphyre", "Noxfortress"]
_FIRE_FINAL2  = ["Eldervulp", "Seraphyre", "Noxfortress"]

_WATER_SHARED = ["Drowsea", "Gloomfin", "Fanglure"]
_WATER_LINE0  = ["Riptalon", "Maulstream"]            # STR
_WATER_LINE1  = ["Baleguard", "Bulwhark"]             # ENG
_WATER_LINE2  = ["Sirenlure", "Abyssibyl"]            # INT
_WATER_FINAL0 = ["Leviacrush", "Lightfin", "Nightmare"]
_WATER_FINAL1 = ["Tidenaught", "Lightfin", "Nightmare"]
_WATER_FINAL2 = ["Thalassibyl", "Lightfin", "Nightmare"]

STAGE_NAMES   = ["Baby", "Child", "Teen", "Adult", "Champion", "Leggenda"]
ELEMENT_ICON  = {"Fire": "🔥", "Water": "💧"}
VARIANT_ICON  = {-1: "", 0: "", 1: " ✨", 2: " 🌑"}
LINE_NAMES    = ["STR", "ENG", "INT"]


def creature_name(element: str, evo_stage: int, line_variant: int, final_variant: int) -> str:
    fire = element.upper() == "FIRE"
    shared = _FIRE_SHARED  if fire else _WATER_SHARED
    line0  = _FIRE_LINE0   if fire else _WATER_LINE0
    line1  = _FIRE_LINE1   if fire else _WATER_LINE1
    line2  = _FIRE_LINE2   if fire else _WATER_LINE2
    final0 = _FIRE_FINAL0  if fire else _WATER_FINAL0
    final1 = _FIRE_FINAL1  if fire else _WATER_FINAL1
    final2 = _FIRE_FINAL2  if fire else _WATER_FINAL2

    if evo_stage <= 2:
        return shared[min(evo_stage, 2)]
    line_variant = max(0, min(2, line_variant))
    if evo_stage == 3:
        return [line0[0], line1[0], line2[0]][line_variant]
    if evo_stage == 4:
        return [line0[1], line1[1], line2[1]][line_variant]
    v = max(0, min(2, final_variant if final_variant >= 0 else 0))
    return [final0, final1, final2][line_variant][v]


@dataclass
class ValhallaEntry:
    element: str        # "Fire" or "Water"
    evo_stage: int      # 0-5
    line_variant: int   # 0=STR 1=ENG 2=INT
    final_variant: int  # -1=none 0=STD 1=Light 2=Dark
    stat_str: int
    stat_int: int
    stat_eng: int
    stat_hap: int
    sessions: int
    battles_won: int    # firmware battles won during life
    battles_lost: int
    deaths_total: int   # firmware total deaths counter at time of this death
    owner: str = ""
    death_timestamp: float = field(default_factory=time.time)
    valhalla_wins: int = 0    # online Valhalla battle wins
    valhalla_losses: int = 0  # online Valhalla battle losses

    @property
    def name(self) -> str:
        return creature_name(self.element, self.evo_stage, self.line_variant, self.final_variant)

    @property
    def display_icon(self) -> str:
        return ELEMENT_ICON.get(self.element, "?") + VARIANT_ICON.get(self.final_variant, "")

    @property
    def stage_label(self) -> str:
        return STAGE_NAMES[min(self.evo_stage, 5)]

    @property
    def battle_power(self) -> float:
        base = self.stat_str * 0.4 + self.stat_int * 0.3 + self.stat_eng * 0.3
        return base + self.evo_stage * 8 + min(self.sessions, 100) * 0.2

    def to_dict(self) -> dict:
        d = asdict(self)
        d["_name"] = self.name
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ValhallaEntry":
        d = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**d)


# ── Persistence ───────────────────────────────────────────────────────────

def load_valhalla() -> list[ValhallaEntry]:
    try:
        with open(VALHALLA_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return [ValhallaEntry.from_dict(e) for e in data]
    except (FileNotFoundError, json.JSONDecodeError, TypeError, KeyError):
        return []


def save_valhalla(entries: list[ValhallaEntry]) -> None:
    try:
        with open(VALHALLA_PATH, "w", encoding="utf-8") as f:
            json.dump([e.to_dict() for e in entries], f, indent=2, ensure_ascii=False)
    except OSError as e:
        log.warning("Impossibile salvare valhalla.json: %s", e)


def add_entry(entry: ValhallaEntry) -> None:
    entries = load_valhalla()
    entries.append(entry)
    save_valhalla(entries)
    log.info("Valhalla: aggiunta creatura '%s' (deaths_total=%d)", entry.name, entry.deaths_total)


def update_entry_valhalla_record(idx: int, wins: int, losses: int) -> None:
    entries = load_valhalla()
    if 0 <= idx < len(entries):
        entries[idx].valhalla_wins  = wins
        entries[idx].valhalla_losses = losses
        save_valhalla(entries)


# ── Deaths-counter cache (per rilevare nuove morti) ───────────────────────

def load_deaths_cache() -> int:
    try:
        with open(_DEATHS_CACHE_PATH, encoding="utf-8") as f:
            return int(json.load(f).get("deaths_total", 0))
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        return 0


def save_deaths_cache(deaths_total: int) -> None:
    try:
        with open(_DEATHS_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump({"deaths_total": deaths_total}, f)
    except OSError:
        pass
