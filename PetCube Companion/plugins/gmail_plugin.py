"""
plugins/gmail_plugin.py
Polling di Gmail per email rilevanti.

Filtri applicati:
  1. Solo mail UNREAD in Inbox
  2. Escluse categorie Promotions, Social, Updates, Forums
  3. Skip mail con List-Unsubscribe header (newsletter)
  4. Skip mail con Precedence: bulk/list (automatiche)
  5. L'utente deve essere nei To/Cc diretti (non Bcc, non lista di distribuzione)

Sentiment input: "Mittente — Oggetto. Corpo email (troncato)"

Setup richiesto (oltre a Calendar):
  1. Google Cloud Console → APIs & Services → Library → Gmail API → Enable
  2. OAuth consent screen → Add scope: gmail.readonly
  3. Cancella token.json esistente per forzare re-auth con nuovo scope
"""
import base64
import logging
import re
import datetime
from typing import Optional

from plugins.base import Plugin, RawEvent
from notification_packet import NotifSource, NotifPriority


logger = logging.getLogger(__name__)

# Scope esteso: serve sia Calendar (già usato) sia Gmail
SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
]
TOKEN_FILE = "token.json"


def _extract_email_address(header_value: str) -> str:
    """
    Estrae 'foo@bar.com' da stringhe tipo 'Mario Rossi <foo@bar.com>' o 'foo@bar.com'.
    Ritorna la stringa in lowercase. Se ci sono virgole (multipli destinatari),
    ritorna tutti separati da virgola.
    """
    if not header_value:
        return ""
    matches = re.findall(r"[\w.+-]+@[\w.-]+\.[\w]+", header_value)
    return ",".join(m.lower() for m in matches)


def _extract_display_name(from_header: str) -> str:
    """
    Estrae il nome visualizzato dall'header From.
    Es: 'Ross Bianchi <ross@example.com>' → 'Ross Bianchi'
    Es: 'ross@example.com' → 'ross' (parte locale prima di @)
    """
    if not from_header:
        return "?"
    # Pattern: "Name <email>"
    m = re.match(r'^\s*"?([^"<]+?)"?\s*<', from_header)
    if m:
        name = m.group(1).strip()
        if name and "@" not in name:
            return name
    # Fallback: parte locale dell'email
    email_match = re.search(r"([\w.+-]+)@", from_header)
    if email_match:
        local = email_match.group(1)
        # Rendi un po' più carino: ross.bianchi → Ross Bianchi
        local = local.replace(".", " ").replace("_", " ").replace("-", " ")
        return local.title()
    return from_header.strip()[:30]


def _decode_body_data(data: str) -> str:
    """Decodifica il campo body.data (base64url) di Gmail in testo UTF-8."""
    if not data:
        return ""
    try:
        return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="ignore")
    except Exception:
        return ""


def _strip_html(html: str) -> str:
    """Rimuove tag/script/style HTML e collassa gli spazi, per usare il body HTML come testo."""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _extract_body_text(payload: dict, max_chars: int = 500) -> str:
    """
    Estrae il testo del corpo dell'email da un payload Gmail (format=full),
    cercando ricorsivamente tra le parti multipart. Preferisce text/plain,
    con fallback su text/html (tag rimossi). Troncato a max_chars.
    """
    def walk(part: dict) -> tuple[str, bool]:
        mime = part.get("mimeType", "")
        body = part.get("body", {})
        if mime == "text/plain" and body.get("data"):
            return _decode_body_data(body["data"]), True
        if mime == "text/html" and body.get("data"):
            return _strip_html(_decode_body_data(body["data"])), False
        best, best_is_plain = "", False
        for sub in part.get("parts", []) or []:
            text, is_plain = walk(sub)
            if text and (is_plain or not best_is_plain):
                best, best_is_plain = text, is_plain
                if is_plain:
                    break
        return best, best_is_plain

    text, _ = walk(payload)
    text = text.strip()
    if len(text) > max_chars:
        text = text[:max_chars] + "..."
    return text


class GmailPlugin(Plugin):
    """Polls Gmail Inbox per email UNREAD rilevanti."""

    @property
    def name(self) -> str:
        return "gmail"

    def __init__(self, config: dict):
        super().__init__(config)
        self.credentials_file = config.get("credentials_file", "credentials.json")
        # Opzionale: se valorizzato, suggerisce questo account nel popup OAuth
        # (evita di scegliere quello sbagliato se hai più sessioni Google nel browser).
        # NB: NON forza il login — l'utente può sempre scegliere un altro account.
        self.login_hint: str = config.get("login_hint", "")
        self.service = None
        self.my_email: str = ""  # Riempito da getProfile() al primo poll
        self._init_service()

    def _init_service(self) -> None:
        """Inizializza il client Gmail via OAuth (riusando lo stesso token.json)."""
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build
        except ImportError:
            logger.error("Librerie Google non installate. Eseguire: pip install -r requirements.txt")
            return

        import os
        creds: Optional[Credentials] = None

        if os.path.exists(TOKEN_FILE):
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    logger.info("Refresh del token Google in corso...")
                    creds.refresh(Request())
                except Exception as e:
                    logger.warning(f"Refresh fallito, serve nuovo OAuth: {e}")
                    creds = None
            if not creds:
                if not os.path.exists(self.credentials_file):
                    logger.error(
                        f"File {self.credentials_file} non trovato. "
                        "Crea credenziali OAuth su Google Cloud Console."
                    )
                    return
                logger.info("Avvio autenticazione OAuth (si aprirà il browser per acconsentire a Calendar + Gmail)...")
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.credentials_file, SCOPES
                )
                # Passa login_hint se configurato (forza preselezione account)
                kwargs = {}
                if self.login_hint:
                    kwargs["login_hint"] = self.login_hint
                    logger.info(f"💡 Suggerimento OAuth: usa l'account {self.login_hint}")
                creds = flow.run_local_server(port=0, **kwargs)

            with open(TOKEN_FILE, "w") as f:
                f.write(creds.to_json())
            # Restringe i permessi a sola lettura del proprietario (rw-------)
            # per proteggere il token OAuth su sistemi Unix/Linux.
            try:
                os.chmod(TOKEN_FILE, 0o600)
            except OSError:
                pass  # Windows non supporta chmod nello stesso modo; ignora.

        self.service = build("gmail", "v1", credentials=creds)
        # Recupera il mio indirizzo
        try:
            profile = self.service.users().getProfile(userId="me").execute()
            self.my_email = profile.get("emailAddress", "").lower()
            logger.info(f"📧 Gmail API pronta (account: {self.my_email})")
        except Exception as e:
            logger.warning(f"Errore recupero profilo Gmail: {e}")

    def poll(self) -> list[RawEvent]:
        """Cerca email UNREAD in Inbox che soddisfano i filtri."""
        if self.service is None or not self.my_email:
            return []

        # Query Gmail: UNREAD in Inbox, escludendo le categorie "rumorose"
        # NB: -category:X esclude la categoria; senza la "-" la include
        query = (
            "is:unread in:inbox "
            "-category:promotions -category:social "
            "-category:updates -category:forums"
        )

        try:
            result = self.service.users().messages().list(
                userId="me",
                q=query,
                maxResults=20,
            ).execute()
        except Exception as e:
            logger.warning(f"Errore Gmail list: {e}")
            return []

        messages = result.get("messages", [])
        if not messages:
            return []

        raw_events: list[RawEvent] = []

        for msg_meta in messages:
            msg_id = msg_meta["id"]
            if msg_id in self.seen_ids:
                continue

            try:
                # Fetch full (serve anche il body per il sentiment, non solo l'oggetto)
                msg = self.service.users().messages().get(
                    userId="me",
                    id=msg_id,
                    format="full",
                ).execute()
            except Exception as e:
                logger.warning(f"Errore Gmail get {msg_id}: {e}")
                continue

            payload = msg.get("payload", {})
            headers = {h["name"]: h["value"]
                       for h in payload.get("headers", [])}

            # Filtro 1: skip se ha List-Unsubscribe (quasi sempre newsletter)
            if headers.get("List-Unsubscribe"):
                self.seen_ids.add(msg_id)
                continue

            # Filtro 2: skip se Precedence è bulk/list/junk (mail automatiche)
            prec = headers.get("Precedence", "").lower()
            if prec in ("bulk", "list", "junk"):
                self.seen_ids.add(msg_id)
                continue

            # Filtro 3: l'utente deve essere in To o Cc (non solo Bcc/lista)
            to_emails = _extract_email_address(headers.get("To", ""))
            cc_emails = _extract_email_address(headers.get("Cc", ""))
            recipients = (to_emails + "," + cc_emails).lower()
            if self.my_email not in recipients:
                self.seen_ids.add(msg_id)
                continue

            # Costruisci il testo per il sentiment
            subject = headers.get("Subject", "(no subject)").strip()
            from_header = headers.get("From", "")
            sender_name = _extract_display_name(from_header)

            # Trim subject se troppo lungo
            if len(subject) > 60:
                subject = subject[:57] + "..."

            body = _extract_body_text(payload)

            text = f"{sender_name} — {subject}"
            if body:
                text += f". {body}"

            # Priority Gmail: sempre NORMAL per default. Il sentiment analyzer
            # alza a HIGH se trova keyword urgenti.
            priority = NotifPriority.NORMAL

            self.seen_ids.add(msg_id)
            raw_events.append(RawEvent(
                source=NotifSource.GMAIL,
                priority=priority,
                text=text,
                external_id=msg_id,
            ))
            logger.info(f"📧 Mail rilevante: {f'{sender_name} — {subject}'!r} ({len(text)} char totali per il sentiment)")

        return raw_events
