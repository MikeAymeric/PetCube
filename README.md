# PetCube

> A Tamagotchi-meets-Pomodoro virtual pet cube. Train, study, and work — your cube knows which, because it can feel which way it's facing.

PetCube is a handheld virtual pet device built on the XIAO ESP32-S3. You raise an original creature by completing real-life pomodoro sessions: tilt the cube **left to train**, **right to study**, **upside down to work**. A companion desktop app turns your real notifications (calendar events, emails, project deadlines) into in-game battles the pet must fight.

**Status**: work in progress. Battle system, companion plugins (Calendar / Gmail / HacknPlan / Discord) and BLE transport are operational. Portable LiPo power and GUI Steps 2-3 are in progress.

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
10. [Credits](#credits)
11. [License](#license)

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
│   ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐          │
│   │ Calendar │  │  Gmail   │  │ HacknPlan│  │ Discord  │  (plugins)│
│   └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘          │
│        └─────────────┼──────────────┴──────────────┘             │
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

External sources (Google Calendar, Gmail, HacknPlan REST) → companion polls them → each new event is wrapped in a `NotifPacket` and sent via BLE GATT to the cube. The cube shows an icon on its idle screen; long-pressing **B** starts a battle where the enemy's stats and element derive deterministically from the notification's text and source.

---

## Repository structure

```
PetCube/
├── PetCube FW/
│   └── PetCube/
│       ├── PetCube.ino              # Main firmware sketch
│       ├── petcube_sprites.h        # 32 creatures × 12 frames
│       ├── petcube_battle.h         # Battle system (stats, enemy selection, clash logic)
│       └── LGFX_Config.h            # LovyanGFX display configuration (GC9A01)
├── PetCube Companion/
│   ├── main.py                     # CLI entry point
│   ├── gui.py                      # CustomTkinter dashboard + tray icon
│   ├── companion_engine.py         # Async core, GUI-controllable
│   ├── plugin_manager.py           # Plugin lifecycle + dispatch
│   ├── ble_sender.py               # BLE GATT client
│   ├── sentiment.py                # Italian text classifier
│   ├── notification_packet.py      # Packet schema
│   ├── plugins/
│   │   ├── base.py                 # Plugin base class + seen_ids persistence
│   │   ├── calendar_plugin.py
│   │   ├── discord_plugin.py
│   │   ├── gmail_plugin.py
│   │   └── hacknplan_plugin.py
│   ├── config.json                 # User config (gitignored)
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

1. Open `firmware/PetCube.ino` in Arduino IDE.
2. Tools → Board → **XIAO_ESP32S3**.
3. Tools → USB CDC On Boot → **Enabled** (needed for serial logging).
4. Tools → Partition Scheme → **8M with spiffs (3MB APP/1.5MB SPIFFS)** or larger (the firmware is around 1.5 MB).
5. Connect the XIAO via USB-C and select the port.
6. Click **Upload**.

The serial monitor (115200 baud) shows the boot sequence, plugin events received, and battle state transitions.

### First-time setup on the cube

On first boot the cube enters **boot screen** → asks **Continue / New Game** → asks to set the CEST clock (A=+1h, B=+1min, C=save). For a new game, it then prompts for the starter element (Fire or Water).

---

## Getting started — Companion app

### Prerequisites

- Python 3.11+ (tested on 3.14 Windows)
- A working PC Bluetooth adapter
- Google Cloud project with OAuth credentials for Calendar + Gmail (steps below)
- A HacknPlan API key (optional, for the HacknPlan plugin)
- A Discord Bot token (optional, for the Discord plugin)

### Install

```powershell
cd companion
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
```

### Google OAuth setup

The Calendar and Gmail plugins share a unified OAuth flow:

1. Go to [Google Cloud Console](https://console.cloud.google.com/).
2. Create a project (e.g. `PetCube Companion`).
3. Enable the **Google Calendar API** and **Gmail API**.
4. Configure the OAuth consent screen (External, Testing mode is fine for personal use).
5. Add the scopes `calendar.readonly` and `gmail.readonly`.
6. Add your own Google account as a test user.
7. Create an **OAuth 2.0 Client ID** of type **Desktop application**.
8. Download the JSON file and save it as `companion/credentials.json`.
9. First run: the app will open a browser to authorize and save `companion/token.json` for subsequent launches.

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

### Run

CLI (no GUI):

```powershell
python main.py
```

GUI (dashboard + tray icon):

```powershell
python gui.py
```

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

`companion/config.json` controls all plugins and transport. Example:

```json
{
  "transport": {
    "prefer": "ble",
    "ble_device_name": "PetCube",
    "ble_scan_timeout": 10
  },
  "plugins": {
    "calendar": {
      "enabled": true,
      "poll_interval_sec": 60,
      "lookahead_minutes": 15,
      "exclude_calendars": ["addressbook.google.com"]
    },
    "gmail": {
      "enabled": true,
      "poll_interval_sec": 600,
      "login_hint": "you@example.com",
      "max_recent": 20
    },
    "hacknplan": {
      "enabled": true,
      "poll_interval_sec": 7200,
      "lookahead_hours": 48,
      "api_key": "<paste here>",
      "target_user_id": null
    },
    "discord": {
      "enabled": false,
      "bot_token": "<paste here>",
      "poll_interval_sec": 10,
      "monitor_channel_ids": []
    }
  }
}
```

### Plugin behaviour

| Plugin | Polls every | Triggers / Filters | Notes |
|---|---|---|---|
| Calendar | 60 s | Events starting within `lookahead_minutes` (default 15). Excludes `*@holiday.calendar.google.com`, shared/group calendars, contacts. | Multi-calendar (primary + shared) |
| Gmail | 10 min | `UNREAD` in Inbox, no `category:promotions/social/updates/forums`. Recipient must be in `To`/`Cc`. Skips `List-Unsubscribe` and `Precedence: bulk/list`. | — |
| HacknPlan | 2 h | Work items assigned to me with `dueDate` within `lookahead_hours` (default 48). Not in `stage.status: completed`. Skips user stories. | Source shown as TRELLO in firmware (shared enum value). |
| Discord | 10 s | **@mentions** of your personal account (`user_id`) in any server (priority HIGH). **@here / @everyone** in channels visible to the bot (priority NORMAL). **Messages in `monitor_channel_ids`** (priority NORMAL). | Requires `Message Content Intent` enabled in Discord Developer Portal. Events are real-time (WebSocket); the 10 s interval only controls how often the queue is drained. |

All plugins persist their seen-IDs to `history/<plugin>.json` (FIFO cap 5000) so the same event is not re-sent after a restart.

---

## How the battle system works

1. **Notification arrives**: a plugin detects a new event, builds a `NotifPacket` with `source`, `priority`, `category` (computed by the spaCy sentiment classifier), and `seed` (the event text, capped at 50 chars).
2. **BLE write**: the companion writes the packet to the cube's GATT characteristic.
3. **Idle screen icon**: the cube shows a 12×12 pixel icon for the source (📅 Calendar, 📧 Gmail, 📋 HacknPlan).
4. **Player triggers battle**: long-pressing **B** for 5 seconds starts the encounter.
5. **Enemy generation**: deterministic hash of `seed + source + category` selects a creature from the bestiary and assigns its stats. Element (Fire / Water) derives from the source; morale alignment (Light / Dark) derives from the sentiment category.
6. **Battle**: best-of-3 *clashes*. Each clash is a real-time timing minigame where the player presses **B** when a moving cursor enters a critical window (its width depends on `seed` length).
7. **Outcome**: win → +HAP and the enemy is added to the registry as a battle-only entry (silhouette + name only, no stats unless the player has also evolved that creature themselves). Lose → -HAP and a stat penalty.

See the [GDD](docs/PetCube_GDD_v0_11.docx) §16 for the full design (stat formulas, element/morale type bonuses, tie-breaker rules, etc.).

---

## Roadmap

### Done (May 2026)
- 32 original creatures with sprites, 12 frames each, full evolution tree
- Pomodoro session loop with orientation-based input
- Battle system (firmware + GATT BLE transport)
- Companion app with Calendar, Gmail, HacknPlan, and Discord plugins
- Italian sentiment classifier (spaCy `it_core_news_sm`)
- GUI Step 1: dark dashboard + tray icon + live log

### In progress
- GUI Step 2: visual config editor (replace manual `config.json` editing)
- GUI Step 3: test console with fake-notification buttons per source/category
- Hardware assembly: solder all components on breadboard/PCB
- Sprite assets: 28 creature spritesheets (12 frames each, 12×1 layout)

### Future
- 3D-printed case
- WiFi transport fallback for when BLE is unavailable
- PCB instead of breadboard
- Additional plugins: Slack, GitHub
- Optional: asynchronous PvP — trade battle-ready creatures between cubes via cloud

---

## Credits

- **Concept, design, firmware, companion app**: Michael Maneia
- **Inspiration**: Tamagotchi (Bandai), pomodoro technique (Francesco Cirillo)
- **Libraries used**:
  - Firmware: [LovyanGFX](https://github.com/lovyan03/LovyanGFX), [Adafruit MPU6050](https://github.com/adafruit/Adafruit_MPU6050), ESP32 Arduino core
  - Companion: [bleak](https://github.com/hbldh/bleak), [spaCy](https://spacy.io/), [Google API Python Client](https://github.com/googleapis/google-api-python-client), [discord.py](https://github.com/Rapptz/discord.py), [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter), [pystray](https://github.com/moses-palmer/pystray)


---

## License

This work is licensed under the [Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International License](https://creativecommons.org/licenses/by-nc-sa/4.0/) (CC BY-NC-SA 4.0).

In short:
- ✅ You can share and adapt this work
- ✅ You must give credit (Michael Maneia)
- ❌ You may not use it commercially
- 🔁 Derivative works must be shared under the same license

See [LICENSE](LICENSE) for the full text.
