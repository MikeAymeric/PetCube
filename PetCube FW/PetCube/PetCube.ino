// ══════════════════════════════════════════════════════════════════
//  PetCube — Firmware
//  Tamagotchi-meets-Pomodoro: schermata principale (sprite + stato),
//  menu Status/Clean/Heal/Registro, escrementi/malattia/morte,
//  battaglie via notifiche Companion (BLE GATT), OTA firmware.
//
//  Controlli (schermata principale):
//    A = apri menu
//    B = setup pomodoro (Training/Study/Work) / orologio (Idle);
//        long-press 5s con notifica pendente = battle
//    C = annulla pomodoro/riposo (-2 HAP) / chiudi orologio;
//        long-press 5s con notifica pendente = dismiss
//
//  Setup pomodoro: 1°B = durata lavoro (A=+5/C=-5 min), 2°B = durata
//  riposo (A=+1/C=-1 min), 3°B = avvia. Cambiare orientamento durante
//  il setup annulla senza penalità; a sessione avviata, annullare
//  costa -2 HAP. Ogni 25 min di pomodoro completato: +stat e +sessioni;
//  al riposo completato: +HAP.
//
//  File richiesti nella stessa cartella: petcube_sprites.h,
//  petcube_battle.h, petcube_backgrounds.h, LGFX_Config.h
//  BLE: stack nativo ESP32 (BLEDevice.h), non ArduinoBLE.
//
//  Changelog completo: vedi README.md
// ══════════════════════════════════════════════════════════════════

#include <Wire.h>
#include <LovyanGFX.hpp>
#include "LGFX_Config.h"
#include <Adafruit_MPU6050.h>
#include <Adafruit_Sensor.h>
#include <Preferences.h>
#include "petcube_sprites.h"
#include "petcube_backgrounds.h"
#include "petcube_battle.h"
#include "petcube_notif_icons.h"
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>
// OTA over-the-air update
#include <Update.h>

LGFX        display;
LGFX_Sprite canvas(&display);
Adafruit_MPU6050 mpu;
Preferences prefs;

// ── PIN ───────────────────────────────────────────────────────
//  D0  GPIO1  → BUZZER
//  D1  GPIO2  → TFT CS        (User_Setup.h: TFT_CS   = 2)
//  D2  GPIO3  → TFT DC        (User_Setup.h: TFT_DC   = 3)
//  D3  GPIO4  → BTN_C
//  D4  GPIO5  → I2C SDA       (MPU6050)
//  D5  GPIO6  → I2C SCL       (MPU6050)
//  D6  GPIO43 → TFT BLK       (User_Setup.h: TFT_BL   = 43)
//  D7  GPIO44 → BTN_B
//  D8  GPIO7  → TFT SCK       (User_Setup.h: TFT_SCLK = 7)
//  D9  GPIO8  → BTN_A
//  D10 GPIO9  → TFT MOSI      (User_Setup.h: TFT_MOSI = 9)
//  TFT RST    → 3V3 (TFT_RST = -1, reset software)
//  BAT+       → TP4056 OUT+
#define BTN_A  D9
#define BTN_B  D7
#define BTN_C  D3
#define BUZZER D0
#define LED    LED_BUILTIN

// ── COSTANTI ──────────────────────────────────────────────────
#define POMO_UNIT_MS         (25UL * 60 * 1000)   // unità di ricompensa pomodoro
#define POMO_DEFAULT_MS      (25UL * 60 * 1000)
#define POMO_STEP_MS         (5UL  * 60 * 1000)
#define POMO_MIN_MS          (5UL  * 60 * 1000)
#define POMO_MAX_MS          (120UL * 60 * 1000)
#define REST_DEFAULT_MS      (5UL  * 60 * 1000)
#define REST_STEP_MS         (1UL  * 60 * 1000)
#define REST_MIN_MS          (1UL  * 60 * 1000)
#define REST_MAX_MS          (30UL * 60 * 1000)
#define DECAY_WINDOW_MS      (4UL  * 60 * 60 * 1000)
#define DECAY_AMOUNT         10
#define HAP_PER_SESSION      8
#define STAT_PER_SESSION     10
#define ORIENT_THRESHOLD     7.0f
#define POOP_HAP_MALUS       2
#define POOP_MEGA_MALUS      5
#define POOP_SICK_MALUS      10
#define SICK_HAP_DECAY       5    // per ora
#define SICK_DEATH_MS        (2UL * 60 * 60 * 1000)
#define POOP_INTERVAL_MIN_MS (30UL * 60 * 1000)
#define POOP_INTERVAL_MAX_MS (45UL * 60 * 1000)
#define CANCEL_HAP_MALUS     2    // penalità HAP se si annulla pomodoro/riposo in corso
#define FW_VERSION           24   // bump al cambio struttura NVS

// ── BLE UUIDs (devono matchare quelli della Companion App in config.json) ──
#define BLE_DEVICE_NAME         "PetCube"
#define BLE_SERVICE_UUID        "12345678-1234-5678-1234-56789abcdef0"
#define BLE_CHAR_UUID           "12345678-1234-5678-1234-56789abcdef1"
#define BLE_CHAR_VERSION_UUID   "12345678-1234-5678-1234-56789abcdef2"
#define BLE_CHAR_OTA_CTRL_UUID  "12345678-1234-5678-1234-56789abcdef3"
#define BLE_CHAR_OTA_DATA_UUID  "12345678-1234-5678-1234-56789abcdef4"
#define BLE_CHAR_IDENTITY_UUID  "12345678-1234-5678-1234-56789abcdef5"
#define DISP_SIZE            240
#define SPR_SCALE            7    // sprite 16×16 → 112×112
#define SPR_SIZE             16
#define SPR_DRAW_SIZE        (SPR_SIZE * SPR_SCALE)  // 112
#define SPR_X                ((DISP_SIZE - SPR_DRAW_SIZE) / 2)  // 64
#define SPR_Y                ((DISP_SIZE - SPR_DRAW_SIZE) / 2)  // 64 — centrato
#define ANIM_IDLE_MS         400
#define ANIM_SLEEP_MS        700
#define ANIM_ATK_MS          380
#define SPR_DRIFT            55
#define SPR_DRIFT_PERIOD_MS  16000
#define EVOLVE_ANIM_MS       3000

// ── Colori ────────────────────────────────────────────────────
// Converte RGB888 → uint16_t RGB565.
// LovyanGFX interpreta int/uint32_t come colori 24-bit: passare
// sempre uint16_t per avere il colore corretto sul display.
// Macro (non funzione) per non spostare il punto in cui l'IDE Arduino
// genera i prototipi delle funzioni (vedi nota su FrameLabel sotto).
#define fc(r, g, b) ((uint16_t)((((uint16_t)(r) >> 3) << 11) | (((uint16_t)(g) >> 2) << 5) | ((uint16_t)(b) >> 3)))

constexpr uint16_t C_BG      = (uint16_t)TFT_BLACK;
constexpr uint16_t C_FG      = (uint16_t)TFT_WHITE;
constexpr uint16_t C_HAP     = (uint16_t)TFT_GREEN;
constexpr uint16_t C_STR     = (uint16_t)TFT_RED;
constexpr uint16_t C_INT     = (uint16_t)TFT_BLUE;
constexpr uint16_t C_ENG     = (uint16_t)TFT_YELLOW;
constexpr uint16_t C_TIMER   = (uint16_t)TFT_ORANGE;
constexpr uint16_t C_CYAN    = 0x07FFu;
constexpr uint16_t C_DIM     = 0x39C7u;
constexpr uint16_t C_POOP    = 0x6200u;
constexpr uint16_t C_MAGENTA = (uint16_t)TFT_MAGENTA;

// Build di prova senza sprite: mostra nome + nome frame animazione al posto
// del bitmap. Da rimuovere quando le sprite definitive saranno pronte.
#define SPRITES_PLACEHOLDER  0

#if SPRITES_PLACEHOLDER
// Definito qui (e non vicino a getFrameLabel) perché l'IDE Arduino genera i
// prototipi delle funzioni prima delle definizioni di struct che compaiono
// più avanti nel file: getFrameLabel andrebbe altrimenti dichiarata prima
// che FrameLabel sia un tipo noto, causando un errore di compilazione.
struct FrameLabel { const char* name; uint16_t color; };
#endif

const int EVO_THRESH[] = { 0, 2, 6, 14, 26, 42 };

// ── ENUM ──────────────────────────────────────────────────────
enum GameState {
  STATE_SETUP, STATE_IDLE, STATE_TRAINING, STATE_STUDY,
  STATE_WORK,  STATE_SLEEP, STATE_DND,
  STATE_SESSION, STATE_EVOLVING, STATE_DEAD,
  // ⚔️  Battle states
  STATE_BATTLE_INTRO,    // 1s sprint pet ↔ nemico
  STATE_BATTLE_CLASH,    // cursore mobile, attendi B
  STATE_BATTLE_RESOLVE,  // calcolo + animazione danno
  STATE_BATTLE_RESULT    // schermata finale V/L
};
enum Screen { SCR_MAIN, SCR_MENU, SCR_STATUS, SCR_CLOCK, SCR_BOOT, SCR_REGISTRO, SCR_BATTLE };
enum Orientation {
  ORI_NORMAL, ORI_LEFT, ORI_RIGHT,
  ORI_FACE_UP, ORI_UPSIDE_DOWN, ORI_FACE_DOWN
};
enum PomoPhase {
  POMO_NONE, POMO_SET_WORK, POMO_SET_REST, POMO_RUN_WORK, POMO_RUN_REST
};
enum Element { FIRE, WATER };

// ── SPRITE TABLE ──────────────────────────────────────────────
// Frame a colori: pixel RGB565 + maschera di visibilità (16x16, vedi
// petcube_sprites.h).
struct SprFrame {
  const uint16_t* px;
  const unsigned char* mask;
};

struct PetSprites {
  SprFrame idle[3];
  SprFrame happy[2];
  SprFrame sleep[2];
  SprFrame atk[2];
  SprFrame angry;
  SprFrame sick[2];
};

#define MK_FRAME(n, f) { spr_##n##_##f##_px, spr_##n##_##f##_mask }

#define MAKE_SPR(n) { \
  { MK_FRAME(n, idle1), MK_FRAME(n, idle2), MK_FRAME(n, idle3) }, \
  { MK_FRAME(n, happy1), MK_FRAME(n, happy2) }, \
  { MK_FRAME(n, sleep1), MK_FRAME(n, sleep2) }, \
  { MK_FRAME(n, atk1),   MK_FRAME(n, atk2)   }, \
  MK_FRAME(n, angry1), \
  { MK_FRAME(n, sick1),  MK_FRAME(n, sick2)  } \
}

const PetSprites SPR_KINDLEKIN      = MAKE_SPR(kindlekin);
const PetSprites SPR_EMBERPAW      = MAKE_SPR(emberpaw);
const PetSprites SPR_PYRUFF       = MAKE_SPR(pyruff);
const PetSprites SPR_BLAZEBRAND      = MAKE_SPR(blazebrand);
const PetSprites SPR_MIGHTFORGE = MAKE_SPR(mightforge);
const PetSprites SPR_FLAMEFORGE   = MAKE_SPR(flameforge);
const PetSprites SPR_SERAPHYRE   = MAKE_SPR(seraphyre);
const PetSprites SPR_NOXFORTRESS  = MAKE_SPR(noxfortress);
const PetSprites SPR_DROWSEA      = MAKE_SPR(drowsea);
const PetSprites SPR_GLOOMFIN     = MAKE_SPR(gloomfin);
const PetSprites SPR_FANGLURE      = MAKE_SPR(fanglure);
const PetSprites SPR_RIPTALON    = MAKE_SPR(riptalon);
const PetSprites SPR_MAULSTREAM   = MAKE_SPR(maulstream);
const PetSprites SPR_LEVIACRUSH = MAKE_SPR(leviacrush);
const PetSprites SPR_LIGHTFIN  = MAKE_SPR(lightfin);
const PetSprites SPR_NIGHTMARE    = MAKE_SPR(nightmare);
// ── Creature aggiuntive (Fire ENG/INT, Water ENG/INT) ────────────────
const PetSprites SPR_SHIELDMANE        = MAKE_SPR(shieldmane);
const PetSprites SPR_FORTIFIRE        = MAKE_SPR(fortifire);
const PetSprites SPR_CITADELLION           = MAKE_SPR(citadellion);
const PetSprites SPR_AUROVULP           = MAKE_SPR(aurovulp);
const PetSprites SPR_VULPYRE      = MAKE_SPR(vulpyre);
const PetSprites SPR_ELDERVULP         = MAKE_SPR(eldervulp);
const PetSprites SPR_BALEGUARD         = MAKE_SPR(baleguard);
const PetSprites SPR_BULWHARK         = MAKE_SPR(bulwhark);
const PetSprites SPR_TIDENAUGHT  = MAKE_SPR(tidenaught);
const PetSprites SPR_SIRENLURE           = MAKE_SPR(sirenlure);
const PetSprites SPR_ABYSSIBYL            = MAKE_SPR(abyssibyl);
const PetSprites SPR_THALASSIBYL         = MAKE_SPR(thalassibyl);

// lineVariant: 0=STR, 1=ENG, 2=INT
// Stadi 0-2 condivisi, stadi 3-4 e Ultimate dipendono da lineVariant
const char* FIRE_SHARED[]   = { "Kindlekin","Emberpaw","Pyruff" };
const char* FIRE_LINE0[]    = { "Blazebrand","Mightforge" };          // STR
const char* FIRE_LINE1[]    = { "Shieldmane","Fortifire" };         // ENG
const char* FIRE_LINE2[]    = { "Aurovulp","Vulpyre" };          // INT
const char* FIRE_FINAL0[]   = { "Flameforge","Seraphyre","Noxfortress" };
const char* FIRE_FINAL1[]   = { "Citadellion","Seraphyre","Noxfortress" };
const char* FIRE_FINAL2[]   = { "Eldervulp","Seraphyre","Noxfortress" };

const char* WATER_SHARED[]  = { "Drowsea","Gloomfin","Fanglure" };
const char* WATER_LINE0[]   = { "Riptalon","Maulstream" };       // STR
const char* WATER_LINE1[]   = { "Baleguard","Bulwhark" };       // ENG
const char* WATER_LINE2[]   = { "Sirenlure","Abyssibyl" };               // INT
const char* WATER_FINAL0[]  = { "Leviacrush","Lightfin","Nightmare" };
const char* WATER_FINAL1[]  = { "Tidenaught","Lightfin","Nightmare" };
const char* WATER_FINAL2[]  = { "Thalassibyl","Lightfin","Nightmare" };

// ── STATO GLOBALE ─────────────────────────────────────────────
GameState    gState        = STATE_IDLE;
Screen       gScreen       = SCR_MAIN;
Orientation  gOrient       = ORI_NORMAL;
Element      gElement      = FIRE;

int statSTR   = 0, statINT  = 0, statENG = 0, statHAP = 50;
int sessTotal = 0, sessActive = 0;   // sessActive: tracking sessioni completate (sessTotal include cancellate)
int evoStage  = 0, finalVariant = -1;
int lineVariant = 0;  // 0=STR, 1=ENG, 2=INT (determinato a Child->Adult)
int battlesWon = 0, battlesLost = 0;

// ── Eredità genetica (Leggende) ──────────────────────────────────
// Se un pet muore avendo raggiunto lo stadio finale (evoStage==5), lascia in
// eredità il 20% delle sue stat STR/INT/ENG al prossimo baby. Slot singolo,
// persistente nel namespace "registro" (sopravvive al reset di una partita).
#define LEGACY_STAT_PCT 20
int legacySTR = 0, legacyINT = 0, legacyENG = 0;
int legendCount = 0;   // numero totale di Leggende raggiunte (badge permanente)

bool          sessionRunning  = false;
GameState     sessionType     = STATE_WORK;
unsigned long sessionStartMs  = 0;
unsigned long lastSessionMs   = 0;
unsigned long lastDecayMs     = 0;
unsigned long evolveStartMs   = 0;

// ── SCREEN SLEEP (risparmio energetico) ─────────────────────────
const unsigned long SCREEN_TIMEOUT_MS = 5UL * 60UL * 1000UL;  // 5 minuti
const uint8_t       SCREEN_BRIGHTNESS = 255;
bool          screenOn        = true;
unsigned long lastActivityMs  = 0;

// Pomodoro
PomoPhase     pomoPhase   = POMO_NONE;
unsigned long pomodoroMs  = POMO_DEFAULT_MS;
unsigned long restMs      = REST_DEFAULT_MS;

// Escrementi
int  poopCount              = 0;   // 0..4 normali, 5=mega, poi sick
bool poopMega               = false;
unsigned long nextPoopMs    = 0;   // quando apparirà il prossimo escremento

// Malattia
bool          isSick        = false;
unsigned long sickStartMs   = 0;
unsigned long lastSickDecayMs = 0;
int           sickEpisodes  = 0;   // contatore vita totale — usato per logica Light (L4)

// ⚔️  Battle — notifiche pendenti (max 3 in coda)
#define MAX_PENDING_NOTIFS 3
struct PendingNotif {
  NotifPacket pkt;
  unsigned long arrivalMs;
  bool active;
};
PendingNotif pendingNotifs[MAX_PENDING_NOTIFS];
int activeNotifIdx = -1;        // -1 = nessuna selezionata, 0..MAX-1 = quella in focus
unsigned long longPressBMs  = 0;  // millis() inizio long-press B (0 = non premuto)
unsigned long longPressCMs  = 0;  // idem C

// Battle in corso
uint8_t  battleStreak       = 0;
uint8_t  battleEnemyIdx     = 0;
NotifPriority battlePriority = PRIO_NORMAL;
uint8_t  battleEnemyVariant = 0;  // 0=Std,1=Light,2=Dark (derivato dal REGISTRO elemento)
BattleElement battleEnemyElem = BE_FIRE;
CombatStats battlePetStats;
CombatStats battleEnemyStats;
uint8_t  battleClashIdx     = 0;   // 0..2
uint8_t  battlePetWins      = 0;
uint8_t  battleEnemyWins    = 0;
uint32_t battlePetDmgTaken  = 0;
uint32_t battleEnemyDmgTaken= 0;
unsigned long battleStateMs = 0;   // start time dello stato corrente

// Timing-game per crit
int      cursorX            = 0;   // posizione cursore 0..127
int      cursorDir          = 1;   // +1 / -1
int      critWindowStart    = 0;
int      critWindowWidth    = 16;  // larghezza zona crit (verrà variata da seed_length)
#define  BATTLE_CURSOR_SPEED 12     // px per frame (era 4: troppo lento, battaglie facili)
bool     petCritThisClash   = false;

// Registro nemici battuti (28 flag = REGISTRO_SIZE, persistenti nel namespace 'registro')
bool enemyKnown[28] = {false};

// ID univoco multiplayer (formato "username#12345"), assegnato dalla Companion
// App via BLE e persistito in NVS. Max 31 char + terminatore.
String petTag = "";

// ── 📡 BLE GATT server state ──────────────────────────────────
BLEServer*        bleServer        = nullptr;
BLECharacteristic* bleNotifChar    = nullptr;
BLECharacteristic* bleVersionChar  = nullptr;
BLECharacteristic* bleOtaCtrlChar  = nullptr;
BLECharacteristic* bleOtaDataChar  = nullptr;
BLECharacteristic* bleIdentityChar = nullptr;
bool              bleAdvertising   = false;
bool              bleClientConnected = false;
bool              bleClientConnectedPrev = false;
bool              bleInitialized   = false;
// Spinlock per protezione pendingNotifs[] tra BLE callback task e main loop
portMUX_TYPE      notifsMux        = portMUX_INITIALIZER_UNLOCKED;

// ── OTA state ────────────────────────────────────────────────
// AWAIT_CONFIRM: trasferimento completato, in attesa che l'utente confermi
// (B) o annulli (C) sullo schermo del PetCube prima di finalizzare.
// CANCELLED: l'utente ha annullato — resta finché non parte una nuova OTA.
enum OtaState : uint8_t {
  OTA_IDLE          = 0,
  OTA_RECEIVING     = 1,
  OTA_DONE          = 2,
  OTA_AWAIT_CONFIRM = 3,
  OTA_CANCELLED     = 4,
  OTA_ERROR         = 0xFF
};
volatile OtaState    otaState         = OTA_IDLE;
volatile uint32_t    otaBytesReceived = 0;
volatile uint32_t    otaTotalSize     = 0;
volatile bool        otaRebootPending = false;

// Coda chunk OTA: la callback BLE accoda i dati ricevuti, il main loop li
// scrive su flash (Update.write). Così la callback BLE resta velocissima
// e non blocca lo stack BLE con operazioni di flash durante un trasferimento
// lungo (causa di disconnessioni a metà OTA).
#define OTA_CHUNK_MAX 512
struct OtaChunk {
  uint8_t data[OTA_CHUNK_MAX];
  size_t  len;
};
QueueHandle_t otaChunkQueue = nullptr;

// MPU
float filtX = 0, filtY = 0;
unsigned long lastMpuMs = 0;

// Orologio CEST — l'utente imposta l'ora al primo avvio
// CEST = UTC+2. Salviamo l'offset in secondi tra millis() e l'ora reale.
// clockOffsetSec = epochSec - millis()/1000
// Se 0 l'ora non è stata impostata.
long clockOffsetSec = 0;  // secondi
bool clockSet       = false;
// Per impostare l'ora: nel menu STATUS tieni premuto B 3 sec (stub per ora)
// Valori di editing orologio
int  clockEditH  = 12, clockEditM = 0;
bool clockEditing = false;

// Bottoni
bool btnAPrev = HIGH, btnBPrev = HIGH, btnCPrev = HIGH;

// ── Screen sleep ──────────────────────────────────────────────
// Segna attività e riaccende lo schermo se era spento.
void wakeScreen(unsigned long now) {
  lastActivityMs = now;
  if (!screenOn) {
    display.setBrightness(SCREEN_BRIGHTNESS);
    screenOn = true;
  }
}

// Isteresi orientamento — richiede N letture consecutive prima di cambiare
#define ORI_HYSTERESIS 8
Orientation oriBuffer[ORI_HYSTERESIS];
int oriBufferIdx = 0;
bool oriBufferFull = false;

Orientation stableOrientation() {
  // Restituisce l'orientamento solo se tutti i campioni nel buffer concordano
  Orientation first = oriBuffer[0];
  int count = oriBufferFull ? ORI_HYSTERESIS : oriBufferIdx;
  for (int i = 1; i < count; i++)
    if (oriBuffer[i] != first) return gOrient; // non stabile, mantieni corrente
  return first;
}

// Boot
int bootChoice = 0;  // 0=carica, 1=ricomincia
bool bootHasData = false;

// Menu
int  menuCursor   = 0;
const int MENU_ITEMS = 4;
const char* MENU_LABELS[] = { "Status", "Clean", "Heal", "Registro" };


// ── REGISTRO ──────────────────────────────────────────────────
// Tutte le creature del gioco in ordine
// Stat base: cuori 1-3 (basso/normale/alto) per STR/INT/ENG/HAP
struct PetEntry {
  const char*      name;
  const char*      element;
  const PetSprites* sprites;
  uint8_t          strH;   // cuori STR 1-3
  uint8_t          intH;   // cuori INT 1-3
  uint8_t          engH;   // cuori ENG 1-3
  uint8_t          hapH;   // cuori HAP 1-3
  uint8_t          obtained; // quante volte ottenuto (salvato NVS)
};

// Registro completo — 32 creature
// Linea 0=STR, 1=ENG, 2=INT per ogni elemento
PetEntry REGISTRO[] = {
  // ── Fire condivisi ──────────────────────────────────────────
  { "Kindlekin",      "Fire",  &SPR_KINDLEKIN,       1,1,1,2, 0 },
  { "Emberpaw",      "Fire",  &SPR_EMBERPAW,        1,1,1,2, 0 },
  { "Pyruff",       "Fire",  &SPR_PYRUFF,         2,1,2,2, 0 },
  // ── Fire linea STR ──────────────────────────────────────────
  { "Blazebrand",      "Fire",  &SPR_BLAZEBRAND,        3,1,2,2, 0 },
  { "Mightforge", "Fire",  &SPR_MIGHTFORGE,   3,2,2,2, 0 },
  { "Flameforge",   "Fire",  &SPR_FLAMEFORGE,     3,2,3,2, 0 },
  { "Seraphyre",   "Light", &SPR_SERAPHYRE,     2,3,2,3, 0 },
  // ── Fire linea ENG ──────────────────────────────────────────
  { "Shieldmane",   "Fire",  &SPR_SHIELDMANE,     2,1,3,2, 0 },
  { "Fortifire",   "Fire",  &SPR_FORTIFIRE,     2,2,3,2, 0 },
  { "Citadellion",      "Fire",  &SPR_CITADELLION,        3,2,3,2, 0 },
  // ── Fire linea INT ──────────────────────────────────────────
  { "Aurovulp",      "Fire",  &SPR_AUROVULP,        1,3,2,2, 0 },
  { "Vulpyre", "Fire",  &SPR_VULPYRE,   2,3,2,2, 0 },
  { "Eldervulp",    "Fire",  &SPR_ELDERVULP,      2,3,2,1, 0 },
  // ── Noxfortress (Dark condiviso Fire) ───────────────────────
  { "Noxfortress",  "Dark",  &SPR_NOXFORTRESS,    3,1,3,1, 0 },
  // ── Water condivisi ─────────────────────────────────────────
  { "Drowsea",      "Water", &SPR_DROWSEA,        1,1,1,2, 0 },
  { "Gloomfin",     "Water", &SPR_GLOOMFIN,        1,1,1,2, 0 },
  { "Fanglure",      "Water", &SPR_FANGLURE,         1,2,2,2, 0 },
  // ── Water linea STR ─────────────────────────────────────────
  { "Riptalon",    "Water", &SPR_RIPTALON,       2,2,2,2, 0 },
  { "Maulstream","Water", &SPR_MAULSTREAM,   3,2,2,2, 0 },
  { "Leviacrush","Water",&SPR_LEVIACRUSH,  3,2,3,2, 0 },
  { "Lightfin","Light", &SPR_LIGHTFIN,   2,3,2,3, 0 },
  // ── Water linea ENG ─────────────────────────────────────────
  { "Baleguard",       "Water", &SPR_BALEGUARD,        2,1,3,2, 0 },
  { "Bulwhark",       "Water", &SPR_BULWHARK,        2,2,3,2, 0 },
  { "Tidenaught","Water", &SPR_TIDENAUGHT, 2,2,3,2, 0 },
  // ── Water linea INT ─────────────────────────────────────────
  { "Sirenlure",      "Water", &SPR_SIRENLURE,        1,3,2,2, 0 },
  { "Abyssibyl",       "Water", &SPR_ABYSSIBYL,         1,3,2,2, 0 },
  { "Thalassibyl",    "Water", &SPR_THALASSIBYL,      1,3,2,3, 0 },
  // ── Nightmare (Dark condiviso Water) ──────────────────
  { "Nightmare","Dark",&SPR_NIGHTMARE,     3,1,3,1, 0 },
};
const int REGISTRO_SIZE = 28;
int registroCursor = 0;  // Creatura corrente nel registro

// Aggiorna ottenuto nel registro quando evolve
void registroMarkObtained(const char* name) {
  for (int i = 0; i < REGISTRO_SIZE; i++) {
    if (strcmp(REGISTRO[i].name, name) == 0) {
      REGISTRO[i].obtained++;
      // Salva in namespace separato "registro" — NON viene cancellato con il reset
      char key[12];
      sprintf(key, "r%d", i);
      prefs.begin("registro", false);
      prefs.putInt(key, REGISTRO[i].obtained);
      prefs.end();
      return;
    }
  }
}

void registroLoad() {
  // Carica da namespace "registro" — persiste anche dopo reset salvataggio
  prefs.begin("registro", true);
  for (int i = 0; i < REGISTRO_SIZE; i++) {
    char key[12];
    sprintf(key, "r%d", i);
    REGISTRO[i].obtained = prefs.getInt(key, 0);
  }
  prefs.end();
}

// ── Eredità genetica: carica/salva nel namespace "registro" ──────
void legacyLoad() {
  prefs.begin("registro", true);
  legendCount = prefs.getInt("legendCnt", 0);
  legacySTR   = prefs.getInt("legSTR", 0);
  legacyINT   = prefs.getInt("legINT", 0);
  legacyENG   = prefs.getInt("legENG", 0);
  prefs.end();
}

// Chiamata alla morte: se il pet ha raggiunto lo stadio finale, registra
// l'eredità (20% delle stat) e incrementa il contatore Leggende.
void legacyRecordOnDeath() {
  if (evoStage < 5) return;
  legendCount++;
  legacySTR = statSTR * LEGACY_STAT_PCT / 100;
  legacyINT = statINT * LEGACY_STAT_PCT / 100;
  legacyENG = statENG * LEGACY_STAT_PCT / 100;
  prefs.begin("registro", false);
  prefs.putInt("legendCnt", legendCount);
  prefs.putInt("legSTR", legacySTR);
  prefs.putInt("legINT", legacyINT);
  prefs.putInt("legENG", legacyENG);
  prefs.end();
}

// Consuma l'eredità su NVS (slot singolo, usa-e-getta): i valori restano in
// RAM per il feedback nella schermata di scelta del baby, finché non viene
// scelto l'elemento.
void legacyClearPersisted() {
  prefs.begin("registro", false);
  prefs.putInt("legSTR", 0);
  prefs.putInt("legINT", 0);
  prefs.putInt("legENG", 0);
  prefs.end();
}

// Menu aggiornato con Registro
// Setup
int setupChoice = 0;

// ── HELPERS ───────────────────────────────────────────────────
const PetSprites* getCurrentSprites() {
  int v = max(0, finalVariant);
  if (gElement == FIRE) {
    if (evoStage == 0) return &SPR_KINDLEKIN;
    if (evoStage == 1) return &SPR_EMBERPAW;
    if (evoStage == 2) return &SPR_PYRUFF;
    if (evoStage == 3) {
      if (lineVariant == 0) return &SPR_BLAZEBRAND;        // STR
      if (lineVariant == 1) return &SPR_SHIELDMANE;     // ENG
      return &SPR_AUROVULP;                              // INT
    }
    if (evoStage == 4) {
      if (lineVariant == 0) return &SPR_MIGHTFORGE;   // STR
      if (lineVariant == 1) return &SPR_FORTIFIRE;     // ENG
      return &SPR_VULPYRE;                         // INT
    }
    // Ultimate Fire
    if (lineVariant == 0) {
      const PetSprites* f0[] = { &SPR_FLAMEFORGE, &SPR_SERAPHYRE, &SPR_NOXFORTRESS };
      return f0[v];
    }
    if (lineVariant == 1) {
      const PetSprites* f1[] = { &SPR_CITADELLION,    &SPR_SERAPHYRE,  &SPR_NOXFORTRESS };
      return f1[v];
    }
    // INT line
    const PetSprites* f2[] = { &SPR_ELDERVULP,    &SPR_SERAPHYRE,    &SPR_NOXFORTRESS };
    return f2[v];
  } else {
    if (evoStage == 0) return &SPR_DROWSEA;
    if (evoStage == 1) return &SPR_GLOOMFIN;
    if (evoStage == 2) return &SPR_FANGLURE;
    if (evoStage == 3) {
      if (lineVariant == 0) return &SPR_RIPTALON;      // STR
      if (lineVariant == 1) return &SPR_BALEGUARD;      // ENG
      return &SPR_SIRENLURE;                              // INT
    }
    if (evoStage == 4) {
      if (lineVariant == 0) return &SPR_MAULSTREAM;  // STR
      if (lineVariant == 1) return &SPR_BULWHARK;      // ENG
      return &SPR_ABYSSIBYL;                               // INT
    }
    // Ultimate Water
    if (lineVariant == 0) {
      const PetSprites* w0[] = { &SPR_LEVIACRUSH,   &SPR_LIGHTFIN, &SPR_NIGHTMARE };
      return w0[v];
    }
    if (lineVariant == 1) {
      const PetSprites* w1[] = { &SPR_TIDENAUGHT, &SPR_LIGHTFIN,       &SPR_NIGHTMARE };
      return w1[v];
    }
    // INT line
    const PetSprites* w2[] = { &SPR_THALASSIBYL,        &SPR_LIGHTFIN,      &SPR_NIGHTMARE };
    return w2[v];
  }
}

const char* getCurrentName() {
  int v = max(0, finalVariant);
  if (gElement == FIRE) {
    if (evoStage < 3) return FIRE_SHARED[evoStage];
    if (evoStage < 5) {
      if (lineVariant == 0) return FIRE_LINE0[evoStage-3];
      if (lineVariant == 1) return FIRE_LINE1[evoStage-3];
      return FIRE_LINE2[evoStage-3];
    }
    if (lineVariant == 0) return FIRE_FINAL0[v];
    if (lineVariant == 1) return FIRE_FINAL1[v];
    return FIRE_FINAL2[v];
  } else {
    if (evoStage < 3) return WATER_SHARED[evoStage];
    if (evoStage < 5) {
      if (lineVariant == 0) return WATER_LINE0[evoStage-3];
      if (lineVariant == 1) return WATER_LINE1[evoStage-3];
      return WATER_LINE2[evoStage-3];
    }
    if (lineVariant == 0) return WATER_FINAL0[v];
    if (lineVariant == 1) return WATER_FINAL1[v];
    return WATER_FINAL2[v];
  }
}

SprFrame getFrame(const PetSprites* spr, unsigned long now) {
  if (isSick)
    return spr->sick[(now / 600) % 2];
  switch (gState) {
    case STATE_SLEEP:
      return spr->sleep[(now / ANIM_SLEEP_MS) % 2];
    case STATE_SESSION:
    case STATE_TRAINING:
    case STATE_WORK: {
      // Rallentato come idle: alterna atk1/idle/atk2/idle
      int f = (now / ANIM_ATK_MS) % 4;
      return (f == 0) ? spr->atk[0] : (f == 2) ? spr->atk[1] : spr->idle[0];
    }
    case STATE_STUDY: {
      int f = (now / ANIM_ATK_MS) % 4;
      return (f < 2) ? spr->happy[(now/ANIM_IDLE_MS)%2] : spr->idle[0];
    }
    default:
      if (statHAP > 80 && (now/1000)%6==0)
        return spr->happy[(now/ANIM_IDLE_MS)%2];
      return spr->idle[(now/ANIM_IDLE_MS)%3];
  }
}

#if SPRITES_PLACEHOLDER
// Frame d'animazione corrente come testo (placeholder finché le sprite non
// sono pronte), con colore in base allo stato: idle=giallo, angry=magenta,
// happy=verde, sick=arancio, sleep=blu.
FrameLabel getFrameLabel(unsigned long now) {
  if (isSick) {
    int f = (now / 600) % 2;
    return { f == 0 ? "sick1" : "sick2", C_TIMER };
  }
  switch (gState) {
    case STATE_SLEEP: {
      int f = (now / ANIM_SLEEP_MS) % 2;
      return { f == 0 ? "sleep1" : "sleep2", C_INT };
    }
    case STATE_SESSION:
    case STATE_TRAINING:
    case STATE_WORK: {
      int f = (now / ANIM_ATK_MS) % 4;
      return { (f < 2) ? "angry1" : "angry2", C_MAGENTA };
    }
    case STATE_STUDY: {
      int f = (now / ANIM_ATK_MS) % 4;
      if (f < 2) return { (now / ANIM_IDLE_MS) % 2 == 0 ? "happy1" : "happy2", C_HAP };
      return { "idle1", C_ENG };
    }
    default:
      if (statHAP > 80 && (now / 1000) % 6 == 0)
        return { (now / ANIM_IDLE_MS) % 2 == 0 ? "happy1" : "happy2", C_HAP };
      return { (now / ANIM_IDLE_MS) % 3 == 0 ? "idle1" : "idle2", C_ENG };
  }
}

// Colore del nome del mostro in base all'elemento (rosso=fuoco, ciano=acqua).
uint16_t getNameColor() {
  return (gElement == FIRE) ? C_STR : C_CYAN;
}

// Disegna nome + frame al posto della sprite, centrati nell'area sprite.
void drawSpritePlaceholder(int x, int y, int w, int h, unsigned long now) {
  const char* name = getCurrentName();
  FrameLabel fl = getFrameLabel(now);

  canvas.setTextFont(2); canvas.setTextSize(1);
  canvas.setTextColor(getNameColor(), C_BG);
  int nw = canvas.textWidth(name);
  canvas.drawString(name, x + (w - nw) / 2, y + h / 2 - 14);

  canvas.setTextColor(fl.color, C_BG);
  int fw = canvas.textWidth(fl.name);
  canvas.drawString(fl.name, x + (w - fw) / 2, y + h / 2 + 4);
}
#endif

// Calcola offset X per moto idle oscillatorio (seno approssimato)
int getIdleDriftX(unsigned long now) {
  // Prima metà del periodo: da -DRIFT a +DRIFT (verso destra)
  // Seconda metà: da +DRIFT a -DRIFT (verso sinistra)
  unsigned long t = now % SPR_DRIFT_PERIOD_MS;
  int half = SPR_DRIFT_PERIOD_MS / 2;
  int drift;
  if (t < (unsigned long)half) {
    drift = (int)(t * 2 * SPR_DRIFT / half) - SPR_DRIFT;
  } else {
    drift = SPR_DRIFT - (int)((t - half) * 2 * SPR_DRIFT / half);
  }
  return drift;
}

bool getIdleMirror(unsigned long now) {
  // Mirror = true nella seconda metà del periodo (movimento verso sinistra)
  unsigned long t = now % SPR_DRIFT_PERIOD_MS;
  return t < (unsigned long)(SPR_DRIFT_PERIOD_MS / 2);
}

void drawSpriteScaled(int x, int y, int scale,
                      const SprFrame& frame, bool mirror = false) {
  for (int row = 0; row < SPR_SIZE; row++) {
    uint8_t b0 = pgm_read_byte(&frame.mask[row * 2]);
    uint8_t b1 = pgm_read_byte(&frame.mask[row * 2 + 1]);
    uint16_t rowbits = (uint16_t)b0 | ((uint16_t)b1 << 8);
    for (int col = 0; col < SPR_SIZE; col++) {
      if (rowbits & (1 << col)) {
        int drawCol = mirror ? (SPR_SIZE - 1 - col) : col;
        uint16_t px = pgm_read_word(&frame.px[row * SPR_SIZE + col]);
        canvas.fillRect(x + drawCol*scale, y + row*scale, scale, scale, px);
      }
    }
  }
}

void drawPoopIcon(int x, int y) {
  const int s = 3;
  canvas.fillRect(x+2*s, y,     3*s, s, C_POOP);
  canvas.fillRect(x+s,   y+s,   5*s, s, C_POOP);
  canvas.fillRect(x+2*s, y+2*s, 3*s, s, C_POOP);
  canvas.fillRect(x+s,   y+3*s, 5*s, s, C_POOP);
  canvas.fillRect(x,     y+4*s, 7*s, s, C_POOP);
  canvas.fillRect(x,     y+5*s, 7*s, s, C_POOP);
}

void drawBar(int x, int y, int w, int h, int val, uint16_t color = C_HAP) {
  canvas.drawRect(x, y, w, h, C_DIM);
  int fill = val * (w-2) / 100;
  if (fill > 0) canvas.fillRect(x+1, y+1, fill, h-2, color);
}

// ── NVS ───────────────────────────────────────────────────────
void saveToNVS() {
  prefs.begin("petcube", false);
  prefs.putInt("el",      (int)gElement);
  prefs.putInt("str",     statSTR);
  prefs.putInt("int_s",   statINT);
  prefs.putInt("eng",     statENG);
  prefs.putInt("hap",     statHAP);
  prefs.putInt("sTotal",  sessTotal);
  prefs.putInt("sAct",    sessActive);
  prefs.putInt("stage",   evoStage);
  prefs.putInt("variant", finalVariant);
  prefs.putInt("lineVar", lineVariant);
  prefs.putInt("poop",    poopCount);
  prefs.putBool("mega",   poopMega);
  prefs.putBool("sick",   isSick);
  prefs.putInt("bWon",    battlesWon);
  prefs.putInt("bLost",   battlesLost);
  prefs.putULong("lastSes",  lastSessionMs);
  prefs.putULong("lastDec",  lastDecayMs);
  prefs.putULong("nextPoop", nextPoopMs);
  prefs.putUInt("pomoMin",   pomodoroMs / 60000);
  prefs.putUInt("restMin",   restMs     / 60000);
  prefs.putULong("sickMs",   sickStartMs);
  prefs.putULong("sickDec",  lastSickDecayMs);
  prefs.putInt("sickEp",     sickEpisodes);
  prefs.putUChar("streak",   battleStreak);
  prefs.putString("tag",     petTag);
  prefs.end();
}

bool loadFromNVS() {
  prefs.begin("petcube", true);
  bool ok = prefs.isKey("el");
  if (ok) {
    gElement      = (Element)prefs.getInt("el",    0);
    statSTR       = prefs.getInt("str",    0);
    statINT       = prefs.getInt("int_s",  0);
    statENG       = prefs.getInt("eng",    0);
    statHAP       = prefs.getInt("hap",    50);
    sessTotal     = prefs.getInt("sTotal", 0);
    sessActive    = prefs.getInt("sAct",   0);
    evoStage      = prefs.getInt("stage",  0);
    finalVariant  = prefs.getInt("variant",-1);
    lineVariant   = prefs.getInt("lineVar", 0);
    poopCount     = prefs.getInt("poop",   0);
    poopMega      = prefs.getBool("mega",  false);
    isSick        = prefs.getBool("sick",  false);
    battlesWon    = prefs.getInt("bWon",   0);
    battlesLost   = prefs.getInt("bLost",  0);
    lastSessionMs = prefs.getULong("lastSes",  0);
    lastDecayMs   = prefs.getULong("lastDec",  0);
    nextPoopMs    = prefs.getULong("nextPoop", 0);
    pomodoroMs    = prefs.getUInt("pomoMin", POMO_DEFAULT_MS / 60000) * 60000UL;
    restMs        = prefs.getUInt("restMin", REST_DEFAULT_MS / 60000) * 60000UL;
    sickStartMs   = prefs.getULong("sickMs",   0);
    lastSickDecayMs = prefs.getULong("sickDec",0);
    sickEpisodes  = prefs.getInt("sickEp",  0);
    battleStreak  = prefs.getUChar("streak", 0);
  }
  // Tag identità multiplayer: caricato a prescindere, può essere stato scritto
  // via BLE prima ancora di un primo salvataggio completo della partita.
  petTag = prefs.getString("tag", "");
  prefs.end();
  return ok;
}

// Reset per una nuova partita (mantiene il registro e l'eredità persistenti).
// Usato sia da "Nuova partita" nella schermata di boot sia dopo la morte del
// pet. Applica l'eredità genetica (se presente) come stat di partenza del
// nuovo baby e consuma lo slot su NVS (resta in RAM per il feedback a schermo).
void resetForNewGame(unsigned long now) {
  prefs.begin("petcube",false); prefs.clear(); prefs.end();
  // Riscrivo subito fw_version per evitare retrigger della migrazione
  prefs.begin("petcube",false); prefs.putInt("fw_ver", FW_VERSION); prefs.end();
  statSTR = legacySTR; statINT = legacyINT; statENG = legacyENG;
  statHAP = 50;
  sessTotal=sessActive=0; evoStage=0; finalVariant=-1; lineVariant=0;
  battlesWon=battlesLost=0; poopCount=0; poopMega=false;
  isSick=false; sickStartMs=0;
  sickEpisodes=0;
  pomoPhase=POMO_NONE; pomodoroMs=POMO_DEFAULT_MS; restMs=REST_DEFAULT_MS;
  lastSessionMs=0; lastDecayMs=now;
  nextPoopMs = now + randomPoopInterval();
  clockSet=false; clockOffsetSec=0;
  clockEditH=12; clockEditM=0;
  gState  = STATE_SETUP;
  gScreen = SCR_CLOCK;  // imposta prima l'orologio
  legacyClearPersisted();
}

// Migrazione NVS: al primo boot di una nuova versione firmware,
// resetta TUTTI i namespace (petcube + registro) per garantire coerenza dati.
// Necessario perché la v12 introduce campi nuovi (sickEpisodes) e cambia la
// semantica di altri (sessActive era ridondante).
bool migrateNVSIfNeeded() {
  prefs.begin("petcube", true);
  int storedVersion = prefs.getInt("fw_ver", 0);
  prefs.end();
  if (storedVersion == FW_VERSION) return false;  // già migrato
  // Reset totale: partita E registro
  prefs.begin("petcube", false);  prefs.clear();  prefs.end();
  prefs.begin("registro", false); prefs.clear();  prefs.end();
  // Scrivo la nuova versione subito così non si ripete al prossimo boot
  prefs.begin("petcube", false);
  prefs.putInt("fw_ver", FW_VERSION);
  prefs.end();
  Serial.printf("NVS migrato a v%d (reset totale)\n", FW_VERSION);
  return true;
}

// ── ORIENTAMENTO ──────────────────────────────────────────────
Orientation detectOrientation(float ax, float ay, float az) {
  float t = ORIENT_THRESHOLD;
  if      (az < -t) return ORI_NORMAL;
  else if (ax >  t) return ORI_FACE_UP;
  else if (ax < -t) return ORI_FACE_DOWN;
  else if (ay >  t) return ORI_RIGHT;
  else if (az >  t) return ORI_UPSIDE_DOWN;
  else if (ay < -t) return ORI_LEFT;
  return ORI_NORMAL;
}

// ── EVOLUZIONE ────────────────────────────────────────────────
// Ordine: Dark > Light > Standard
//   Dark:  nessuna sessione in 36h E HAP <= 30
//   Light: HAP >= 80 E nessuna malattia in tutta la vita (sickEpisodes == 0)
//   Std:   in tutti gli altri casi
int checkEvoVariant() {
  unsigned long now = millis();
  bool noSess = lastSessionMs > 0 && (now - lastSessionMs > 36UL*3600000);
  if (noSess && statHAP <= 30) return 2;                  // Dark
  if (statHAP >= 80 && sickEpisodes == 0) return 1;       // Light
  return 0;                                                // Standard
}

void checkEvolution() {
  if (evoStage >= 5) return;
  int next = evoStage + 1;
  if (sessTotal < EVO_THRESH[next]) return;
  if (next == 5) finalVariant = checkEvoVariant();
  // A Child->Adult (stage 2->3) determina la linea in base alla stat dominante
  if (next == 3) {
    // La stat più alta determina la linea (3 vie: STR / ENG / INT)
    // In caso di pareggio: priorità STR > ENG > INT
    if (statSTR >= statENG && statSTR >= statINT) {
      lineVariant = 0;  // STR
    } else if (statENG >= statINT) {
      lineVariant = 1;  // ENG
    } else {
      lineVariant = 2;  // INT
    }
    const char* lnames[] = { "STR", "ENG", "INT" };
    Serial.printf("Linea evolutiva: %d (%s)\n", lineVariant, lnames[lineVariant]);
  }
  evoStage = next;
  gState = STATE_EVOLVING;
  // Segna la nuova creatura nel registro
  registroMarkObtained(getCurrentName());
  // Jingle evoluzione — evolveStartMs impostato DOPO i delay
  // così i 3 secondi partono quando lo schermo appare davvero
  tone(BUZZER,523,80); delay(90);
  tone(BUZZER,659,80); delay(90);
  tone(BUZZER,784,80); delay(90);
  tone(BUZZER,1047,400); delay(410);
  evolveStartMs = millis();
  saveToNVS();
}

// ── POMODORO ──────────────────────────────────────────────────
// 1° B in Training/Study/Work: apre il setup durata pomodoro
void beginPomodoroSetup(GameState type) {
  sessionType = type;
  gState      = STATE_SESSION;
  pomoPhase   = POMO_SET_WORK;
  tone(BUZZER, 880, 80);
}

// A/C durante POMO_SET_WORK: regola la durata pomodoro di ±5 min
void adjustPomodoroWorkMs(bool increase) {
  long ms = (long)pomodoroMs + (increase ? (long)POMO_STEP_MS : -(long)POMO_STEP_MS);
  pomodoroMs = (unsigned long)constrain(ms, (long)POMO_MIN_MS, (long)POMO_MAX_MS);
  tone(BUZZER, 660, 30);
}

// A/C durante POMO_SET_REST: regola la durata riposo di ±1 min
void adjustPomodoroRestMs(bool increase) {
  long ms = (long)restMs + (increase ? (long)REST_STEP_MS : -(long)REST_STEP_MS);
  restMs = (unsigned long)constrain(ms, (long)REST_MIN_MS, (long)REST_MAX_MS);
  tone(BUZZER, 660, 30);
}

// 2° B: passa al setup durata riposo. 3° B: avvia il pomodoro.
void advancePomodoroSetup() {
  if (pomoPhase == POMO_SET_WORK) {
    pomoPhase = POMO_SET_REST;
    tone(BUZZER, 988, 80);
  } else if (pomoPhase == POMO_SET_REST) {
    pomoPhase      = POMO_RUN_WORK;
    sessionRunning = true;
    sessionStartMs = millis();  // impostato DOPO i tone per evitare underflow
    tone(BUZZER, 1175, 120);
  }
}

// Pomodoro completato: stat/sessioni in proporzione ai 25min completati,
// poi avvia automaticamente il riposo.
void completePomodoroWork() {
  lastSessionMs = millis();
  unsigned long units = pomodoroMs / POMO_UNIT_MS;
  if (units > 0) {
    sessTotal  += units;
    sessActive += units;
    switch (sessionType) {
      case STATE_TRAINING: statSTR = min(100, statSTR + STAT_PER_SESSION*(int)units); break;
      case STATE_STUDY:    statINT = min(100, statINT + STAT_PER_SESSION*(int)units); break;
      default:             statENG = min(100, statENG + STAT_PER_SESSION*(int)units); break;
    }
  }
  tone(BUZZER,1047,80); delay(90);
  tone(BUZZER,1319,80); delay(90);
  tone(BUZZER,1568,200);
  checkEvolution();
  saveToNVS();
  pomoPhase      = POMO_RUN_REST;
  sessionStartMs = millis();  // impostato DOPO i tone per evitare underflow
}

// Riposo completato: aumenta la felicità e chiude il pomodoro.
void completePomodoroRest() {
  statHAP = min(100, statHAP + HAP_PER_SESSION);
  tone(BUZZER,1568,80); delay(90);
  tone(BUZZER,1319,80); delay(90);
  tone(BUZZER,1047,200);
  saveToNVS();
  sessionRunning = false;
  pomoPhase      = POMO_NONE;
  // Imposta lo stato in base all'orientamento corrente, non torna di default a IDLE
  if (gState != STATE_EVOLVING) enterStateFromOri(gOrient);
}

// Annulla setup/pomodoro/riposo in corso. Penalità HAP solo se il
// pomodoro o il riposo erano già avviati (non durante il setup).
void cancelPomodoro() {
  if (pomoPhase == POMO_RUN_WORK || pomoPhase == POMO_RUN_REST) {
    statHAP = max(0, statHAP - CANCEL_HAP_MALUS);
  }
  sessionRunning = false;
  pomoPhase      = POMO_NONE;
  gState = STATE_IDLE;
  tone(BUZZER, 440, 150);
  saveToNVS();
}

// ── DECAY ─────────────────────────────────────────────────────
void checkDecay(unsigned long now) {
  // Sospeso in Sleep (cubo in posizione normale)
  if (gOrient == ORI_NORMAL) { lastDecayMs = now; return; }
  if (lastDecayMs == 0) { lastDecayMs = now; return; }
  if (now - lastDecayMs < DECAY_WINDOW_MS) return;
  bool noSess = lastSessionMs == 0 || (now - lastSessionMs >= DECAY_WINDOW_MS);
  if (noSess) {
    statHAP = max(0, statHAP - DECAY_AMOUNT);
    saveToNVS();
  }
  lastDecayMs = now;
}

// ── ESCREMENTI ────────────────────────────────────────────────
unsigned long randomPoopInterval() {
  long range = POOP_INTERVAL_MAX_MS - POOP_INTERVAL_MIN_MS;
  return POOP_INTERVAL_MIN_MS + (unsigned long)(random(range));
}

void checkPoop(unsigned long now) {
  if (isSick) return;
  if (gState == STATE_DEAD)  return;
  if (gState == STATE_SLEEP) return;  // in Sleep non si producono escrementi
  if (nextPoopMs == 0) {
    nextPoopMs = now + randomPoopInterval();
    return;
  }
  if (now < nextPoopMs) return;

  // Appare un nuovo escremento
  if (!poopMega && poopCount < 4) {
    poopCount++;
    statHAP = max(0, statHAP - POOP_HAP_MALUS);
    tone(BUZZER, 200, 100);
  } else if (!poopMega && poopCount >= 4) {
    // 5° escremento: diventa mega
    poopMega  = true;
    poopCount = 1;   // 1 mega conta come 1
    statHAP   = max(0, statHAP - POOP_MEGA_MALUS);
    tone(BUZZER, 150, 300);
  } else if (poopMega) {
    // Il mega non è stato pulito: la creatura si ammala
    isSick        = true;
    sickStartMs   = now;
    lastSickDecayMs = now;
    sickEpisodes++;   // L4: tracking malattie per logica Light
    statHAP = max(0, statHAP - POOP_SICK_MALUS);
    tone(BUZZER, 100, 500);
    saveToNVS();
    nextPoopMs = 0;
    return;
  }
  nextPoopMs = now + randomPoopInterval();
  saveToNVS();
}

void cleanPoop(unsigned long now) {
  poopCount  = 0;
  poopMega   = false;
  tone(BUZZER, 880, 80); delay(90); tone(BUZZER, 1047, 80);
  // millis() fresco DOPO i delay — evita che nextPoopMs cada già nel passato
  nextPoopMs = millis() + randomPoopInterval();
  saveToNVS();
}

// ── MALATTIA ──────────────────────────────────────────────────
void checkSick(unsigned long now) {
  if (!isSick) return;
  // Decay HAP per malattia (ogni ora)
  if (lastSickDecayMs == 0) lastSickDecayMs = now;
  if (now - lastSickDecayMs >= 3600000UL) {
    statHAP = max(0, statHAP - SICK_HAP_DECAY);
    lastSickDecayMs = now;
    saveToNVS();
  }
  // Morte dopo 2 ore
  if (sickStartMs > 0 && now - sickStartMs >= SICK_DEATH_MS) {
    legacyRecordOnDeath();  // se evoStage==5: registra eredità + Leggenda
    gState = STATE_DEAD;
    gScreen = SCR_MAIN;
    tone(BUZZER, 200, 1000);
    saveToNVS();
  }
}

void healPet() {
  isSick        = false;
  sickStartMs   = 0;
  lastSickDecayMs = 0;
  poopCount     = 0;
  poopMega      = false;
  nextPoopMs    = millis() + randomPoopInterval();
  statHAP       = min(100, statHAP + 20);
  tone(BUZZER, 784, 80); delay(90);
  tone(BUZZER, 1047, 80); delay(90);
  tone(BUZZER, 1319, 200);
  saveToNVS();
}

// ── FEED ──────────────────────────────────────────────────────
// ── TRANSIZIONE STATO DA ORIENTAMENTO ────────────────────────
Orientation lastDisplayOri = ORI_NORMAL;

void updateDisplayRotation(Orientation ori) {
  if (ori == lastDisplayOri) return;
  lastDisplayOri = ori;
  switch (ori) {
    case ORI_LEFT:        display.setRotation(1); break;
    case ORI_RIGHT:       display.setRotation(3); break;
    case ORI_UPSIDE_DOWN: display.setRotation(2); break;
    case ORI_FACE_UP:     display.setRotation(2); break;
    default:              display.setRotation(0); break;
  }
}

void enterStateFromOri(Orientation ori) {
  if (gState == STATE_EVOLVING || gState == STATE_SETUP ||
      gState == STATE_DEAD) return;
  // Se è in corso un setup/pomodoro/riposo e cambia orientamento: annulla
  // senza penalità
  if (gState == STATE_SESSION && pomoPhase != POMO_NONE) {
    cancelPomodoro();
  }
  updateDisplayRotation(ori);
  switch (ori) {
    case ORI_NORMAL:      gState = STATE_SLEEP;     break;
    case ORI_LEFT:        gState = STATE_TRAINING;  break;
    case ORI_RIGHT:       gState = STATE_STUDY;     break;
    case ORI_FACE_UP:     gState = STATE_WORK;      break;
    case ORI_UPSIDE_DOWN: gState = STATE_DND;       break;
    case ORI_FACE_DOWN:   gState = STATE_IDLE;      break;
  }
}

// ═══════════════════════════════════════════════════════════════
//  DRAW FUNCTIONS
// ═══════════════════════════════════════════════════════════════

void setDisplayRotation(Orientation ori) {
  updateDisplayRotation(ori);
}

// ── Cuori helper ──────────────────────────────────────────────
void drawHeart(int x, int y, bool filled) {
  const int s = 3;
  uint16_t c = filled ? C_STR : C_DIM;
  if (filled) {
    canvas.fillRect(x+s,   y,     s,   s,   c);
    canvas.fillRect(x+3*s, y,     s,   s,   c);
    canvas.fillRect(x,     y+s,   5*s, 2*s, c);
    canvas.fillRect(x+s,   y+3*s, 3*s, s,   c);
    canvas.fillRect(x+2*s, y+4*s, s,   s,   c);
  } else {
    canvas.fillRect(x+s,   y,     s, s, c);
    canvas.fillRect(x+3*s, y,     s, s, c);
    canvas.fillRect(x,     y+s,   s, s, c);
    canvas.fillRect(x+4*s, y+s,   s, s, c);
    canvas.fillRect(x,     y+2*s, s, s, c);
    canvas.fillRect(x+4*s, y+2*s, s, s, c);
    canvas.fillRect(x+s,   y+3*s, s, s, c);
    canvas.fillRect(x+3*s, y+3*s, s, s, c);
    canvas.fillRect(x+2*s, y+4*s, s, s, c);
  }
}

void drawHearts(int x, int y, int filled, int total=3) {
  for (int i = 0; i < total; i++) {
    drawHeart(x + i*20, y, i < filled);
  }
}

// ── Registro ──────────────────────────────────────────────────
void drawRegistroScreen(unsigned long now) {
  canvas.fillSprite(C_BG);
  const PetEntry& e = REGISTRO[registroCursor];

  canvas.setTextFont(2); canvas.setTextColor(C_CYAN, C_BG);
  char hdr[24];
  sprintf(hdr, "%d/%d  Registro", registroCursor+1, REGISTRO_SIZE);
  drawCenteredStr(14, hdr);
  canvas.drawFastHLine(0, 32, DISP_SIZE, C_DIM);

  if (e.obtained == 0) {
    canvas.setTextFont(4); canvas.setTextColor(C_DIM, C_BG);
    drawCenteredStr(100, "???");
    canvas.setTextFont(2); canvas.setTextColor(C_FG, C_BG);
    drawCenteredStr(150, "Non ancora");
    drawCenteredStr(172, "ottenuto!");
  } else {
    // Sprite spostata a (28,50): a (14,40) l'angolo superiore sinistro
    // finiva fuori dall'area circolare visibile.
    const SprFrame& frame = e.sprites->idle[(now/ANIM_IDLE_MS)%3];
    drawSpriteScaled(28, 50, 4, frame);

    // Nome in font2 (anziché font4) e spostato a destra della sprite,
    // così i nomi lunghi non finiscono fuori dal cerchio visibile.
    canvas.setTextFont(2); canvas.setTextColor(C_FG, C_BG);
    canvas.drawString(e.name, 100, 58);
    canvas.setTextColor(C_CYAN, C_BG);
    char ob[20]; sprintf(ob, "%s  x%d", e.element, e.obtained);
    canvas.drawString(ob, 100, 80);

    // Cuori: S, I, E, H — due colonne
    canvas.setTextColor(C_FG, C_BG);
    canvas.drawString("S", 84, 102); drawHearts(100, 100, e.strH);
    canvas.drawString("I", 84, 130); drawHearts(100, 128, e.intH);
    canvas.drawString("E", 84, 158); drawHearts(100, 156, e.engH);
    canvas.drawString("H", 84, 186); drawHearts(100, 184, e.hapH);
  }

  canvas.drawFastHLine(0, 215, DISP_SIZE, C_DIM);
  // Font ridotto a size1 e centrato: a size2 il testo era troppo largo
  // per la corda del cerchio visibile a quest'altezza.
  canvas.setTextFont(1); canvas.setTextSize(1); canvas.setTextColor(C_DIM, C_BG);
  drawCenteredStr(222, "A=cicla  C=esci");
  canvas.pushSprite(0, 0);
}


void drawBootScreen() {
  canvas.fillSprite(C_BG);
  canvas.setTextFont(4); canvas.setTextColor(C_CYAN, C_BG);
  canvas.drawString("PetCube", 72, 40);
  canvas.setTextFont(2); canvas.setTextColor(C_DIM, C_BG);
  canvas.drawString("v0.24", 98, 74);
  // Badge permanente: numero di Leggende raggiunte (evoStage finale)
  if (legendCount > 0) {
    char lb[20]; sprintf(lb, "Leggende: %d", legendCount);
    canvas.setTextFont(1); canvas.setTextSize(2); canvas.setTextColor(C_HAP, C_BG);
    drawCenteredStr(88, lb);
    canvas.setTextSize(1);
  }
  canvas.drawFastHLine(30, 96, 180, C_DIM);

  // Opzione 0: continua
  uint16_t c0 = (bootChoice == 0) ? C_FG : C_DIM;
  if (bootChoice == 0) canvas.fillRect(30, 102, 180, 28, 0x1082);
  canvas.setTextFont(2); canvas.setTextColor(c0, C_BG);
  canvas.drawString("> Continua partita", 38, 109);

  // Opzione 1: nuova partita
  uint16_t c1 = (bootChoice == 1) ? C_FG : C_DIM;
  if (bootChoice == 1) canvas.fillRect(30, 140, 180, 28, 0x1082);
  canvas.setTextColor(c1, C_BG);
  canvas.drawString("> Nuova partita", 38, 147);

  canvas.drawFastHLine(30, 178, 180, C_DIM);
  canvas.setTextFont(1); canvas.setTextSize(2); canvas.setTextColor(C_DIM, C_BG);
  drawCenteredStr(188, "A=cambia  B=OK");
  canvas.setTextSize(1);
  canvas.pushSprite(0, 0);
}

void drawSetupScreen(unsigned long now) {
  canvas.fillSprite(C_BG);
  canvas.setTextFont(2); canvas.setTextColor(C_FG, C_BG);
  drawCenteredStr(18, "Scegli elemento:");
  canvas.drawFastHLine(20, 40, 200, C_DIM);

  // Eredità genetica: feedback delle stat ricevute dall'ultima Leggenda
  if (legacySTR > 0 || legacyINT > 0 || legacyENG > 0) {
    char buf[40];
    sprintf(buf, "Eredita: STR+%d INT+%d ENG+%d", legacySTR, legacyINT, legacyENG);
    canvas.setTextFont(1); canvas.setTextSize(1); canvas.setTextColor(C_FG, C_BG);
    drawCenteredStr(46, buf);
  }

  int frame = (now / 400) % 2;
  const SprFrame& botaFrame = SPR_KINDLEKIN.idle[frame];
  const SprFrame& puniFrame = SPR_DROWSEA.idle[frame];

  // Fire a sinistra, Water a destra (sprite ×5 = 80×80)
  const int sz = 5;
  int lx = 30, rx = 130, sy = 60;
  if (setupChoice == 0) canvas.drawRect(lx-4, sy-4, SPR_SIZE*sz+8, SPR_SIZE*sz+8, C_TIMER);
  else                  canvas.drawRect(rx-4, sy-4, SPR_SIZE*sz+8, SPR_SIZE*sz+8, C_CYAN);

  drawSpriteScaled(lx, sy, sz, botaFrame);
  drawSpriteScaled(rx, sy, sz, puniFrame);

  canvas.setTextFont(2);
  canvas.setTextColor(setupChoice==0 ? C_TIMER : C_DIM, C_BG);
  canvas.drawString("Fire",  45, 168);
  canvas.setTextColor(setupChoice==1 ? C_CYAN : C_DIM, C_BG);
  canvas.drawString("Water", 140, 168);

  // y=185 (anziché 210): a quella quota la corda del cerchio visibile
  // è larga abbastanza da contenere il testo senza tagli.
  canvas.setTextFont(1); canvas.setTextSize(2); canvas.setTextColor(C_DIM, C_BG);
  drawCenteredStr(185, "A=cambia  B=OK");
  canvas.setTextSize(1);
  canvas.pushSprite(0, 0);
}

// Prototipo esplicito: la generazione automatica di Arduino non gestisce
// correttamente i parametri di default e altrimenti non lo troverebbe
// nelle chiamate precedenti alla definizione (vicino a drawBattleScreen).
void drawBadgedCenteredStr(int y, const char* s, uint16_t color, int padX = 6, int padY = 2);

void drawMainScreen(unsigned long now) {
  // Sfondo ambientale per Idle/Sleep/DND/Work/Study/Training/Session
  // (pomodoro+riposo); solo Dead resta a sfondo nero. Ogni stato ha il
  // proprio sfondo (DND riusa quello di Sleep); durante una sessione lo
  // sfondo segue il tipo di sessione (Training/Study/Work), o quello di
  // Sleep durante il riposo.
  bool useBg = (gState == STATE_IDLE   || gState == STATE_SLEEP ||
                gState == STATE_DND    || gState == STATE_WORK  ||
                gState == STATE_STUDY  || gState == STATE_TRAINING ||
                gState == STATE_SESSION);
  if (useBg) {
    const uint16_t* bg = BG_NORMAL;
    switch (gState) {
      case STATE_SLEEP:
      case STATE_DND:      bg = BG_SLEEP;    break;
      case STATE_WORK:     bg = BG_WORK;     break;
      case STATE_STUDY:    bg = BG_STUDY;    break;
      case STATE_TRAINING: bg = BG_TRAINING; break;
      case STATE_SESSION:
        if (pomoPhase == POMO_RUN_REST || pomoPhase == POMO_SET_REST) {
          bg = BG_NORMAL;
        } else if (sessionType == STATE_TRAINING) {
          bg = BG_TRAINING;
        } else if (sessionType == STATE_STUDY) {
          bg = BG_STUDY;
        } else {
          bg = BG_WORK;
        }
        break;
      default: break;
    }
    canvas.pushImage(0, 0, DISP_SIZE, DISP_SIZE, bg);
  } else {
    canvas.fillSprite(C_BG);
  }
  const PetSprites* spr = getCurrentSprites();

  // ── DEAD ──────────────────────────────────────────────────────
  if (gState == STATE_DEAD) {
    if ((now/500)%2) {
      canvas.setTextFont(4); canvas.setTextColor(C_DIM, C_BG);
      canvas.drawString("ADDIO...", 68, 105);
    }
#if SPRITES_PLACEHOLDER
    drawSpritePlaceholder(SPR_X, SPR_Y, SPR_DRAW_SIZE, SPR_DRAW_SIZE, now);
#else
    drawSpriteScaled(SPR_X, SPR_Y, SPR_SCALE, spr->sick[(now/600)%2]);
#endif
    // Stadio finale raggiunto: lascia un'eredità al prossimo baby
    if (evoStage >= 5) {
      canvas.setTextFont(2); canvas.setTextColor(C_HAP, C_BG);
      drawCenteredStr(60, "LEGGENDA!");
    }
    canvas.setTextFont(1); canvas.setTextSize(2); canvas.setTextColor(C_DIM, C_BG);
    drawCenteredStr(185, "B: nuovo inizio");
    canvas.setTextSize(1);
    canvas.pushSprite(0, 0);
    return;
  }

  // ── Avviso malattia ───────────────────────────────────────────
  // Le etichette di stato (Work/Study/DND/Training/Rest) sono state rimosse:
  // lo sfondo ambientale identifica già lo stato. "SICK!" resta come avviso.
  if (isSick && (now/400)%2) {
    const char* sickLabel = "SICK!";
    canvas.setTextFont(2);
    if (useBg) {
      drawBadgedCenteredStr(14, sickLabel, C_STR);
    } else {
      int lw = canvas.textWidth(sickLabel);
      int lx = (DISP_SIZE - lw) / 2;
      canvas.setTextColor(C_STR, C_BG);
      canvas.drawString(sickLabel, lx, 14);
    }
  }

  // ── Setup pomodoro/riposo ────────────────────────────────────────
  if (pomoPhase == POMO_SET_WORK || pomoPhase == POMO_SET_REST) {
    unsigned long ms = (pomoPhase == POMO_SET_WORK) ? pomodoroMs : restMs;
    char buf[12];
    sprintf(buf, "%lu min", ms / 60000);
    canvas.setTextFont(2);
    int tw = canvas.textWidth(buf);
    if (useBg) {
      canvas.fillRoundRect((DISP_SIZE - tw) / 2 - 6, 32, tw + 12, 18, 4, C_BG);
    }
    canvas.setTextColor(C_TIMER, C_BG);
    canvas.drawString(buf, (DISP_SIZE - tw) / 2, 36);

    // Suggerimento comandi: spostato sotto la sprite e ingrandito
    // (font1 size2) con colore C_FG per restare leggibile sullo sfondo.
    canvas.setTextFont(1); canvas.setTextSize(2);
    const char* hint = "A+  C-  B=OK";
    if (useBg) {
      drawBadgedCenteredStr(190, hint, C_FG);
    } else {
      int hw = canvas.textWidth(hint);
      canvas.setTextColor(C_FG, C_BG);
      canvas.drawString(hint, (DISP_SIZE - hw) / 2, 190);
    }
    canvas.setTextSize(1);
  }
  // ── Timer pomodoro/riposo ─────────────────────────────────────
  else if (sessionRunning) {
    unsigned long total   = (pomoPhase == POMO_RUN_REST) ? restMs : pomodoroMs;
    unsigned long elapsed = now - sessionStartMs;
    unsigned long remain  = total > elapsed ? total - elapsed : 0;
    char buf[8];
    sprintf(buf, "%02lu:%02lu", remain/60000, (remain%60000)/1000);
    canvas.setTextFont(2);
    int tw = canvas.textWidth(buf);
    if (useBg) {
      canvas.fillRoundRect((DISP_SIZE - tw) / 2 - 6, 32, tw + 12, 18, 4, C_BG);
      canvas.fillRoundRect(24, 46, 192, 16, 4, C_BG);
    }
    canvas.setTextColor(C_TIMER, C_BG);
    canvas.drawString(buf, (DISP_SIZE - tw) / 2, 36);
    int prog = total > 0 ? (int)((unsigned long)elapsed * 180 / total) : 0;
    canvas.drawRect(30, 50, 180, 6, C_DIM);
    if (prog > 0) canvas.fillRect(31, 51, min(prog, 178), 4, C_TIMER);
  }

  // ── Icona BT ─────────────────────────────────────────────────
  // Posizionata entro l'area visibile circolare (Ø240px / 32.4mm):
  // a (188,12) l'icona finiva fuori dal cerchio e veniva tagliata dalla cornice.
  if (bleClientConnected) {
    canvas.setTextFont(1); canvas.setTextSize(2); canvas.setTextColor(C_CYAN, C_BG);
    canvas.drawString("B", 168, 14);
    canvas.setTextSize(1);
  } else if (bleAdvertising && (now/700)%2 == 0) {
    canvas.fillRect(168, 18, 6, 6, C_DIM);
  }

  // ── Sprite ───────────────────────────────────────────────────
#if SPRITES_PLACEHOLDER
  drawSpritePlaceholder(SPR_X, SPR_Y, SPR_DRAW_SIZE, SPR_DRAW_SIZE, now);
#else
  SprFrame frame = getFrame(spr, now);
  bool mirrorX = (gState == STATE_IDLE && !isSick) ? getIdleMirror(now) : false;
  drawSpriteScaled(SPR_X, SPR_Y, SPR_SCALE, frame, mirrorX);
#endif

  // ── Escrementi ───────────────────────────────────────────────
  if (gState == STATE_IDLE && !isSick) {
    if (poopMega) {
      // Mega: doppia dimensione, bottom-right (mx ridotto da 168 a 140
      // per restare entro l'area circolare visibile, vedi icona BT sopra)
      const int s = 4;
      int mx = 140, my = 193;
      canvas.fillRect(mx+3*s, my,     5*s, s, C_POOP);
      canvas.fillRect(mx+s,   my+s,   9*s, s, C_POOP);
      canvas.fillRect(mx+3*s, my+2*s, 5*s, s, C_POOP);
      canvas.fillRect(mx+s,   my+3*s, 9*s, s, C_POOP);
      canvas.fillRect(mx,     my+4*s, 11*s,s, C_POOP);
      canvas.fillRect(mx,     my+5*s, 11*s,s, C_POOP);
    } else if (poopCount > 0) {
      int startX = 163, startY = 193;
      for (int i = 0; i < poopCount && i < 4; i++) {
        int col = i % 2, row = i / 2;
        drawPoopIcon(startX - col * 26, startY - row * 22);
      }
    }
  }

  // ── Icona notifica ────────────────────────────────────────────
  if (gState == STATE_IDLE) {
    int n = countActiveNotifs();
    if (n > 0) {
      int firstIdx = firstActiveNotif();
      NotifSource src = pendingNotifs[firstIdx].pkt.source;
      int ix = (DISP_SIZE - ICON_NOTIF_SIZE) / 2, iy = 8;

      const uint16_t* iconPx = nullptr;
      switch (src) {
        case SRC_DISCORD:  iconPx = ICON_DISCORD;   break;
        case SRC_CALENDAR: iconPx = ICON_CALENDAR;  break;
        case SRC_GMAIL:    iconPx = ICON_GMAIL;     break;
        case SRC_TRELLO:   iconPx = ICON_HACKNPLAN; break;
        case SRC_TELEGRAM: iconPx = ICON_TELEGRAM;  break;
        case SRC_WHATSAPP: iconPx = ICON_WHATSAPP;  break;
        default: break;
      }

      if (iconPx) {
        canvas.pushImage(ix, iy, ICON_NOTIF_SIZE, ICON_NOTIF_SIZE, iconPx, (uint16_t)0x0000);
      } else {
        canvas.fillRoundRect(ix, iy, ICON_NOTIF_SIZE, ICON_NOTIF_SIZE, 4, C_DIM);
        canvas.setTextFont(2); canvas.setTextColor(C_BG, C_DIM);
        const char* ch = "?";
        switch (src) {
          case SRC_SLACK:  ch = "S"; break;
          case SRC_GITHUB: ch = "G"; break;
          default: break;
        }
        canvas.drawString(ch, ix + ICON_NOTIF_SIZE/2 - 4, iy + ICON_NOTIF_SIZE/2 - 8);
      }

      // Badge contatore: angolo in basso a sinistra dell'icona
      char cnt[2]; sprintf(cnt, "%d", n);
      int bx = ix - 5, by = iy + ICON_NOTIF_SIZE - 11;
      canvas.fillRoundRect(bx, by, 16, 16, 4, C_BG);
      canvas.setTextFont(1); canvas.setTextSize(2); canvas.setTextColor(C_STR, C_BG);
      canvas.drawString(cnt, bx+3, by);
      canvas.setTextSize(1);
    }
  }

  canvas.pushSprite(0, 0);
}

void drawMenuScreen(unsigned long now) {
  canvas.fillSprite(C_BG);

  // Sprite piccolo in alto centrato (×4 = 64×64)
#if SPRITES_PLACEHOLDER
  drawSpritePlaceholder(88, 10, 64, 64, now);
#else
  const PetSprites* spr = getCurrentSprites();
  SprFrame frame = getFrame(spr, now);
  drawSpriteScaled(88, 10, 4, frame);
#endif

  canvas.drawFastHLine(20, 82, 200, C_DIM);

  for (int i = 0; i < MENU_ITEMS; i++) {
    int y = 90 + i * 26;

    char label[20];
    strcpy(label, MENU_LABELS[i]);

    bool enabled = true;
    if (i == 1) enabled = (poopCount > 0 || poopMega);
    if (i == 2) enabled = isSick;

    uint16_t c = !enabled ? C_DIM : (i == menuCursor ? C_TIMER : C_FG);
    if (i == menuCursor) canvas.fillRect(20, y-2, 200, 22, 0x1082);
    canvas.setTextFont(2); canvas.setTextColor(c, C_BG);
    canvas.drawString(i == menuCursor ? (String(">") + label).c_str() : label, 30, y);
  }

  // Footer a size1 e centrato a y=216: a size2/y=226 il testo era
  // più largo della corda del cerchio visibile e finiva tagliato.
  canvas.drawFastHLine(20, 212, 200, C_DIM);
  canvas.setTextFont(1); canvas.setTextSize(1); canvas.setTextColor(C_DIM, C_BG);
  drawCenteredStr(216, "A=giu B=ok C=esci");
  canvas.pushSprite(0, 0);
}

void drawStatusScreen() {
  canvas.fillSprite(C_BG);

  // Nome + stadio. Font ridotto a 2 e centrato: a font4/x=20 i nomi più
  // lunghi (es. "Noxfortress") finivano fuori dall'area circolare visibile.
  canvas.setTextFont(2); canvas.setTextColor(C_FG, C_BG);
  drawCenteredStr(16, getCurrentName());

  // Tag identità multiplayer (es. "Mike#47213"), se assegnato dalla Companion App
  if (petTag.length() > 0) {
    canvas.setTextFont(1); canvas.setTextSize(1); canvas.setTextColor(C_DIM, C_BG);
    int tw = canvas.textWidth(petTag);
    canvas.drawString(petTag, max(0, 197 - tw), 36);
  }

  const char* stageNames[] = {"Spark","Wisp","Sprite","Spirit","Avatar","Primal"};
  canvas.setTextFont(2); canvas.setTextColor(C_CYAN, C_BG);
  canvas.drawString(stageNames[min(evoStage,5)], 24, 52);
  if (evoStage >= 3) {
    const char* lnames[] = { "STR", "ENG", "INT" };
    canvas.drawString(lnames[lineVariant], 120, 52);
  }
  canvas.drawFastHLine(10, 72, 220, C_DIM);

  // Barre stat
  const int bx = 55, bw = 130, bh = 10, lx = 14;
  canvas.setTextFont(2);
  canvas.setTextColor(C_HAP, C_BG); canvas.drawString("HAP", lx, 80);
  drawBar(bx, 82, bw, bh, statHAP, C_HAP);
  canvas.setTextColor(C_STR, C_BG); canvas.drawString("STR", lx, 100);
  drawBar(bx, 102, bw, bh, statSTR, C_STR);
  canvas.setTextColor(C_INT, C_BG); canvas.drawString("INT", lx, 120);
  drawBar(bx, 122, bw, bh, statINT, C_INT);
  canvas.setTextColor(C_ENG, C_BG); canvas.drawString("ENG", lx, 140);
  drawBar(bx, 142, bw, bh, statENG, C_ENG);

  canvas.drawFastHLine(10, 160, 220, C_DIM);

  // Sessioni / Evo / Battaglie
  char buf[24];
  canvas.setTextFont(2); canvas.setTextColor(C_FG, C_BG);
  sprintf(buf, "Sess: %d", sessTotal); canvas.drawString(buf, 20, 168);
  if (evoStage < 5) {
    sprintf(buf, "Evo: %d", EVO_THRESH[evoStage+1]); canvas.drawString(buf, 130, 168);
  }
  sprintf(buf, "W:%d  L:%d", battlesWon, battlesLost); canvas.drawString(buf, 24, 192);
  if (isSick) {
    canvas.setTextColor(C_STR, C_BG); canvas.drawString("MALATO!", 140, 192);
  }

  // Footer a size1 e centrato a y=218: a size2/y=220 era troppo largo
  // per la corda del cerchio visibile e finiva tagliato.
  canvas.drawFastHLine(10, 214, 220, C_DIM);
  canvas.setTextFont(1); canvas.setTextSize(1); canvas.setTextColor(C_DIM, C_BG);
  drawCenteredStr(218, "B=ora C=indietro");
  canvas.pushSprite(0, 0);
}

void drawClockScreen(unsigned long now) {
  canvas.fillSprite(C_BG);

  long totalSec = (long)(now / 1000) + clockOffsetSec;
  int  hh = (totalSec / 3600) % 24;
  int  mm = (totalSec / 60)   % 60;
  int  ss =  totalSec         % 60;

  if (!clockSet) {
    canvas.setTextFont(2); canvas.setTextColor(C_FG, C_BG);
    drawCenteredStr(28, "Imposta ora CEST:");
    canvas.drawFastHLine(20, 50, 200, C_DIM);

    char buf[12];
    sprintf(buf, "%02d : %02d", clockEditH, clockEditM);
    canvas.setTextFont(4); canvas.setTextColor(C_TIMER, C_BG);
    int tw = canvas.textWidth(buf);
    canvas.drawString(buf, (DISP_SIZE - tw) / 2, 90);

    // Hint a size1 e centrati: a size2 la seconda riga era troppo larga
    // per la corda del cerchio visibile e finiva tagliata.
    canvas.setTextFont(1); canvas.setTextSize(1); canvas.setTextColor(C_DIM, C_BG);
    drawCenteredStr(160, "A=+ora B=+min");
    drawCenteredStr(174, "C=salva (salta=C)");
  } else {
    char buf[10];
    sprintf(buf, "%02d:%02d", hh, mm);
    canvas.setTextFont(4); canvas.setTextColor(C_FG, C_BG);
    int tw = canvas.textWidth(buf);
    canvas.drawString(buf, (DISP_SIZE - tw) / 2, 70);

    // Barra secondi
    canvas.drawRect(30, 110, 180, 10, C_DIM);
    canvas.fillRect(31, 111, ss * 178 / 59, 8, C_CYAN);

    // Sprite animato centrato. Y ridotta da 128 a 100: a y=128 il bordo
    // inferiore (y=240) finiva ben fuori dall'area circolare visibile.
#if SPRITES_PLACEHOLDER
    drawSpritePlaceholder(SPR_X, 100, SPR_DRAW_SIZE, SPR_DRAW_SIZE, now);
#else
    const PetSprites* spr = getCurrentSprites();
    drawSpriteScaled(SPR_X, 100, SPR_SCALE, spr->idle[(now/400)%3]);
#endif

    // Hint a size1 e centrato: a size2/x=75 finiva tagliato a destra.
    canvas.setTextFont(1); canvas.setTextSize(1); canvas.setTextColor(C_DIM, C_BG);
    drawCenteredStr(220, "C = chiudi");
  }

  canvas.pushSprite(0, 0);
}

void handleClockButtons(bool btnANow, bool btnBNow, bool btnCNow) {
  if (!clockSet) {
    // Modalità impostazione ora
    if (btnAPrev==HIGH && btnANow==LOW) {
      clockEditH = (clockEditH + 1) % 24;
      tone(BUZZER, 660, 30);
      delay(50);
    }
    if (btnBPrev==HIGH && btnBNow==LOW) {
      clockEditM = (clockEditM + 1) % 60;
      tone(BUZZER, 880, 30);
      delay(50);
    }
    if (btnCPrev==HIGH && btnCNow==LOW) {
      // Salva: calcola offset
      long nowSec = (long)(millis() / 1000);
      long inputSec = (long)clockEditH * 3600 + (long)clockEditM * 60;
      clockOffsetSec = inputSec - nowSec;
      clockSet = true;
      tone(BUZZER, 784, 80); delay(90); tone(BUZZER, 1047, 150);
      delay(50);
      // Vai alla schermata giusta dopo l'orologio
      if (gState == STATE_SETUP)
        gScreen = SCR_MAIN;  // setup uovo (gState rimane STATE_SETUP)
      else
        gScreen = SCR_MAIN;  // gioco normale
    }
  } else {
    // Orologio visibile — C chiude
    if (btnCPrev==HIGH && btnCNow==LOW) {
      gScreen = SCR_MAIN;
      delay(50);
    }
  }
}

void drawEvolvingScreen(unsigned long now) {
  canvas.fillSprite(C_BG);
  const PetSprites* spr = getCurrentSprites();

  unsigned long nowFresh = millis();
  unsigned long el = (nowFresh >= evolveStartMs) ? (nowFresh - evolveStartMs) : 0;

  // Flash sprite
  if ((now / 80) % 2 == 0) {
#if SPRITES_PLACEHOLDER
    drawSpritePlaceholder(SPR_X, SPR_Y, SPR_DRAW_SIZE, SPR_DRAW_SIZE, now);
#else
    drawSpriteScaled(SPR_X, SPR_Y, SPR_SCALE, spr->idle[0]);
#endif
  }

  canvas.setTextFont(4); canvas.setTextColor(C_TIMER, C_BG);
  drawCenteredStr(14, "Evoluzione!");

  // Barra progresso
  int prog = min((int)(el * DISP_SIZE / EVOLVE_ANIM_MS), DISP_SIZE);
  canvas.fillRect(0, 44, prog, 4, C_TIMER);

  if (el > EVOLVE_ANIM_MS / 2) {
    canvas.setTextFont(2); canvas.setTextColor(C_FG, C_BG);
    int tw = canvas.textWidth(getCurrentName());
    canvas.drawString(getCurrentName(), (DISP_SIZE - tw) / 2, 210);
  }

  canvas.pushSprite(0, 0);

  if (el >= EVOLVE_ANIM_MS) {
    gState  = STATE_IDLE;
    gScreen = SCR_MAIN;
    enterStateFromOri(gOrient);
  }
}

// ── AZIONE MENU ───────────────────────────────────────────────
void executeMenuItem(unsigned long now) {
  switch (menuCursor) {
    case 0: // Status
      gScreen = SCR_STATUS;
      break;
    case 1: // Clean
      if (poopCount > 0 || poopMega) {
        cleanPoop(now);
        gScreen = SCR_MAIN;
      }
      break;
    case 2: // Heal
      if (isSick) {
        healPet();
        gScreen = SCR_MAIN;
      }
      break;
    case 3: // Registro
      registroCursor = 0;
      gScreen = SCR_REGISTRO;
      break;
  }
}

// ══════════════════════════════════════════════════════════════
//  📡 MODULO BLE GATT SERVER
// ══════════════════════════════════════════════════════════════

// Forward decl per usare onNotificationReceived dal callback
void onNotificationReceived(const NotifPacket& pkt);

// Callback connessione/disconnessione del client (es. PC con bleak)
class PetCubeBLEServerCallbacks : public BLEServerCallbacks {
  void onConnect(BLEServer* server) override {
    bleClientConnected = true;
    // Lo stack BLE dell'ESP32 ferma l'advertising automaticamente alla
    // connessione: allineiamo il flag, altrimenti bleUpdateState() penserà
    // che sia ancora attivo e non lo riavvierà più dopo la disconnessione.
    bleAdvertising = false;
    // Il main loop suonerà il beep di connessione vedendo il cambio di stato
    // (qui non chiamiamo tone() perché siamo in un task BLE, meglio non bloccare)
    Serial.printf("📡 BLE client connesso (t=%lu ms)\n", millis());
  }
  void onDisconnect(BLEServer* server) override {
    bleClientConnected = false;
    // ESP32 BLE non riavvia l'advertising automaticamente dopo disconnessione
    // Lo riavvieremo dal main loop in bleUpdateState()
    Serial.printf("📡 BLE client disconnesso (t=%lu ms)\n", millis());
    if (otaState == OTA_RECEIVING || otaState == OTA_AWAIT_CONFIRM) {
      Serial.printf("⚠️  OTA: disconnesso a %u/%u bytes\n",
                    (unsigned)otaBytesReceived, (unsigned)otaTotalSize);
    }
  }
};

// Callback ricezione dati su characteristic
class PetCubeBLECharCallbacks : public BLECharacteristicCallbacks {
  void onWrite(BLECharacteristic* ch) override {
    String value = ch->getValue();
    size_t len = value.length();
    if (len != sizeof(NotifPacket)) {
      Serial.printf("⚠️  BLE write con size sbagliata: %d (atteso %d)\n",
                    (int)len, (int)sizeof(NotifPacket));
      return;
    }
    NotifPacket pkt;
    memcpy(&pkt, value.c_str(), sizeof(NotifPacket));
    if (pkt.version != 1) {
      Serial.printf("⚠️  BLE pacchetto con versione %d (atteso 1)\n", pkt.version);
      return;
    }
    Serial.printf("📡 BLE write ricevuto: %d byte\n", (int)len);
    onNotificationReceived(pkt);
  }
};

// Callback scrittura/lettura tag identità multiplayer ("username#12345").
// La Companion App scrive il tag dopo la connessione; viene persistito in NVS
// così sopravvive ai riavvii del cubo.
class PetCubeIdentityCallbacks : public BLECharacteristicCallbacks {
  void onWrite(BLECharacteristic* ch) override {
    String value = ch->getValue();
    if (value.length() > 31) value = value.substring(0, 31);
    petTag = value;
    ch->setValue(petTag.c_str());
    prefs.begin("petcube", false);
    prefs.putString("tag", petTag);
    prefs.end();
    Serial.printf("📡 Tag identità impostato: %s\n", petTag.c_str());
  }
};

// ── OTA Callbacks ────────────────────────────────────────────

// CTRL characteristic:
//   Write 0x01 + uint32_le(total_size)  → avvia OTA session
//   Write 0x02                          → commit (verifica + riavvio)
//   Write 0x03                          → abort
//   Read                                → byte di stato (OtaState)
class PetCubeOtaCtrlCallbacks : public BLECharacteristicCallbacks {
  void onWrite(BLECharacteristic* ch) override {
    String val = ch->getValue();
    if (val.length() == 0) return;
    uint8_t cmd = (uint8_t)val[0];

    if (cmd == 0x01 && val.length() >= 5) {
      // START — bytes 1-4 = total firmware size (little-endian)
      uint32_t sz;
      memcpy(&sz, val.c_str() + 1, 4);

      // OTA segmentata su più connessioni: ogni connessione BLE dura solo
      // ~97s, troppo poco per ~1MB. La companion riconnette e ripete START
      // più volte; se è già in corso una sessione per la stessa size, non
      // ricominciamo da capo ma rispondiamo con otaBytesReceived così la
      // companion sa da dove riprendere.
      bool resume = (otaState == OTA_RECEIVING && sz == otaTotalSize);
      if (!resume) {
        Update.abort();
        xQueueReset(otaChunkQueue);  // scarta eventuali chunk residui di una sessione precedente
        if (Update.begin(sz, U_FLASH)) {
          otaState = OTA_RECEIVING;
          otaBytesReceived = 0;
          otaTotalSize = sz;
          Serial.printf("OTA START: %u bytes\n", sz);
        } else {
          otaState = OTA_ERROR;
          uint8_t err = 0x00;
          ch->setValue(&err, 1);
          Serial.println("OTA START: Update.begin() fallito");
          return;
        }
      } else {
        Serial.printf("OTA START: ripresa da %u/%u bytes\n", (unsigned)otaBytesReceived, (unsigned)otaTotalSize);
      }

      // Risposta: 0x01 + otaBytesReceived (uint32 little-endian), così la
      // companion sa da quale offset continuare l'invio dei chunk.
      uint8_t resp[5] = { 0x01, 0, 0, 0, 0 };
      uint32_t br = otaBytesReceived;
      memcpy(resp + 1, &br, 4);
      ch->setValue(resp, 5);

    } else if (cmd == 0x02) {
      // COMMIT — trasferimento completato: NON finalizzare subito,
      // attendi la conferma dell'utente (B/C) sullo schermo del PetCube.
      if (otaState == OTA_RECEIVING) {
        otaState = OTA_AWAIT_CONFIRM;
        uint8_t st = (uint8_t)otaState;
        ch->setValue(&st, 1);
        Serial.println("OTA COMMIT: in attesa di conferma sul dispositivo");
      } else {
        Update.abort();
        otaState = OTA_ERROR;
        uint8_t err = 0x00;
        ch->setValue(&err, 1);
        Serial.println("OTA COMMIT fallito: stato non valido");
      }

    } else if (cmd == 0x03) {
      // ABORT
      Update.abort();
      otaState = OTA_IDLE;
      Serial.println("OTA abortito dal client");
    }
  }

  void onRead(BLECharacteristic* ch) override {
    // Risposta estesa: stato (1 byte) + otaBytesReceived (uint32 LE).
    // Usata dalla companion per il polling durante OTA_AWAIT_CONFIRM e per
    // sapere a che punto è il trasferimento dopo una riconnessione.
    uint8_t resp[5] = { (uint8_t)otaState, 0, 0, 0, 0 };
    uint32_t br = otaBytesReceived;
    memcpy(resp + 1, &br, 4);
    ch->setValue(resp, 5);
  }
};

// DATA characteristic: write without response — riceve i chunk binari.
// La scrittura su flash (Update.write) NON avviene qui: il chunk viene solo
// copiato in coda, il main loop la elabora (vedi inizio di loop()).
class PetCubeOtaDataCallbacks : public BLECharacteristicCallbacks {
  void onWrite(BLECharacteristic* ch) override {
    if (otaState != OTA_RECEIVING) return;
    String val = ch->getValue();
    size_t len = val.length();
    if (len == 0) return;
    if (len > OTA_CHUNK_MAX) {
      // Diagnostica: un chunk più grande del buffer viene scartato in
      // silenzio (write senza risposta, il client non riceve errore).
      static uint32_t lastWarnMs = 0;
      uint32_t nowMs = millis();
      if (nowMs - lastWarnMs > 1000) {
        Serial.printf("OTA: chunk da %u byte > OTA_CHUNK_MAX (%u), scartato\n",
                      (unsigned)len, (unsigned)OTA_CHUNK_MAX);
        lastWarnMs = nowMs;
      }
      return;
    }

    OtaChunk chunk;
    chunk.len = len;
    memcpy(chunk.data, val.c_str(), len);
    if (xQueueSend(otaChunkQueue, &chunk, 0) != pdTRUE) {
      // Coda piena: il loop non sta tenendo il passo col trasferimento.
      Update.abort();
      otaState = OTA_ERROR;
      Serial.printf("OTA: coda chunk piena dopo %u bytes\n", (unsigned)otaBytesReceived);
    }
  }
};

// Inizializza BLE stack una volta sola (al boot)
void bleInit() {
  if (bleInitialized) return;
  // 32 slot × 512 byte = 16 KB di buffer: assorbe le code chunk che
  // arrivano via BLE (~34 KB/s) mentre il main loop è impegnato a
  // disegnare un frame (vedi early-return OTA_RECEIVING in loop()).
  otaChunkQueue = xQueueCreate(32, sizeof(OtaChunk));
  BLEDevice::init(BLE_DEVICE_NAME);
  // Senza questa chiamata l'MTU locale resta a 23 (default): qualunque MTU
  // negoziato dal client verrebbe troncato a 23, causando scritture ATT
  // più grandi del consentito durante l'OTA (chunk da ~500 byte) e una
  // disconnessione silenziosa a metà trasferimento.
  BLEDevice::setMTU(517);
  bleServer = BLEDevice::createServer();
  bleServer->setCallbacks(new PetCubeBLEServerCallbacks());

  BLEService* svc = bleServer->createService(BLE_SERVICE_UUID);
  bleNotifChar = svc->createCharacteristic(
    BLE_CHAR_UUID,
    BLECharacteristic::PROPERTY_WRITE
  );
  bleNotifChar->setCallbacks(new PetCubeBLECharCallbacks());

  // Caratteristica VERSION (read-only) — espone FW_VERSION come uint16 little-endian
  bleVersionChar = svc->createCharacteristic(
    BLE_CHAR_VERSION_UUID,
    BLECharacteristic::PROPERTY_READ
  );
  uint16_t fwVer = FW_VERSION;
  bleVersionChar->setValue((uint8_t*)&fwVer, 2);

  // Caratteristica OTA CTRL — write + read per gestione sessione OTA
  bleOtaCtrlChar = svc->createCharacteristic(
    BLE_CHAR_OTA_CTRL_UUID,
    BLECharacteristic::PROPERTY_WRITE | BLECharacteristic::PROPERTY_READ
  );
  bleOtaCtrlChar->setCallbacks(new PetCubeOtaCtrlCallbacks());

  // Caratteristica OTA DATA — write without response per ricezione chunk
  bleOtaDataChar = svc->createCharacteristic(
    BLE_CHAR_OTA_DATA_UUID,
    BLECharacteristic::PROPERTY_WRITE_NR
  );
  bleOtaDataChar->setCallbacks(new PetCubeOtaDataCallbacks());

  // Caratteristica IDENTITY — tag multiplayer "username#12345" (read + write)
  bleIdentityChar = svc->createCharacteristic(
    BLE_CHAR_IDENTITY_UUID,
    BLECharacteristic::PROPERTY_WRITE | BLECharacteristic::PROPERTY_READ
  );
  bleIdentityChar->setCallbacks(new PetCubeIdentityCallbacks());
  bleIdentityChar->setValue(petTag.c_str());

  svc->start();

  // Configura advertising una volta sola (l'aggiunta del Service UUID si accumulerebbe)
  BLEAdvertising* adv = BLEDevice::getAdvertising();
  adv->addServiceUUID(BLE_SERVICE_UUID);
  adv->setScanResponse(true);
  adv->setMinPreferred(0x06);  // help iOS find the device
  adv->setMinPreferred(0x12);

  bleInitialized = true;
  Serial.println("📡 BLE GATT server inizializzato.");
}

// Avvia advertising — il cubo diventa visibile e accettabile da PC
void bleStartAdvertising() {
  if (!bleInitialized || bleAdvertising) return;
  BLEDevice::startAdvertising();
  bleAdvertising = true;
  Serial.println("📡 BLE advertising START.");
}

// Stop advertising — il cubo non risponde più a scan, ma la connessione attiva continua
void bleStopAdvertising() {
  if (!bleInitialized || !bleAdvertising) return;
  BLEDevice::getAdvertising()->stop();
  bleAdvertising = false;
  Serial.println("📡 BLE advertising STOP.");
}

// Forza disconnessione del client (chiamato quando il cubo entra in stati non-Idle)
void bleDisconnectClient() {
  if (!bleInitialized || !bleClientConnected || !bleServer) return;
  // L'API ESP32 espone getConnectedCount() e disconnect(conn_id).
  // Per semplicità disconnettiamo tutti i client iterando sui conn_id 0..N-1
  // (Il numero è tipicamente 1 nel nostro use case)
  int count = bleServer->getConnectedCount();
  for (int i = 0; i < count; i++) {
    bleServer->disconnect(i);
  }
}

// Mantenimento dello stato BLE in base allo stato del cubo.
// Chiamato dal main loop. Garantisce che:
//   - sempre (in qualunque stato): advertising attivo se non c'è un client
//     connesso, così il cubo è sempre raggiungibile dalla Companion App
//     per inviare notifiche, anche fuori da Idle
//   - con un client connesso: advertising spento
//
// IMPORTANTE: NON forziamo la disconnessione del client durante stati transitori.
// Il client se ne andrà da solo se non riceve risposta, o resterà connesso
// silente. Forzare la disconnessione durante l'avvio di una battle può causare
// race condition con il task BLE.
void bleUpdateState() {
  bool shouldAdvertise = !bleClientConnected;

  if (shouldAdvertise && !bleAdvertising) {
    bleStartAdvertising();
  } else if (!shouldAdvertise && bleAdvertising) {
    bleStopAdvertising();
  }

  // Edge detection sul cambio connessione → feedback audio
  if (bleClientConnected != bleClientConnectedPrev) {
    if (bleClientConnected) {
      // Beep ascendente
      tone(BUZZER, 660, 60); delay(70);
      tone(BUZZER, 880, 80);
      Serial.println("📡 BLE client connesso.");
    } else {
      // Beep discendente
      tone(BUZZER, 880, 60); delay(70);
      tone(BUZZER, 660, 80);
      Serial.println("📡 BLE client disconnesso.");
    }
    bleClientConnectedPrev = bleClientConnected;
  }
}

// ══════════════════════════════════════════════════════════════
//  ⚔️  MODULO BATTLE
// ══════════════════════════════════════════════════════════════

// ── HELPER: dayOfWeek (0=Sun..6=Sat) dall'orologio software ──
uint8_t getDayOfWeek() {
  // Il firmware non ha RTC: l'orologio è solo HH:MM impostato dall'utente.
  // TODO: tracking giorno della settimana; per ora fisso (Mercoledì).
  return 3;
}

// ── HELPER: variante del REGISTRO entry ─────────────────────
// 0=Standard, 1=Light, 2=Dark (basata sul campo 'element' del REGISTRO)
uint8_t registroEntryVariant(uint8_t idx) {
  if (strcmp(REGISTRO[idx].element, "Light") == 0) return 1;
  if (strcmp(REGISTRO[idx].element, "Dark") == 0)  return 2;
  return 0;
}

// ── HELPER: elemento del REGISTRO entry come BattleElement ──
BattleElement registroEntryElement(uint8_t idx) {
  const char* e = REGISTRO[idx].element;
  // Light/Dark sono "morale", l'elemento di battaglia segue il pool:
  // Kindlekin..Noxfortress (0..13) = Fire, Drowsea..Nightmare (14..27) = Water
  if (idx <= 13) return BE_FIRE;
  return BE_WATER;
}

// ── NOTIFICHE: ricezione (chiamata da BLE callback task o mock seriale) ─
// Protetta da spinlock perché può essere chiamata da task FreeRTOS diverso.
// Il tone() viene chiamato dal main loop, non da qui (sarebbe da task BLE
// non sicuro). Useremo un flag globale per segnalare un nuovo beep da fare.
volatile bool pendingNotifBeep = false;

void onNotificationReceived(const NotifPacket& pkt) {
  bool inserted = false;
  portENTER_CRITICAL(&notifsMux);
  for (int i = 0; i < MAX_PENDING_NOTIFS; i++) {
    if (!pendingNotifs[i].active) {
      pendingNotifs[i].pkt = pkt;
      pendingNotifs[i].arrivalMs = millis();
      pendingNotifs[i].active = true;
      inserted = true;
      break;
    }
  }
  portEXIT_CRITICAL(&notifsMux);

  if (inserted) {
    Serial.printf("📬 Notifica ricevuta: source=%d cat=%d hash=%u preview=\"%s\"\n",
                  pkt.source, pkt.category, pkt.seedHash, pkt.seedPreview);
    pendingNotifBeep = true;  // Il main loop suonerà
  } else {
    Serial.println("⚠️  Coda notifiche piena, scarto.");
  }
}

// TTL scaduto: rimuovi notifica
void purgeExpiredNotifs(unsigned long now) {
  portENTER_CRITICAL(&notifsMux);
  for (int i = 0; i < MAX_PENDING_NOTIFS; i++) {
    if (pendingNotifs[i].active && (now - pendingNotifs[i].arrivalMs > NOTIF_TTL_MS)) {
      pendingNotifs[i].active = false;
      if (activeNotifIdx == i) activeNotifIdx = -1;
      portEXIT_CRITICAL(&notifsMux);
      Serial.printf("⏰ Notifica %d scaduta (TTL).\n", i);  // log fuori dalla sezione critica
      portENTER_CRITICAL(&notifsMux);
    }
  }
  portEXIT_CRITICAL(&notifsMux);
}

// Conta notifiche attive
int countActiveNotifs() {
  int n = 0;
  portENTER_CRITICAL(&notifsMux);
  for (int i = 0; i < MAX_PENDING_NOTIFS; i++) if (pendingNotifs[i].active) n++;
  portEXIT_CRITICAL(&notifsMux);
  return n;
}

// Restituisce indice del primo slot attivo (per default visualizzazione)
int firstActiveNotif() {
  int idx = -1;
  portENTER_CRITICAL(&notifsMux);
  for (int i = 0; i < MAX_PENDING_NOTIFS; i++) {
    if (pendingNotifs[i].active) { idx = i; break; }
  }
  portEXIT_CRITICAL(&notifsMux);
  return idx;
}

// ── REGISTRO: aggiungi nemico come 'silhouette+nome' ────────
void markEnemyKnown(uint8_t idx) {
  if (idx >= REGISTRO_SIZE || enemyKnown[idx]) return;
  enemyKnown[idx] = true;
  // Persisto nel namespace 'registro'
  char key[8];
  sprintf(key, "k%d", idx);
  prefs.begin("registro", false);
  prefs.putBool(key, true);
  prefs.end();
  Serial.printf("👁  Nuovo nemico battuto: %s\n", REGISTRO[idx].name);
}

void loadEnemyKnown() {
  prefs.begin("registro", true);
  for (int i = 0; i < REGISTRO_SIZE; i++) {
    char key[8];
    sprintf(key, "k%d", i);
    enemyKnown[i] = prefs.getBool(key, false);
  }
  prefs.end();
}

// ── INDICE REGISTRO del pet corrente ────────────────────────
uint8_t currentPetRegistroIdx() {
  // Mappo lo stadio + linea + variante all'indice nel REGISTRO[]
  // Vedi REGISTRO[] in PetCube.ino
  if (gElement == FIRE) {
    if (evoStage == 0) return IDX_KINDLEKIN;
    if (evoStage == 1) return IDX_EMBERPAW;
    if (evoStage == 2) return IDX_PYRUFF;
    if (evoStage == 3) {
      if (lineVariant == 0) return IDX_BLAZEBRAND;
      if (lineVariant == 1) return IDX_SHIELDMANE;
      return IDX_AUROVULP;
    }
    if (evoStage == 4) {
      if (lineVariant == 0) return IDX_MIGHTFORGE;
      if (lineVariant == 1) return IDX_FORTIFIRE;
      return IDX_VULPYRE;
    }
    // Ultimate
    int v = max(0, finalVariant);
    if (lineVariant == 0) {
      // STR final: Flameforge, Seraphyre, Noxfortress
      static const uint8_t f0[] = { IDX_FLAMEFORGE, IDX_SERAPHYRE, IDX_NOXFORTRESS };
      return f0[v];
    }
    if (lineVariant == 1) {
      static const uint8_t f1[] = { IDX_CITADELLION, IDX_SERAPHYRE, IDX_NOXFORTRESS };
      return f1[v];
    }
    static const uint8_t f2[] = { IDX_ELDERVULP, IDX_SERAPHYRE, IDX_NOXFORTRESS };
    return f2[v];
  } else {
    if (evoStage == 0) return IDX_DROWSEA;
    if (evoStage == 1) return IDX_GLOOMFIN;
    if (evoStage == 2) return IDX_FANGLURE;
    if (evoStage == 3) {
      if (lineVariant == 0) return IDX_RIPTALON;
      if (lineVariant == 1) return IDX_BALEGUARD;
      return IDX_SIRENLURE;
    }
    if (evoStage == 4) {
      if (lineVariant == 0) return IDX_MAULSTREAM;
      if (lineVariant == 1) return IDX_BULWHARK;
      return IDX_ABYSSIBYL;
    }
    int v = max(0, finalVariant);
    if (lineVariant == 0) {
      static const uint8_t w0[] = { IDX_LEVIACRUSH, IDX_LIGHTFIN, IDX_NIGHTMARE };
      return w0[v];
    }
    if (lineVariant == 1) {
      static const uint8_t w1[] = { IDX_TIDENAUGHT, IDX_LIGHTFIN, IDX_NIGHTMARE };
      return w1[v];
    }
    static const uint8_t w2[] = { IDX_THALASSIBYL, IDX_LIGHTFIN, IDX_NIGHTMARE };
    return w2[v];
  }
}

// ── AVVIO BATTLE ───────────────────────────────────────────
void startBattle(int notifIdx) {
  if (notifIdx < 0 || notifIdx >= MAX_PENDING_NOTIFS) return;
  // Copio il packet PRIMA di marcarlo inactive (evita race con nuove BLE notif)
  NotifPacket pkt;
  bool wasActive = false;
  portENTER_CRITICAL(&notifsMux);
  if (pendingNotifs[notifIdx].active) {
    pkt = pendingNotifs[notifIdx].pkt;
    wasActive = true;
  }
  portEXIT_CRITICAL(&notifsMux);
  if (!wasActive) return;

  // 1. Pet stats
  uint8_t petIdx = currentPetRegistroIdx();
  if (petIdx >= REGISTRO_SIZE) {
    Serial.printf("⚠️  Invalid petIdx=%d, abort battle\n", petIdx);
    return;
  }
  battlePetStats = computePetCombatStats(petIdx, statSTR, statINT, statENG, statHAP);

  // 2. Enemy selection
  BattleElement petElem = (gElement == FIRE) ? BE_FIRE : BE_WATER;
  battleEnemyIdx = selectEnemy(pkt, evoStage, petElem, getDayOfWeek());
  if (battleEnemyIdx >= REGISTRO_SIZE) {
    Serial.printf("⚠️  Invalid enemyIdx=%d, abort battle\n", battleEnemyIdx);
    return;
  }
  battleEnemyElem = registroEntryElement(battleEnemyIdx);
  battleEnemyVariant = registroEntryVariant(battleEnemyIdx);
  battlePriority = pkt.priority;

  // 3. Enemy stats con priority + streak modifiers
  battleEnemyStats = computeEnemyCombatStats(battleEnemyIdx, battlePriority, battleStreak);

  // 4. Crit window: base 16px + 1px per ogni 5 char di seed (max +12px)
  critWindowWidth = 16 + min((int)(pkt.seedLength / 5), 12);

  // 5. Reset state
  battleClashIdx = 0;
  battlePetWins = 0;
  battleEnemyWins = 0;
  battlePetDmgTaken = 0;
  battleEnemyDmgTaken = 0;

  // 6. Marca notifica come consumata
  portENTER_CRITICAL(&notifsMux);
  pendingNotifs[notifIdx].active = false;
  if (activeNotifIdx == notifIdx) activeNotifIdx = -1;
  portEXIT_CRITICAL(&notifsMux);

  // 7. Stato
  gState = STATE_BATTLE_INTRO;
  gScreen = SCR_BATTLE;
  battleStateMs = millis();
  Serial.printf("⚔️  Battle: %s vs %s (priority=%d streak=%d)\n",
                REGISTRO[petIdx].name, REGISTRO[battleEnemyIdx].name,
                battlePriority, battleStreak);
  tone(BUZZER, 660, 60); delay(70);
  tone(BUZZER, 880, 60); delay(70);
  tone(BUZZER, 1100, 120);
}

// ── DISMISS volontario ─────────────────────────────────────
void dismissNotification(int notifIdx) {
  if (notifIdx < 0 || notifIdx >= MAX_PENDING_NOTIFS) return;
  portENTER_CRITICAL(&notifsMux);
  if (!pendingNotifs[notifIdx].active) {
    portEXIT_CRITICAL(&notifsMux);
    return;
  }
  pendingNotifs[notifIdx].active = false;
  if (activeNotifIdx == notifIdx) activeNotifIdx = -1;
  portEXIT_CRITICAL(&notifsMux);
  Serial.printf("✋  Notifica %d dismissed.\n", notifIdx);
  tone(BUZZER, 400, 100);
}

// ── ESEGUI UN CLASH (calcolo) ──────────────────────────────
ClashResult resolveClash() {
  ClashResult r;
  // Stat offensiva = max(ATK, SPA) per ciascuno
  uint16_t pet_off = max(battlePetStats.atk, battlePetStats.spa);
  uint16_t en_off  = max(battleEnemyStats.atk, battleEnemyStats.spa);

  // Type elem
  BattleElement petElem = (gElement == FIRE) ? BE_FIRE : BE_WATER;
  uint8_t te_pet  = typeElemPct(petElem, battleEnemyElem);
  uint8_t te_en   = typeElemPct(battleEnemyElem, petElem);
  // Type moral
  uint8_t petVariant = (finalVariant >= 0) ? (uint8_t)finalVariant : 0;
  uint8_t tm_pet = typeMoralPct(petVariant, battleEnemyVariant);
  uint8_t tm_en  = typeMoralPct(battleEnemyVariant, petVariant);

  // RNG per ciascuno
  uint8_t rng_pet = BATTLE_RNG_MIN_PCT + random(BATTLE_RNG_MAX_PCT - BATTLE_RNG_MIN_PCT + 1);
  uint8_t rng_en  = BATTLE_RNG_MIN_PCT + random(BATTLE_RNG_MAX_PCT - BATTLE_RNG_MIN_PCT + 1);
  // Crit
  uint8_t crit_pet = petCritThisClash ? BATTLE_CRIT_MULT : 1;
  bool en_crit = (random(100) < BATTLE_ENEMY_CRIT_PCT);
  uint8_t crit_en = en_crit ? BATTLE_CRIT_MULT : 1;

  r.pet_dmg = computeDamage(pet_off, battleEnemyStats.def, te_pet, tm_pet, crit_pet, rng_pet);
  r.enemy_dmg = computeDamage(en_off, battlePetStats.def, te_en, tm_en, crit_en, rng_en);
  r.pet_won = (r.pet_dmg > r.enemy_dmg);

  battlePetDmgTaken += r.enemy_dmg;
  battleEnemyDmgTaken += r.pet_dmg;
  if (r.pet_won) battlePetWins++;
  else if (r.pet_dmg < r.enemy_dmg) battleEnemyWins++;
  // se uguale (raro), nessuno vince — il clash è in pareggio. Per semplicità lo conto come del nemico.
  else battleEnemyWins++;

  Serial.printf("  Clash %d: pet_dmg=%d enemy_dmg=%d → %s (pet_crit=%d en_crit=%d)\n",
                battleClashIdx + 1, r.pet_dmg, r.enemy_dmg,
                r.pet_won ? "PET" : "ENEMY", petCritThisClash, en_crit);
  return r;
}

// ── FINE BATTLE: applica conseguenze ───────────────────────
void finalizeBattle() {
  // Determina esito
  bool pet_won_battle;
  if (battlePetWins > battleEnemyWins) pet_won_battle = true;
  else if (battleEnemyWins > battlePetWins) pet_won_battle = false;
  else {
    // Tie-breaker: chi ha damage_taken / HP_max minore
    float pet_pct = (float)battlePetDmgTaken / (float)max((uint16_t)1, battlePetStats.hp);
    float en_pct  = (float)battleEnemyDmgTaken / (float)max((uint16_t)1, battleEnemyStats.hp);
    pet_won_battle = (pet_pct < en_pct);
  }

  if (pet_won_battle) {
    // +5 HAP
    statHAP = min(100, statHAP + BATTLE_WIN_HAP);
    // +3 alla stat dominante del nemico
    PetStats en_base = getStatsFromRegistro(battleEnemyIdx);
    uint8_t dom_stat = 0;  // 0=ATK 1=SPA 2=DEF 3=HP
    if (en_base.spa > en_base.atk) dom_stat = 1;
    uint8_t maxV = max(en_base.atk, en_base.spa);
    if (en_base.def > maxV) { dom_stat = 2; maxV = en_base.def; }
    if (en_base.hp  > maxV) { dom_stat = 3; maxV = en_base.hp;  }
    switch (dom_stat) {
      case 0: statSTR = min(100, statSTR + BATTLE_WIN_STAT); break;
      case 1: statINT = min(100, statINT + BATTLE_WIN_STAT); break;
      case 2: statENG = min(100, statENG + BATTLE_WIN_STAT); break;
      case 3: statHAP = min(100, statHAP + BATTLE_WIN_STAT); break;
    }
    // Registro: marca nemico come 'visto'
    markEnemyKnown(battleEnemyIdx);
    battleStreak++;
    Serial.printf("🏆 VITTORIA! +5 HAP, +3 al dom_stat=%d (streak=%d)\n", dom_stat, battleStreak);
    tone(BUZZER, 1047, 100); delay(110);
    tone(BUZZER, 1319, 100); delay(110);
    tone(BUZZER, 1568, 200);
  } else {
    // Sconfitta: chance malattia in base agli escrementi
    uint8_t pct = illnessChanceAfterDefeat(poopCount, poopMega);
    if (pct > 0 && random(100) < pct && !isSick) {
      isSick = true;
      sickStartMs = millis();
      lastSickDecayMs = millis();
      sickEpisodes++;
      Serial.printf("💀 SCONFITTA + malattia (chance %d%%)\n", pct);
    } else {
      Serial.printf("💀 SCONFITTA (chance malattia %d%% non scattata)\n", pct);
    }
    battleStreak = 0;
    tone(BUZZER, 300, 200); delay(220);
    tone(BUZZER, 200, 300);
  }
  saveToNVS();
}

void enterBattleStateMain() {
  // Esci dalla battle, torna a Idle
  gState = STATE_IDLE;
  gScreen = SCR_MAIN;
}

void drawCenteredStr(int y, const char* s) {
  int w = canvas.textWidth(s);
  int x = max(0, (DISP_SIZE - w) / 2);
  canvas.drawString(s, x, y);
}

// Disegna un'etichetta centrata su un badge scuro (per leggibilità sullo sfondo).
void drawBadgedCenteredStr(int y, const char* s, uint16_t color, int padX, int padY) {
  int w = canvas.textWidth(s);
  int h = canvas.fontHeight();
  int x = max(0, (DISP_SIZE - w) / 2);
  canvas.fillRoundRect(x - padX, y - padY, w + padX * 2, h + padY * 2, 4, C_BG);
  canvas.setTextColor(color, C_BG);
  canvas.drawString(s, x, y);
}

// ── Schermata di avanzamento OTA ─────────────────────────────
// Mostrata durante la ricezione del firmware (OTA_RECEIVING).
void drawOtaProgressScreen(uint32_t received, uint32_t total) {
  canvas.fillSprite(C_BG);
  canvas.setTextFont(2);
  canvas.setTextColor(C_CYAN, C_BG);
  drawCenteredStr(80, "Aggiornamento");
  drawCenteredStr(102, "in corso...");
  canvas.setTextColor(C_FG, C_BG);
  int pct = total ? (int)((uint64_t)received * 100 / total) : 0;
  char buf[16];
  snprintf(buf, sizeof(buf), "%d%%", pct);
  drawCenteredStr(140, buf);
  canvas.pushSprite(0, 0);
}

// ── Schermata di conferma OTA ────────────────────────────────
// Mostrata quando il trasferimento firmware è completo e si attende
// la scelta dell'utente: B = installa e riavvia, C = annulla.
void drawOtaConfirmScreen(unsigned long now) {
  canvas.fillSprite(C_BG);
  canvas.setTextFont(2);
  canvas.setTextColor(C_CYAN, C_BG);
  drawCenteredStr(70, "Nuovo firmware");
  drawCenteredStr(92, "pronto!");
  canvas.setTextColor(C_FG, C_BG);
  drawCenteredStr(130, "Aggiornare ora?");
  canvas.setTextColor(C_HAP, C_BG);
  drawCenteredStr(165, "B = Si, installa");
  canvas.setTextColor(C_STR, C_BG);
  drawCenteredStr(185, "C = No, annulla");
  canvas.pushSprite(0, 0);
}

// Proiettile vincitore->perdente: fiammella rossa/gialla (Fire) o goccia azzurra (Water).
void drawProjectile(int x, int y, BattleElement elem) {
  if (elem == BE_FIRE) {
    canvas.fillCircle(x, y, 8, C_STR);
    canvas.fillCircle(x, y - 1, 4, C_ENG);
  } else {
    canvas.fillCircle(x, y + 3, 7, C_CYAN);
    canvas.fillTriangle(x, y - 8, x - 5, y + 3, x + 5, y + 3, C_CYAN);
  }
}

// ── RENDERING BATTLE ──────────────────────────────────────────
void drawBattleScreen(unsigned long now) {
  canvas.pushImage(0, 0, DISP_SIZE, DISP_SIZE, BG_NORMAL);
  unsigned long el = now - battleStateMs;

  // Banda scura in alto: leggibilità del punteggio sullo sfondo.
  canvas.fillRect(0, 0, DISP_SIZE, 32, C_BG);
  canvas.setTextFont(2); canvas.setTextColor(C_CYAN, C_BG);
  char buf[32];
  sprintf(buf, "Clash %d/3   P:%d  E:%d", min((int)battleClashIdx + 1, 3),
          battlePetWins, battleEnemyWins);
  drawCenteredStr(12, buf);
  canvas.drawFastHLine(0, 32, DISP_SIZE, C_DIM);

  // Indici sprite
  uint8_t petIdx = currentPetRegistroIdx();
  if (petIdx >= REGISTRO_SIZE || battleEnemyIdx >= REGISTRO_SIZE) {
    canvas.setTextFont(2); canvas.setTextColor(C_STR, C_BG);
    drawCenteredStr(115, "Battle error");
    canvas.pushSprite(0, 0);
    enterBattleStateMain();
    return;
  }
  const PetSprites* petSpr = REGISTRO[petIdx].sprites;
  const PetSprites* enSpr  = REGISTRO[battleEnemyIdx].sprites;
  if (!petSpr || !enSpr) {
    canvas.setTextFont(2); canvas.setTextColor(C_STR, C_BG);
    drawCenteredStr(115, "Sprite NULL");
    canvas.pushSprite(0, 0);
    enterBattleStateMain();
    return;
  }

  // Posizioni sprite battle: pet sx ×5 (80px), nemico dx ×5, in basso.
  // Bar/VS/esito stanno nella fascia in alto (32-110), le sprite sotto
  // (110-190): a y=110/190 l'angolo esterno resta entro l'area circolare
  // visibile (verificato per petX=28/enX=132).
  const int bscale = 5;
  const int bsz    = SPR_SIZE * bscale;  // 80
  int petX = 28, enX = DISP_SIZE - 28 - bsz;
  int yPos = 110;

  if (gState == STATE_BATTLE_INTRO) {
    int progress = min((int)el, 800);
    int offset = (progress * 30) / 800;
    petX += offset;
    enX  -= offset;
    int idx0 = (now/200) % 3;
    drawSpriteScaled(petX, yPos, bscale, petSpr->idle[idx0], true);
    drawSpriteScaled(enX,  yPos, bscale, enSpr->idle[idx0]);
    canvas.setTextFont(4);
    drawBadgedCenteredStr(58, "VS", C_FG);
    if (el >= 1000) {
      gState = STATE_BATTLE_CLASH;
      battleStateMs = now;
      cursorX = 36;
      cursorDir = 1;
      petCritThisClash = false;
      critWindowStart = 120 - critWindowWidth / 2 + random(-15, 16);
      critWindowStart = constrain(critWindowStart, 37, 203 - critWindowWidth);
    }
  }
  else if (gState == STATE_BATTLE_CLASH) {
    int idx0 = (now / 250) % 2;
    drawSpriteScaled(petX, yPos, bscale, petSpr->atk[idx0], true);
    drawSpriteScaled(enX,  yPos, bscale, enSpr->atk[idx0]);

    // Timing-game bar ristretta a 170px (centrata, x:35-205): a 220px
    // (x:10-230) le estremità finivano fuori dall'area circolare visibile.
    // Sfondo opaco dietro la barra: sullo sfondo immagine, drawRect da solo
    // disegna solo il bordo e lascerebbe l'interno trasparente.
    canvas.fillRect(36, 44, 168, 18, C_BG);
    canvas.fillRect(critWindowStart, 44, critWindowWidth, 18, C_ENG);

    cursorX += cursorDir * BATTLE_CURSOR_SPEED;
    if (cursorX >= 200) { cursorX = 200; cursorDir = -1; }
    if (cursorX <= 36)  { cursorX = 36;  cursorDir =  1; }
    canvas.fillRect(cursorX, 44, 4, 18, C_BG);
    canvas.drawFastVLine(cursorX+1, 40, 26, C_FG);
    canvas.drawRect(35, 43, 170, 20, C_FG);

    canvas.setTextFont(2);
    drawBadgedCenteredStr(72, "B = colpo", C_DIM);

    if (el >= 4000) {
      petCritThisClash = false;
      gState = STATE_BATTLE_RESOLVE;
      battleStateMs = now;
    }
  }
  else if (gState == STATE_BATTLE_RESOLVE) {
    drawSpriteScaled(petX, yPos, bscale, petSpr->atk[(now/100)%2], true);
    drawSpriteScaled(enX,  yPos, bscale, enSpr->atk[(now/100)%2]);

    static ClashResult lastResult;
    if (el < 50) lastResult = resolveClash();

    // Proiettile: viaggia dalla sprite del vincitore della fase verso il
    // perdente. Colore/forma in base al tipo del vincitore (Fire/Water).
    const unsigned long PROJECTILE_MS = 900;
    if (el < PROJECTILE_MS) {
      int petCx = petX + bsz / 2, enCx = enX + bsz / 2, cy = yPos + bsz / 2;
      int fromX, toX;
      BattleElement winnerElem;
      if (lastResult.pet_won) {
        fromX = petCx; toX = enCx;
        winnerElem = (gElement == FIRE) ? BE_FIRE : BE_WATER;
      } else {
        fromX = enCx; toX = petCx;
        winnerElem = battleEnemyElem;
      }
      float t = (float)el / PROJECTILE_MS;
      int px = fromX + (int)((toX - fromX) * t);
      int py = cy - (int)(25 * sinf(t * PI));  // piccolo arco verticale
      drawProjectile(px, py, winnerElem);
    }

    char dmg[28];
    sprintf(dmg, "P:-%d   E:-%d", lastResult.enemy_dmg, lastResult.pet_dmg);
    canvas.setTextFont(2);
    drawBadgedCenteredStr(50, dmg, C_TIMER);

    if (el >= 1500) {
      battleClashIdx++;
      if (battleClashIdx >= 3 || battlePetWins >= 2 || battleEnemyWins >= 2) {
        gState = STATE_BATTLE_RESULT;
        finalizeBattle();
      } else {
        gState = STATE_BATTLE_CLASH;
        cursorX = (cursorDir > 0) ? 10 : 228;
        petCritThisClash = false;
        critWindowStart = 120 - critWindowWidth / 2 + random(-15, 16);
        critWindowStart = constrain(critWindowStart, 12, 218 - critWindowWidth);
      }
      battleStateMs = now;
    }
  }
  else if (gState == STATE_BATTLE_RESULT) {
    bool pet_won = (battlePetWins > battleEnemyWins) ||
                   (battlePetWins == battleEnemyWins &&
                    (float)battlePetDmgTaken / max((uint16_t)1, battlePetStats.hp) <
                    (float)battleEnemyDmgTaken / max((uint16_t)1, battleEnemyStats.hp));
    drawSpriteScaled(petX, yPos, bscale, pet_won ? petSpr->happy[(now/250)%2] : petSpr->sick[(now/400)%2], true);
    drawSpriteScaled(enX,  yPos, bscale, pet_won ? enSpr->sick[(now/400)%2]  : enSpr->happy[(now/250)%2]);
    canvas.setTextFont(4);
    drawBadgedCenteredStr(55, pet_won ? "VITTORIA!" : "SCONFITTA", pet_won ? C_HAP : C_STR);
    if (el >= 2500) enterBattleStateMain();
  }

  canvas.pushSprite(0, 0);
}

// ══════════════════════════════════════════════════════════════
//  SETUP & LOOP
// ══════════════════════════════════════════════════════════════

void setup() {
  Serial.begin(115200);
  Wire.begin();
  pinMode(BTN_A, INPUT_PULLUP);
  pinMode(BTN_B, INPUT_PULLUP);
  pinMode(BTN_C, INPUT_PULLUP);
  pinMode(BUZZER, OUTPUT);
  pinMode(LED, OUTPUT);
  digitalWrite(LED, HIGH);
  randomSeed(analogRead(0));

  display.init();
  display.setRotation(0);
  display.setBrightness(SCREEN_BRIGHTNESS);
  display.fillScreen(C_BG);
  canvas.setColorDepth(16);
  canvas.createSprite(DISP_SIZE, DISP_SIZE);
  // Gli array di sfondo (petcube_backgrounds.h) sono in formato rgb565_t
  // nativo: pushImage richiede setSwapBytes(true) per interpretarli
  // correttamente (default e' swap565_t).
  canvas.setSwapBytes(true);

  // Splash
  canvas.fillSprite(C_BG);
  canvas.setTextFont(4);
  canvas.setTextColor(C_FG, C_BG);
  canvas.drawString("PetCube", 80, 100);
  canvas.setTextFont(2);
  canvas.drawString("v0.24  Loading...", 55, 135);
  canvas.pushSprite(0, 0);

  if (!mpu.begin()) {
    canvas.fillSprite(C_BG);
    canvas.setTextFont(2);
    canvas.setTextColor(C_STR, C_BG);
    canvas.drawString("MPU non trovato!", 30, 110);
    canvas.pushSprite(0, 0);
    while (1);
  }
  mpu.setAccelerometerRange(MPU6050_RANGE_2_G);
  mpu.setGyroRange(MPU6050_RANGE_250_DEG);
  mpu.setFilterBandwidth(MPU6050_BAND_21_HZ);
  lastMpuMs = millis();

  // Migrazione NVS se siamo a una nuova versione firmware
  migrateNVSIfNeeded();

  bootHasData = loadFromNVS();
  registroLoad();
  legacyLoad();
  loadEnemyKnown();

  // 📡 Inizializza BLE GATT server (advertising verrà avviato quando entriamo in Idle)
  bleInit();
  bootChoice  = 0;  // default: continua (se ci sono dati)

  // Mostra sempre la schermata di boot
  gScreen = SCR_BOOT;
  gState  = STATE_IDLE;  // stato neutro finché l'utente sceglie
  lastDecayMs = millis();
  lastActivityMs = millis();

  delay(600);
  tone(BUZZER,523,80); delay(90);
  tone(BUZZER,659,80); delay(90);
  tone(BUZZER,784,200);
}

void loop() {
  // Riavvio post-OTA: aspetta che il client BLE riceva l'ACK, poi reboot
  if (otaRebootPending) {
    delay(500);
    ESP.restart();
  }

  unsigned long now = millis();

  // ── OTA: scrive su flash i chunk ricevuti via BLE ───────────────
  // Eseguito qui (non nella callback BLE) per non bloccare lo stack BLE
  // con operazioni di scrittura flash durante un trasferimento lungo.
  if (otaChunkQueue) {
    OtaChunk chunk;
    int processed = 0;
    while (processed < 4 && xQueueReceive(otaChunkQueue, &chunk, 0) == pdTRUE) {
      if (otaState == OTA_RECEIVING && Update.isRunning()) {
        size_t written = Update.write(chunk.data, chunk.len);
        if (written != chunk.len) {
          Update.abort();
          otaState = OTA_ERROR;
          Serial.printf("OTA write error dopo %u bytes\n", (unsigned)otaBytesReceived);
        } else {
          otaBytesReceived += written;
          // Log di avanzamento ogni ~100 KB
          static uint32_t lastLoggedKb = 0;
          uint32_t kb = otaBytesReceived / 1024;
          if (kb / 100 != lastLoggedKb / 100) {
            Serial.printf("OTA: %u KB scritti\n", kb);
          }
          lastLoggedKb = kb;
        }
      }
      processed++;
    }
  }

  // ── OTA in corso ─────────────────────────────────────────────
  // Saltiamo il resto del loop (sensori, disegno schermate, pulsanti):
  // a ~34 KB/s anche pochi ms di ritardo per iterazione fanno traboccare
  // la coda chunk ("OTA: coda chunk piena"). Mostriamo solo una
  // schermata di avanzamento, aggiornata periodicamente.
  if (otaState == OTA_RECEIVING) {
    static uint32_t lastOtaDrawMs = 0;
    if (now - lastOtaDrawMs >= 300) {
      drawOtaProgressScreen(otaBytesReceived, otaTotalSize);
      lastOtaDrawMs = now;
    }
    // Riavvia l'advertising se il client si disconnette a metà OTA
    bleUpdateState();
    // Cede la CPU: senza yield lo scheduler non esegue il task host NimBLE
    // e la connessione BLE cade dopo un tempo fisso (~97s)
    delay(1);
    return;
  }

  // ── MPU ───────────────────────────────────────────────────────
  float dt = (now - lastMpuMs) / 1000.0f;
  lastMpuMs = now;
  sensors_event_t a, g, temp;
  mpu.getEvent(&a, &g, &temp);
  float ax = a.acceleration.x, ay = a.acceleration.y, az = a.acceleration.z;
  float accelX = atan2(ay, az);
  float accelY = atan2(-ax, sqrt(ay*ay + az*az));
  filtX = 0.96f*(filtX + g.gyro.x*dt) + 0.04f*accelX;
  filtY = 0.96f*(filtY + g.gyro.y*dt) + 0.04f*accelY;

  // Isteresi orientamento
  Orientation rawOri = detectOrientation(ax, ay, az);
  oriBuffer[oriBufferIdx] = rawOri;
  oriBufferIdx = (oriBufferIdx + 1) % ORI_HYSTERESIS;
  if (oriBufferIdx == 0) oriBufferFull = true;
  Orientation stableOri = stableOrientation();
  if (stableOri != gOrient) {
    gOrient = stableOri;
    if (gScreen == SCR_MAIN) enterStateFromOri(stableOri);
    wakeScreen(now);
  }

  // ── Bottoni ───────────────────────────────────────────────────
  bool btnANow = digitalRead(BTN_A);
  bool btnBNow = digitalRead(BTN_B);
  bool btnCNow = digitalRead(BTN_C);
  if ((btnAPrev==HIGH && btnANow==LOW) || (btnBPrev==HIGH && btnBNow==LOW) || (btnCPrev==HIGH && btnCNow==LOW)) {
    wakeScreen(now);
  }

  if (gScreen == SCR_BOOT) {
    if (btnAPrev==HIGH && btnANow==LOW) {
      bootChoice = (bootChoice + 1) % (bootHasData ? 2 : 1);
      tone(BUZZER, 660, 30);
      delay(50);
    }
    if (btnBPrev==HIGH && btnBNow==LOW) {
      if (bootChoice == 1 || !bootHasData) {
        // Nuova partita: reset (mantiene il registro e l'eredità persistenti)
        resetForNewGame(now);
      } else {
        // Continua: imposta orologio poi vai in gioco
        gState  = STATE_IDLE;
        clockSet    = false;   // forza sempre la schermata di impostazione
        clockEditH  = 12;
        clockEditM  = 0;
        gScreen = SCR_CLOCK;
        if (nextPoopMs == 0)
          nextPoopMs = now + randomPoopInterval();
      }
      tone(BUZZER,784,80); delay(90); tone(BUZZER,1047,150);
      delay(50);
    }
    // aggiorna prev e skip resto
    btnAPrev=btnANow; btnBPrev=btnBNow; btnCPrev=btnCNow;
    // display boot
    drawBootScreen();
    delay(40);
    return;
  }

  // ── Conferma OTA ─────────────────────────────────────────────
  // Trasferimento completato: chiediamo all'utente se installare (B)
  // o annullare (C) prima di finalizzare l'aggiornamento.
  if (otaState == OTA_AWAIT_CONFIRM) {
    if (btnBPrev==HIGH && btnBNow==LOW) {
      // Installa: finalizza l'update e riavvia
      if (Update.end(true)) {
        otaState = OTA_DONE;
        otaRebootPending = true;   // riavvio gestito dal main loop
        Serial.println("OTA confermata dall'utente — riavvio imminente");
      } else {
        Update.abort();
        otaState = OTA_ERROR;
        Serial.printf("OTA: finalizzazione fallita: %s\n", Update.errorString());
      }
      tone(BUZZER,784,80); delay(90); tone(BUZZER,1047,150);
      delay(50);
    }
    if (btnCPrev==HIGH && btnCNow==LOW) {
      // Annulla: scarta l'update ricevuto
      Update.abort();
      otaState = OTA_CANCELLED;
      Serial.println("OTA annullata dall'utente");
      tone(BUZZER, 440, 120);
      delay(50);
    }
    btnAPrev=btnANow; btnBPrev=btnBNow; btnCPrev=btnCNow;
    drawOtaConfirmScreen(now);
    delay(40);
    return;
  }

  if (gScreen == SCR_CLOCK) {
    // SCR_CLOCK prima di tutto — anche durante STATE_SETUP
    handleClockButtons(btnANow, btnBNow, btnCNow);
  }
  else if (gState == STATE_SETUP) {
    // A: cicla tra Fire e Water
    if (btnAPrev==HIGH && btnANow==LOW) {
      setupChoice = (setupChoice + 1) % 2;
      tone(BUZZER, setupChoice==0 ? 660 : 440, 50);
      delay(50);
    }
    // B: seleziona
    if (btnBPrev==HIGH && btnBNow==LOW) {
      gElement   = (setupChoice==0) ? FIRE : WATER;
      statHAP    = 50;
      lastDecayMs = now;
      nextPoopMs  = now + randomPoopInterval();
      gState     = STATE_IDLE;
      gScreen    = SCR_MAIN;
      saveToNVS();
      // Segna il Baby I di partenza nel registro
      registroMarkObtained(gElement == FIRE ? "Kindlekin" : "Drowsea");
      // L'eredità (se presente) è già stata applicata e consumata su NVS
      // da resetForNewGame(); ora azzero anche il feedback a schermo.
      legacySTR = legacyINT = legacyENG = 0;
      tone(BUZZER,784,80); delay(90); tone(BUZZER,1047,200);
      delay(50);
    }
    // C: niente
  }
  else if (gState == STATE_DEAD) {
    // B: torna alla scelta del baby (mantiene registro ed eredità)
    if (btnBPrev==HIGH && btnBNow==LOW) {
      resetForNewGame(now);
      tone(BUZZER,784,80); delay(90); tone(BUZZER,1047,150);
      delay(50);
    }
  }
  else if (gState == STATE_EVOLVING) {
    // Nessun tasto può skippare l'evoluzione — dura sempre EVOLVE_ANIM_MS (3s)
  }
  else if (gScreen == SCR_MAIN) {
    // ⚔️  Long-press tracking per battle (B) e dismiss (C) — solo in Idle con notifica pendente
    bool hasNotif = (gState == STATE_IDLE && countActiveNotifs() > 0);

    if (hasNotif) {
      // Long-press B
      if (btnBNow == LOW) {
        if (longPressBMs == 0) longPressBMs = now;
        else if (now - longPressBMs >= LONG_PRESS_MS) {
          // Trigger battle
          int idx = firstActiveNotif();
          if (idx >= 0) {
            startBattle(idx);
            longPressBMs = 0;
            // Skippa il resto della logica SCR_MAIN
            btnAPrev = btnANow; btnBPrev = btnBNow; btnCPrev = btnCNow;
            return;
          }
        }
      } else {
        longPressBMs = 0;
      }
      // Long-press C: dismiss
      if (btnCNow == LOW) {
        if (longPressCMs == 0) longPressCMs = now;
        else if (now - longPressCMs >= LONG_PRESS_MS) {
          int idx = firstActiveNotif();
          if (idx >= 0) {
            dismissNotification(idx);
            longPressCMs = 0;
            btnAPrev = btnANow; btnBPrev = btnBNow; btnCPrev = btnCNow;
            return;
          }
        }
      } else {
        longPressCMs = 0;
      }
    } else {
      longPressBMs = 0;
      longPressCMs = 0;
    }

    // A: apri menu — solo in IDLE
    //    durante il setup pomodoro/riposo: incrementa la durata
    if (btnAPrev==HIGH && btnANow==LOW) {
      if (pomoPhase == POMO_SET_WORK) {
        adjustPomodoroWorkMs(true);
      } else if (pomoPhase == POMO_SET_REST) {
        adjustPomodoroRestMs(true);
      } else if (!sessionRunning && gState == STATE_IDLE) {
        gScreen    = SCR_MENU;
        menuCursor = 0;
        tone(BUZZER, 660, 30);
      }
      delay(50);
    }
    // B: in Training/Study/Work avvia/avanza il setup pomodoro
    //    (1° B = setup durata pomodoro, 2° B = setup durata riposo,
    //    3° B = avvio pomodoro)
    //    in Idle → apri orologio (a meno che ci sia una notifica pendente)
    //    in Sleep/DND → niente
    if (btnBPrev==HIGH && btnBNow==LOW) {
      if (pomoPhase == POMO_SET_WORK || pomoPhase == POMO_SET_REST) {
        advancePomodoroSetup();
      } else if (!sessionRunning) {
        if (gState == STATE_IDLE) {
          // Se c'è una notifica pendente, NON aprire l'orologio:
          // l'utente potrebbe star iniziando un long-press B per battle.
          // L'orologio resta disabilitato finché la notifica non è gestita.
          if (!hasNotif) {
            gScreen = SCR_CLOCK;  // orologio solo da Idle senza notifiche
          }
          // Se hasNotif: il press B avvia il tracking del long-press
          // (gestito sopra), nessun'altra azione qui.
        } else if (gState == STATE_TRAINING ||
                   gState == STATE_STUDY    ||
                   gState == STATE_WORK) {
          beginPomodoroSetup(gState);
        }
        // Sleep / DND / Dead → B non fa niente
      }
      delay(50);
    }
    // C: durante il setup pomodoro/riposo decrementa la durata,
    //    altrimenti annulla il pomodoro/riposo in corso (nessuna penalità)
    if (btnCPrev==HIGH && btnCNow==LOW) {
      if (pomoPhase == POMO_SET_WORK) {
        adjustPomodoroWorkMs(false);
      } else if (pomoPhase == POMO_SET_REST) {
        adjustPomodoroRestMs(false);
      } else if (sessionRunning) {
        cancelPomodoro();
      }
      delay(50);
    }
  }
  else if (gScreen == SCR_BATTLE) {
    // ⚔️  Durante la battle, B è usato SOLO per il timing-game del clash
    if (gState == STATE_BATTLE_CLASH) {
      if (btnBPrev==HIGH && btnBNow==LOW) {
        // Verifica se il cursore è nella zona crit
        if (cursorX >= critWindowStart && cursorX <= critWindowStart + critWindowWidth) {
          petCritThisClash = true;
          tone(BUZZER, 1500, 60);
        } else {
          tone(BUZZER, 400, 60);
        }
        gState = STATE_BATTLE_RESOLVE;
        battleStateMs = now;
      }
    }
    // C: nessun escape volontario dalla battle (design choice — ti costringi)
  }
  else if (gScreen == SCR_MENU) {
    if (btnAPrev==HIGH && btnANow==LOW) {
      menuCursor = (menuCursor + 1) % MENU_ITEMS;
      tone(BUZZER, 660, 30);
      delay(50);
    }
    if (btnBPrev==HIGH && btnBNow==LOW) {
      executeMenuItem(now);
      delay(50);
    }
    if (btnCPrev==HIGH && btnCNow==LOW) {
      gScreen = SCR_MAIN;
      delay(50);
    }
  }
  else if (gScreen == SCR_STATUS) {
    // C: torna al menu
    if (btnCPrev==HIGH && btnCNow==LOW) {
      gScreen = SCR_MENU;
      delay(50);
    }
    // B: reimpostazione orologio
    if (btnBPrev==HIGH && btnBNow==LOW) {
      clockSet    = false;
      clockEditH  = 12;
      clockEditM  = 0;
      gScreen     = SCR_CLOCK;
      delay(50);
    }
    // A non fa nulla in status
  }
  else if (gScreen == SCR_REGISTRO) {
    // A: cicla tra le creature
    if (btnAPrev==HIGH && btnANow==LOW) {
      registroCursor = (registroCursor + 1) % REGISTRO_SIZE;
      tone(BUZZER, 660, 30);
      delay(50);
    }
    // C: esci dal registro
    if (btnCPrev==HIGH && btnCNow==LOW) {
      gScreen = SCR_MENU;
      delay(50);
    }
  }

  btnAPrev = btnANow;
  btnBPrev = btnBNow;
  btnCPrev = btnCNow;

  // Ricalcola now dopo i delay dei bottoni per evitare underflow del timer
  now = millis();

  // ── Timer pomodoro/riposo ─────────────────────────────────────
  if (sessionRunning && pomoPhase == POMO_RUN_WORK && now - sessionStartMs >= pomodoroMs) {
    completePomodoroWork();
  } else if (sessionRunning && pomoPhase == POMO_RUN_REST && now - sessionStartMs >= restMs) {
    completePomodoroRest();
  }

  // ── Logiche background ────────────────────────────────────────
  checkDecay(now);
  checkPoop(now);
  checkSick(now);
  purgeExpiredNotifs(now);  // ⚔️  scarta notifiche scadute (TTL 30 min)
  bleUpdateState();          // 📡  gestisce advertising on/off e beep connessione

  // Suono notifica BLE (chiamato dal main loop perché il callback è in task BLE)
  // In Sleep/DND il cubo resta silenzioso: la notifica viene comunque
  // accodata e gestibile una volta tornati in Idle.
  if (pendingNotifBeep) {
    pendingNotifBeep = false;
    wakeScreen(now);
    if (gState != STATE_SLEEP && gState != STATE_DND) {
      tone(BUZZER, 1200, 80);
    }
  }

  // ── Screen sleep (risparmio energetico) ─────────────────────────
  // Spegne il backlight dopo SCREEN_TIMEOUT_MS di inattività, a meno
  // che non sia in corso un pomodoro/riposo. wakeScreen() lo riaccende
  // su notifica BLE/Wi-Fi, pressione di un tasto o cambio di stato.
  if (sessionRunning) {
    lastActivityMs = now;
    if (!screenOn) {
      display.setBrightness(SCREEN_BRIGHTNESS);
      screenOn = true;
    }
  } else if (screenOn && now - lastActivityMs >= SCREEN_TIMEOUT_MS) {
    display.setBrightness(0);
    screenOn = false;
  }

  // ── Display ───────────────────────────────────────────────────
  if (gScreen == SCR_BOOT) {
    drawBootScreen();
  } else if (gScreen == SCR_CLOCK) {
    drawClockScreen(now);   // SCR_CLOCK prima di STATE_SETUP
  } else if (gState == STATE_SETUP) {
    drawSetupScreen(now);
  } else if (gState == STATE_EVOLVING) {
    drawEvolvingScreen(now);
  } else if (gScreen == SCR_BATTLE) {
    drawBattleScreen(now);
  } else if (gScreen == SCR_MENU) {
    drawMenuScreen(now);
  } else if (gScreen == SCR_STATUS) {
    drawStatusScreen();
  } else if (gScreen == SCR_REGISTRO) {
    drawRegistroScreen(now);
  } else {
    drawMainScreen(now);
  }

  // ── LED ───────────────────────────────────────────────────────
  if (sessionRunning)
    digitalWrite(LED, (now/500)%2==0 ? LOW : HIGH);
  else if (isSick)
    digitalWrite(LED, (now/200)%2==0 ? LOW : HIGH);  // lampeggio veloce se malato
  else
    digitalWrite(LED, HIGH);

  delay(40);
}
