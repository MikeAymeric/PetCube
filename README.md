# PetCube

> A Tamagotchi-meets-Pomodoro virtual pet cube. Train, study, and work — your cube knows which, because it can feel which way it's facing.

PetCube is a handheld virtual pet device built on the XIAO ESP32-S3. You raise an original creature by completing real-life pomodoro sessions: tilt the cube **left to train**, **right to study**, **upside down to work**. A companion desktop app turns your real notifications (calendar events, emails, project deadlines) into in-game battles the pet must fight.

**Status**: alpha. Battle system, companion plugins (Calendar / Gmail / HacknPlan / Discord / Telegram / WhatsApp), an achievement system (47 achievements), BLE transport, and OTA firmware updates are operational. Hardware is hand-soldered on two Electrocookie perfboards (breadboard form factor) with portable LiPo power, housed in a 3D-printed case (final touches in progress).

---

## Table of contents

1. [Hardware](#hardware)
2. [Architecture](#architecture)
3. [Repository structure](#repository-structure)
4. [Getting started — Firmware](#getting-started--firmware)
5. [Getting started — Companion app](#getting-started--companion-app)
6. [First connection](#first-connection)
7. [Configuration reference](#configuration-reference)
8. [How the battle system works](#how-the-battle-system-works)
9. [Roadmap](#roadmap)
10. [Changelog](#changelog)
11. [Credits](#credits)
12. [License](#license)

---

## Hardware

| Component | Role | Notes |
|---|---|---|
| Seeed XIAO ESP32-S3 | MCU | Built-in BLE + WiFi, Arduino-compatible |
| GC9A01 TFT 240×240 round | Display | SPI, 3.3V logic |
| MPU6050 | Orientation sensor | I²C address `0x68` |
| 3× momentary buttons | Inputs A / B / C | Active-low, internal pull-ups |
| Passive piezo buzzer | Audio | Single GPIO, `tone()` driven |
| TP4056 USB-C module | LiPo charger | With DW01+FS8205 protection; OUT+ → XIAO BAT pad |
| LiPo 3.7V 500–1000 mAh | Battery | — |

### Wiring

| XIAO Pin | GPIO | Connected to |
|----------|------|--------------|
| **3.3V** | — | TFT VCC · MPU6050 VCC · TFT RES |
| **GND** | — | TFT GND · MPU6050 GND · TP4056 OUT− · Buzzer − · Pulsanti − |
| **D0** | GPIO1 | Buzzer + |
| **D1** | GPIO2 | TFT CS |
| **D2** | GPIO3 | TFT DC |
| **D3** | GPIO4 | Button C |
| **D4** | GPIO5 | MPU6050 SDA (I²C) |
| **D5** | GPIO6 | MPU6050 SCL (I²C) |
| **D6** | GPIO43 | TFT BLK (backlight) |
| **D7** | GPIO44 | Button B |
| **D8** | GPIO7 | TFT SCL (SPI clock) |
| **D9** | GPIO8 | Button A |
| **D10** | GPIO9 | TFT SDA (SPI MOSI) |
| **D11** | GPIO10 | TFT DC |
| **BAT+** | — | TP4056 OUT+ |
| **TFT RES** | — | 3V3 (reset software, `TFT_RST = -1`) |

> All buttons connect between the listed pin and **GND** — no external resistor needed (firmware uses `INPUT_PULLUP`).
> TFT pins labelled SDA/SCL by the manufacturer are SPI, not I²C.
> The cube charges via the TP4056 USB-C port; do **not** plug the XIAO USB-C simultaneously.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                      Companion app (PC, Python)                  │
│                                                                  │
│   ┌─────────────────────────────────────────────────────────┐   │
│   │ Calendar · Gmail · HacknPlan · Discord · Telegram ·      │   │
│   │ WhatsApp                                  (plugins)       │   │
│   └────────────────────────┬────────────────────────────────┘   │
│                      ▼                                           │
│               ┌──────────────┐                                   │
│               │  Sentiment   │  (spaCy IT — categorizes event)   │
│               │  classifier  │                                   │
│               └──────┬───────┘                                   │
│                      ▼                                           │
│               ┌──────────────┐                                   │
│               │  BLE sender  │  (bleak, GATT write)              │
│               └──────┬───────┘                                   │
└──────────────────────┼───────────────────────────────────────────┘
                       │ Notification packet (20 bytes header + seed)
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│                      PetCube firmware (XIAO)                     │
│                                                                  │
│   GATT server  →  Notification queue  →  Idle screen icon       │
│                                                                  │
│   Long-press B (5s) on icon  →  Battle vs. enemy generated       │
│   from notification seed                                         │
└─────────────────────────────────────────────────────────────────┘
```

External sources (Google Calendar, Gmail, HacknPlan REST, Discord, Telegram, WhatsApp Web) → companion polls/listens to them → each new event is wrapped in a `NotifPacket` and sent via BLE GATT to the cube. The cube shows an icon on its idle screen; long-pressing **B** starts a battle where the enemy's stats and element derive deterministically from the notification's text and source.

---

## Repository structure

```
PetCube/
├── PetCube FW/
│   └── PetCube/
│       ├── PetCube.ino              # Main firmware sketch
│       ├── petcube_sprites.h        # 28 creatures × 12 frames
│       ├── petcube_backgrounds.h    # Environment backgrounds per state
│       ├── petcube_battle.h         # Battle system (stats, enemy selection, clash logic)
│       └── LGFX_Config.h            # LovyanGFX display configuration (GC9A01)
├── PetCube Companion/
│   ├── main.py                     # CLI entry point
│   ├── gui.py                      # CustomTkinter dashboard + tray icon
│   ├── setup_wizard.py             # First-run config wizard (GUI)
│   ├── companion_engine.py         # Async core, GUI-controllable
│   ├── plugin_manager.py           # Plugin lifecycle + dispatch
│   ├── config_schema.py            # config.json schema, defaults, helpers
│   ├── ble_sender.py               # BLE GATT client
│   ├── firmware_updater.py         # Firmware version check + BLE/USB OTA flashing + factory reset
│   ├── app_updater.py              # Companion self-update from GitHub Releases
│   ├── playwright_env.py           # Persistent Playwright browser path (PyInstaller)
│   ├── sentiment.py                # Italian text classifier
│   ├── notification_packet.py      # Packet schema
│   ├── achievements.py             # 47 achievement definitions + unlock/cache helpers
│   ├── version.py                  # Companion app version
│   ├── plugins/
│   │   ├── base.py                 # Plugin base class + seen_ids persistence
│   │   ├── calendar_plugin.py
│   │   ├── discord_plugin.py
│   │   ├── gmail_plugin.py
│   │   ├── hacknplan_plugin.py
│   │   ├── telegram_plugin.py
│   │   └── whatsapp_plugin.py      # WhatsApp Web via Playwright (Chromium)
│   ├── setup_telegram_session.py   # One-time Telethon login (run once)
│   ├── list_telegram_chats.py      # List chat IDs for monitor_chat_ids
│   ├── install_playwright.bat      # One-time Chromium install for the packaged .exe
│   ├── test_e2e.py                 # End-to-end smoke tests (no hardware required)
│   ├── config.example.json         # Config template (copy to config.json)
│   ├── config.json                 # User config (gitignored)
│   ├── achievements_cache.json     # Last-known achievement bitmask (gitignored)
│   ├── history/                    # Persisted seen_ids (gitignored)
│   └── requirements.txt
├── Sprite/
│   ├── process_sprites.py          # Sprite processing pipeline (magenta removal, scaling)
│   └── processed/                  # Output frames per creature (gitignored)
├── GDD.md                          # Game design document
└── README.md
```

---

## Getting started — Firmware

### Prerequisites

- Arduino IDE 2.x (or arduino-cli)
- ESP32 board package by Espressif Systems (≥ 3.0.0)
- Board selected: **XIAO_ESP32S3**
- Libraries:
  - `LovyanGFX` by lovyan03 (display configuration is in `PetCube FW/PetCube/LGFX_Config.h`, no extra setup needed)
  - `Adafruit MPU6050`
  - `Adafruit Unified Sensor`
  - `Adafruit BusIO`
  - `ArduinoBLE` is **not** used — the firmware uses the native ESP32 BLE stack via `BLEDevice.h`

### Build & flash

1. Open `PetCube FW/PetCube/PetCube.ino` in Arduino IDE.
2. Tools → Board → **XIAO_ESP32S3**.
3. Tools → USB CDC On Boot → **Enabled** (needed for serial logging).
4. Tools → Partition Scheme → **8M with spiffs (3MB APP/1.5MB SPIFFS)** or larger (the firmware is around 1.5 MB).
5. Connect the XIAO via USB-C and select the port.
6. Click **Upload**.

The serial monitor (115200 baud) shows the boot sequence, plugin events received, and battle state transitions.

### First-time setup on the cube

On first boot the cube enters **boot screen** → asks **Continue / New Game**. For a new game, it then prompts for the starter element (Fire or Water). The clock is synced automatically over BLE the first time the Companion connects (see [Changelog v27](#changelog)) — until then, the clock footer at the bottom of the screen stays hidden.

---

## Getting started — Companion app

### Prerequisites

- Python 3.11+ (tested on 3.14 Windows)
- A working PC Bluetooth adapter
- Google Cloud project with OAuth credentials for Calendar + Gmail (steps below)
- A HacknPlan API key (optional, for the HacknPlan plugin)
- A Discord Bot token (optional, for the Discord plugin)
- Telegram API credentials from [my.telegram.org](https://my.telegram.org) (optional, for the Telegram plugin)
- Playwright + Chromium (optional, for the WhatsApp Web plugin)

### Install

```powershell
cd "PetCube Companion"
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
```

> `requirements.txt` includes optional dependencies for plugins still in development (WhatsApp, Instagram). They install fine but are not required for Calendar/Gmail/HacknPlan/Discord/Telegram.

Copy `config.example.json` to `config.json` (gitignored) and fill in the credentials for the plugins you want to enable.

### Google OAuth setup

The Calendar and Gmail plugins share a unified OAuth flow:

1. Go to [Google Cloud Console](https://console.cloud.google.com/).
2. Create a project (e.g. `PetCube Companion`).
3. Enable the **Google Calendar API** and **Gmail API**.
4. Configure the OAuth consent screen (External, Testing mode is fine for personal use).
5. Add the scopes `calendar.readonly` and `gmail.readonly`.
6. Add your own Google account as a test user.
7. Create an **OAuth 2.0 Client ID** of type **Desktop application**.
8. Download the JSON file and save it as `PetCube Companion/credentials.json`.
9. First run: the app will open a browser to authorize and save `PetCube Companion/token.json` for subsequent launches.

### HacknPlan API key

1. Sign in to [HacknPlan](https://app.hacknplan.com/).
2. Click your avatar → **My Account** → **API**.
3. Click **Generate Token** and copy it into `config.json` → `plugins.hacknplan.api_key`.

### Discord Bot setup

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications) and click **New Application**.
2. In the left sidebar go to **Bot** → click **Add Bot** → confirm.
3. Under **Token**, click **Reset Token**, copy it, and paste it into `config.json` → `plugins.discord.bot_token`.
4. Scroll down to **Privileged Gateway Intents** and enable **Message Content Intent** (required to read message text).
5. Go to **OAuth2 → URL Generator**, tick the `bot` scope and the `Read Messages / View Channels` permission.
6. Open the generated URL in a browser and invite the bot to the servers you want to monitor.
7. In `config.json` set `plugins.discord.enabled` to `true`.

Optionally, list the IDs of specific channels to monitor in `monitor_channel_ids`. To find a channel ID: in Discord, enable **Settings → Advanced → Developer Mode**, then right-click a channel → **Copy Channel ID**.

> **Note**: the bot must be **online** (companion running) to receive events. Messages sent while the companion is stopped are not replayed on reconnect.

### Telegram setup

The Telegram plugin uses [Telethon](https://docs.telethon.dev/) as a **user client** (it logs in as you, not as a separate bot) — this lets it see your DMs and group messages directly.

1. Go to [my.telegram.org](https://my.telegram.org/) → **API development tools** and log in with your phone number.
2. Create an app (any name/platform) → note the **api_id** and **api_hash**.
3. In `config.json` → `plugins.telegram`, set `api_id`, `api_hash`, and `phone_number` (e.g. `+393331234567`), and set `enabled` to `true`.
4. From `PetCube Companion/`, run:
   ```powershell
   python setup_telegram_session.py
   ```
   Enter the OTP code sent to your Telegram app. This creates a `telegram_session.session` file (gitignored) used for all future logins — you only need to do this once.
5. Optional: to monitor specific groups/channels (not just your DMs), run `python list_telegram_chats.py` to list all chats with their IDs, then add the ones you want to `monitor_chat_ids`.

**Behaviour**: any DM is HIGH priority. Messages in `monitor_chat_ids` are NORMAL priority, upgraded to HIGH if your username is mentioned (`@yourusername`).

### WhatsApp setup

The WhatsApp plugin monitors unread chats on **WhatsApp Web** by driving a Chromium browser via [Playwright](https://playwright.dev/) — there's no official API, so it works the same way as opening web.whatsapp.com in a browser and keeping it logged in.

1. Install Playwright's Chromium build:
   - Running from source: `pip install playwright` (already in `requirements.txt`), then `playwright install chromium`.
   - Running the packaged `.exe`: double-click `install_playwright.bat` once (browsers are installed to `%LOCALAPPDATA%\PetCube Companion\playwright-browsers`, since the exe's temp folder doesn't persist between runs).
2. In `config.json` → `plugins.whatsapp`, set `enabled: true` and **`headless: false`** for the first run.
3. Start the companion. A Chromium window opens on web.whatsapp.com — scan the QR code with your phone (WhatsApp → Linked devices → Link a device). The session is saved under `session_dir` (default `whatsapp_session`, gitignored).
4. Stop the companion, set `headless: true` back in `config.json`, and restart. From now on the browser runs in the background and the session persists.
5. Optional: set `monitor_chats` to a list of substrings (case-insensitive) to only get notified about specific chats/groups — DMs are always notified regardless of this filter. Leave empty to be notified about any chat with an unread badge.

**Behaviour**: any DM is HIGH priority. Messages in groups/channels matching `monitor_chats` are NORMAL priority. The phone must stay connected to the internet (WhatsApp Web requirement).

> **Note**: WhatsApp Web's DOM selectors may change with WhatsApp updates — if the plugin stops detecting messages, the selectors in `plugins/whatsapp_plugin.py` may need updating.

### Run

CLI (no GUI):

```powershell
python main.py
```

GUI (dashboard + tray icon):

```powershell
python gui.py
```

### Companion GUI tabs

| Tab | Purpose |
|---|---|
| **Dashboard** | Live status, connection state, recent notification log, start/stop the companion engine. |
| **Impostazioni** | Edit `config.json` — device identity, plugin credentials/toggles, transport and logging settings. |
| **Test** | Send fake notifications per source/category without waiting for a real event, to test the cube's reaction. |
| **Achievements** | All 47 achievements grouped by category, with locked/unlocked status and progress. Refreshes automatically on BLE connect, or via the "🔄 Aggiorna via BLE" button. |
| **Aggiornamenti** | Check/install firmware updates over BLE or USB, view Companion app version/update, and the "🗑 Reset di fabbrica" button (wipes save data, registry, and achievements on the cube after confirmation). |

---

## First connection

1. Power on the cube and let it reach idle state (you should see the pet on screen).
2. Run the companion (`python gui.py` or `python main.py`).
3. The BLE sender scans for a device named `PetCube` (10 s timeout). When a real event is generated by a plugin, it is dispatched and sent.
4. On the cube, an icon appears next to the pet in idle mode.
5. **Long-press B for 5 seconds** to start the battle generated from that notification.

If the cube is not in idle (e.g. in a session, sleep, or menu), the BLE advertising is stopped — return to idle for the cube to be discoverable.

---

## Configuration reference

`PetCube Companion/config.json` (gitignored) controls device, plugins, transport and firmware update settings. Copy `config.example.json` to get started:

```json
{
  "device": {
    "ble_name": "PetCube",
    "ble_service_uuid": "12345678-1234-5678-1234-56789abcdef0",
    "ble_char_uuid": "12345678-1234-5678-1234-56789abcdef1",
    "username": "",
    "device_id": "",
    "wifi_fallback_url": ""
  },
  "plugins": {
    "calendar": {
      "enabled": false,
      "poll_interval_sec": 60,
      "lookahead_minutes": 15,
      "credentials_file": "credentials.json",
      "exclude_calendars": []
    },
    "gmail": {
      "enabled": false,
      "poll_interval_sec": 600,
      "credentials_file": "credentials.json",
      "login_hint": "",
      "max_recent": 20
    },
    "hacknplan": {
      "enabled": false,
      "poll_interval_sec": 7200,
      "lookahead_hours": 48,
      "api_key": "",
      "target_user_id": null
    },
    "discord": {
      "enabled": false,
      "bot_token": "",
      "user_id": null,
      "poll_interval_sec": 30,
      "monitor_channel_ids": []
    },
    "telegram": {
      "enabled": false,
      "api_id": 0,
      "api_hash": "",
      "phone_number": "",
      "session_file": "telegram_session",
      "poll_interval_sec": 30,
      "monitor_chat_ids": []
    },
    "whatsapp": {
      "enabled": false,
      "session_dir": "whatsapp_session",
      "poll_interval_sec": 30,
      "headless": true,
      "monitor_chats": []
    }
  },
  "transport": {
    "prefer": "ble",
    "ble_scan_timeout_sec": 10
  },
  "logging": {
    "level": "INFO"
  },
  "firmware": {
    "github_owner": "MikeAymeric",
    "github_repo": "PetCube"
  }
}
```

> `config.example.json` also lists `slack`, `github` and `trello` plugin stubs (all `enabled: false`). They're reserved for future plugins (see [Roadmap](#roadmap)) and have no effect yet.

> **Easier setup**: on first launch (no `config.json`), `python gui.py` opens a setup wizard that walks through enabling plugins and entering credentials instead of editing JSON by hand.

### Plugin behaviour

| Plugin | Polls every | Triggers / Filters | Notes |
|---|---|---|---|
| Calendar | 60 s | Events starting within `lookahead_minutes` (default 15). Excludes `*@holiday.calendar.google.com`, shared/group calendars, contacts. | Multi-calendar (primary + shared) |
| Gmail | 10 min | `UNREAD` in Inbox, no `category:promotions/social/updates/forums`. Recipient must be in `To`/`Cc`. Skips `List-Unsubscribe` and `Precedence: bulk/list`. | — |
| HacknPlan | 2 h | Work items assigned to me with `dueDate` within `lookahead_hours` (default 48). Not in `stage.status: completed`. Skips user stories. | Source shown as TRELLO in firmware (shared enum value). |
| Discord | 10 s | **@mentions** of your personal account (`user_id`) in any server (priority HIGH). **@here / @everyone** in channels visible to the bot (priority NORMAL). **Messages in `monitor_channel_ids`** (priority NORMAL). | Requires `Message Content Intent` enabled in Discord Developer Portal. Events are real-time (WebSocket); the 10 s interval only controls how often the queue is drained. |
| Telegram | 30 s | **Any DM** (priority HIGH). **Messages in `monitor_chat_ids`** (priority NORMAL, upgraded to HIGH on `@username` mention). | User client via Telethon (logs in as you, not a bot). Requires one-time `setup_telegram_session.py` login. Events are real-time; the interval only controls queue drain. |
| WhatsApp | 30 s | **Any DM** (priority HIGH). **Chats/groups matching `monitor_chats`** (priority NORMAL). Empty `monitor_chats` = any chat with an unread badge. | WhatsApp Web via Playwright/Chromium. Requires one-time QR-code login (`headless: false`). DOM-selector based — may need updates if WhatsApp Web changes. |

All plugins persist their seen-IDs to `history/<plugin>.json` (FIFO cap 5000) so the same event is not re-sent after a restart.

---

## How the battle system works

1. **Notification arrives**: a plugin detects a new event, builds a `NotifPacket` with `source`, `priority`, `category` (computed by the spaCy sentiment classifier), and `seed` (the event text, capped at 50 chars).
2. **BLE write**: the companion writes the packet to the cube's GATT characteristic.
3. **Idle screen icon**: the cube shows a 32×32 color icon with a badge for the unread count, one per source (📅 Calendar, 📧 Gmail, 📋 HacknPlan, 💬 Discord, ✈️ Telegram, 📱 WhatsApp).
4. **Player triggers battle**: long-pressing **B** for 5 seconds starts the encounter.
5. **Enemy generation**: deterministic hash of `seed + source + category` selects a creature from the bestiary and assigns its stats. Element (Fire / Water) derives from the source; morale alignment (Light / Dark) derives from the sentiment category.
6. **Battle**: best-of-3 *clashes*. Each clash is a real-time timing minigame where the player presses **B** when a moving cursor enters a critical window (its width depends on `seed` length).
7. **Outcome**: win → +HAP and the enemy is added to the registry as a battle-only entry (silhouette + name only, no stats unless the player has also evolved that creature themselves). Lose → -HAP and a stat penalty.

See the [GDD](docs/PetCube_GDD_v0_11.docx) §16 for the full design (stat formulas, element/morale type bonuses, tie-breaker rules, etc.).

---

## Roadmap

### Done (June 2026)
- 28 original creatures with sprites, 12 frames each, full evolution tree
- Pomodoro session loop with orientation-based input
- Battle system (firmware + GATT BLE transport)
- OTA firmware updates over BLE, segmented/resumable across reconnects
- Factory reset over BLE, decoupled from firmware version bumps
- Achievement system (47 achievements, firmware bitmask + Companion tab)
- Companion app with Calendar, Gmail, HacknPlan, Discord, Telegram, and WhatsApp plugins
- Italian sentiment classifier (spaCy `it_core_news_sm`)
- GUI: dark dashboard + tray icon + live log + setup wizard + config editor
- Companion self-update from GitHub Releases
- Hardware assembled: all components hand-soldered on two Electrocookie perfboards (breadboard form factor) with portable LiPo power, 3D-printed case
- Automatic clock sync from the Companion's PC clock over BLE (no more manual time-setting)
- GUI test console with fake-notification buttons per source/category/priority

### In progress
- Final adjustments to the 3D-printed case

### Future
- WiFi transport fallback for when BLE is unavailable
- PCB instead of perfboard
- Additional plugins: Instagram, Slack, GitHub, Trello
- Optional: asynchronous PvP — trade battle-ready creatures between cubes via cloud

---

## Changelog

Firmware version history (`FW_VERSION` in `PetCube.ino`). As of v26, a `FW_VERSION` bump no longer wipes saved data automatically — see the v26 entry below.

- **v31 / Companion 0.2.11**: UI polish pass. On the status screen, the multiplayer identity tag was moved to the top-right corner so it no longer overlaps the evolution-stage badge. The menu screen now shows a small icon for each entry (stats, broom, heart, registry) and highlights the selected item with an accent bar. The registry screen was redesigned: a large centered sprite, the creature's name below it, an element/count badge plus a new evolution-stage badge ("Wisp", "Primal / STR", etc.), and the four S/I/E/H stats arranged two per row; the header and footer (index, controls, clock) are unchanged.
- **v30 / Companion 0.2.10**: The Bluetooth icon and the clock footer on the cube's screen were enlarged and the clock is now drawn in white, with a small sun (07:00–19:00) or moon (19:00–07:00) icon shown to its right. Added a new read/write BLE BRIGHTNESS characteristic (persisted in NVS) and a STATS characteristic exposing the lifetime Pomodoro session counter; the Companion's Settings tab gained a "Display" section with a slider to adjust the cube's screen brightness remotely. On the Companion side: a new "🔄 Sincronizza ora" button in the header connects on demand to sync the clock, identity tag and achievements without waiting for a notification; newly unlocked achievements now trigger a Windows tray notification; the Dashboard gained a 14-day bar chart of completed Pomodoro sessions; and the app now enforces a single running instance, bringing the existing window to front if launched again.
- **v29 / Companion 0.2.9**: Removed the dedicated clock screen — the current time (HH:MM) is now shown as a small persistent footer at the bottom of the main, stats and registry screens (once synced from the Companion). The B button no longer opens the clock from Idle or from the stats screen (it now does nothing in both cases). The BLE status indicator on the main screen was replaced with a Bluetooth icon: shown in color when a Companion is connected, in grayscale and blinking while advertising. The stats and registry screens were redesigned with a bolder layout — colored stat bars with value badges, an evolution-stage pill, session/battle info badges, and element-colored badges and hearts in the registry. On the Companion side, the clock sync over BLE (introduced in v27) now also happens when checking the firmware version or refreshing achievements, not just when sending a notification — so the cube's clock stays accurate even if no notifications are sent for a while.
- **v28**: The Companion now uses an on-demand BLE connection — it connects only when there's a notification to send (or when the user manually refreshes achievements / updates the firmware), syncing identity, achievements and the clock on each connection, then disconnects immediately. This reduces the cube's power draw when idle. Also added a "🗑 Reset achievements" button to the Companion's Achievements tab: writes a new RESET command (`0x02`) that immediately zeroes the achievement bitmask and lifetime counters (NVS namespace `achv`) without rebooting or touching the save game/registry.
- **v27**: Removed the manual clock-setting screen. The cube now syncs its clock automatically from the Companion's PC clock over a new write-only BLE TIME characteristic (seconds since local midnight), sent on every BLE connection. Until the first sync after boot, the clock screen shows "Sincronizzazione...".
- **v26**: Decoupled the NVS reset from the firmware version bump — updating the firmware now preserves the save game, registry, legends/legacy data, and achievements. A full factory reset (wiping `petcube`, `registro`, and `achv`) is now an explicit action: a new "🗑 Reset di fabbrica" button in the Companion's Aggiornamenti tab (with confirmation) writes a flag over a new read/write BLE RESET characteristic, the cube reboots, and wipes everything on the next boot before loading any data.
- **v25**: Added the achievement system — 47 achievements tracked firmware-side as a bitmask (NVS namespace `achv`, not wiped by the version-bump reset), evaluated after every relevant gameplay event (evolutions, registry, legends, pomodoro sessions, care/illness, battles, notifications, orientation, time of day) and exposed read-only over a new BLE characteristic. The Companion app gained an "Achievements" tab showing all 47 entries grouped by category with locked/unlocked status, refreshed automatically on BLE connect or via a dedicated button. Also fixed the dead `battlesWon`/`battlesLost` per-life counters, which were tracked but never incremented.
- **v24**: Fixed display rotation in the Work state (orientation Face up), which was showing upside-down — now correctly rotated 180°.
- **v23**: Fixed orientation→state mapping (Normal=Sleep, Left=Training, Right=Study, Face up=Work, Upside down=DND, Face down=Idle) and the matching decay-suspension check. Added screen power saving: the display backlight turns off after 5 minutes of inactivity, waking on a BLE/Wi-Fi notification, a button press, or a state/orientation change; stays on during an active pomodoro/rest session.
- **v22**: Notification source icons redesigned — replaced the old 12×12 monochrome XBM icons with new 32×32 color icons, redrawn at the top-center of the idle screen with a black rounded badge showing the notification count in red on the icon's bottom-left corner.
- **v21**: Codebase cleanup — removed dead/diagnostic-only code (OTA queue high-water mark and write-time tracking, custom BLE GAP handler, unused `onConnect`/`onMtuChanged`/`onConnParamsUpdate` callbacks, Serial mock-notification test harness, MPU debug dump), shortened verbose comments. The firmware's version history was moved out of the source file and into this README.
- **v20**: Segmented/resumable OTA across BLE reconnects — `OTA START` resumes from `otaBytesReceived` instead of restarting. Fixed transfer stalling at ~60% by restarting BLE advertising during `OTA_RECEIVING` after a disconnect.
- **v19**: Background art extended to Session and battle screens; battle UI redesigned for readability (dark badges, VS bar, projectiles colored by element). Removed duplicate "Light" evolutions (Mitamamon/Lucemon/Vikemon/Ryugumon) — registry reduced from 32 to 28 creatures. OTA: post-transfer confirmation prompt (B = install, C = cancel) before finalizing and rebooting; chunks queued and written to flash from the main loop instead of the BLE callback; local BLE MTU set to 517; companion limits OTA chunks to 512 bytes; chunk queue increased from 12 to 32 slots (16 KB) to prevent overflow during transfer.
- **v18**: Environmental background (`Sprite/BG_Normal.png`) on Idle, Sleep, DND, Work, Study, Training. Status label hidden on Idle/Sleep, shown with a dark badge elsewhere. Battle cursor speed 4 → 12 px/frame.
- **v17**: Configurable pomodoro system — removed the "Feed" mechanic; in Training/Study/Work, B opens pomodoro setup (work duration ±5 min, rest duration ±1 min via A/C), a third B starts the timer. Orientation change during setup cancels without penalty; during an active session it costs `CANCEL_HAP_MALUS` HAP. Stats/sessions increase per 25 min of completed pomodoro; HAP increases on completed rest. Durations persisted in NVS.
- **v16**: BLE advertising active in any state (not just Idle), so the companion can send notifications anytime. Fixed a bug where the cube stayed permanently undiscoverable after the first connection. Sleep/DND are silent for BLE notification beeps (notification still queues).
- **v15**: Migrated display from TFT_eSPI to LovyanGFX — fixes a boot crash (Guru Meditation StoreProhibited) on ESP32-S3 + GC9A01 with a flickering black screen. Display config moved to `LGFX_Config.h`.
- **v14**: Integrated BLE GATT server — "PetCube" service receives `NotifPacket` from the companion app; advertising active only in Idle (battery saving); BT icon shown while connected; ascending/descending beeps on connect/disconnect.
- **v13**: Full battle system — source icon next to the pet (max 3 queued, 30 min TTL), long-press B to battle / long-press C to dismiss, best-of-3 clashes with a timing minigame for criticals, Fire/Water + Light/Dark type bonus damage formulas, HP as tie-breaker, defeating an enemy adds +3 to its dominant stat, losing can trigger sickness scaled by accumulated mess. New NVS fields: `streak` and `enemyKnown[32]`.

---

## Credits

- **Concept, design, firmware, companion app**: Michael Maneia
- **Inspiration**: Tamagotchi (Bandai), pomodoro technique (Francesco Cirillo)
- **Libraries used**:
  - Firmware: [LovyanGFX](https://github.com/lovyan03/LovyanGFX), [Adafruit MPU6050](https://github.com/adafruit/Adafruit_MPU6050), ESP32 Arduino core
  - Companion: [bleak](https://github.com/hbldh/bleak), [spaCy](https://spacy.io/), [Google API Python Client](https://github.com/googleapis/google-api-python-client), [discord.py](https://github.com/Rapptz/discord.py), [Telethon](https://github.com/LonamiWebs/Telethon), [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter), [pystray](https://github.com/moses-palmer/pystray)


---

## License

This work is licensed under the [Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International License](https://creativecommons.org/licenses/by-nc-sa/4.0/) (CC BY-NC-SA 4.0).

In short:
- ✅ You can share and adapt this work
- ✅ You must give credit (Michael Maneia)
- ❌ You may not use it commercially
- 🔁 Derivative works must be shared under the same license

See [LICENSE](LICENSE) for the full text.
