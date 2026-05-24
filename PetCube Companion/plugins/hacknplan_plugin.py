"""
plugins/hacknplan_plugin.py
Polling di HacknPlan per work items assegnati a me con due date imminente.

Filtri applicati:
  1. Assignee = me (recuperato dall'endpoint /users/me al boot)
  2. dueDate compresa tra now e now + 24h
  3. Stage NON completato (esclude work items già "Done")

Polling: default ogni 2 ore (rate limit HacknPlan: 5 calls/sec, abbondante)

Setup richiesto:
  1. https://app.hacknplan.com → login
  2. Avatar → My Account → API
  3. Generate new API key, copiala in config.json
"""
import datetime
import logging
from typing import Optional

import requests

from plugins.base import Plugin, RawEvent
from notification_packet import NotifSource, NotifPriority


logger = logging.getLogger(__name__)

API_BASE = "https://api.hacknplan.com/v0"

# Mapping priority HacknPlan → NotifPriority PetCube
# HacknPlan usa "importance" per le priority (1=Low, 2=Normal, 3=High, 4=Critical)
HACKNPLAN_PRIORITY_MAP = {
    1: NotifPriority.NORMAL,  # Low
    2: NotifPriority.NORMAL,  # Normal
    3: NotifPriority.HIGH,    # High
    4: NotifPriority.HIGH,    # Critical
}

# Se mancano meno di queste ore alla due date, forziamo HIGH
URGENCY_THRESHOLD_HOURS = 6

# Quanto avanti guardare per "imminenti"
LOOKAHEAD_HOURS = 24


class HacknplanPlugin(Plugin):
    """Polls HacknPlan per work items con due date imminente."""

    @property
    def name(self) -> str:
        return "hacknplan"

    def __init__(self, config: dict):
        super().__init__(config)
        self.api_key: str = config.get("api_key", "")
        # Override opzionale: se valorizzato, il plugin filtra per questo user_id
        # invece di quello derivato dalla API key.
        self._target_user_id_override: Optional[int] = config.get("target_user_id") or None
        # Lookahead configurabile (in ore). Default 24h, ma puoi aumentare
        # se le tue scadenze sono pianificate giorni in anticipo.
        self._lookahead_hours: int = int(config.get("lookahead_hours", LOOKAHEAD_HOURS))
        self.user_id: Optional[int] = None
        self.username: str = "?"
        self._projects_cache: list[tuple[int, str]] = []
        self._projects_cache_age_polls = 0
        self._projects_refresh_every = 12
        if not self.api_key:
            logger.error("HacknPlan: api_key non configurata in config.json")
            return
        self._init_user()

    def _headers(self) -> dict:
        return {
            "Authorization": f"ApiKey {self.api_key}",
            "Accept": "application/json",
        }

    def _init_user(self) -> None:
        """Recupera il mio user_id da /users/me."""
        try:
            r = requests.get(
                f"{API_BASE}/users/me",
                headers=self._headers(),
                timeout=10,
            )
            r.raise_for_status()
            data = r.json()
            # HacknPlan usa "id" come field name
            self.user_id = data.get("id") or data.get("userId")
            self.username = data.get("username") or data.get("name") or data.get("displayName") or "?"
            logger.info(f"📋 HacknPlan API pronta (utente: {self.username}, id={self.user_id})")
            # Se è impostato un override, sostituisce l'user_id usato per il filtro
            if self._target_user_id_override:
                logger.info(
                    f"📋 Override attivo: filtro work items per user_id={self._target_user_id_override} "
                    f"invece di {self.user_id}"
                )
                self.user_id = self._target_user_id_override
        except Exception as e:
            logger.error(f"HacknPlan: errore recupero profilo utente: {e}")

    def _refresh_projects(self) -> None:
        """Recupera elenco progetti del workspace."""
        endpoints_to_try = [
            f"{API_BASE}/users/me/projects",
            f"{API_BASE}/projects",
        ]

        for endpoint in endpoints_to_try:
            try:
                r = requests.get(
                    endpoint,
                    headers=self._headers(),
                    params={"offset": 0, "limit": 100},
                    timeout=10,
                )
                if r.status_code == 404:
                    continue
                r.raise_for_status()
                data = r.json()

                items = data.get("items", data) if isinstance(data, dict) else data
                if not isinstance(items, list):
                    continue

                # Field name HacknPlan è "id" (verificato da dump API)
                self._projects_cache = [
                    (p["id"], p.get("name", "(no name)"))
                    for p in items
                    if "id" in p
                ]

                if not self._projects_cache and items:
                    logger.warning(
                        f"📋 Trovati {len(items)} progetti ma nessuno con campo 'id'. "
                        f"Campi del primo: {list(items[0].keys()) if items else 'N/A'}"
                    )

                names = ", ".join(n for _, n in self._projects_cache[:5])
                tail = "..." if len(self._projects_cache) > 5 else ""
                logger.info(
                    f"📋 Progetti HacknPlan attivi: {len(self._projects_cache)} ({names}{tail})"
                )
                return
            except Exception as e:
                logger.warning(f"📋 Errore con {endpoint}: {e}")
                continue

    def poll(self) -> list[RawEvent]:
        """Cerca work items con due date imminente in tutti i progetti."""
        if not self.api_key or self.user_id is None:
            return []

        # Refresh elenco progetti periodicamente
        if not self._projects_cache or self._projects_cache_age_polls >= self._projects_refresh_every:
            self._refresh_projects()
            self._projects_cache_age_polls = 0
        self._projects_cache_age_polls += 1

        if not self._projects_cache:
            return []

        now = datetime.datetime.now(datetime.timezone.utc)
        threshold = now + datetime.timedelta(hours=self._lookahead_hours)

        all_events: list[RawEvent] = []

        for project_id, project_name in self._projects_cache:
            try:
                events = self._poll_project(project_id, project_name, now, threshold)
                all_events.extend(events)
            except Exception as e:
                # Un progetto malato non interrompe gli altri
                import traceback
                logger.warning(
                    f"HacknPlan: errore polling progetto {project_name!r}: {e}\n"
                    f"{traceback.format_exc()}"
                )

        return all_events

    def _poll_project(
        self,
        project_id: int,
        project_name: str,
        now: datetime.datetime,
        threshold: datetime.datetime,
    ) -> list[RawEvent]:
        """Polling di un singolo progetto."""
        # HacknPlan richiede SIA offset SIA limit (max 100) per la paginazione
        try:
            r = requests.get(
                f"{API_BASE}/projects/{project_id}/workitems",
                headers=self._headers(),
                params={
                    "offset": 0,
                    "limit": 100,
                },
                timeout=10,
            )
            if r.status_code != 200:
                body_preview = r.text[:300] if r.text else "(no body)"
                logger.warning(
                    f"📋 GET workitems progetto {project_id} → HTTP {r.status_code}: {body_preview}"
                )
                return []
            data = r.json()
        except Exception as e:
            logger.warning(f"HacknPlan: errore GET workitems progetto {project_id}: {e}")
            return []

        items = data.get("items", data) if isinstance(data, dict) else data
        if not isinstance(items, list):
            return []

        events: list[RawEvent] = []

        for wi in items:
            # HacknPlan usa "workItemId" come field name (verificato da dump API)
            wi_id = wi.get("workItemId") or wi.get("id")
            if wi_id is None:
                continue

            # Skip le User Stories: sono contenitori di task, raramente hanno una
            # vera dueDate utile. I task figli sono i veri target.
            if wi.get("isStory"):
                continue

            # Filtro client-side: deve essere assegnato a me
            # Field name VERO: "assignedUsers" (non "assignees")
            assigned_users = wi.get("assignedUsers") or []
            if isinstance(assigned_users, list):
                # Ogni elemento può essere un user obj diretto o un wrapper.
                # Dump tipo: {"user": {"id": 20150, "username": "..."}, ...}
                # oppure direttamente {"id": ..., "username": ...}
                assignee_ids = []
                for a in assigned_users:
                    if not isinstance(a, dict):
                        continue
                    # Prova nested user.id
                    if "user" in a and isinstance(a["user"], dict):
                        assignee_ids.append(a["user"].get("id"))
                    # Prova diretto id
                    if "id" in a:
                        assignee_ids.append(a["id"])
                    if "userId" in a:
                        assignee_ids.append(a["userId"])
            else:
                assignee_ids = []
            if self.user_id not in assignee_ids:
                continue

            # Dedup
            dedup_key = f"{project_id}::{wi_id}"
            if dedup_key in self.seen_ids:
                continue

            # Filtro 1: deve avere una due date
            due_str = wi.get("dueDate")
            if not due_str:
                continue

            # Filtro 2: due date entro la threshold
            due_dt = _parse_iso(due_str)
            if due_dt is None:
                continue
            if due_dt < now or due_dt > threshold:
                continue

            # Filtro 3: skip se già completato.
            # HacknPlan stages hanno un campo "status" con valori tipo
            # "created", "in_progress", "completed". Skip solo i "completed".
            stage = wi.get("stage") or {}
            if isinstance(stage, dict):
                stage_status = (stage.get("status") or "").lower()
                if stage_status in ("completed", "done", "finished"):
                    continue

            # Costruisci testo per sentiment: "Progetto — Titolo"
            title = (wi.get("title") or "(no title)").strip()
            if len(title) > 50:
                title = title[:47] + "..."
            text = f"{project_name} — {title}"

            # Priority: campo VERO è "importanceLevel" che in HacknPlan è un object
            # tipo {"importanceLevelId": 3, "name": "High", ...}, non un intero diretto.
            importance_raw = wi.get("importanceLevel")
            importance_id = None
            if isinstance(importance_raw, dict):
                importance_id = (
                    importance_raw.get("importanceLevelId")
                    or importance_raw.get("id")
                    or importance_raw.get("level")
                )
            elif isinstance(importance_raw, int):
                importance_id = importance_raw
            base_priority = HACKNPLAN_PRIORITY_MAP.get(importance_id or 2, NotifPriority.NORMAL)

            hours_left = (due_dt - now).total_seconds() / 3600.0
            if hours_left < URGENCY_THRESHOLD_HOURS:
                priority = NotifPriority.HIGH
            else:
                priority = base_priority

            self.seen_ids.add(dedup_key)
            events.append(RawEvent(
                source=NotifSource.TRELLO,
                priority=priority,
                text=text,
                external_id=dedup_key,
            ))
            logger.info(
                f"📋 Work item imminente: {text!r} "
                f"(due in {hours_left:.1f}h, priority={priority.name})"
            )

        return events


def _parse_iso(s: str) -> Optional[datetime.datetime]:
    """Parse ISO 8601 datetime string, ritornando un datetime aware UTC."""
    if not s:
        return None
    try:
        # Python 3.7+ accetta solo offset numerici, non "Z"
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        else:
            dt = dt.astimezone(datetime.timezone.utc)
        return dt
    except Exception:
        return None
