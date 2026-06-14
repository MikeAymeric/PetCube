"""
pomodoro_history.py
Storico locale delle sessioni Pomodoro completate sul PetCube.

Il firmware (>= v30) espone via BLE un contatore lifetime (STATS,
uint32 LE) che conta le sessioni Pomodoro completate dal cubo dall'ultimo
reset achievement. La Companion non riceve un evento per ogni sessione:
ad ogni sincronizzazione (refresh achievement / "Sincronizza ora") legge
questo contatore e, se è aumentato rispetto all'ultima lettura nota,
registra la differenza come sessioni completate "oggi".

Storico salvato in pomodoro_history.json: {"YYYY-MM-DD": count, ...}
più "last_count" per calcolare i delta. Una diminuzione del contatore
(reset achievement sul cubo) viene trattata come nuovo punto di partenza,
senza sottrarre sessioni dallo storico già registrato.
"""
import json
import logging
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

HISTORY_PATH = Path(__file__).resolve().parent / "pomodoro_history.json"

# Numero di giorni mostrati nel grafico della Dashboard.
HISTORY_DAYS = 14


def _load() -> dict:
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError, OSError, json.JSONDecodeError):
        return {}


def _save(data: dict) -> None:
    try:
        with open(HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except OSError as e:
        logger.warning("Impossibile salvare lo storico Pomodoro: %s", e)


def record_session_count(total: int) -> None:
    """
    Aggiorna lo storico a partire dal contatore lifetime letto via BLE.
    Se il contatore è aumentato rispetto all'ultima lettura, accredita
    la differenza alla data odierna. Se è diminuito o non c'è una
    lettura precedente, si limita ad aggiornare il riferimento.
    """
    data = _load()
    last_total = int(data.get("last_count", -1))
    if last_total >= 0 and total > last_total:
        today = date.today().isoformat()
        delta = total - last_total
        data[today] = int(data.get(today, 0)) + delta
    data["last_count"] = total
    _save(data)


def get_recent_history(days: int = HISTORY_DAYS) -> list[tuple[str, int]]:
    """
    Ritorna le ultime `days` voci come lista di (data ISO, conteggio),
    in ordine cronologico, includendo i giorni senza sessioni (count 0).
    """
    from datetime import timedelta
    data = _load()
    today = date.today()
    result = []
    for i in range(days - 1, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        result.append((d, int(data.get(d, 0))))
    return result
