// ═══════════════════════════════════════════════════════════════
//  petcube_battle.h
//  Modulo battaglie PetCube v0.13
//
//  Contiene:
//    - Stat base di combat per le 32 creature
//    - Costanti meccaniche (RNG range, type bonus, modifier streak/priority)
//    - Strutture pacchetto BLE notifica
//    - Helper di selezione nemico (categoria + hash → indice REGISTRO)
//    - Formule di calcolo stat finali e danno
//
//  La state machine battle e il rendering sono in PetCube.ino.
// ═══════════════════════════════════════════════════════════════

#ifndef PETCUBE_BATTLE_H
#define PETCUBE_BATTLE_H

#include <Arduino.h>

// ── INDICI REGISTRO ───────────────────────────────────────────
// Posizioni nel REGISTRO[] di PetCube.ino — devono coincidere.
#define IDX_KINDLEKIN         0
#define IDX_EMBERPAW         1
#define IDX_PYRUFF          2
#define IDX_BLAZEBRAND         3
#define IDX_MIGHTFORGE    4
#define IDX_FLAMEFORGE      5
#define IDX_SERAPHYRE      6
#define IDX_SHIELDMANE      7
#define IDX_FORTIFIRE      8
#define IDX_CITADELLION         9
#define IDX_AUROVULP         10
#define IDX_VULPYRE    11
#define IDX_ELDERVULP       12
#define IDX_NOXFORTRESS     13
#define IDX_DROWSEA         14
#define IDX_GLOOMFIN        15
#define IDX_FANGLURE         16
#define IDX_RIPTALON       17
#define IDX_MAULSTREAM   18
#define IDX_LEVIACRUSH  19
#define IDX_LIGHTFIN   20
#define IDX_BALEGUARD       21
#define IDX_BULWHARK       22
#define IDX_TIDENAUGHT 23
#define IDX_SIRENLURE         24
#define IDX_ABYSSIBYL          25
#define IDX_THALASSIBYL       26
#define IDX_NIGHTMARE     27

// ── STAT BASE DELLE 32 CREATURE ──────────────────────────────────
// Formato: { ATK, SPA, DEF, HP }
// Totali per stadio: Spark=40, Wisp=70, Sprite=120, Spirit=200, Avatar=270, Primal=320
struct PetStats {
  uint8_t atk;
  uint8_t spa;
  uint8_t def;
  uint8_t hp;
};

const PetStats PET_STATS[] PROGMEM = {
  // Fire condivisi + linea STR
  /*  0 Kindlekin       */ { 10,  8, 12, 10 },
  /*  1 Emberpaw       */ { 18, 13, 18, 21 },
  /*  2 Pyruff        */ { 38, 22, 28, 32 },
  /*  3 Blazebrand       */ { 62, 48, 45, 45 },
  /*  4 Mightforge  */ { 78, 65, 65, 62 },
  /*  5 Flameforge    */ { 92, 70, 82, 76 },
  /*  6 Seraphyre    */ { 72, 90, 75, 83 },
  // Fire linea ENG
  /*  7 Shieldmane    */ { 58, 50, 50, 42 },
  /*  8 Fortifire    */ { 72, 68, 70, 60 },
  /*  9 Citadellion       */ { 85, 80, 82, 73 },
  // Fire linea INT
  /* 10 Aurovulp       */ { 42, 65, 35, 58 },
  /* 11 Vulpyre  */ { 60, 78, 55, 77 },
  /* 12 Eldervulp     */ { 92, 85, 70, 73 },
  /* 13 Noxfortress   */ { 88, 75, 90, 67 },
  // Water condivisi + linea STR
  /* 14 Drowsea       */ {  8, 10, 12, 10 },
  /* 15 Gloomfin      */ { 21, 12, 17, 20 },
  /* 16 Fanglure       */ { 32, 28, 30, 30 },
  /* 17 Riptalon     */ { 55, 45, 50, 50 },
  /* 18 Maulstream */ { 80, 55, 60, 75 },
  /* 19 Leviacrush*/ { 88, 82, 78, 72 },
  /* 20 Lightfin */ { 82, 80, 76, 82 },
  // Water linea ENG
  /* 21 Baleguard     */ { 50, 48, 52, 50 },
  /* 22 Bulwhark     */ { 65, 68, 60, 77 },
  /* 23 Tidenaught    */ { 75, 92, 75, 78 },
  // Water linea INT
  /* 24 Sirenlure       */ { 45, 60, 38, 57 },
  /* 25 Abyssibyl        */ { 55, 65, 75, 75 },
  /* 26 Thalassibyl     */ { 78, 85, 82, 75 },
  // Water Dark (linea STR)
  /* 27 Nightmare   */ { 90, 70, 92, 68 },
};

inline PetStats getStatsFromRegistro(uint8_t idx) {
  PetStats s;
  memcpy_P(&s, &PET_STATS[idx], sizeof(PetStats));
  return s;
}

// ── COSTANTI BATTLE ───────────────────────────────────────────
#define BATTLE_CLASHES        3
#define BATTLE_CRIT_MULT      2
#define BATTLE_RNG_MIN_PCT    75      // RNG 0.75x
#define BATTLE_RNG_MAX_PCT    125     // RNG 1.25x
#define BATTLE_DEF_DIVIDER    4       // danno_netto = danno_lordo - DEF/4
#define BATTLE_ENEMY_CRIT_PCT 10      // 10% chance crit nemico

// Type bonus (× 100 per evitare float)
#define TYPE_ELEM_FAVOR       130     // ×1.30 (Fire vs Water o Water vs Fire)
#define TYPE_ELEM_DISFAVOR    77      // ×0.77 (1/1.30)
#define TYPE_ELEM_NEUTRAL     100     // ×1.00 (stesso elemento)
#define TYPE_MORAL_FAVOR      120     // ×1.20 (Light vs Dark o Dark vs Light)
#define TYPE_MORAL_NEUTRAL    100     // ×1.00 (altre combinazioni)

// Priority modifier
#define PRIO_LOW_PCT          80
#define PRIO_NORMAL_PCT      100
#define PRIO_HIGH_PCT        130

// Streak modifier (rubber band)
#define STREAK_BOOST_3_PCT   120     // dopo 3 vittorie consecutive: nemico +20%
#define STREAK_BOOST_5_PCT   140     // dopo 5: +40%

// Reward
#define BATTLE_WIN_HAP         5
#define BATTLE_WIN_STAT        3

// Notifica
#define NOTIF_TTL_MS         (30UL * 60 * 1000)   // 30 min
#define LONG_PRESS_MS         5000                // 5s long-press

// Probabilità malattia post-sconfitta in base agli escrementi (×100)
inline uint8_t illnessChanceAfterDefeat(int poopCount, bool poopMega) {
  if (poopMega) return 40;
  switch (poopCount) {
    case 0: return 0;
    case 1: return 5;
    case 2: return 10;
    case 3: return 15;
    default: return 20;
  }
}

// ── PACCHETTO NOTIFICA (BLE/WiFi) ─────────────────────────────
// 64 byte max, allineato a struttura fissa per evitare ambiguità endianness.
enum NotifSource : uint8_t {
  SRC_DISCORD   = 0,
  SRC_GMAIL     = 1,
  SRC_CALENDAR  = 2,
  SRC_SLACK     = 3,
  SRC_TRELLO    = 4,
  SRC_GITHUB    = 5,
  SRC_TELEGRAM  = 6,
  SRC_WHATSAPP  = 7,
  SRC_INSTAGRAM = 8,  // non più usato (plugin rimosso), mantenuto per compatibilità wire
  SRC_GENERIC   = 255
};

enum NotifPriority : uint8_t { PRIO_LOW = 0, PRIO_NORMAL = 1, PRIO_HIGH = 2 };

enum NotifCategory : uint8_t {
  CAT_LODE        = 0,  // Positive + Low urgency
  CAT_OPPORTUNITA = 1,  // Positive + High urgency  -> linea ENG
  CAT_ROUTINE     = 2,  // Neutral + Low            -> Standard
  CAT_SCADENZA    = 3,  // Neutral + High           -> linea STR
  CAT_CRITICA     = 4,  // Negative + Low           -> Dark
  CAT_CRISI       = 5,  // Negative + High          -> Dark
  CAT_CURIOSITA   = 6,  // Question + Low           -> linea INT
  CAT_AIUTO       = 7   // Question + High          -> linea INT
};

struct NotifPacket {
  uint8_t version;       // 1 byte — schema versioning
  NotifSource source;    // 1 byte
  NotifPriority priority;// 1 byte
  NotifCategory category;// 1 byte
  uint16_t seedHash;     // 2 byte — hash del seed
  uint8_t seedLength;    // 1 byte — char count del seed originale (max 50)
  uint8_t _reserved;     // 1 byte padding
  uint32_t timestamp;    // 4 byte — epoch
  char seedPreview[52];  // 52 byte — primi caratteri del seed per display (zero-terminato)
};
// Totale: 64 byte esatti.

// ── SELEZIONE NEMICO ──────────────────────────────────────────
// Step 1: stadio del nemico = stadio del pet
// Step 2: elemento del nemico da source, fallback opposto se uguale al pet
// Step 3: categoria semantica filtra candidati (linea STR/ENG/INT o Light/Dark)
// Step 4: hash del seed sceglie tra i candidati

enum BattleElement : uint8_t { BE_FIRE = 0, BE_WATER = 1 };

// Mappa source → elemento atteso del nemico.
// Per Calendar, dipende dal giorno della settimana (0=Sunday).
inline BattleElement sourceToElement(NotifSource src, uint8_t dayOfWeek, BattleElement petElement) {
  BattleElement out;
  switch (src) {
    case SRC_DISCORD:
    case SRC_SLACK:
      out = BE_FIRE;
      break;
    case SRC_GMAIL:
    case SRC_TRELLO:
    case SRC_GITHUB:
    case SRC_TELEGRAM:
    case SRC_WHATSAPP:
      out = BE_WATER;
      break;
    case SRC_CALENDAR:
      // Lun(1)/Mer(3)/Ven(5) = Fire, Mar(2)/Gio(4)/Sab(6) = Water, Dom(0) = match pet
      if (dayOfWeek == 0) out = petElement;
      else                out = (dayOfWeek % 2 == 1) ? BE_FIRE : BE_WATER;
      break;
    default:
      out = BE_FIRE;
  }
  // Fallback: se l'elemento del nemico coincide col pet, ribaltalo
  if (out == petElement) out = (petElement == BE_FIRE) ? BE_WATER : BE_FIRE;
  return out;
}

// Lista candidati per uno stadio dato. Restituisce numero di candidati e popola array.
// pet_stage: 0=Spark, 1=Wisp, 2=Sprite, 3=Spirit, 4=Avatar, 5=Primal
// pet_element: per la regola "Baby/Sprite stesso elemento OK"
// enemy_element: già risolto da sourceToElement
// category: per filtrare (Light/Dark/STR/ENG/INT)
// Output: candidates[] contiene indici REGISTRO, ritorna count.
inline uint8_t enemyCandidates(uint8_t pet_stage,
                                BattleElement enemy_element,
                                NotifCategory category,
                                uint8_t* candidates) {
  uint8_t count = 0;

  // Caso speciale Spark (stadio 0): solo Kindlekin e Drowsea esistono
  if (pet_stage == 0) {
    candidates[count++] = (enemy_element == BE_FIRE) ? IDX_KINDLEKIN : IDX_DROWSEA;
    return count;
  }
  // Caso speciale SparkI (stadio 1): solo Emberpaw e Gloomfin
  if (pet_stage == 1) {
    candidates[count++] = (enemy_element == BE_FIRE) ? IDX_EMBERPAW : IDX_GLOOMFIN;
    return count;
  }
  // Caso speciale Sprite (stadio 2): solo Pyruff e Fanglure
  if (pet_stage == 2) {
    candidates[count++] = (enemy_element == BE_FIRE) ? IDX_PYRUFF : IDX_FANGLURE;
    return count;
  }

  // Spirit (3), Avatar (4): solo STR/ENG/INT esistono (no Light/Dark)
  // Per categorie Light/Dark a questi stadi, pesco da tutte e 3 le linee come fallback
  if (pet_stage == 3) {
    if (enemy_element == BE_FIRE) {
      // STR=Blazebrand (3), ENG=Shieldmane (7), INT=Aurovulp (10)
      switch (category) {
        case CAT_SCADENZA:                              // STR
          candidates[count++] = IDX_BLAZEBRAND; break;
        case CAT_OPPORTUNITA:                           // ENG
          candidates[count++] = IDX_SHIELDMANE; break;
        case CAT_CURIOSITA: case CAT_AIUTO:             // INT
          candidates[count++] = IDX_AUROVULP; break;
        default:                                        // Light/Dark/Routine
          candidates[count++] = IDX_BLAZEBRAND;
          candidates[count++] = IDX_SHIELDMANE;
          candidates[count++] = IDX_AUROVULP;
      }
    } else {
      // STR=Riptalon (17), ENG=Baleguard (21), INT=Sirenlure (24)
      switch (category) {
        case CAT_SCADENZA:                              // STR
          candidates[count++] = IDX_RIPTALON; break;
        case CAT_OPPORTUNITA:                           // ENG
          candidates[count++] = IDX_BALEGUARD; break;
        case CAT_CURIOSITA: case CAT_AIUTO:             // INT
          candidates[count++] = IDX_SIRENLURE; break;
        default:
          candidates[count++] = IDX_RIPTALON;
          candidates[count++] = IDX_BALEGUARD;
          candidates[count++] = IDX_SIRENLURE;
      }
    }
    return count;
  }

  if (pet_stage == 4) {
    if (enemy_element == BE_FIRE) {
      // STR=Mightforge(4), ENG=Fortifire(8), INT=Vulpyre(11)
      switch (category) {
        case CAT_SCADENZA:    candidates[count++] = IDX_MIGHTFORGE; break;
        case CAT_OPPORTUNITA: candidates[count++] = IDX_FORTIFIRE; break;
        case CAT_CURIOSITA: case CAT_AIUTO:
                              candidates[count++] = IDX_VULPYRE; break;
        default:
          candidates[count++] = IDX_MIGHTFORGE;
          candidates[count++] = IDX_FORTIFIRE;
          candidates[count++] = IDX_VULPYRE;
      }
    } else {
      // STR=Maulstream(18), ENG=Bulwhark(22), INT=Abyssibyl(25)
      switch (category) {
        case CAT_SCADENZA:    candidates[count++] = IDX_MAULSTREAM; break;
        case CAT_OPPORTUNITA: candidates[count++] = IDX_BULWHARK; break;
        case CAT_CURIOSITA: case CAT_AIUTO:
                              candidates[count++] = IDX_ABYSSIBYL; break;
        default:
          candidates[count++] = IDX_MAULSTREAM;
          candidates[count++] = IDX_BULWHARK;
          candidates[count++] = IDX_ABYSSIBYL;
      }
    }
    return count;
  }

  // Primal (5): tutti gli Std/Light/Dark esistono
  if (enemy_element == BE_FIRE) {
    // Fire Primal: Flameforge(5), Seraphyre(6), Citadellion(9), Eldervulp(12), Noxfortress(13)
    switch (category) {
      case CAT_LODE:                                                // Light
        candidates[count++] = IDX_SERAPHYRE;
        break;
      case CAT_CRITICA: case CAT_CRISI:                             // Dark
        candidates[count++] = IDX_NOXFORTRESS;
        break;
      case CAT_SCADENZA:                                            // STR Standard
        candidates[count++] = IDX_FLAMEFORGE; break;
      case CAT_OPPORTUNITA:                                         // ENG Standard
        candidates[count++] = IDX_CITADELLION; break;
      case CAT_CURIOSITA: case CAT_AIUTO:                           // INT Standard
        candidates[count++] = IDX_ELDERVULP; break;
      default:                                                      // Routine: tutti
        candidates[count++] = IDX_FLAMEFORGE;
        candidates[count++] = IDX_CITADELLION;
        candidates[count++] = IDX_ELDERVULP;
        candidates[count++] = IDX_SERAPHYRE;
    }
  } else {
    // Water Primal
    switch (category) {
      case CAT_LODE:
        candidates[count++] = IDX_LIGHTFIN;
        break;
      case CAT_CRITICA: case CAT_CRISI:
        candidates[count++] = IDX_NIGHTMARE;  // Dark condiviso tutte le linee Water
        break;
      case CAT_SCADENZA:
        candidates[count++] = IDX_LEVIACRUSH; break;
      case CAT_OPPORTUNITA:
        candidates[count++] = IDX_TIDENAUGHT; break;
      case CAT_CURIOSITA: case CAT_AIUTO:
        candidates[count++] = IDX_THALASSIBYL; break;
      default:
        candidates[count++] = IDX_LEVIACRUSH;
        candidates[count++] = IDX_TIDENAUGHT;
        candidates[count++] = IDX_THALASSIBYL;
        candidates[count++] = IDX_LIGHTFIN;
    }
  }
  return count;
}

// Sceglie il nemico finale dato hash del seed + candidati.
inline uint8_t selectEnemy(const NotifPacket& pkt,
                            uint8_t pet_stage,
                            BattleElement pet_element,
                            uint8_t dayOfWeek) {
  BattleElement enemy_elem = sourceToElement(pkt.source, dayOfWeek, pet_element);
  uint8_t candidates[8];
  uint8_t n = enemyCandidates(pet_stage, enemy_elem, pkt.category, candidates);
  if (n == 0) return IDX_PYRUFF;  // fallback safety
  return candidates[pkt.seedHash % n];
}

// ── CALCOLO STAT IN COMBAT ────────────────────────────────────
// Le stat finali del pet usano stat base + stat allevamento/2 (max +50).
// Le stat finali del nemico usano stat base × priority × streak.

struct CombatStats {
  uint16_t atk;
  uint16_t spa;
  uint16_t def;
  uint16_t hp;
};

inline CombatStats computePetCombatStats(uint8_t pet_registry_idx,
                                          int statSTR, int statINT,
                                          int statENG, int statHAP) {
  PetStats base = getStatsFromRegistro(pet_registry_idx);
  CombatStats c;
  c.atk = base.atk + statSTR / 2;
  c.spa = base.spa + statINT / 2;
  c.def = base.def + statENG / 2;
  c.hp  = base.hp  + statHAP / 2;
  return c;
}

inline uint16_t streakModifierPct(uint8_t streak) {
  if (streak >= 5) return STREAK_BOOST_5_PCT;
  if (streak >= 3) return STREAK_BOOST_3_PCT;
  return 100;
}

inline uint16_t priorityModifierPct(NotifPriority p) {
  switch (p) {
    case PRIO_LOW: return PRIO_LOW_PCT;
    case PRIO_HIGH: return PRIO_HIGH_PCT;
    default: return PRIO_NORMAL_PCT;
  }
}

inline CombatStats computeEnemyCombatStats(uint8_t enemy_idx,
                                            NotifPriority priority,
                                            uint8_t streak) {
  PetStats base = getStatsFromRegistro(enemy_idx);
  uint32_t prio = priorityModifierPct(priority);
  uint32_t streak_pct = streakModifierPct(streak);
  uint32_t combined = (prio * streak_pct) / 100;  // es. 130 * 120 / 100 = 156
  CombatStats c;
  c.atk = ((uint32_t)base.atk * combined) / 100;
  c.spa = ((uint32_t)base.spa * combined) / 100;
  c.def = ((uint32_t)base.def * combined) / 100;
  c.hp  = ((uint32_t)base.hp  * combined) / 100;
  return c;
}

// ── CALCOLO DANNO ─────────────────────────────────────────────
// type_elem_pct: 130 (favor) / 100 (neutral) / 77 (disfavor)
// type_moral_pct: 120 (favor) / 100 (neutral)
// crit: 1 (no) o 2 (sì)
inline uint16_t computeDamage(uint16_t stat_off,
                               uint16_t def_avversario,
                               uint8_t type_elem_pct,
                               uint8_t type_moral_pct,
                               uint8_t crit_mult,
                               uint8_t rng_pct  /* 75..125 */) {
  // danno_lordo = stat_off × rng × type_elem × type_moral × crit
  uint32_t lordo = (uint32_t)stat_off * rng_pct / 100;
  lordo = lordo * type_elem_pct / 100;
  lordo = lordo * type_moral_pct / 100;
  lordo = lordo * crit_mult;
  // sottraggo DEF/4 per damage reduction
  int32_t netto = (int32_t)lordo - (int32_t)(def_avversario / BATTLE_DEF_DIVIDER);
  if (netto < 1) netto = 1;
  return (uint16_t)netto;
}

// Determina type_elem in base agli elementi del pet e del nemico
inline uint8_t typeElemPct(BattleElement attacker, BattleElement defender) {
  if (attacker == defender) return TYPE_ELEM_NEUTRAL;
  return TYPE_ELEM_FAVOR;  // chi attacca col vantaggio fa più danno
}

// Determina type_moral in base alle varianti
// pet_variant / enemy_variant: 0=Std, 1=Light, 2=Dark
inline uint8_t typeMoralPct(uint8_t attacker_variant, uint8_t defender_variant) {
  // Light vs Dark e Dark vs Light entrambi danno bonus
  bool isLightVsDark = (attacker_variant == 1 && defender_variant == 2);
  bool isDarkVsLight = (attacker_variant == 2 && defender_variant == 1);
  if (isLightVsDark || isDarkVsLight) return TYPE_MORAL_FAVOR;
  return TYPE_MORAL_NEUTRAL;
}

// ── HELPER: ESITO BATTLE ──────────────────────────────────────
struct ClashResult {
  uint16_t pet_dmg;
  uint16_t enemy_dmg;
  bool pet_won;
};

struct BattleResult {
  uint8_t pet_wins;
  uint8_t enemy_wins;
  uint32_t pet_damage_taken_total;
  uint32_t enemy_damage_taken_total;
};

#endif // PETCUBE_BATTLE_H
