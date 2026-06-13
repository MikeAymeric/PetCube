"""
achievements.py
Catalogo statico dei 47 achievement del giocatore. La bitmask di sblocco è
calcolata interamente dal firmware (namespace NVS "achv") ed esposta via BLE
sulla caratteristica ACHV (uint64 little-endian, vedi firmware_updater.py).
Qui viviamo solo lato presentazione: id, titolo, icona (emoji per ora) e
descrizione, raggruppati per categoria.
"""
import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Cache locale dell'ultima bitmask letta via BLE, cosi' la tab Achievements
# mostra l'ultimo stato noto anche a cubo disconnesso.
CACHE_PATH = Path(__file__).resolve().parent / "achievements_cache.json"


@dataclass(frozen=True)
class Achievement:
    id: int
    title: str
    icon: str
    description: str
    category: str


ACHIEVEMENTS: list[Achievement] = [
    # ── Evolution ──
    Achievement(0,  "Glow Up",                  "🐣", "La tua creatura ha completato la prima evoluzione.", "Evolution"),
    Achievement(1,  "Adulting",                 "🧑", "La tua creatura ha raggiunto lo stadio Adulto.", "Evolution"),
    Achievement(2,  "Final Form",               "💪", "La tua creatura ha raggiunto lo stadio finale (Ultimate).", "Evolution"),
    Achievement(3,  "Light Side",               "✨", "Hai ottenuto la variante Light alla forma finale.", "Evolution"),
    Achievement(4,  "Dark Side",                "🌑", "Hai ottenuto la variante Dark alla forma finale.", "Evolution"),
    Achievement(5,  "No Cap, Balanced",         "⚖️", "Forma finale raggiunta con STR/INT/ENG perfettamente equilibrate.", "Evolution"),
    Achievement(6,  "Master of Fire and Water", "🔥", "Hai giocato sia con un baby di Fuoco che con uno di Acqua.", "Evolution"),
    # ── Collection ──
    Achievement(7,  "Gotta Catch a Few",        "📒", "Hai registrato almeno 5 creature nel Registro.", "Collection"),
    Achievement(8,  "Gotta Catch Half",         "📚", "Hai registrato almeno 14 creature nel Registro.", "Collection"),
    Achievement(9,  "Gotta Catch 'Em All",      "🏆", "Hai completato il Registro con tutte le 28 creature.", "Collection"),
    Achievement(10, "Shiny Hunter",             "🌟", "Hai registrato una creatura della variante Light.", "Collection"),
    Achievement(11, "Edgelord Unlocked",        "🖤", "Hai registrato una creatura della variante Dark.", "Collection"),
    Achievement(12, "Two Sides of the Coin",    "🔥", "Hai registrato una forma finale di Fuoco e una di Acqua.", "Collection"),
    # ── Legends ──
    Achievement(13, "Press F to Pay Respects",  "🪦", "La tua creatura è morta per la prima volta.", "Legends"),
    Achievement(14, "Hall of Flame",            "👑", "Hai raggiunto lo status di Leggenda per la prima volta.", "Legends"),
    Achievement(15, "GOAT Status",              "🐐", "Hai raggiunto lo status di Leggenda 5 volte.", "Legends"),
    Achievement(16, "Nepo Baby",                "👶", "Hai iniziato una nuova vita con stat ereditate dalla Leggenda precedente.", "Legends"),
    Achievement(17, "The Circle of Life",       "🔄", "Hai vissuto e reincarnato il tuo pet almeno 3 volte.", "Legends"),
    # ── Productivity ──
    Achievement(18, "Big Brain Time",           "🧠", "Hai completato 10 sessioni Pomodoro in totale.", "Productivity"),
    Achievement(19, "Touch Grass... Later",     "🌱", "Hai completato 50 sessioni Pomodoro in totale.", "Productivity"),
    Achievement(20, "No Days Off",              "📅", "Hai completato 100 sessioni Pomodoro in totale.", "Productivity"),
    Achievement(21, "Galaxy Brain",             "🌌", "La tua creatura ha raggiunto 90 di Intelligenza.", "Productivity"),
    Achievement(22, "We Go Gym",                "🏋️", "La tua creatura ha raggiunto 90 di Forza.", "Productivity"),
    Achievement(23, "Caffeine Powered",         "⚡", "La tua creatura ha raggiunto 90 di Energia.", "Productivity"),
    Achievement(24, "Jack of All Trades",       "🃏", "Hai completato almeno una sessione di ogni tipo (Training, Study, Work).", "Productivity"),
    # ── Care / Survival ──
    Achievement(25, "Clean Freak",              "🧹", "Hai pulito gli escrementi 20 volte.", "Care"),
    Achievement(26, "It Was Mega",              "💩", "Hai prodotto il tuo primo escremento Mega.", "Care"),
    Achievement(27, "He's Not Dead Yet!",       "🚑", "Hai guarito la tua creatura malata con la felicità sotto 20.", "Care"),
    Achievement(28, "Built Different",          "🦠", "La tua creatura ha superato 5 episodi di malattia.", "Care"),
    Achievement(29, "Vibing",                   "😌", "La tua creatura ha raggiunto 100 di felicità.", "Care"),
    Achievement(30, "Mood: Rock Bottom",        "📉", "La felicità della tua creatura è scesa a 0.", "Care"),
    Achievement(31, "Self Care Sunday",         "🛁", "Hai curato la tua creatura 10 volte.", "Care"),
    # ── Battles ──
    Achievement(32, "It's Go Time",             "⚔️", "Hai vinto la tua prima battaglia.", "Battles"),
    Achievement(33, "Undefeated",               "🏅", "Hai raggiunto una striscia di 5 vittorie consecutive.", "Battles"),
    Achievement(34, "L + Ratio",                "💀", "Hai perso la tua prima battaglia.", "Battles"),
    Achievement(35, "Comeback Arc",             "🔥", "Hai vinto una battaglia subito dopo una sconfitta.", "Battles"),
    Achievement(36, "Big Boss Energy",          "👹", "Hai vinto 25 battaglie in totale.", "Battles"),
    Achievement(37, "Glass Cannon",             "🩹", "Hai vinto una battaglia mentre la tua creatura era malata.", "Battles"),
    # ── Connectivity ──
    Achievement(38, "Notification Hell",        "📬", "Hai ricevuto notifiche da almeno 5 fonti diverse.", "Connectivity"),
    Achievement(39, "Houston, We Solved It",    "🚀", "Hai ricevuto una notifica di Crisi.", "Connectivity"),
    Achievement(40, "Multiplayer Unlocked",     "🤝", "Hai associato un tag identità tramite la companion.", "Connectivity"),
    # ── Daily Life ──
    Achievement(41, "Upside Down",              "🙃", "Hai capovolto il cubo sottosopra.", "Daily Life"),
    Achievement(42, "5AM Club",                 "🌅", "Hai completato una sessione tra le 4:00 e le 6:00.", "Daily Life"),
    Achievement(43, "Vampire Mode",             "🦇", "Hai completato una sessione tra le 0:00 e le 4:00.", "Daily Life"),
    Achievement(44, "Touch Deprived",           "📵", "Sono passate più di 36 ore senza alcuna sessione.", "Daily Life"),
    Achievement(45, "All Angles Covered",       "📐", "Hai orientato il cubo in tutte le 6 posizioni possibili.", "Daily Life"),
    Achievement(46, "Marathon Mood",            "🏃", "Hai raggiunto una striscia di 10 vittorie consecutive.", "Daily Life"),
]

ACHIEVEMENTS_COUNT = len(ACHIEVEMENTS)

CATEGORY_ORDER = [
    "Evolution", "Collection", "Legends", "Productivity",
    "Care", "Battles", "Connectivity", "Daily Life",
]


def is_unlocked(mask: int, achv_id: int) -> bool:
    return bool(mask & (1 << achv_id))


def unlocked_count(mask: int) -> int:
    return bin(mask & ((1 << ACHIEVEMENTS_COUNT) - 1)).count("1")


def load_cached_mask() -> int:
    """Legge l'ultima bitmask achievement salvata su disco (0 se assente)."""
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return int(data.get("mask", 0))
    except (FileNotFoundError, ValueError, OSError, json.JSONDecodeError):
        return 0


def save_cached_mask(mask: int) -> None:
    """Salva la bitmask achievement su disco per uso offline."""
    try:
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump({"mask": mask}, f)
    except OSError as e:
        logger.warning("Impossibile salvare la cache achievement: %s", e)
