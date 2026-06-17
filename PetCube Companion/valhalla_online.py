"""
valhalla_online.py
Battaglie online del Valhalla tramite Firebase Realtime Database REST API.
Non richiede l'SDK Firebase: usa solo 'requests' (già in requirements.txt).

Struttura dati Firebase:
  /challenges/{username_safe}/{challenge_id}  → sfida in arrivo
  /queue/{challenge_id}                        → coda matchmaking casuale
  /results/{challenge_id}                      → risultato battaglia
"""
import logging
import random
import threading
import time
import uuid
from typing import Callable, Optional

import requests

from valhalla import ValhallaEntry

log = logging.getLogger(__name__)


# ── Battle resolution ─────────────────────────────────────────────────────

def resolve_battle(attacker: ValhallaEntry, defender: ValhallaEntry) -> dict:
    """Calcolo automatico della battaglia. Restituisce un dict con il risultato."""
    a_atk = attacker.stat_str * 0.5 + attacker.stat_eng * 0.25 + attacker.stat_int * 0.25
    d_atk = defender.stat_str * 0.5 + defender.stat_eng * 0.25 + defender.stat_int * 0.25
    a_def = attacker.stat_eng * 0.4  + attacker.stat_str * 0.35 + attacker.stat_int * 0.25
    d_def = defender.stat_eng * 0.4  + defender.stat_str * 0.35 + defender.stat_int * 0.25
    a_hp  = 60.0 + attacker.evo_stage * 12 + attacker.stat_hap * 0.4
    d_hp  = 60.0 + defender.evo_stage * 12 + defender.stat_hap * 0.4

    rng = random.Random()  # unseeded → truly random
    turn = 0
    while a_hp > 0 and d_hp > 0 and turn < 25:
        dmg_a = max(1.0, a_atk - d_def * 0.45) * rng.uniform(0.85, 1.15)
        dmg_d = max(1.0, d_atk - a_def * 0.45) * rng.uniform(0.85, 1.15)
        d_hp -= dmg_a
        a_hp -= dmg_d
        turn += 1

    attacker_wins = a_hp >= d_hp
    return {
        "winner":       attacker.name if attacker_wins else defender.name,
        "winner_owner": attacker.owner if attacker_wins else defender.owner,
        "loser":        defender.name if attacker_wins else attacker.name,
        "loser_owner":  defender.owner if attacker_wins else attacker.owner,
        "turns":        turn,
        "attacker_won": attacker_wins,
        "timestamp":    time.time(),
    }


# ── Firebase REST helpers ─────────────────────────────────────────────────

def _safe_key(s: str) -> str:
    """Firebase keys cannot contain . # $ [ ] /"""
    for ch in ".#$[]/@":
        s = s.replace(ch, "_")
    return s


class ValhallaBattleClient:
    def __init__(
        self,
        firebase_url: str,
        username: str,
        on_challenge: Optional[Callable[[str, str, dict], None]] = None,
    ):
        """
        firebase_url : URL base del progetto Firebase (senza trailing slash)
        username     : tag del giocatore locale (es. "Mario#1234")
        on_challenge : callback(challenger_username, challenge_id, creature_dict)
                       chiamata quando arriva una sfida
        """
        self.base_url    = firebase_url.rstrip("/")
        self.username    = username
        self.on_challenge = on_challenge
        self._stop_event  = threading.Event()
        self._poll_thread: Optional[threading.Thread] = None
        self._seen_challenges: set[str] = set()

    # ── Sfide ─────────────────────────────────────────────────────

    def send_challenge(self, target_username: str, creature: ValhallaEntry) -> Optional[str]:
        """Invia una sfida a target_username. Restituisce il challenge_id o None."""
        cid = str(uuid.uuid4())[:8]
        payload = {
            "id":          cid,
            "from":        self.username,
            "creature":    creature.to_dict(),
            "timestamp":   time.time(),
            "status":      "pending",
        }
        url = f"{self.base_url}/challenges/{_safe_key(target_username)}/{cid}.json"
        try:
            r = requests.put(url, json=payload, timeout=10)
            r.raise_for_status()
            log.info("Sfida inviata a '%s' (id=%s)", target_username, cid)
            return cid
        except Exception as e:
            log.warning("Invio sfida fallito: %s", e)
            return None

    def send_random_challenge(self, creature: ValhallaEntry) -> Optional[str]:
        """Inserisce la propria creatura in coda per matchmaking casuale."""
        cid = str(uuid.uuid4())[:8]
        payload = {
            "id":        cid,
            "from":      self.username,
            "creature":  creature.to_dict(),
            "timestamp": time.time(),
        }
        url = f"{self.base_url}/queue/{cid}.json"
        try:
            r = requests.put(url, json=payload, timeout=10)
            r.raise_for_status()
            log.info("Sfida casuale in coda (id=%s)", cid)
            return cid
        except Exception as e:
            log.warning("Sfida casuale fallita: %s", e)
            return None

    def accept_challenge(
        self, challenge_id: str, my_creature: ValhallaEntry
    ) -> Optional[dict]:
        """Accetta una sfida, calcola la battaglia e posta il risultato."""
        url = f"{self.base_url}/challenges/{_safe_key(self.username)}/{challenge_id}.json"
        try:
            r = requests.get(url, timeout=10)
            r.raise_for_status()
            challenge = r.json()
            if not challenge:
                return None
        except Exception as e:
            log.warning("Recupero sfida fallito: %s", e)
            return None

        try:
            challenger_creature = ValhallaEntry.from_dict(challenge["creature"])
        except Exception as e:
            log.warning("Dati creatura sfidante non validi: %s", e)
            return None

        result = resolve_battle(my_creature, challenger_creature)

        # Posta il risultato e cancella la sfida
        try:
            res_url = f"{self.base_url}/results/{challenge_id}.json"
            requests.put(res_url, json=result, timeout=10)
            requests.delete(url, timeout=10)
            self._seen_challenges.discard(challenge_id)
        except Exception as e:
            log.warning("Post risultato fallito: %s", e)

        return result

    def reject_challenge(self, challenge_id: str) -> None:
        """Rifiuta e cancella una sfida."""
        url = f"{self.base_url}/challenges/{_safe_key(self.username)}/{challenge_id}.json"
        try:
            requests.delete(url, timeout=10)
            self._seen_challenges.discard(challenge_id)
        except Exception as e:
            log.warning("Rifiuto sfida fallito: %s", e)

    # ── Polling ───────────────────────────────────────────────────

    def start_polling(self, interval_sec: float = 15.0) -> None:
        self._stop_event.clear()
        self._poll_thread = threading.Thread(
            target=self._poll_loop, args=(interval_sec,), daemon=True, name="valhalla-poll"
        )
        self._poll_thread.start()
        log.info("Valhalla: polling sfide avviato (ogni %.0fs)", interval_sec)

    def stop_polling(self) -> None:
        self._stop_event.set()
        log.info("Valhalla: polling sfide fermato")

    def _poll_loop(self, interval_sec: float) -> None:
        while not self._stop_event.wait(interval_sec):
            self._check_incoming_challenges()

    def _check_incoming_challenges(self) -> None:
        if not self.on_challenge or not self.username:
            return
        url = f"{self.base_url}/challenges/{_safe_key(self.username)}.json"
        try:
            r = requests.get(url, timeout=10)
            if r.status_code != 200:
                return
            data = r.json()
            if not data or not isinstance(data, dict):
                return
            for cid, challenge in data.items():
                if not isinstance(challenge, dict):
                    continue
                if cid in self._seen_challenges:
                    continue
                if challenge.get("status") == "pending":
                    self._seen_challenges.add(cid)
                    challenger = challenge.get("from", "Unknown")
                    creature   = challenge.get("creature", {})
                    self.on_challenge(challenger, cid, creature)
        except Exception as e:
            log.debug("Polling sfide fallito: %s", e)
