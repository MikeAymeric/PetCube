"""
valhalla_online.py
Battaglie online del Valhalla tramite MQTT (broker pubblico, nessun account richiesto).
Richiede: pip install paho-mqtt

Struttura topic MQTT (prefisso petcube/valhalla/):
  inbox/{username_safe}      → sfida diretta in arrivo per un utente
  queue                      → matchmaking casuale (chiunque può rispondere)
  result/{challenge_id}      → risultato battaglia (retained, qos=1)
"""
import json
import logging
import time
import uuid
from typing import Callable, Optional

from valhalla import ValhallaEntry

log = logging.getLogger(__name__)

DEFAULT_BROKER = "broker.hivemq.com"
DEFAULT_PORT   = 1883
_PREFIX        = "petcube/valhalla"


# ── Battle resolution ─────────────────────────────────────────────────────────

def resolve_battle(attacker: ValhallaEntry, defender: ValhallaEntry) -> dict:
    """Calcolo automatico della battaglia. Restituisce un dict con il risultato."""
    import random
    a_atk = attacker.stat_str * 0.5 + attacker.stat_eng * 0.25 + attacker.stat_int * 0.25
    d_atk = defender.stat_str * 0.5 + defender.stat_eng * 0.25 + defender.stat_int * 0.25
    a_def = attacker.stat_eng * 0.4  + attacker.stat_str * 0.35 + attacker.stat_int * 0.25
    d_def = defender.stat_eng * 0.4  + defender.stat_str * 0.35 + defender.stat_int * 0.25
    a_hp  = 60.0 + attacker.evo_stage * 12 + attacker.stat_hap * 0.4
    d_hp  = 60.0 + defender.evo_stage * 12 + defender.stat_hap * 0.4

    rng  = random.Random()
    turn = 0
    while a_hp > 0 and d_hp > 0 and turn < 25:
        d_hp -= max(1.0, a_atk - d_def * 0.45) * rng.uniform(0.85, 1.15)
        a_hp -= max(1.0, d_atk - a_def * 0.45) * rng.uniform(0.85, 1.15)
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


# ── MQTT helpers ──────────────────────────────────────────────────────────────

def _safe_key(s: str) -> str:
    """Sanitizza username per topic MQTT: vieta +  #  /  spazio."""
    for ch in "+#/ \\":
        s = s.replace(ch, "_")
    return s


# ── Client ────────────────────────────────────────────────────────────────────

class ValhallaBattleClient:
    def __init__(
        self,
        broker: str,
        port: int,
        username: str,
        on_challenge: Optional[Callable[[str, str, dict], None]] = None,
    ):
        """
        broker      : hostname del broker MQTT (default broker.hivemq.com)
        port        : porta TCP (default 1883)
        username    : tag del giocatore locale (es. "Mario#1234")
        on_challenge: callback(challenger_username, challenge_id, creature_dict)
        """
        self.broker       = broker
        self.port         = port
        self.username     = username
        self.on_challenge = on_challenge
        self._client      = None
        self._connected   = False
        self._seen:    set[str]              = set()   # challenge_id già visti
        self._result_cbs: dict[str, Callable] = {}     # cid → callback risultato

    # ── Connessione ───────────────────────────────────────────────

    def _ensure_client(self):
        """Crea e connette il client MQTT al bisogno. Ritorna il client o None."""
        if self._client and self._connected:
            return self._client
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            log.error("paho-mqtt non installato. Usa 'Installa paho-mqtt' in Impostazioni → Valhalla.")
            return None

        cid = f"petcube_{_safe_key(self.username)}_{uuid.uuid4().hex[:6]}"
        c = mqtt.Client(client_id=cid)
        c.on_connect    = self._on_connect
        c.on_message    = self._on_message
        c.on_disconnect = self._on_disconnect
        try:
            c.connect(self.broker, self.port, keepalive=60)
            c.loop_start()
            # Aspetta connessione max 5s
            deadline = time.time() + 5
            while not self._connected and time.time() < deadline:
                time.sleep(0.1)
        except Exception as e:
            log.warning("MQTT connect %s:%d fallito: %s", self.broker, self.port, e)
            return None
        self._client = c
        return c

    def _on_connect(self, client, userdata, flags, rc):
        if rc != 0:
            log.warning("MQTT: connessione rifiutata (rc=%d)", rc)
            return
        self._connected = True
        safe = _safe_key(self.username)
        client.subscribe(f"{_PREFIX}/inbox/{safe}", qos=1)
        client.subscribe(f"{_PREFIX}/queue",         qos=0)
        log.info("MQTT: connesso a %s, iscritto inbox/%s + queue", self.broker, safe)

    def _on_disconnect(self, client, userdata, rc):
        self._connected = False

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
        except Exception:
            return
        t = msg.topic
        if t == f"{_PREFIX}/queue" or t.startswith(f"{_PREFIX}/inbox/"):
            from_queue = (t == f"{_PREFIX}/queue")
            self._handle_incoming(payload, from_queue)
        elif t.startswith(f"{_PREFIX}/result/"):
            cid = t.rsplit("/", 1)[-1]
            cb  = self._result_cbs.pop(cid, None)
            if cb:
                cb(payload)

    def _handle_incoming(self, payload: dict, from_queue: bool) -> None:
        cid        = payload.get("id", "")
        challenger = payload.get("from", "")
        creature   = payload.get("creature", {})
        # Scarta messaggi propri, vuoti o già visti
        if not cid or challenger == self.username or cid in self._seen:
            return
        self._seen.add(cid)
        if self.on_challenge:
            self.on_challenge(challenger, cid, creature)

    # ── API pubblica ──────────────────────────────────────────────

    def send_challenge(self, target: str, creature: ValhallaEntry) -> Optional[str]:
        """Invia una sfida diretta a 'target'. Ritorna il challenge_id o None."""
        c = self._ensure_client()
        if not c:
            return None
        cid     = uuid.uuid4().hex[:8]
        payload = json.dumps({"id": cid, "from": self.username,
                               "creature": creature.to_dict(), "timestamp": time.time()})
        res = c.publish(f"{_PREFIX}/inbox/{_safe_key(target)}", payload, qos=1)
        if res.rc == 0:
            log.info("MQTT: sfida inviata a '%s' (id=%s)", target, cid)
            return cid
        log.warning("MQTT: invio sfida fallito (rc=%d)", res.rc)
        return None

    def send_random_challenge(self, creature: ValhallaEntry) -> Optional[str]:
        """Pubblica in coda di matchmaking. Ritorna il challenge_id o None."""
        c = self._ensure_client()
        if not c:
            return None
        cid     = uuid.uuid4().hex[:8]
        payload = json.dumps({"id": cid, "from": self.username,
                               "creature": creature.to_dict(), "timestamp": time.time()})
        res = c.publish(f"{_PREFIX}/queue", payload, qos=0)
        if res.rc == 0:
            log.info("MQTT: sfida casuale in coda (id=%s)", cid)
            return cid
        return None

    def accept_challenge(
        self,
        challenge_id: str,
        my_creature: ValhallaEntry,
        challenger_creature_dict: dict,   # già ricevuto nel messaggio MQTT
    ) -> Optional[dict]:
        """Risolve la battaglia e pubblica il risultato sul topic result."""
        c = self._ensure_client()
        if not c:
            return None
        try:
            challenger = ValhallaEntry.from_dict(challenger_creature_dict)
        except Exception as e:
            log.warning("MQTT: dati creatura sfidante non validi: %s", e)
            return None
        result = resolve_battle(my_creature, challenger)
        c.publish(f"{_PREFIX}/result/{challenge_id}",
                  json.dumps(result), qos=1, retain=True)
        log.info("MQTT: risultato pubblicato per sfida %s", challenge_id)
        return result

    def reject_challenge(self, challenge_id: str) -> None:
        """Notifica il rifiuto della sfida."""
        c = self._ensure_client()
        if not c:
            return
        c.publish(f"{_PREFIX}/result/{challenge_id}",
                  json.dumps({"rejected": True, "id": challenge_id}), qos=1, retain=True)

    def subscribe_result(self, challenge_id: str, callback: Callable) -> None:
        """Iscriviti al topic risultato per ricevere l'esito di una sfida inviata."""
        c = self._ensure_client()
        if not c:
            return
        self._result_cbs[challenge_id] = callback
        c.subscribe(f"{_PREFIX}/result/{challenge_id}", qos=1)

    def start_polling(self, interval_sec: float = 15.0) -> None:
        """Connette il client e iscrive ai topic (loop MQTT già in daemon thread)."""
        self._ensure_client()

    def stop_polling(self) -> None:
        """Disconnette il client MQTT."""
        if self._client:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception:
                pass
            self._client    = None
            self._connected = False
        log.info("MQTT: client fermato")
