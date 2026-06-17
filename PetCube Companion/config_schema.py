"""
config_schema.py
Schema condiviso di config.json: definizione campi plugin, valori di
default per un nuovo config e helper di conversione valore <-> stringa.
Usato sia da gui.py (tab Impostazioni) sia da setup_wizard.py.
"""
import copy
import random

# (key, label, field_type)
# field_type: "text" | "password" | "int" | "int_nullable" | "list_int" | "list_str"
PLUGIN_FIELDS: dict[str, list[tuple[str, str, str]]] = {
    "calendar": [
        ("poll_interval_sec",  "Polling (sec)",       "int"),
        ("lookahead_minutes",  "Preavviso (min)",      "int"),
        ("credentials_file",   "File credenziali",    "text"),
    ],
    "discord": [
        ("bot_token",          "Bot Token",            "password"),
        ("user_id",            "User ID (o vuoto)",    "int_nullable"),
        ("poll_interval_sec",  "Polling (sec)",        "int"),
        ("monitor_channel_ids","Channel IDs (virgola)","list_int"),
    ],
    "gmail": [
        ("poll_interval_sec",  "Polling (sec)",        "int"),
        ("credentials_file",   "File credenziali",    "text"),
        ("login_hint",         "Login hint (email)",  "text"),
    ],
    "hacknplan": [
        ("poll_interval_sec",  "Polling (sec)",        "int"),
        ("lookahead_hours",    "Preavviso (ore)",      "int"),
        ("api_key",            "API Key",              "password"),
        ("target_user_id",     "Target User ID (o vuoto)", "int_nullable"),
    ],
    "telegram": [
        ("api_id",            "API ID (my.telegram.org)",  "int"),
        ("api_hash",          "API Hash",                  "password"),
        ("phone_number",      "Numero di telefono",        "text"),
        ("session_file",      "File sessione",             "text"),
        ("poll_interval_sec", "Polling (sec)",             "int"),
        ("monitor_chat_ids",  "Chat IDs extra (virgola)",  "list_int"),
    ],
    "whatsapp": [
        ("session_dir",       "Dir sessione browser",                     "text"),
        ("poll_interval_sec", "Polling (sec)",                            "int"),
        ("monitor_chats",     "Chat da monitorare (virgola, vuoto=tutte)", "list_str"),
    ],
    "slack":   [],
    "github":  [],
    "trello":  [],
}

PLUGIN_DISPLAY_NAME = {
    "calendar":  "Calendar",
    "discord":   "Discord",
    "gmail":     "Gmail",
    "hacknplan": "HacknPlan",
    "slack":     "Slack",
    "github":    "GitHub",
    "trello":    "Trello",
    "telegram":  "Telegram",
    "whatsapp":  "WhatsApp",
}

# Breve descrizione mostrata nel wizard per aiutare l'utente a capire
# cosa serve per attivare ogni plugin.
PLUGIN_HELP = {
    "calendar":  "Notifiche dagli eventi imminenti di Google Calendar. Richiede credentials.json (OAuth Google).",
    "discord":   "Notifiche dai messaggi di un bot Discord. Richiede un Bot Token.",
    "gmail":     "Notifiche dalle email non lette in arrivo. Richiede credentials.json (OAuth Google).",
    "hacknplan": "Notifiche dai work item HacknPlan in scadenza. Richiede una API Key.",
    "telegram":  "Notifiche dai messaggi Telegram. Richiede API ID/Hash da my.telegram.org e numero di telefono.",
    "whatsapp":  "Notifiche dai messaggi WhatsApp Web (sessione browser).",
    "slack":     "Notifiche da Slack.",
    "github":    "Notifiche da GitHub.",
    "trello":    "Notifiche da Trello.",
}

# Ordine di visualizzazione dei plugin nelle UI
PLUGIN_ORDER = [
    "calendar", "discord", "gmail", "hacknplan",
    "telegram", "whatsapp",
    "slack", "github", "trello",
]


def value_to_str(val, field_type: str) -> str:
    if val is None:
        return ""
    if field_type in ("list_int", "list_str"):
        if isinstance(val, list):
            return ", ".join(str(v) for v in val)
        return str(val)
    return str(val)


def parse_field_value(raw: str, field_type: str):
    raw = raw.strip()
    if field_type == "int":
        try:
            return int(raw)
        except ValueError:
            return 0
    if field_type == "int_nullable":
        if not raw:
            return None
        try:
            return int(raw)
        except ValueError:
            return None
    if field_type == "list_int":
        if not raw:
            return []
        result = []
        for part in raw.split(","):
            part = part.strip()
            if part:
                try:
                    result.append(int(part))
                except ValueError:
                    pass
        return result
    if field_type == "list_str":
        if not raw:
            return []
        return [part.strip().strip("\"'") for part in raw.split(",")
                if part.strip().strip("\"'")]
    # text / password
    return raw


# Default per ogni plugin quando non ancora presente in config.json.
_PLUGIN_DEFAULTS: dict[str, dict] = {
    "calendar": {
        "enabled": False, "poll_interval_sec": 60, "lookahead_minutes": 15,
        "credentials_file": "credentials.json", "exclude_calendars": [],
    },
    "discord": {
        "enabled": False, "bot_token": "", "user_id": None,
        "poll_interval_sec": 30, "monitor_channel_ids": [],
    },
    "gmail": {
        "enabled": False, "poll_interval_sec": 600,
        "credentials_file": "credentials.json", "login_hint": "", "max_recent": 20,
    },
    "hacknplan": {
        "enabled": False, "poll_interval_sec": 7200, "lookahead_hours": 48,
        "api_key": "", "target_user_id": None,
    },
    "telegram": {
        "enabled": False, "api_id": 0, "api_hash": "", "phone_number": "",
        "session_file": "telegram_session", "poll_interval_sec": 30,
        "monitor_chat_ids": [],
    },
    "whatsapp": {
        "enabled": False, "session_dir": "whatsapp_session",
        "poll_interval_sec": 30, "monitor_chats": [],
    },
    "slack":  {"enabled": False},
    "github": {"enabled": False},
    "trello": {"enabled": False},
}


def generate_device_id() -> str:
    """Genera un ID numerico a 5 cifre per il tag univoco del dispositivo (#12345)."""
    return f"{random.randint(0, 99999):05d}"


def device_tag(username: str, device_id: str) -> str:
    """Combina username e ID nel formato 'username#12345' usato in modalità multiplayer."""
    username = (username or "").strip() or "PetCube"
    device_id = device_id or generate_device_id()
    return f"{username}#{device_id}"


def default_config() -> dict:
    """Ritorna un nuovo dict di config.json con valori di default ragionevoli."""
    return {
        "device": {
            "ble_name": "PetCube",
            "ble_service_uuid": "12345678-1234-5678-1234-56789abcdef0",
            "ble_char_uuid": "12345678-1234-5678-1234-56789abcdef1",
            "username": "",
            "device_id": "",
            "wifi_fallback_url": "",
        },
        "plugins": copy.deepcopy(_PLUGIN_DEFAULTS),
        "transport": {
            "prefer": "ble",
            "ble_scan_timeout_sec": 10,
        },
        "logging": {
            "level": "INFO",
        },
        "firmware": {
            "github_owner": "MikeAymeric",
            "github_repo": "PetCube",
        },
        "valhalla": {
            "mqtt_broker": "broker.hivemq.com",
            "mqtt_port": 1883,
        },
    }
