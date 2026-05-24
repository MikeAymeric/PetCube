"""
plugins/calendar_plugin.py
Polling di Google Calendar per eventi imminenti su TUTTI i calendari
ai quali l'utente è iscritto (primario + condivisi), escludendo i
calendari di festività e quelli sottoscritti pubblicamente.

Trigger: ogni evento il cui orario di inizio cade nei prossimi N minuti
(default 15) genera una notifica. Eventi già visti vengono saltati.

Setup richiesto:
  1. https://console.cloud.google.com → crea progetto
  2. Abilita API Google Calendar
  3. Crea credenziali OAuth 2.0 (Desktop app)
  4. Scarica credentials.json e mettilo nella cartella petcube_companion/

Al primo avvio si aprirà il browser per autenticare. Il token viene
salvato in token.json e riutilizzato nelle esecuzioni successive.
"""
import logging
import os
import datetime
from typing import Optional

from plugins.base import Plugin, RawEvent
from notification_packet import NotifSource, NotifPriority


logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
]
TOKEN_FILE = "token.json"

# Pattern per riconoscere calendari da escludere:
# - holiday.calendar.google.com → festività ufficiali (Italia, USA, ecc.)
# - group.v.calendar.google.com → calendari pubblici sottoscritti
# - addressbook.google.com → compleanni dei contatti (rumore tipicamente)
EXCLUDED_CALENDAR_ID_SUFFIXES = (
    "@holiday.calendar.google.com",
    "@group.v.calendar.google.com",
    "@import.calendar.google.com",
)
EXCLUDED_CALENDAR_ID_PATTERNS = (
    "#contacts@",
    "addressbook.google.com",
)


def _is_excluded_calendar(cal: dict) -> bool:
    """
    Determina se un calendario va escluso dal polling.
    Esclude festività, calendari pubblici sottoscritti, contatti/compleanni.
    Calendari condivisi da persone (es. team, partner) NON sono esclusi.
    """
    cal_id = cal.get("id", "").lower()
    if any(cal_id.endswith(suf) for suf in EXCLUDED_CALENDAR_ID_SUFFIXES):
        return True
    if any(p in cal_id for p in EXCLUDED_CALENDAR_ID_PATTERNS):
        return True
    return False


class CalendarPlugin(Plugin):
    """Polls TUTTI i calendari Google Calendar dell'utente (eccetto festività/pubblici)."""

    @property
    def name(self) -> str:
        return "calendar"

    def __init__(self, config: dict):
        super().__init__(config)
        self.lookahead_minutes = int(config.get("lookahead_minutes", 15))
        self.credentials_file = config.get("credentials_file", "credentials.json")
        self.service = None
        # Cache dell'elenco calendari, refreshato ogni N poll
        self._calendar_ids: list[tuple[str, str]] = []  # (id, summary)
        self._calendar_list_age_polls = 0
        self._calendar_list_refresh_every = 30  # ricarica lista ogni 30 poll (~30 min)
        self._init_service()

    def _init_service(self) -> None:
        """Inizializza il client Google Calendar via OAuth."""
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build
        except ImportError:
            logger.error("Librerie Google non installate. Eseguire: pip install -r requirements.txt")
            return

        creds: Optional[Credentials] = None

        if os.path.exists(TOKEN_FILE):
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                logger.info("Refresh del token Google in corso...")
                creds.refresh(Request())
            else:
                if not os.path.exists(self.credentials_file):
                    logger.error(
                        f"File {self.credentials_file} non trovato. "
                        "Crea credenziali OAuth su Google Cloud Console e scarica il JSON."
                    )
                    return
                logger.info("Avvio autenticazione OAuth (si aprirà il browser)...")
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.credentials_file, SCOPES
                )
                creds = flow.run_local_server(port=0)

            with open(TOKEN_FILE, "w") as f:
                f.write(creds.to_json())

        self.service = build("calendar", "v3", credentials=creds)
        logger.info("Google Calendar API pronta.")

    def _refresh_calendar_list(self) -> None:
        """Recupera l'elenco aggiornato dei calendari da pollare."""
        if self.service is None:
            return
        try:
            result = self.service.calendarList().list().execute()
            items = result.get("items", [])
            kept: list[tuple[str, str]] = []
            skipped: list[str] = []
            for cal in items:
                summary = cal.get("summary", "(no name)")
                if _is_excluded_calendar(cal):
                    skipped.append(summary)
                    continue
                kept.append((cal["id"], summary))
            self._calendar_ids = kept
            logger.info(
                f"📆 Calendari attivi: {len(kept)} "
                f"({', '.join(s for _, s in kept[:5])}{'...' if len(kept) > 5 else ''}) "
                f"— esclusi: {len(skipped)}"
            )
        except Exception as e:
            logger.warning(f"Errore refresh calendar list: {e}")

    def poll(self) -> list[RawEvent]:
        """Recupera eventi imminenti da tutti i calendari attivi."""
        if self.service is None:
            return []

        # Refresh elenco calendari periodicamente (o al primo poll)
        if not self._calendar_ids or self._calendar_list_age_polls >= self._calendar_list_refresh_every:
            self._refresh_calendar_list()
            self._calendar_list_age_polls = 0
        self._calendar_list_age_polls += 1

        if not self._calendar_ids:
            return []

        now = datetime.datetime.utcnow()
        time_min = now.isoformat() + "Z"
        time_max = (now + datetime.timedelta(minutes=self.lookahead_minutes)).isoformat() + "Z"

        all_raw_events: list[RawEvent] = []

        for cal_id, cal_summary in self._calendar_ids:
            try:
                events_result = self.service.events().list(
                    calendarId=cal_id,
                    timeMin=time_min,
                    timeMax=time_max,
                    singleEvents=True,
                    orderBy="startTime",
                    maxResults=10,
                ).execute()
                events = events_result.get("items", [])

                for event in events:
                    event_id = event.get("id")
                    if not event_id:
                        continue
                    # Dedup chiave: id-evento + id-calendario (lo stesso event_id può
                    # apparire identico tra calendari diversi se l'evento è condiviso)
                    dedup_key = f"{cal_id}::{event_id}"
                    if dedup_key in self.seen_ids:
                        continue

                    title = event.get("summary", "(no title)")
                    start_str = event["start"].get("dateTime") or event["start"].get("date")
                    priority = self._compute_priority(start_str, now)

                    self.seen_ids.add(dedup_key)
                    all_raw_events.append(RawEvent(
                        source=NotifSource.CALENDAR,
                        priority=priority,
                        text=title,
                        external_id=dedup_key,
                    ))
                    logger.info(
                        f"📅 Evento imminente: {title!r} "
                        f"(cal={cal_summary!r}, priority={priority.name})"
                    )
            except Exception as e:
                # Un calendario malato non deve interrompere il polling degli altri
                logger.warning(f"Errore polling calendario {cal_summary!r}: {e}")

        return all_raw_events

    def _compute_priority(self, start_str: str, now: datetime.datetime) -> NotifPriority:
        """Determina priority in base al lead time."""
        try:
            from dateutil import parser
            start_dt = parser.isoparse(start_str)
            # Normalizza a UTC naive per il confronto
            if start_dt.tzinfo is not None:
                start_dt = start_dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)
            delta = (start_dt - now).total_seconds() / 60.0
            if delta < 5:
                return NotifPriority.HIGH
            if delta < 15:
                return NotifPriority.NORMAL
            return NotifPriority.LOW
        except Exception:
            return NotifPriority.NORMAL
