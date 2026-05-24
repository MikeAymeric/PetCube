# PetCube Companion

> Desktop bridge between your real notifications and the PetCube hardware.

The companion app polls a set of external sources (Google Calendar, Gmail, HacknPlan) at independent intervals, classifies each new event with an Italian sentiment analyzer, packages it into a compact notification packet, and sends it to the cube via Bluetooth Low Energy (BLE) GATT. The cube then turns the notification into an in-game encounter the player can choose to fight.

This README covers the architecture, plugin contract, packet format, configuration, and debugging tips. For the high-level project overview, see the [repository README](../README.md).

---

## Table of contents

1. [Architecture](#architecture)
2. [File layout](#file-layout)
3. [Install](#install)
4. [Configuration](#configuration)
5. [Running](#running)
6. [Plugin contract](#plugin-contract)
7. [Notification packet format](#notification-packet-format)
8. [Sentiment classifier](#sentiment-classifier)
9. [BLE transport](#ble-transport)
10. [GUI](#gui)
11. [Persistence](#persistence)
12. [Debugging](#debugging)
13. [Adding a new plugin](#adding-a-new-plugin)

---

## Architecture

```
            ┌─────────────────────────────────────────────────────┐
            │                  CompanionEngine                    │
            │                                                     │
            │   ┌───────────────────┐    ┌─────────────────────┐  │
 user ────► │   │  PluginManager    │ ─► │  Pending queue      │  │
 (start)    │   │  (async loops)    │    │  (asyncio.Queue)    │  │
            │   └─────┬─────────────┘    └──────────┬──────────┘  │
            │         │                              │            │
            │   ┌─────┴───────┐                ┌─────▼─────────┐  │
            │   │  Plugins    │                │  Sender loop  │  │
            │   │  (Calendar, │                │  (await       │  │
            │   │   Gmail,    │                │   sender.send)│  │
            │   │   HacknPlan)│                └─────┬─────────┘  │
            │   └─────┬───────┘                      │            │
            │         │                              │            │
            │         ▼                              ▼            │
            │   ┌──────────────┐               ┌──────────────┐   │
            │   │  Sentiment   │               │  BLE sender  │   │
            │   │  (spaCy IT)  │               │  (bleak)     │   │
            │   └──────────────┘               └──────┬───────┘   │
            └─────────────────────────────────────────┼───────────┘
                                                      │
                                                      ▼
                                              ┌───────────────┐
                                              │   PetCube     │
                                              │   (GATT char) │
                                              └───────────────┘
```

The engine runs in its own thread with a dedicated asyncio loop. Plugins poll their sources on independent intervals; when a new event is detected, the plugin builds a `NotifPacket` (after classifying it through the sentiment module) and pushes it into the pending queue. A separate sender coroutine consumes the queue and writes each packet over BLE.

Two entry points share the same engine:
- **`main.py`** — CLI, runs the engine and logs to stdout. Headless, scriptable.
- **`gui.py`** — CustomTkinter dashboard that controls the engine, shows live logs, plugin status, and history of sent notifications.

---

## File layout

```
companion/
├── main.py                  # CLI entry point
├── gui.py                   # GUI entry point (CustomTkinter + tray)
├── companion_engine.py      # Engine API (start/stop, listeners, status)
├── plugin_manager.py        # Plugin lifecycle, polling loops, dispatch
├── ble_sender.py            # BLE GATT client (bleak)
├── notification_packet.py   # Packet schema (source/priority/category enums)
├── sentiment.py             # Italian text classifier (spaCy + keyword rules)
├── plugins/
│   ├── base.py              # Plugin base class, seen_ids persistence
│   ├── calendar_plugin.py
│   ├── gmail_plugin.py
│   └── hacknplan_plugin.py
├── history/                 # Per-plugin seen-ID stores (gitignored)
├── config.json              # User config (gitignored)
├── config.example.json      # Template with placeholders
├── credentials.json         # Google OAuth client (gitignored)
├── token.json               # Google OAuth refresh token (gitignored)
└── requirements.txt
```

---

## Install

### Prerequisites
- Python 3.11 or newer (tested with 3.11–3.14 on Windows 10/11)
- A working PC Bluetooth adapter (USB BT dongles work too)
- Google Cloud project with OAuth credentials (Calendar + Gmail APIs enabled)
- Optional: HacknPlan account with API token

### Steps

```powershell
cd companion
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
python -m spacy download it_core_news_sm
```

On Linux/macOS, replace the `venv\Scripts\activate` line with `source venv/bin/activate`.

### Dependencies

| Package | Purpose |
|---|---|
| `bleak` | Cross-platform BLE client |
| `google-api-python-client`, `google-auth-oauthlib` | Calendar + Gmail API |
| `requests` | HacknPlan REST + WiFi fallback (planned) |
| `spacy` + `it_core_news_sm` | Italian NLP for sentiment categorization |
| `python-dateutil` | ISO date parsing |
| `customtkinter` | GUI framework (optional, only for `gui.py`) |
| `pystray`, `pillow` | System tray icon (optional, only for `gui.py`) |

If you only want the CLI, the GUI dependencies (`customtkinter`, `pystray`, `pillow`) can be skipped.

---

## Configuration

Copy `config.example.json` to `config.json` and edit it.

### Transport

```json
"transport": {
  "prefer": "ble",
  "ble_device_name": "PetCube",
  "ble_scan_timeout": 10
}
```

| Field | Values | Meaning |
|---|---|---|
| `prefer` | `"ble"` / `"mock"` / `"wifi"` | `mock` logs packets without sending — useful for testing without the cube |
| `ble_device_name` | string | Name advertised by the cube's GATT server |
| `ble_scan_timeout` | seconds | How long `bleak` scans before giving up |

### Plugin block

Every plugin has the same skeleton in `config.json`:

```json
"plugins": {
  "<plugin_name>": {
    "enabled": true,
    "poll_interval_sec": 60,
    "...plugin-specific keys..."
  }
}
```

#### Calendar (`calendar`)

| Key | Default | Notes |
|---|---|---|
| `poll_interval_sec` | 60 | Poll every minute |
| `lookahead_minutes` | 15 | Only fire on events starting within this window |
| `exclude_calendars` | `[]` | Substrings to exclude from calendar IDs |

Excluded by default: `*@holiday.calendar.google.com`, `*@group.v.calendar.google.com`, `*@import.calendar.google.com`, `addressbook.google.com`, `#contacts@`.

#### Gmail (`gmail`)

| Key | Default | Notes |
|---|---|---|
| `poll_interval_sec` | 600 | 10 minutes — Gmail API has quotas |
| `login_hint` | none | Pre-fills the OAuth screen with this account |
| `max_recent` | 20 | Cap on number of `UNREAD` to process per poll |

Filters: ignores `category:promotions/social/updates/forums`, skips mails with `List-Unsubscribe` or `Precedence: bulk/list` headers, and only fires when you are in `To` or `Cc` (not BCC or as part of a mailing list).

#### HacknPlan (`hacknplan`)

| Key | Default | Notes |
|---|---|---|
| `poll_interval_sec` | 7200 | 2 hours |
| `lookahead_hours` | 48 | Only work items with `dueDate` within this window |
| `api_key` | empty | Generate at [HacknPlan account → API](https://app.hacknplan.com/) |
| `target_user_id` | `null` | Optional override of the user-ID derived from the API key (useful if you have multiple accounts) |

Filters: only work items assigned to me, not in `stage.status: completed/done/finished`, not `isStory: true` (user stories are containers).

---

## Running

### CLI

```powershell
python main.py
```

Logs are written to stdout. Press `Ctrl+C` to stop (the engine shuts down plugins gracefully and persists `seen_ids` before exiting).

### GUI

```powershell
python gui.py
```

A dark-themed window opens with:
- A start/stop button
- A sidebar showing plugin status (green dot = active), transport mode, and statistics (sent, failed, uptime)
- A live log pane with color-coded log levels
- A scrollable history of sent notifications (✓ green = sent, ✗ red = failed)

Closing the X button minimizes to system tray. Right-click the tray icon for **Show** / **Quit**.

---

## Plugin contract

Every plugin subclasses `plugins.base.Plugin`:

```python
from plugins.base import Plugin
from notification_packet import RawEvent, NotifSource, NotifPriority

class MyPlugin(Plugin):
    name = "my_plugin"

    def __init__(self, config: dict):
        super().__init__(config)
        # plugin-specific init

    def poll(self) -> list[RawEvent]:
        """Called every poll_interval_sec by the plugin manager.
        Return a list of new RawEvent objects (deduplication is handled
        automatically via self.seen_ids).
        """
        events = []
        for item in self._fetch_from_source():
            external_id = str(item["id"])
            if external_id in self.seen_ids:
                continue
            self.seen_ids.add(external_id)
            events.append(RawEvent(
                source=NotifSource.GENERIC,
                priority=NotifPriority.NORMAL,
                text=item["title"],
                external_id=external_id,
            ))
        return events
```

### `RawEvent` fields

| Field | Type | Required | Notes |
|---|---|---|---|
| `source` | `NotifSource` enum | yes | Maps to the icon shown on the cube |
| `priority` | `NotifPriority` enum | yes | `LOW` / `NORMAL` / `HIGH` — affects battle difficulty |
| `text` | str | yes | Max 50 chars; longer values are truncated |
| `external_id` | str | yes | Used for dedup; must be unique within the plugin's lifetime |

The plugin manager calls the sentiment classifier on `text`, builds the final `NotifPacket`, and pushes it to the queue. The plugin does not see the sentiment categorization itself.

---

## Notification packet format

`NotifPacket` is serialized to a fixed-size byte payload before BLE write:

```
Offset  Length  Field         Meaning
─────────────────────────────────────────────────────────
0       1       version       Protocol version (currently 1)
1       1       source        NotifSource enum value
2       1       priority      NotifPriority enum value
3       1       category      NotifCategory (sentiment output)
4       4       hash          uint32 deterministic hash of seed
8       1       seed_length   Length of seed string in bytes
9       1       flags         Reserved
10      2       timestamp_lo  Lower 16 bits of unix epoch
12      2       reserved      Reserved for future use
14      N       seed          UTF-8 bytes, up to 50 chars
```

Total: 14 bytes header + up to 50 bytes seed = max 64 bytes (fits in a single BLE GATT write under the default 247-byte MTU).

The cube uses `hash` to deterministically seed the random number generator that picks the enemy, so the same notification always produces the same encounter.

---

## Sentiment classifier

`sentiment.py` categorizes each `text` into one of eight categories:

| Category | Italian label | Trigger examples |
|---|---|---|
| `LODE` | Lode | "ottimo lavoro", "complimenti", "grazie" |
| `OPPORTUNITA` | Opportunità | "nuova offerta", "promozione", "invito" |
| `ROUTINE` | Routine | default fallback for neutral text |
| `CURIOSITA` | Curiosità | "novità", "scoperta", "informazione" |
| `SCADENZA` | Scadenza | "entro", "deadline", "scade", "domani" |
| `CRITICA` | Critica | "problema", "errore", "fallimento" |
| `AIUTO` | Aiuto | "help", "aiuto", "supporto" |
| `CRISI` | Crisi | "urgente", "critico", "emergenza", "bug bloccante" |

Implementation uses spaCy's Italian model (`it_core_news_sm`) for lemmatization, plus keyword rules with fallback to the original token form (the Italian lemmatizer mangles some English words like "help" → "elpvere", so both forms are checked).

To re-tune, edit `CATEGORY_KEYWORDS` in `sentiment.py`. Each category is a dict of `keyword: weight`; the highest-scoring category wins. Ties fall back to `ROUTINE`.

---

## BLE transport

`ble_sender.py` uses [bleak](https://github.com/hbldh/bleak) for cross-platform BLE.

### Protocol
- The cube advertises a GATT service when in `STATE_IDLE`. Advertising stops in other states (session, sleep, battle, menu).
- Service UUID: `12345678-1234-5678-1234-56789abcdef0`
- Characteristic UUID (write-without-response): `12345678-1234-5678-1234-56789abcdef1`

### Send flow
1. `Sender.send(pkt)` scans for the configured device name with a 10 s timeout.
2. On match, connects, writes the serialized packet, disconnects.
3. Returns `True` on success, `False` on any failure (timeout, write error, etc.).

### Common failure modes
- **`Device 'PetCube' not found`**: cube is not in idle (e.g. in a session or sleep), powered off, or out of BLE range.
- **`bleak.exc.BleakError: Could not connect`**: another client is already paired with the cube. Disconnect it first.
- **Random disconnects mid-write**: usually a power issue on the cube side. Make sure it's well-powered.

---

## GUI

The GUI is built with CustomTkinter and is **optional** — the engine runs identically without it.

### Architecture
- The GUI runs on the main thread (Tkinter requirement).
- The engine runs in a background thread.
- Cross-thread communication uses `queue.Queue` + `root.after(100ms)` polling (Tkinter-safe pattern). Never call Tkinter methods directly from the engine thread.

### Status updates
The engine exposes `get_status() -> EngineStatus`. The GUI polls it every 500 ms and updates the dashboard.

### Tray
Tray icon uses [pystray](https://github.com/moses-palmer/pystray), which spins up its own GIL-free thread. Quit propagates back via `self.after(0, self._real_quit)` to avoid Tkinter threading issues.

---

## Persistence

### Seen-IDs

Each plugin maintains a `seen_ids` set (FIFO, capped at 5000) persisted to `history/<plugin_name>.json`. This ensures the same notification is not re-sent if the companion restarts.

The persistence is atomic: writes go to a `.tmp` file then `os.replace()` swaps it in.

To reset a plugin's history (e.g. for end-to-end testing):

```powershell
del history\<plugin_name>.json
```

Or for everything:

```powershell
del history\*.json
```

### Google OAuth tokens

`token.json` stores the OAuth refresh token. If revoked (e.g. password change), delete it and the next run will re-prompt the browser flow.

---

## Debugging

### Increase log verbosity

Set the root logger to DEBUG in `main.py`:

```python
logging.basicConfig(level=logging.DEBUG, ...)
```

### Inspect a notification packet without sending it

Set `transport.prefer` to `"mock"` in `config.json`:

```json
"transport": { "prefer": "mock" }
```

The companion will print each packet to stdout instead of writing it over BLE. Useful when the cube is unavailable or to verify plugin output.

### Send a fake notification

A future GUI step will add test-console buttons. For now, you can hand-craft a `NotifPacket` and feed it into the engine:

```python
from notification_packet import NotifPacket, NotifSource, NotifPriority, NotifCategory
pkt = NotifPacket(
    source=NotifSource.GMAIL,
    priority=NotifPriority.HIGH,
    category=NotifCategory.CRISI,
    seed_preview="Test bug critico in produzione",
)
engine._pending_queue.put_nowait(pkt)
```

### Common log patterns

| Message | Meaning |
|---|---|
| `📋 Progetti HacknPlan attivi: N` | HacknPlan plugin successfully fetched projects |
| `📋 Work item imminente: ...` | A HacknPlan work item is going to be dispatched |
| `📧 Gmail API ready (account: ...)` | OAuth succeeded |
| `📆 Calendari attivi: N` | Calendar plugin found N calendars |
| `📦 NotifPacket pronto: ...` | A packet has been built and queued |
| `[BLE SEND] ...` | Packet successfully written to the cube |
| `Device 'PetCube' not found.` | Cube not discoverable — check it is in idle |

---

## Adding a new plugin

1. Create `plugins/my_plugin.py`, subclass `Plugin`, implement `poll() -> list[RawEvent]`.
2. Register it in `plugin_manager.py`'s plugin registry:

   ```python
   from plugins.my_plugin import MyPlugin
   PLUGIN_REGISTRY = {
       "calendar": CalendarPlugin,
       "gmail": GmailPlugin,
       "hacknplan": HacknPlanPlugin,
       "my_plugin": MyPlugin,   # ← here
   }
   ```

3. Add a section under `plugins` in `config.json`:

   ```json
   "my_plugin": {
     "enabled": true,
     "poll_interval_sec": 300
   }
   ```

4. Choose a `NotifSource` for your events. If you want a new dedicated source rather than reusing an existing one (e.g. `TRELLO` is reused by HacknPlan), you'll also need to:
   - Add a new value to the `NotifSource` enum in `notification_packet.py`
   - Add a matching `SRC_*` constant in the firmware (`PetCube.ino`)
   - Provide a 12×12 XBM icon in `petcube_sprites.h` and add a case in the icon switch of `drawMainScreen()`
   - Reflash the cube

If reusing an existing source is fine, skip the firmware changes.

---

## License

CC BY-NC-SA 4.0 — see the [repository README](../README.md#license) for details.
