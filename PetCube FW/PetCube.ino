// ═══════════════════════════════════════════════════════════════
//  PetCube — Firmware v0.14
//  Schermata principale: solo sprite (×3) + stato pomodoro in alto
//  Menu testuale: Status / Feed / Clean / Heal / Registro
//  Escrementi, malattia, morte, nutrizione
//  ⚔️  Sistema battaglie con notifiche da Companion App PC
//  📡  Server BLE GATT per ricezione notifiche dalla Companion
//
//  Controlli schermata principale:
//    A = apri menu
//    B = avvia sessione (tipo da orientamento); se Idle → orologio CEST
//        Long-press B (5s) con notifica pendente → battle!
//    C = annulla sessione in corso (penalità -2 HAP); se orologio → chiudi
//        Long-press C (5s) con notifica pendente → dismiss volontario
//
//  File necessari nella stessa cartella:
//    PetCube.ino
//    petcube_sprites.h
//    petcube_battle.h
//
//  Libreria richiesta (oltre alle solite):
//    BLE built-in di ESP32 Arduino Core (NO install separata richiesta)
//
//  ── CHANGELOG v13 → v14 ───────────────────────────────────────
//  📡  Server BLE GATT integrato:
//      - Service "PetCube" che riceve NotifPacket da Companion App PC
//      - Advertising attivo SOLO in Idle (risparmio batteria)
//      - Icona BT in alto a sinistra durante connessione PC
//      - Beep ascendenti su connessione, discendenti su disconnessione
//      - Spinlock per accesso thread-safe a pendingNotifs[]
//  • Bump FW_VERSION a 14, migrazione NVS automatica
//
//  ── CHANGELOG v12 → v13 ───────────────────────────────────────
//  ⚔️  Sistema battaglie completo:
//      - Notifiche da PC via mock interno (BLE in arrivo nelle prossime patch)
//      - Icona source accanto al pet, max 3 in coda, TTL 30 min
//      - Long-press B → battle, long-press C → dismiss
//      - 3 clash best-of-3 con timing-game per critici (cursore mobile)
//      - Formule danno con type bonus Fire/Water + Light/Dark
//      - HP come tie-breaker
//      - Stat dominante del nemico → +3 alla stat allevamento corrispondente
//      - Sconfitta: chance malattia scalata con escrementi a terra
//  • Nuovo campo NVS streak (vittorie consecutive)
//  • Nuovo campo NVS enemyKnown[32] (silhouette+nome battuto in battle)
//  • Migrazione NVS forzata da v12 → v13
// ═══════════════════════════════════════════════════════════════

#include <Wire.h>
#include <TFT_eSPI.h>
#include <Adafruit_MPU6050.h>
#include <Adafruit_Sensor.h>
#include <Preferences.h>
#include "petcube_sprites.h"
#include "petcube_battle.h"
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>

TFT_eSPI   tft;
TFT_eSprite canvas(&tft);
Adafruit_MPU6050 mpu;
Preferences prefs;

// ── PIN ───────────────────────────────────────────────────────
#define BTN_A  D9   // apri menu / cursore menu / cicla uovo setup
#define BTN_B  D7   // avvia sessione / conferma menu / orologio idle / seleziona uovo
#define BTN_C  D3   // annulla sessione / chiudi menu / chiudi orologio
#define BUZZER D6
#define LED    LED_BUILTIN

// ── COSTANTI ──────────────────────────────────────────────────
#define SESSION_MS           (25UL * 60 * 1000)
#define DECAY_WINDOW_MS      (4UL  * 60 * 60 * 1000)
#define DECAY_AMOUNT         10
#define HAP_PER_SESSION      8
#define STAT_PER_SESSION     10
#define ORIENT_THRESHOLD     7.0f
#define FEED_COOLDOWN_MS     (60UL * 60 * 1000)
#define FEED_HAP_BONUS       15
#define FEED_STAT_BONUS      5
#define POOP_HAP_MALUS       2
#define POOP_MEGA_MALUS      5
#define POOP_SICK_MALUS      10
#define SICK_HAP_DECAY       5    // per ora
#define SICK_DEATH_MS        (2UL * 60 * 60 * 1000)
#define POOP_INTERVAL_MIN_MS (30UL * 60 * 1000)
#define POOP_INTERVAL_MAX_MS (45UL * 60 * 1000)
#define CANCEL_HAP_MALUS     2    // penalità HAP per cancel sessione
#define FW_VERSION           14   // bump al cambio struttura NVS

// ── BLE UUIDs (devono matchare quelli della Companion App in config.json) ──
#define BLE_DEVICE_NAME      "PetCube"
#define BLE_SERVICE_UUID     "12345678-1234-5678-1234-56789abcdef0"
#define BLE_CHAR_UUID        "12345678-1234-5678-1234-56789abcdef1"
#define DISP_SIZE            240
#define SPR_SCALE            7    // sprite 16×16 → 112×112
#define SPR_SIZE             16
#define SPR_DRAW_SIZE        (SPR_SIZE * SPR_SCALE)  // 112
#define SPR_X                ((DISP_SIZE - SPR_DRAW_SIZE) / 2)  // 64
#define SPR_Y                68   // sotto l'area stato
#define ANIM_IDLE_MS         400
#define ANIM_SLEEP_MS        700
#define ANIM_ATK_MS          380
#define SPR_DRIFT            55
#define SPR_DRIFT_PERIOD_MS  16000
#define EVOLVE_ANIM_MS       3000

// ── Colori ────────────────────────────────────────────────────
#define C_BG    TFT_BLACK
#define C_FG    TFT_WHITE
#define C_HAP   TFT_GREEN
#define C_STR   TFT_RED
#define C_INT   TFT_BLUE
#define C_ENG   TFT_YELLOW
#define C_TIMER TFT_ORANGE
#define C_CYAN  0x07FFu
#define C_DIM   0x39C7u
#define C_POOP  0x6200u

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
enum Element { FIRE, WATER };

// ── SPRITE TABLE ──────────────────────────────────────────────
struct DigiSprites {
  const unsigned char* idle[3];
  const unsigned char* happy[2];
  const unsigned char* sleep[2];
  const unsigned char* atk[2];
  const unsigned char* angry;
  const unsigned char* sick[2];
};

#define MAKE_SPR(n) { \
  { spr_##n##_idle1, spr_##n##_idle2, spr_##n##_idle3 }, \
  { spr_##n##_happy1, spr_##n##_happy2 }, \
  { spr_##n##_sleep1, spr_##n##_sleep2 }, \
  { spr_##n##_atk1,   spr_##n##_atk2   }, \
  spr_##n##_angry1, \
  { spr_##n##_sick1,  spr_##n##_sick2  } \
}

const DigiSprites SPR_BOTAMON      = MAKE_SPR(botamon);
const DigiSprites SPR_KOROMON      = MAKE_SPR(koromon);
const DigiSprites SPR_AGUMON       = MAKE_SPR(agumon);
const DigiSprites SPR_GREYMON      = MAKE_SPR(greymon);
const DigiSprites SPR_METALGREYMON = MAKE_SPR(metalgreymon);
const DigiSprites SPR_WARGREYMON   = MAKE_SPR(wargreymon);
const DigiSprites SPR_PHOENIXMON   = MAKE_SPR(phoenixmon);
const DigiSprites SPR_MUGENDRAMON  = MAKE_SPR(mugendramon);
const DigiSprites SPR_PUNIMON      = MAKE_SPR(punimon);
const DigiSprites SPR_TSUNOMON     = MAKE_SPR(tsunomon);
const DigiSprites SPR_GABUMON      = MAKE_SPR(gabumon);
const DigiSprites SPR_GARURUMON    = MAKE_SPR(garurumon);
const DigiSprites SPR_WEREGARURUMON   = MAKE_SPR(weregarurumon);
const DigiSprites SPR_METALGARURUMON = MAKE_SPR(metalgarurumon);
const DigiSprites SPR_CRESGARURUMON  = MAKE_SPR(cresgarurumon);
const DigiSprites SPR_SKULLMAMMON    = MAKE_SPR(skullmammon);
// ── Nuovi Digimon (Fire ENG/INT, Water ENG/INT) ────────────────
const DigiSprites SPR_TYRANNOMON        = MAKE_SPR(tyrannomon);
const DigiSprites SPR_GIGADRAMON        = MAKE_SPR(gigadramon);
const DigiSprites SPR_DUKEMON           = MAKE_SPR(dukemon);
const DigiSprites SPR_MITAMAMON         = MAKE_SPR(mitamamon);
const DigiSprites SPR_MERAMON           = MAKE_SPR(meramon);
const DigiSprites SPR_DEATHMERAMON      = MAKE_SPR(deathmeramon);
const DigiSprites SPR_BEELZEMON         = MAKE_SPR(beelzemon);
const DigiSprites SPR_LUCEMON           = MAKE_SPR(lucemon);
const DigiSprites SPR_SEADRAMON         = MAKE_SPR(seadramon);
const DigiSprites SPR_MERMAIMON         = MAKE_SPR(mermaimon);
const DigiSprites SPR_ANCIENTMERMAIMON  = MAKE_SPR(ancientmermaimon);
const DigiSprites SPR_VIKEMON           = MAKE_SPR(vikemon);
const DigiSprites SPR_GESOMON           = MAKE_SPR(gesomon);
const DigiSprites SPR_WHAMON            = MAKE_SPR(whamon);
const DigiSprites SPR_PLESIOMON         = MAKE_SPR(plesiomon);
const DigiSprites SPR_RYUGUMON          = MAKE_SPR(ryugumon);

// lineVariant: 0=STR, 1=ENG, 2=INT
// Stadi 0-2 condivisi, stadi 3-4 e Ultimate dipendono da lineVariant
const char* FIRE_SHARED[]   = { "Botamon","Koromon","Agumon" };
const char* FIRE_LINE0[]    = { "Greymon","MetalGreymon" };          // STR
const char* FIRE_LINE1[]    = { "Tyrannomon","Gigadramon" };         // ENG
const char* FIRE_LINE2[]    = { "Meramon","Deathmeramon" };          // INT
const char* FIRE_FINAL0[]   = { "WarGreymon","Phoenixmon","Mugendramon" };
const char* FIRE_FINAL1[]   = { "Dukemon","Mitamamon","Mugendramon" };
const char* FIRE_FINAL2[]   = { "Beelzemon","Lucemon","Mugendramon" };

const char* WATER_SHARED[]  = { "Punimon","Tsunomon","Gabumon" };
const char* WATER_LINE0[]   = { "Garurumon","WereGarurumon" };       // STR
const char* WATER_LINE1[]   = { "Seadramon","Mermaimon" };       // ENG
const char* WATER_LINE2[]   = { "Gesomon","Whamon" };               // INT
const char* WATER_FINAL0[]  = { "MetalGarurumon","CresGarurumon","SkullMammothmon" };
const char* WATER_FINAL1[]  = { "AncientMermaimon","Vikemon","SkullMammothmon" };
const char* WATER_FINAL2[]  = { "Plesiomon","Ryugumon","SkullMammothmon" };

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

bool          sessionRunning  = false;
GameState     sessionType     = STATE_WORK;
unsigned long sessionStartMs  = 0;
unsigned long lastSessionMs   = 0;
unsigned long lastDecayMs     = 0;
unsigned long evolveStartMs   = 0;

// Feed
unsigned long lastFeedMs    = 0;

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
bool     petCritThisClash   = false;

// Registro nemici battuti (32 flag, persistenti nel namespace 'registro')
bool enemyKnown[32] = {false};

// ── 📡 BLE GATT server state ──────────────────────────────────
BLEServer*        bleServer        = nullptr;
BLECharacteristic* bleNotifChar    = nullptr;
bool              bleAdvertising   = false;   // stiamo facendo advertising?
bool              bleClientConnected = false; // un PC è connesso?
bool              bleClientConnectedPrev = false; // per detection edge
bool              bleInitialized   = false;   // stack già inizializzato?
// Spinlock per protezione pendingNotifs[] tra BLE callback task e main loop
portMUX_TYPE      notifsMux        = portMUX_INITIALIZER_UNLOCKED;

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
const int MENU_ITEMS = 5;
const char* MENU_LABELS[] = { "Status", "Feed", "Clean", "Heal", "Registro" };


// ── REGISTRO ──────────────────────────────────────────────────
// Tutti i Digimon del gioco in ordine
// Stat base: cuori 1-3 (basso/normale/alto) per STR/INT/ENG/HAP
struct DigiEntry {
  const char*      name;
  const char*      element;
  const DigiSprites* sprites;
  uint8_t          strH;   // cuori STR 1-3
  uint8_t          intH;   // cuori INT 1-3
  uint8_t          engH;   // cuori ENG 1-3
  uint8_t          hapH;   // cuori HAP 1-3
  uint8_t          obtained; // quante volte ottenuto (salvato NVS)
};

// Registro completo — 32 Digimon
// Linea 0=STR, 1=ENG, 2=INT per ogni elemento
DigiEntry REGISTRO[] = {
  // ── Fire condivisi ──────────────────────────────────────────
  { "Botamon",      "Fire",  &SPR_BOTAMON,       1,1,1,2, 0 },
  { "Koromon",      "Fire",  &SPR_KOROMON,        1,1,1,2, 0 },
  { "Agumon",       "Fire",  &SPR_AGUMON,         2,1,2,2, 0 },
  // ── Fire linea STR ──────────────────────────────────────────
  { "Greymon",      "Fire",  &SPR_GREYMON,        3,1,2,2, 0 },
  { "MetalGreymon", "Fire",  &SPR_METALGREYMON,   3,2,2,2, 0 },
  { "WarGreymon",   "Fire",  &SPR_WARGREYMON,     3,2,3,2, 0 },
  { "Phoenixmon",   "Light", &SPR_PHOENIXMON,     2,3,2,3, 0 },
  // ── Fire linea ENG ──────────────────────────────────────────
  { "Tyrannomon",   "Fire",  &SPR_TYRANNOMON,     2,1,3,2, 0 },
  { "Gigadramon",   "Fire",  &SPR_GIGADRAMON,     2,2,3,2, 0 },
  { "Dukemon",      "Fire",  &SPR_DUKEMON,        3,2,3,2, 0 },
  { "Mitamamon",    "Light", &SPR_MITAMAMON,      2,3,3,3, 0 },
  // ── Fire linea INT ──────────────────────────────────────────
  { "Meramon",      "Fire",  &SPR_MERAMON,        1,3,2,2, 0 },
  { "Deathmeramon", "Fire",  &SPR_DEATHMERAMON,   2,3,2,2, 0 },
  { "Beelzemon",    "Dark",  &SPR_BEELZEMON,      2,3,2,1, 0 },
  { "Lucemon",      "Light", &SPR_LUCEMON,        1,3,1,3, 0 },
  // ── Mugendramon (Dark condiviso Fire) ───────────────────────
  { "Mugendramon",  "Dark",  &SPR_MUGENDRAMON,    3,1,3,1, 0 },
  // ── Water condivisi ─────────────────────────────────────────
  { "Punimon",      "Water", &SPR_PUNIMON,        1,1,1,2, 0 },
  { "Tsunomon",     "Water", &SPR_TSUNOMON,        1,1,1,2, 0 },
  { "Gabumon",      "Water", &SPR_GABUMON,         1,2,2,2, 0 },
  // ── Water linea STR ─────────────────────────────────────────
  { "Garurumon",    "Water", &SPR_GARURUMON,       2,2,2,2, 0 },
  { "WereGarurumon","Water", &SPR_WEREGARURUMON,   3,2,2,2, 0 },
  { "MetalGarurumon","Water",&SPR_METALGARURUMON,  3,2,3,2, 0 },
  { "CresGarurumon","Light", &SPR_CRESGARURUMON,   2,3,2,3, 0 },
  // ── Water linea ENG ─────────────────────────────────────────
  { "Seadramon",       "Water", &SPR_SEADRAMON,        2,1,3,2, 0 },
  { "Mermaimon",       "Water", &SPR_MERMAIMON,        2,2,3,2, 0 },
  { "AncientMermaimon","Water", &SPR_ANCIENTMERMAIMON, 2,2,3,2, 0 },
  { "Vikemon",         "Light", &SPR_VIKEMON,          3,2,2,3, 0 },
  // ── Water linea INT ─────────────────────────────────────────
  { "Gesomon",      "Water", &SPR_GESOMON,        1,3,2,2, 0 },
  { "Whamon",       "Water", &SPR_WHAMON,         1,3,2,2, 0 },
  { "Plesiomon",    "Water", &SPR_PLESIOMON,      1,3,2,3, 0 },
  { "Ryugumon",     "Light", &SPR_RYUGUMON,       1,3,1,3, 0 },
  // ── SkullMammothmon (Dark condiviso Water) ──────────────────
  { "SkullMammothmon","Dark",&SPR_SKULLMAMMON,     3,1,3,1, 0 },
};
const int REGISTRO_SIZE = 32;
int registroCursor = 0;  // Digimon corrente nel registro

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

// Menu aggiornato con Registro
// Setup
int setupChoice = 0;

// ── HELPERS ───────────────────────────────────────────────────
const DigiSprites* getCurrentSprites() {
  int v = max(0, finalVariant);
  if (gElement == FIRE) {
    if (evoStage == 0) return &SPR_BOTAMON;
    if (evoStage == 1) return &SPR_KOROMON;
    if (evoStage == 2) return &SPR_AGUMON;
    if (evoStage == 3) {
      if (lineVariant == 0) return &SPR_GREYMON;        // STR
      if (lineVariant == 1) return &SPR_TYRANNOMON;     // ENG
      return &SPR_MERAMON;                              // INT
    }
    if (evoStage == 4) {
      if (lineVariant == 0) return &SPR_METALGREYMON;   // STR
      if (lineVariant == 1) return &SPR_GIGADRAMON;     // ENG
      return &SPR_DEATHMERAMON;                         // INT
    }
    // Ultimate Fire
    if (lineVariant == 0) {
      const DigiSprites* f0[] = { &SPR_WARGREYMON, &SPR_PHOENIXMON, &SPR_MUGENDRAMON };
      return f0[v];
    }
    if (lineVariant == 1) {
      const DigiSprites* f1[] = { &SPR_DUKEMON,    &SPR_MITAMAMON,  &SPR_MUGENDRAMON };
      return f1[v];
    }
    // INT line
    const DigiSprites* f2[] = { &SPR_BEELZEMON,    &SPR_LUCEMON,    &SPR_MUGENDRAMON };
    return f2[v];
  } else {
    if (evoStage == 0) return &SPR_PUNIMON;
    if (evoStage == 1) return &SPR_TSUNOMON;
    if (evoStage == 2) return &SPR_GABUMON;
    if (evoStage == 3) {
      if (lineVariant == 0) return &SPR_GARURUMON;      // STR
      if (lineVariant == 1) return &SPR_SEADRAMON;      // ENG
      return &SPR_GESOMON;                              // INT
    }
    if (evoStage == 4) {
      if (lineVariant == 0) return &SPR_WEREGARURUMON;  // STR
      if (lineVariant == 1) return &SPR_MERMAIMON;      // ENG
      return &SPR_WHAMON;                               // INT
    }
    // Ultimate Water
    if (lineVariant == 0) {
      const DigiSprites* w0[] = { &SPR_METALGARURUMON,   &SPR_CRESGARURUMON, &SPR_SKULLMAMMON };
      return w0[v];
    }
    if (lineVariant == 1) {
      const DigiSprites* w1[] = { &SPR_ANCIENTMERMAIMON, &SPR_VIKEMON,       &SPR_SKULLMAMMON };
      return w1[v];
    }
    // INT line
    const DigiSprites* w2[] = { &SPR_PLESIOMON,        &SPR_RYUGUMON,      &SPR_SKULLMAMMON };
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

const unsigned char* getFrame(const DigiSprites* spr, unsigned long now) {
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
                      const unsigned char* bmp, bool mirror = false,
                      uint16_t color = C_FG) {
  for (int row = 0; row < SPR_SIZE; row++) {
    uint8_t b0 = pgm_read_byte(&bmp[row * 2]);
    uint8_t b1 = pgm_read_byte(&bmp[row * 2 + 1]);
    uint16_t rowbits = (uint16_t)b0 | ((uint16_t)b1 << 8);
    for (int col = 0; col < SPR_SIZE; col++) {
      if (rowbits & (1 << col)) {
        int drawCol = mirror ? (SPR_SIZE - 1 - col) : col;
        canvas.fillRect(x + drawCol*scale, y + row*scale, scale, scale, color);
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
  prefs.putULong("lastFeed", lastFeedMs);
  prefs.putULong("nextPoop", nextPoopMs);
  prefs.putULong("sickMs",   sickStartMs);
  prefs.putULong("sickDec",  lastSickDecayMs);
  prefs.putInt("sickEp",     sickEpisodes);
  prefs.putUChar("streak",   battleStreak);
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
    lastFeedMs    = prefs.getULong("lastFeed", 0);
    nextPoopMs    = prefs.getULong("nextPoop", 0);
    sickStartMs   = prefs.getULong("sickMs",   0);
    lastSickDecayMs = prefs.getULong("sickDec",0);
    sickEpisodes  = prefs.getInt("sickEp",  0);
    battleStreak  = prefs.getUChar("streak", 0);
  }
  prefs.end();
  return ok;
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
  // Segna il nuovo Digimon nel registro
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

// ── SESSIONE ──────────────────────────────────────────────────
void startSession(GameState type) {
  sessionType    = type;
  gState         = STATE_SESSION;
  sessionRunning = true;
  sessionStartMs = millis();  // impostato DOPO i tone per evitare underflow
  tone(BUZZER, 880, 80);
}

void completeSession() {
  sessionRunning = false;
  lastSessionMs  = millis();
  sessTotal++;
  switch (sessionType) {
    case STATE_TRAINING: statSTR = min(100, statSTR+STAT_PER_SESSION); sessActive++; break;
    case STATE_STUDY:    statINT = min(100, statINT+STAT_PER_SESSION); sessActive++; break;
    default:             statENG = min(100, statENG+STAT_PER_SESSION); sessActive++; break;
  }
  statHAP = min(100, statHAP + HAP_PER_SESSION);
  tone(BUZZER,1047,80); delay(90);
  tone(BUZZER,1319,80); delay(90);
  tone(BUZZER,1568,200);
  checkEvolution();
  saveToNVS();
  // Imposta lo stato in base all'orientamento corrente, non torna di default a IDLE
  if (gState != STATE_EVOLVING) enterStateFromOri(gOrient);
}

void cancelSession() {
  sessionRunning = false;
  // Penalità sessione fallita
  statHAP  = max(0, statHAP - CANCEL_HAP_MALUS);
  sessTotal++;  // conta come sessione totale ma non come attiva
  gState = STATE_IDLE;
  tone(BUZZER, 440, 150);
  saveToNVS();
}

// ── DECAY ─────────────────────────────────────────────────────
void checkDecay(unsigned long now) {
  // Sospeso in Sleep (cubo con schermo verso l'alto)
  if (gOrient == ORI_FACE_UP) { lastDecayMs = now; return; }
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
    // Il mega non è stato pulito: il Digimon si ammala
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
    gState = STATE_DEAD;
    gScreen = SCR_MAIN;
    tone(BUZZER, 200, 1000);
    saveToNVS();
  }
}

void healDigi() {
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
void feedDigi(unsigned long now) {
  lastFeedMs = now;
  statHAP    = min(100, statHAP + FEED_HAP_BONUS);
  int stat   = random(3);
  if      (stat == 0) statSTR = min(100, statSTR + FEED_STAT_BONUS);
  else if (stat == 1) statINT = min(100, statINT + FEED_STAT_BONUS);
  else                statENG = min(100, statENG + FEED_STAT_BONUS);
  tone(BUZZER, 659, 80); delay(90);
  tone(BUZZER, 784, 80); delay(90);
  tone(BUZZER, 1047, 150);
  saveToNVS();
}

// ── TRANSIZIONE STATO DA ORIENTAMENTO ────────────────────────
Orientation lastDisplayOri = ORI_NORMAL;

void updateDisplayRotation(Orientation ori) {
  if (ori == lastDisplayOri) return;
  lastDisplayOri = ori;
  switch (ori) {
    case ORI_LEFT:        tft.setRotation(1); break;
    case ORI_RIGHT:       tft.setRotation(3); break;
    case ORI_UPSIDE_DOWN: tft.setRotation(2); break;
    default:              tft.setRotation(0); break;
  }
}

void enterStateFromOri(Orientation ori) {
  if (gState == STATE_EVOLVING || gState == STATE_SETUP ||
      gState == STATE_DEAD) return;
  // Se c'è una sessione in corso e cambia orientamento: annulla
  if (gState == STATE_SESSION && sessionRunning) {
    cancelSession();
  }
  updateDisplayRotation(ori);
  switch (ori) {
    case ORI_NORMAL:      gState = STATE_IDLE;     break;
    case ORI_LEFT:        gState = STATE_TRAINING;  break;
    case ORI_RIGHT:       gState = STATE_STUDY;     break;
    case ORI_FACE_UP:     gState = STATE_SLEEP;     break;
    case ORI_UPSIDE_DOWN: gState = STATE_WORK;      break;
    case ORI_FACE_DOWN:   gState = STATE_DND;       break;
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
  const DigiEntry& e = REGISTRO[registroCursor];

  canvas.setTextFont(2); canvas.setTextColor(C_CYAN, C_BG);
  char hdr[24];
  sprintf(hdr, "%d/%d  Registro", registroCursor+1, REGISTRO_SIZE);
  canvas.drawString(hdr, 20, 12);
  canvas.drawFastHLine(0, 32, DISP_SIZE, C_DIM);

  if (e.obtained == 0) {
    canvas.setTextFont(4); canvas.setTextColor(C_DIM, C_BG);
    canvas.drawString("???", 80, 100);
    canvas.setTextFont(2); canvas.setTextColor(C_FG, C_BG);
    canvas.drawString("Non ancora", 70, 150);
    canvas.drawString("ottenuto!", 75, 172);
  } else {
    const unsigned char* frame = e.sprites->idle[(now/ANIM_IDLE_MS)%3];
    drawSpriteScaled(14, 40, 4, frame);

    canvas.setTextFont(4); canvas.setTextColor(C_FG, C_BG);
    canvas.drawString(e.name, 84, 42);
    canvas.setTextFont(2); canvas.setTextColor(C_CYAN, C_BG);
    char ob[20]; sprintf(ob, "%s  x%d", e.element, e.obtained);
    canvas.drawString(ob, 84, 76);

    // Cuori: S, I, E, H — due colonne
    canvas.setTextColor(C_FG, C_BG);
    canvas.drawString("S", 84, 102); drawHearts(100, 100, e.strH);
    canvas.drawString("I", 84, 130); drawHearts(100, 128, e.intH);
    canvas.drawString("E", 84, 158); drawHearts(100, 156, e.engH);
    canvas.drawString("H", 84, 186); drawHearts(100, 184, e.hapH);
  }

  canvas.drawFastHLine(0, 215, DISP_SIZE, C_DIM);
  canvas.setTextFont(1); canvas.setTextSize(2); canvas.setTextColor(C_DIM, C_BG);
  canvas.drawString("A=cicla  C=esci", 50, 220);
  canvas.setTextSize(1);
  canvas.pushSprite(0, 0);
}


void drawBootScreen() {
  canvas.fillSprite(C_BG);
  canvas.setTextFont(4); canvas.setTextColor(C_CYAN, C_BG);
  canvas.drawString("PetCube", 72, 40);
  canvas.setTextFont(2); canvas.setTextColor(C_DIM, C_BG);
  canvas.drawString("v0.14", 98, 74);
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
  canvas.drawString("A=cambia  B=OK", 58, 188);
  canvas.setTextSize(1);
  canvas.pushSprite(0, 0);
}

void drawSetupScreen(unsigned long now) {
  canvas.fillSprite(C_BG);
  canvas.setTextFont(2); canvas.setTextColor(C_FG, C_BG);
  canvas.drawString("Scegli elemento:", 50, 18);
  canvas.drawFastHLine(20, 40, 200, C_DIM);

  int frame = (now / 400) % 2;
  const unsigned char* botaFrame = SPR_BOTAMON.idle[frame];
  const unsigned char* puniFrame = SPR_PUNIMON.idle[frame];

  // Fire a sinistra, Water a destra (sprite ×5 = 80×80)
  const int sz = 5;
  int lx = 30, rx = 130, sy = 60;
  if (setupChoice == 0) canvas.drawRect(lx-4, sy-4, SPR_SIZE*sz+8, SPR_SIZE*sz+8, C_TIMER);
  else                  canvas.drawRect(rx-4, sy-4, SPR_SIZE*sz+8, SPR_SIZE*sz+8, C_CYAN);

  drawSpriteScaled(lx, sy, sz, botaFrame, false, setupChoice==0 ? C_TIMER : C_DIM);
  drawSpriteScaled(rx, sy, sz, puniFrame, false, setupChoice==1 ? C_CYAN  : C_DIM);

  canvas.setTextFont(2);
  canvas.setTextColor(setupChoice==0 ? C_TIMER : C_DIM, C_BG);
  canvas.drawString("Fire",  45, 168);
  canvas.setTextColor(setupChoice==1 ? C_CYAN : C_DIM, C_BG);
  canvas.drawString("Water", 140, 168);

  canvas.setTextFont(1); canvas.setTextSize(2); canvas.setTextColor(C_DIM, C_BG);
  canvas.drawString("A=cambia  B=OK", 55, 210);
  canvas.setTextSize(1);
  canvas.pushSprite(0, 0);
}

void drawMainScreen(unsigned long now) {
  canvas.fillSprite(C_BG);
  const DigiSprites* spr = getCurrentSprites();

  // ── DEAD ──────────────────────────────────────────────────────
  if (gState == STATE_DEAD) {
    if ((now/500)%2) {
      canvas.setTextFont(4); canvas.setTextColor(C_DIM, C_BG);
      canvas.drawString("ADDIO...", 68, 105);
    }
    drawSpriteScaled(SPR_X, SPR_Y, SPR_SCALE, spr->sick[(now/600)%2]);
    canvas.pushSprite(0, 0);
    return;
  }

  // ── Label stato ───────────────────────────────────────────────
  const char* stateLabel = "Idle";
  uint16_t labelColor = C_DIM;
  if (isSick) {
    stateLabel = ((now/400)%2) ? "SICK!" : "";
    labelColor = C_STR;
  } else {
    switch (gState) {
      case STATE_TRAINING: stateLabel = "Training"; labelColor = C_STR;   break;
      case STATE_STUDY:    stateLabel = "Study";    labelColor = C_INT;   break;
      case STATE_WORK:     stateLabel = "Work";     labelColor = C_ENG;   break;
      case STATE_SLEEP:    stateLabel = "Sleep";    labelColor = C_CYAN;  break;
      case STATE_DND:      stateLabel = "DND";      labelColor = C_DIM;   break;
      case STATE_SESSION:
        if (sessionType==STATE_TRAINING)      { stateLabel="Training"; labelColor=C_STR; }
        else if (sessionType==STATE_STUDY)    { stateLabel="Study";    labelColor=C_INT; }
        else                                  { stateLabel="Work";     labelColor=C_ENG; }
        break;
      default: break;
    }
  }

  canvas.setTextFont(2); canvas.setTextColor(labelColor, C_BG);
  int lw = canvas.textWidth(stateLabel);
  canvas.drawString(stateLabel, (DISP_SIZE - lw) / 2, 14);

  // ── Timer sessione ────────────────────────────────────────────
  if (sessionRunning) {
    unsigned long elapsed = now - sessionStartMs;
    unsigned long remain  = SESSION_MS > elapsed ? SESSION_MS - elapsed : 0;
    char buf[8];
    sprintf(buf, "%02lu:%02lu", remain/60000, (remain%60000)/1000);
    canvas.setTextFont(2); canvas.setTextColor(C_TIMER, C_BG);
    int tw = canvas.textWidth(buf);
    canvas.drawString(buf, (DISP_SIZE - tw) / 2, 36);
    int prog = (int)((unsigned long)elapsed * 180 / SESSION_MS);
    canvas.drawRect(30, 56, 180, 6, C_DIM);
    if (prog > 0) canvas.fillRect(31, 57, min(prog, 178), 4, C_TIMER);
  }

  // ── Icona BT ─────────────────────────────────────────────────
  if (bleClientConnected) {
    canvas.setTextFont(1); canvas.setTextSize(2); canvas.setTextColor(C_CYAN, C_BG);
    canvas.drawString("B", 188, 12);
    canvas.setTextSize(1);
  } else if (bleAdvertising && (now/700)%2 == 0) {
    canvas.fillRect(188, 16, 6, 6, C_DIM);
  }

  // ── Sprite ───────────────────────────────────────────────────
  const unsigned char* frame = getFrame(spr, now);
  int driftX   = (gState == STATE_IDLE && !isSick) ? getIdleDriftX(now) : 0;
  bool mirrorX = (gState == STATE_IDLE && !isSick) ? getIdleMirror(now) : false;
  uint16_t sprColor = isSick ? 0x07E0 : C_FG;  // verde se malato
  drawSpriteScaled(SPR_X + driftX, SPR_Y, SPR_SCALE, frame, mirrorX, sprColor);

  // ── Escrementi ───────────────────────────────────────────────
  if (gState == STATE_IDLE && !isSick) {
    if (poopMega) {
      // Mega: doppia dimensione, bottom-right
      const int s = 4;
      int mx = 168, my = 193;
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
      int ix = 155, iy = 13;

      const unsigned char* iconBmp = nullptr;
      switch (src) {
        case SRC_DISCORD:  iconBmp = ICON_DISCORD;   break;
        case SRC_CALENDAR: iconBmp = ICON_CALENDAR;  break;
        case SRC_GMAIL:    iconBmp = ICON_GMAIL;     break;
        case SRC_TRELLO:   iconBmp = ICON_HACKNPLAN; break;
        case SRC_TELEGRAM: iconBmp = ICON_TELEGRAM;  break;
        case SRC_WHATSAPP: iconBmp = ICON_WHATSAPP;  break;
        default: break;
      }

      if (iconBmp) {
        canvas.drawXBitmap(ix, iy, iconBmp, 12, 12, C_FG);
      } else {
        canvas.fillRoundRect(ix-1, iy-1, 14, 14, 2, C_DIM);
        canvas.setTextFont(1); canvas.setTextSize(1); canvas.setTextColor(C_BG, C_BG);
        const char* ch = "?";
        switch (src) {
          case SRC_DISCORD: ch = "D"; break;
          case SRC_SLACK:   ch = "S"; break;
          case SRC_GITHUB:  ch = "G"; break;
          default: break;
        }
        canvas.drawString(ch, ix+4, iy+3);
      }
      if (n > 1) {
        char cnt[3]; sprintf(cnt, "%d", n);
        canvas.setTextFont(1); canvas.setTextSize(2); canvas.setTextColor(C_TIMER, C_BG);
        canvas.drawString(cnt, ix-14, iy+1);
        canvas.setTextSize(1);
      }
    }
  }

  canvas.pushSprite(0, 0);
}

void drawMenuScreen(unsigned long now) {
  canvas.fillSprite(C_BG);

  // Sprite piccolo in alto centrato (×4 = 64×64)
  const DigiSprites* spr = getCurrentSprites();
  const unsigned char* frame = getFrame(spr, now);
  drawSpriteScaled(88, 10, 4, frame);

  canvas.drawFastHLine(20, 82, 200, C_DIM);

  unsigned long now_ = millis();
  for (int i = 0; i < MENU_ITEMS; i++) {
    int y = 90 + i * 26;

    char label[20];
    strcpy(label, MENU_LABELS[i]);

    bool enabled = true;
    if (i == 1) {
      if (lastFeedMs > 0 && now_ - lastFeedMs < FEED_COOLDOWN_MS) {
        unsigned long wait = (FEED_COOLDOWN_MS - (now_ - lastFeedMs)) / 60000 + 1;
        sprintf(label, "Feed(%lum)", wait);
        enabled = false;
      }
    }
    if (i == 2) enabled = (poopCount > 0 || poopMega);
    if (i == 3) enabled = isSick;

    uint16_t c = !enabled ? C_DIM : (i == menuCursor ? C_TIMER : C_FG);
    if (i == menuCursor) canvas.fillRect(20, y-2, 200, 22, 0x1082);
    canvas.setTextFont(2); canvas.setTextColor(c, C_BG);
    canvas.drawString(i == menuCursor ? (String(">") + label).c_str() : label, 30, y);
  }

  canvas.drawFastHLine(20, 222, 200, C_DIM);
  canvas.setTextFont(1); canvas.setTextSize(2); canvas.setTextColor(C_DIM, C_BG);
  canvas.drawString("A=giu  B=ok  C=esci", 30, 226);
  canvas.setTextSize(1);
  canvas.pushSprite(0, 0);
}

void drawStatusScreen() {
  canvas.fillSprite(C_BG);

  // Nome + stadio
  canvas.setTextFont(4); canvas.setTextColor(C_FG, C_BG);
  canvas.drawString(getCurrentName(), 20, 14);

  const char* stageNames[] = {"Baby I","Baby II","Child","Adult","Perfect","Ultimate"};
  canvas.setTextFont(2); canvas.setTextColor(C_CYAN, C_BG);
  canvas.drawString(stageNames[min(evoStage,5)], 20, 50);
  if (evoStage >= 3) {
    const char* lnames[] = { "STR", "ENG", "INT" };
    canvas.drawString(lnames[lineVariant], 120, 50);
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
  sprintf(buf, "W:%d  L:%d", battlesWon, battlesLost); canvas.drawString(buf, 20, 192);
  if (isSick) {
    canvas.setTextColor(C_STR, C_BG); canvas.drawString("MALATO!", 140, 192);
  }

  canvas.drawFastHLine(10, 214, 220, C_DIM);
  canvas.setTextFont(1); canvas.setTextSize(2); canvas.setTextColor(C_DIM, C_BG);
  canvas.drawString("B=ora  C=indietro", 40, 220);
  canvas.setTextSize(1);
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
    canvas.drawString("Imposta ora CEST:", 35, 28);
    canvas.drawFastHLine(20, 50, 200, C_DIM);

    char buf[12];
    sprintf(buf, "%02d : %02d", clockEditH, clockEditM);
    canvas.setTextFont(4); canvas.setTextColor(C_TIMER, C_BG);
    int tw = canvas.textWidth(buf);
    canvas.drawString(buf, (DISP_SIZE - tw) / 2, 90);

    canvas.setTextFont(1); canvas.setTextSize(2); canvas.setTextColor(C_DIM, C_BG);
    canvas.drawString("A=+ora   B=+min", 40, 160);
    canvas.drawString("C=salva  (salta=C)", 28, 182);
    canvas.setTextSize(1);
  } else {
    char buf[10];
    sprintf(buf, "%02d:%02d", hh, mm);
    canvas.setTextFont(4); canvas.setTextColor(C_FG, C_BG);
    int tw = canvas.textWidth(buf);
    canvas.drawString(buf, (DISP_SIZE - tw) / 2, 70);

    // Barra secondi
    canvas.drawRect(30, 110, 180, 10, C_DIM);
    canvas.fillRect(31, 111, ss * 178 / 59, 8, C_CYAN);

    // Sprite animato centrato
    const DigiSprites* spr = getCurrentSprites();
    drawSpriteScaled(SPR_X, 128, SPR_SCALE, spr->idle[(now/400)%3]);

    canvas.setTextFont(1); canvas.setTextSize(2); canvas.setTextColor(C_DIM, C_BG);
    canvas.drawString("C = chiudi", 75, 220);
    canvas.setTextSize(1);
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
  const DigiSprites* spr = getCurrentSprites();

  unsigned long nowFresh = millis();
  unsigned long el = (nowFresh >= evolveStartMs) ? (nowFresh - evolveStartMs) : 0;

  // Flash sprite
  if ((now / 80) % 2 == 0)
    drawSpriteScaled(SPR_X, SPR_Y, SPR_SCALE, spr->idle[0]);

  canvas.setTextFont(4); canvas.setTextColor(C_TIMER, C_BG);
  canvas.drawString("Evoluzione!", 40, 14);

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
    case 1: // Feed
      if (lastFeedMs == 0 || now - lastFeedMs >= FEED_COOLDOWN_MS) {
        feedDigi(now);
        gScreen = SCR_MAIN;
      }
      break;
    case 2: // Clean
      if (poopCount > 0 || poopMega) {
        cleanPoop(now);
        gScreen = SCR_MAIN;
      }
      break;
    case 3: // Heal
      if (isSick) {
        healDigi();
        gScreen = SCR_MAIN;
      }
      break;
    case 4: // Registro
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
    // Il main loop suonerà il beep di connessione vedendo il cambio di stato
    // (qui non chiamiamo tone() perché siamo in un task BLE, meglio non bloccare)
  }
  void onDisconnect(BLEServer* server) override {
    bleClientConnected = false;
    // ESP32 BLE non riavvia l'advertising automaticamente dopo disconnessione
    // Lo riavvieremo dal main loop quando rientriamo in Idle
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

// Inizializza BLE stack una volta sola (al boot)
void bleInit() {
  if (bleInitialized) return;
  BLEDevice::init(BLE_DEVICE_NAME);
  bleServer = BLEDevice::createServer();
  bleServer->setCallbacks(new PetCubeBLEServerCallbacks());

  BLEService* svc = bleServer->createService(BLE_SERVICE_UUID);
  bleNotifChar = svc->createCharacteristic(
    BLE_CHAR_UUID,
    BLECharacteristic::PROPERTY_WRITE
  );
  bleNotifChar->setCallbacks(new PetCubeBLECharCallbacks());
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
//   - in Idle: advertising attivo (visibile ai PC)
//   - in altri stati: advertising spento
//
// IMPORTANTE: NON forziamo la disconnessione del client durante stati transitori.
// Il client se ne andrà da solo se non riceve risposta, o resterà connesso
// silente. Forzare la disconnessione durante l'avvio di una battle può causare
// race condition con il task BLE.
void bleUpdateState() {
  bool shouldAdvertise = (gState == STATE_IDLE) && !bleClientConnected;

  if (shouldAdvertise && !bleAdvertising) {
    bleStartAdvertising();
  } else if (!shouldAdvertise && bleAdvertising && gState != STATE_IDLE) {
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
  // Approssimazione: il firmware non ha RTC, l'orologio è solo HH:MM impostato
  // dall'utente al boot. Per ora ritorniamo un valore fisso (Mercoledì = 3),
  // verrà sostituito quando aggiungeremo tracking giorno.
  // TODO v0.14: integrare giorno della settimana.
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
  // Botamon..Mugendramon (0..15) = Fire, Punimon..SkullMammon (16..31) = Water
  if (idx <= 15) return BE_FIRE;
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

// Mock per testing: invia una notifica fittizia
void mockNotification(NotifSource src, NotifPriority prio, NotifCategory cat,
                       const char* preview) {
  NotifPacket pkt;
  pkt.version = 1;
  pkt.source = src;
  pkt.priority = prio;
  pkt.category = cat;
  // Hash semplice (sum dei byte) del preview
  uint16_t h = 0;
  uint8_t len = 0;
  for (const char* p = preview; *p && len < 50; p++, len++) h = h * 31 + *p;
  pkt.seedHash = h;
  pkt.seedLength = len;
  pkt._reserved = 0;
  pkt.timestamp = millis() / 1000;
  strncpy(pkt.seedPreview, preview, sizeof(pkt.seedPreview) - 1);
  pkt.seedPreview[sizeof(pkt.seedPreview) - 1] = '\0';
  onNotificationReceived(pkt);
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
  if (idx >= 32 || enemyKnown[idx]) return;
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
  for (int i = 0; i < 32; i++) {
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
    if (evoStage == 0) return IDX_BOTAMON;
    if (evoStage == 1) return IDX_KOROMON;
    if (evoStage == 2) return IDX_AGUMON;
    if (evoStage == 3) {
      if (lineVariant == 0) return IDX_GREYMON;
      if (lineVariant == 1) return IDX_TYRANNOMON;
      return IDX_MERAMON;
    }
    if (evoStage == 4) {
      if (lineVariant == 0) return IDX_METALGREYMON;
      if (lineVariant == 1) return IDX_GIGADRAMON;
      return IDX_DEATHMERAMON;
    }
    // Ultimate
    int v = max(0, finalVariant);
    if (lineVariant == 0) {
      // STR final: WarGreymon, Phoenixmon, Mugendramon
      static const uint8_t f0[] = { IDX_WARGREYMON, IDX_PHOENIXMON, IDX_MUGENDRAMON };
      return f0[v];
    }
    if (lineVariant == 1) {
      static const uint8_t f1[] = { IDX_DUKEMON, IDX_MITAMAMON, IDX_MUGENDRAMON };
      return f1[v];
    }
    static const uint8_t f2[] = { IDX_BEELZEMON, IDX_LUCEMON, IDX_MUGENDRAMON };
    return f2[v];
  } else {
    if (evoStage == 0) return IDX_PUNIMON;
    if (evoStage == 1) return IDX_TSUNOMON;
    if (evoStage == 2) return IDX_GABUMON;
    if (evoStage == 3) {
      if (lineVariant == 0) return IDX_GARURUMON;
      if (lineVariant == 1) return IDX_SEADRAMON;
      return IDX_GESOMON;
    }
    if (evoStage == 4) {
      if (lineVariant == 0) return IDX_WEREGARURUMON;
      if (lineVariant == 1) return IDX_MERMAIMON;
      return IDX_WHAMON;
    }
    int v = max(0, finalVariant);
    if (lineVariant == 0) {
      static const uint8_t w0[] = { IDX_METALGARURUMON, IDX_CRESGARURUMON, IDX_SKULLMAMMON };
      return w0[v];
    }
    if (lineVariant == 1) {
      static const uint8_t w1[] = { IDX_ANCIENTMERMAIMON, IDX_VIKEMON, IDX_SKULLMAMMON };
      return w1[v];
    }
    static const uint8_t w2[] = { IDX_PLESIOMON, IDX_RYUGUMON, IDX_SKULLMAMMON };
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
  if (petIdx >= 32) {
    Serial.printf("⚠️  Invalid petIdx=%d, abort battle\n", petIdx);
    return;
  }
  battlePetStats = computePetCombatStats(petIdx, statSTR, statINT, statENG, statHAP);

  // 2. Enemy selection
  BattleElement petElem = (gElement == FIRE) ? BE_FIRE : BE_WATER;
  battleEnemyIdx = selectEnemy(pkt, evoStage, petElem, getDayOfWeek());
  if (battleEnemyIdx >= 32) {
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
    DigiStats en_base = getStatsFromRegistro(battleEnemyIdx);
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

// ── RENDERING BATTLE ──────────────────────────────────────────
void drawBattleScreen(unsigned long now) {
  canvas.fillSprite(C_BG);
  unsigned long el = now - battleStateMs;

  // Header: clash score
  canvas.setTextFont(2); canvas.setTextColor(C_CYAN, C_BG);
  char buf[32];
  sprintf(buf, "Clash %d/3   P:%d  E:%d", min((int)battleClashIdx + 1, 3),
          battlePetWins, battleEnemyWins);
  drawCenteredStr(12, buf);
  canvas.drawFastHLine(0, 32, DISP_SIZE, C_DIM);

  // Indici sprite
  uint8_t petIdx = currentPetRegistroIdx();
  if (petIdx >= 32 || battleEnemyIdx >= 32) {
    canvas.setTextFont(2); canvas.setTextColor(C_STR, C_BG);
    drawCenteredStr(115, "Battle error");
    canvas.pushSprite(0, 0);
    enterBattleStateMain();
    return;
  }
  const DigiSprites* petSpr = REGISTRO[petIdx].sprites;
  const DigiSprites* enSpr  = REGISTRO[battleEnemyIdx].sprites;
  if (!petSpr || !enSpr) {
    canvas.setTextFont(2); canvas.setTextColor(C_STR, C_BG);
    drawCenteredStr(115, "Sprite NULL");
    canvas.pushSprite(0, 0);
    enterBattleStateMain();
    return;
  }

  // Posizioni sprite battle: pet sx ×4 (64px), nemico dx ×4
  const int bscale = 4;
  const int bsz    = SPR_SIZE * bscale;  // 64
  int petX = 14, enX = DISP_SIZE - 14 - bsz;
  int yPos = 38;

  if (gState == STATE_BATTLE_INTRO) {
    int progress = min((int)el, 800);
    int offset = (progress * 30) / 800;
    petX += offset;
    enX  -= offset;
    int idx0 = (now/200) % 3;
    drawSpriteScaled(petX, yPos, bscale, petSpr->idle[idx0], true);
    drawSpriteScaled(enX,  yPos, bscale, enSpr->idle[idx0]);
    canvas.setTextFont(4); canvas.setTextColor(C_FG, C_BG);
    drawCenteredStr(115, "VS");
    if (el >= 1000) {
      gState = STATE_BATTLE_CLASH;
      battleStateMs = now;
      cursorX = 10;
      cursorDir = 1;
      petCritThisClash = false;
      critWindowStart = 120 - critWindowWidth / 2 + random(-15, 16);
      critWindowStart = constrain(critWindowStart, 12, 218 - critWindowWidth);
    }
  }
  else if (gState == STATE_BATTLE_CLASH) {
    int idx0 = (now / 250) % 2;
    drawSpriteScaled(petX, yPos, bscale, petSpr->atk[idx0], true);
    drawSpriteScaled(enX,  yPos, bscale, enSpr->atk[idx0]);

    // Timing-game bar
    canvas.drawRect(10, 180, 220, 20, C_FG);
    canvas.fillRect(critWindowStart, 181, critWindowWidth, 18, C_ENG);

    cursorX += cursorDir * 4;
    if (cursorX >= 228) { cursorX = 228; cursorDir = -1; }
    if (cursorX <= 10)  { cursorX = 10;  cursorDir =  1; }
    canvas.fillRect(cursorX, 181, 4, 18, C_BG);
    canvas.drawFastVLine(cursorX+1, 177, 26, C_FG);

    canvas.setTextFont(2); canvas.setTextColor(C_DIM, C_BG);
    drawCenteredStr(162, "B = colpo");

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

    char dmg[28];
    sprintf(dmg, "P:-%d   E:-%d", lastResult.enemy_dmg, lastResult.pet_dmg);
    canvas.setTextFont(2); canvas.setTextColor(C_TIMER, C_BG);
    drawCenteredStr(170, dmg);

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
    canvas.setTextColor(pet_won ? C_HAP : C_STR, C_BG);
    drawCenteredStr(170, pet_won ? "VITTORIA!" : "SCONFITTA");
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

  tft.init();
  tft.setRotation(0);
  tft.fillScreen(TFT_BLACK);
  canvas.setColorDepth(16);
  canvas.createSprite(DISP_SIZE, DISP_SIZE);

  // Splash
  canvas.fillSprite(C_BG);
  canvas.setTextFont(4);
  canvas.setTextColor(C_FG, C_BG);
  canvas.drawString("PetCube", 80, 100);
  canvas.setTextFont(2);
  canvas.drawString("v0.3  Loading...", 55, 135);
  canvas.pushSprite(0, 0);

  if (!mpu.begin()) {
    canvas.fillSprite(C_BG);
    canvas.setTextFont(2);
    canvas.setTextColor(TFT_RED, C_BG);
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
  loadEnemyKnown();

  // 📡 Inizializza BLE GATT server (advertising verrà avviato quando entriamo in Idle)
  bleInit();
  bootChoice  = 0;  // default: continua (se ci sono dati)

  // Mostra sempre la schermata di boot
  gScreen = SCR_BOOT;
  gState  = STATE_IDLE;  // stato neutro finché l'utente sceglie
  lastDecayMs = millis();

  delay(600);
  tone(BUZZER,523,80); delay(90);
  tone(BUZZER,659,80); delay(90);
  tone(BUZZER,784,200);
}

void loop() {
  unsigned long now = millis();

  // ⚔️  Serial mock notifiche per testing (rimuovere quando BLE è pronto)
  // Comandi disponibili nel Serial Monitor:
  //   'd' → mock notifica Discord (Crisi)
  //   'm' → mock notifica Gmail (Scadenza)
  //   'c' → mock notifica Calendar (Routine)
  //   's' → mock notifica Slack (Aiuto)
  //   't' → mock notifica Trello (Opportunità)
  //   'g' → mock notifica GitHub (Critica)
  //   'l' → mock notifica Gmail (Lode)
  if (Serial.available()) {
    char k = Serial.read();
    switch (k) {
      case 'd': mockNotification(SRC_DISCORD,  PRIO_HIGH,   CAT_CRISI,        "Server is down, fix ASAP"); break;
      case 'm': mockNotification(SRC_GMAIL,    PRIO_NORMAL, CAT_SCADENZA,     "Report due tomorrow EOD"); break;
      case 'c': mockNotification(SRC_CALENDAR, PRIO_NORMAL, CAT_ROUTINE,      "Daily standup at 10am"); break;
      case 's': mockNotification(SRC_SLACK,    PRIO_HIGH,   CAT_AIUTO,        "Can you help me with this?"); break;
      case 't': mockNotification(SRC_TRELLO,   PRIO_NORMAL, CAT_OPPORTUNITA,  "New card assigned to you"); break;
      case 'g': mockNotification(SRC_GITHUB,   PRIO_LOW,    CAT_CRITICA,      "PR review requested"); break;
      case 'l': mockNotification(SRC_GMAIL,    PRIO_LOW,    CAT_LODE,         "Great job on the demo!"); break;
    }
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
  }

  // Debug MPU su Serial Monitor (~ogni 500ms)
  static unsigned long lastSerialMs = 0;
  if (now - lastSerialMs >= 500) {
    const char* oriNames[] = { "NORMAL","LEFT","RIGHT","FACE_UP","UPSIDE_DOWN","FACE_DOWN" };
    const char* stateNames[] = {
      "SETUP","IDLE","TRAINING","STUDY","WORK","SLEEP","DND","SESSION","EVOLVING","DEAD",
      "BATTLE_INTRO","BATTLE_CLASH","BATTLE_RESOLVE","BATTLE_RESULT"
    };
    Serial.printf("ax=%+5.2f ay=%+5.2f az=%+5.2f  ori=%-12s state=%s\n",
      ax, ay, az,
      oriNames[gOrient],
      stateNames[gState]);
    lastSerialMs = now;
  }

  // ── Bottoni ───────────────────────────────────────────────────
  bool btnANow = digitalRead(BTN_A);
  bool btnBNow = digitalRead(BTN_B);
  bool btnCNow = digitalRead(BTN_C);

  if (gScreen == SCR_BOOT) {
    if (btnAPrev==HIGH && btnANow==LOW) {
      bootChoice = (bootChoice + 1) % (bootHasData ? 2 : 1);
      tone(BUZZER, 660, 30);
      delay(50);
    }
    if (btnBPrev==HIGH && btnBNow==LOW) {
      if (bootChoice == 1 || !bootHasData) {
        // Nuova partita: reset (mantiene il registro persistente)
        prefs.begin("petcube",false); prefs.clear(); prefs.end();
        // Riscrivo subito fw_version per evitare retrigger della migrazione
        prefs.begin("petcube",false); prefs.putInt("fw_ver", FW_VERSION); prefs.end();
        statSTR=statINT=statENG=0; statHAP=50;
        sessTotal=sessActive=0; evoStage=0; finalVariant=-1; lineVariant=0;
        battlesWon=battlesLost=0; poopCount=0; poopMega=false;
        isSick=false; sickStartMs=0; lastFeedMs=0;
        sickEpisodes=0;
        lastSessionMs=0; lastDecayMs=now;
        nextPoopMs = now + randomPoopInterval();
        clockSet=false; clockOffsetSec=0;
        clockEditH=12; clockEditM=0;
        gState  = STATE_SETUP;
        gScreen = SCR_CLOCK;  // imposta prima l'orologio
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
      registroMarkObtained(gElement == FIRE ? "Botamon" : "Punimon");
      tone(BUZZER,784,80); delay(90); tone(BUZZER,1047,200);
      delay(50);
    }
    // C: niente
  }
  else if (gState == STATE_DEAD) {
    // Tieni premuto A+C per 3 secondi per resettare
    if (btnANow==LOW && btnCNow==LOW) {
      // Reset
      prefs.begin("petcube",false); prefs.clear(); prefs.end();
      ESP.restart();
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
    if (btnAPrev==HIGH && btnANow==LOW) {
      if (!sessionRunning && gState == STATE_IDLE) {
        gScreen    = SCR_MENU;
        menuCursor = 0;
        tone(BUZZER, 660, 30);
      }
      delay(50);
    }
    // B: avvia sessione in Training/Study/Work
    //    in Idle → apri orologio (a meno che ci sia una notifica pendente)
    //    in Sleep/DND → niente
    if (btnBPrev==HIGH && btnBNow==LOW) {
      if (!sessionRunning) {
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
          startSession(gState);
        }
        // Sleep / DND / Dead → B non fa niente
      }
      delay(50);
    }
    // C: annulla sessione in corso
    if (btnCPrev==HIGH && btnCNow==LOW) {
      if (sessionRunning) cancelSession();
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
    // A: cicla tra i Digimon
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

  // ── Timer sessione ────────────────────────────────────────────
  if (sessionRunning && now - sessionStartMs >= SESSION_MS) {
    completeSession();
  }

  // ── Logiche background ────────────────────────────────────────
  checkDecay(now);
  checkPoop(now);
  checkSick(now);
  purgeExpiredNotifs(now);  // ⚔️  scarta notifiche scadute (TTL 30 min)
  bleUpdateState();          // 📡  gestisce advertising on/off e beep connessione

  // Suono notifica BLE (chiamato dal main loop perché il callback è in task BLE)
  if (pendingNotifBeep) {
    pendingNotifBeep = false;
    tone(BUZZER, 1200, 80);
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
