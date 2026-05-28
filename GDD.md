# PetCube — Game Design Document

**Versione firmware:** 0.14  
**Versione GDD:** 1.0  
**Data:** 2026-05-28

---

## 1. Concept

PetCube è un virtual pet tascabile ispirato ai Tamagotchi / Digimon, contenuto in un cubo fisico.  
Il giocatore alleva un mostro digitale compiendo attività reali — sessioni di lavoro, studio o allenamento — che si traducono in statistiche e percorsi evolutivi diversi.  
Le notifiche ricevute dal PC del giocatore diventano sfide di battaglia contro mostri nemici, creando un loop dove la produttività quotidiana influenza direttamente la crescita del pet.

---

## 2. Hardware

| Componente | Modello | Note |
|---|---|---|
| MCU | Seeed XIAO ESP32-S3 | 240 MHz, 8 MB Flash, 2 MB PSRAM, BLE 5 |
| Display | GC9A01 240×240 round TFT | SPI, RGB565, cerchio diametro 240 px |
| IMU | Adafruit MPU6050 | I2C — rileva orientamento cubo |
| Buzzer | Passivo | PWM via `tone()` |
| Pulsanti | 3 × momentaneo (A, B, C) | Pull-up interno |
| LED | LED_BUILTIN | Feedback di avvio |

### Pin mapping

| Segnale | Pad XIAO | GPIO |
|---|---|---|
| TFT MOSI | D10 | 9 |
| TFT SCLK | D8 | 7 |
| TFT CS | D1 | 2 |
| TFT DC | D0 | 1 |
| TFT RST | D2 | 3 |
| I2C SDA (MPU) | D4 | 5 |
| I2C SCL (MPU) | D5 | 6 |
| BTN_A | D9 | 8 |
| BTN_B | D7 | 44 |
| BTN_C | D3 | 4 |
| BUZZER | D6 | 43 |

---

## 3. Controlli

| Tasto | Contesto | Azione |
|---|---|---|
| A | Main / Idle | Apri menu |
| A | Menu | Cursore su |
| A | Setup uovo | Cicla scelta elemento |
| B | Main / Idle | Avvia sessione (tipo dipende dall'orientamento) |
| B | Main / Idle | Apri schermata orologio |
| B | Menu | Conferma voce |
| B | Setup uovo | Conferma scelta |
| B (long 5s) | Notifica pendente | Avvia battaglia |
| C | Sessione in corso | Annulla sessione (−2 HAP) |
| C | Orologio | Chiudi |
| C | Menu | Chiudi menu |
| C (long 5s) | Notifica pendente | Dismiss volontario |

---

## 4. Orientamento cubo e stati

L'MPU6050 rileva l'asse gravitazionale con isteresi (8 campioni consecutivi) per evitare fluttuazioni. La faccia in basso determina lo stato attivo.

| Faccia in basso | Stato | Effetto |
|---|---|---|
| Frontale (normale) | **Idle** | Animazione passeggiata |
| Sinistra | **Training** | Sessione STR |
| Destra | **Study** | Sessione INT |
| Sotto (capovolta) | **Work** | Sessione ENG |
| Sopra (schermo verso l'alto) | **Sleep** | Sospende decay HAP |
| Posteriore (schermo a terra) | **DND** | Non disturbare |

---

## 5. Statistiche

| Stat | Colore | Aumenta con | Diminuisce con |
|---|---|---|---|
| **STR** — Forza | Rosso | Sessioni Training | — |
| **INT** — Intelligenza | Blu | Sessioni Study | — |
| **ENG** — Energia | Giallo | Sessioni Work | — |
| **HAP** — Felicità | Verde | Sessioni completate, Feed, Vittorie in battaglia | Decay ogni 4h senza sessione (−10), Malattia (−5/h), Cancel sessione (−2), Escrementi (−2/−5/−10), Sconfitta in battaglia |

Tutte le stat sono nel range 0–100. Nessuna stat scende spontaneamente tranne HAP.

---

## 6. Sessioni (meccanica Pomodoro)

Una sessione dura **25 minuti** fissi. Il giocatore posiziona il cubo con la faccia corretta, poi preme B per avviare.

- **Completata**: +10 alla stat corrispondente, +8 HAP, `sessTotal++`, `sessActive++`
- **Annullata** (tasto C): +0 stat, −2 HAP, `sessTotal++` (conta per evoluzione ma non per linea)
- Il numero di sessioni totali accumulate determina il momento dell'evoluzione.

### Soglie di evoluzione (`EVO_THRESH`)

| Stadio | Nome | Sessioni totali richieste |
|---|---|---|
| 0 | Baby I | 0 (partenza) |
| 1 | Baby II | 2 |
| 2 | Child | 6 |
| 3 | Adult | 14 |
| 4 | Perfect | 26 |
| 5 | Ultimate | 42 |

---

## 7. Albero evolutivo

### 7.1 Scelta elemento (Setup)

Al primo avvio il giocatore sceglie tra **Fire** e **Water**. I tre stadi condivisi (Baby I, Baby II, Child) sono fissi per elemento.

### 7.2 Determinazione linea (Child → Adult, stadio 2 → 3)

La stat più alta al momento dell'evoluzione determina la linea evolutiva per gli stadi Adult e Perfect.  
In caso di parità: STR > ENG > INT.

| Linea | Stat dominante |
|---|---|
| 0 — STR | Forza |
| 1 — ENG | Energia |
| 2 — INT | Intelligenza |

### 7.3 Determinazione variante Ultimate (stadio 4 → 5)

La variante finale dipende dalle condizioni al momento dell'ultima evoluzione:

| Variante | Condizione |
|---|---|
| **Dark** | Nessuna sessione nelle ultime 36h **E** HAP ≤ 30 |
| **Light** | HAP ≥ 80 **E** zero malattie nell'intera vita (`sickEpisodes == 0`) |
| **Standard** | Tutti gli altri casi |

### 7.4 Albero Fire

```
Baby I:   Botamon
Baby II:  Koromon
Child:    Agumon
             │
     ┌───────┼───────┐
  STR (L0)  ENG (L1) INT (L2)
     │         │         │
  Greymon  Tyrannomon  Meramon
     │         │         │
MetalGreymon Gigadramon Deathmeramon
     │         │         │
   ──┴─────────┴─────────┴──
   Std         Std        Std
  WarGreymon  Dukemon  Beelzemon (Dark)
   Light       Light     Light
  Phoenixmon  Phoenixmon Phoenixmon
   Dark        Dark       Dark
  Mugendramon Mugendramon Mugendramon
```

### 7.5 Albero Water

```
Baby I:   Punimon
Baby II:  Tsunomon
Child:    Gabumon
             │
     ┌───────┼───────┐
  STR (L0)  ENG (L1) INT (L2)
     │         │         │
  Garurumon Seadramon  Gesomon
     │         │         │
WereGarurumon Mermaimon  Whamon
     │         │         │
   ──┴─────────┴─────────┴──
   Std           Std         Std
MetalGarurumon AncientMermaimon Plesiomon
   Light          Light          Light
CresGarurumon  CresGarurumon  CresGarurumon
   Dark            Dark           Dark
SkullMammothmon SkullMammothmon SkullMammothmon
```

### 7.6 Sommario Digimon (32 totali)

| # | Nome | Elemento | Stadio | Linea |
|---|---|---|---|---|
| 0 | Botamon | Fire | Baby I | — |
| 1 | Koromon | Fire | Baby II | — |
| 2 | Agumon | Fire | Child | — |
| 3 | Greymon | Fire | Adult | STR |
| 4 | MetalGreymon | Fire | Perfect | STR |
| 5 | WarGreymon | Fire | Ultimate Std | STR |
| 6 | Phoenixmon | Light | Ultimate Light | Fire (tutte) |
| 7 | Tyrannomon | Fire | Adult | ENG |
| 8 | Gigadramon | Fire | Perfect | ENG |
| 9 | Dukemon | Fire | Ultimate Std | ENG |
| 10 | Mitamamon | Light | — | (solo nemico) |
| 11 | Meramon | Fire | Adult | INT |
| 12 | Deathmeramon | Fire | Perfect | INT |
| 13 | Beelzemon | Dark | Ultimate Std | INT |
| 14 | Lucemon | Light | — | (solo nemico) |
| 15 | Mugendramon | Dark | Ultimate Dark | Fire (tutte) |
| 16 | Punimon | Water | Baby I | — |
| 17 | Tsunomon | Water | Baby II | — |
| 18 | Gabumon | Water | Child | — |
| 19 | Garurumon | Water | Adult | STR |
| 20 | WereGarurumon | Water | Perfect | STR |
| 21 | MetalGarurumon | Water | Ultimate Std | STR |
| 22 | CresGarurumon | Light | Ultimate Light | Water (tutte) |
| 23 | Seadramon | Water | Adult | ENG |
| 24 | Mermaimon | Water | Perfect | ENG |
| 25 | AncientMermaimon | Water | Ultimate Std | ENG |
| 26 | Vikemon | Light | — | (solo nemico) |
| 27 | Gesomon | Water | Adult | INT |
| 28 | Whamon | Water | Perfect | INT |
| 29 | Plesiomon | Water | Ultimate Std | INT |
| 30 | Ryugumon | Light | — | (solo nemico) |
| 31 | SkullMammothmon | Dark | Ultimate Dark | Water (tutte) |

> Mitamamon, Lucemon, Vikemon e Ryugumon esistono nel registro come nemici battibili ma non sono più ottenibili come evoluzioni del giocatore.

---

## 8. Ciclo di vita

### 8.1 Escrementi

Ogni 30–45 minuti appare un escremento (randomizzato). Non vengono prodotti durante Sleep o malattia. Tipi:

| Tipo | Condizione | Malus HAP |
|---|---|---|
| Normale | Standard | −2 per escremento |
| Mega | 5° escremento non pulito | −5 |
| Malattia | Oltre il mega non pulito | Attiva stato Sick |

Pulizia tramite menu → Clean.

### 8.2 Malattia (Sick)

- Il pet lampeggia verde con label "SICK!"
- HAP decade di −5 ogni ora
- Se non curato entro **2 ore** → **morte**
- Cura tramite menu → Heal
- Ogni episodio di malattia incrementa `sickEpisodes`, rendendo impossibile la variante Light

### 8.3 Morte

- Il pet mostra l'animazione "sick" e il testo "ADDIO..."
- Resettare tramite schermata di boot (tasto B = ricomincia)

### 8.4 Nutrizione

- Menu → Feed: +15 HAP, +5 alla stat più bassa
- Cooldown: 1 ora tra un pasto e l'altro

---

## 9. Sistema battaglie

### 9.1 Origine delle battaglie

Le battaglie sono innescate da notifiche reali ricevute dal PC tramite la **Companion App** via **BLE GATT**. Ogni notifica arriva come `NotifPacket` (64 byte fissi).

Fino a 3 notifiche possono essere in coda (`pendingNotifs[]`). TTL per notifica: 30 minuti.  
Il giocatore accetta la battaglia con **long-press B (5s)** o la dismette con **long-press C (5s)**.

### 9.2 Selezione del nemico

Il nemico viene selezionato deterministicamente da `seedHash` (2 byte nel pacchetto) in 4 passi:

1. **Stadio**: uguale allo stadio del pet
2. **Elemento**: determinato dalla source della notifica (vedi tabella sotto); se uguale al pet, viene ribaltato
3. **Variante morale** (Light/Dark/Std): dalla categoria semantica della notifica
4. **Candidato finale**: `seedHash % count_candidati`

| Source | Elemento nemico |
|---|---|
| Discord, Slack | Fire |
| Gmail, Trello, GitHub, Telegram, WhatsApp | Water |
| Calendar Lun/Mer/Ven | Fire |
| Calendar Mar/Gio/Sab | Water |
| Calendar Dom | stesso del pet → ribaltato |

### 9.3 Categoria semantica → variante

| Categoria notifica | Variante nemico | Linea |
|---|---|---|
| CAT_LODE (positivo bassa urgenza) | Standard | — |
| CAT_OPPORTUNITA (positivo alta urgenza) | Standard | ENG |
| CAT_ROUTINE (neutro bassa urgenza) | Standard | — |
| CAT_SCADENZA (neutro alta urgenza) | Standard | STR |
| CAT_CRITICA / CAT_CRISI (negativo) | Dark | — |
| CAT_CURIOSITA / CAT_AIUTO (domanda) | Standard | INT |

### 9.4 Meccanica di battaglia (Timing Game)

La battaglia è divisa in **3 clash** (best-of-3).

**Fase clash:**
- Un cursore si muove avanti e indietro nella barra (range 10–228 px, ~4 secondi ciclo)
- Una **crit window** (larghezza proporzionale a `seedLength`) è evidenziata al centro
- Il giocatore preme B per fermare il cursore
- Se il cursore cade nella crit window → **critico** (danno ×2)
- Il nemico ha il 10% di probabilità autonoma di critico

**Formula danno:**

```
danno_lordo = ATK_attaccante × RNG(0.75–1.25) × type_elem × type_moral × priority × streak
danno_netto = danno_lordo − DEF_difensore / 4
```

**Bonus tipo elemento:**

| Matchup | Moltiplicatore |
|---|---|
| Fire vs Water (o viceversa) | ×1.30 favorito / ×0.77 sfavorito |
| Stesso elemento | ×1.00 |

**Bonus tipo morale:**

| Matchup | Moltiplicatore |
|---|---|
| Light vs Dark (o viceversa) | ×1.20 |
| Altre combinazioni | ×1.00 |

**Modificatori streak (rubber-band anti-win-streak):**

| Vittorie consecutive | Bonus al nemico |
|---|---|
| 3 | ×1.20 |
| 5+ | ×1.40 |

**Tie-breaker:** se i clash sono 1–1 dopo 3, vince chi ha più HP rimanenti.

### 9.5 Ricompense e penalità

| Evento | Effetto |
|---|---|
| Vittoria | +5 HAP, +3 alla stat dominante del nemico |
| Sconfitta | Chance malattia scalata con escrementi (0%–40%) |
| Vittoria | Il nemico viene aggiunto al Registro (sbloccato) |

---

## 10. Registro

Il Registro mostra tutti i 32 Digimon del gioco con:
- **Ottenuto**: silhouette sbloccata se mai allevato o affrontato in battaglia
- **Contatore**: quante volte è stato ottenuto come evoluzione

I dati di registro sono salvati nel namespace NVS `registro` **separato** dallo stato del pet — sopravvivono al reset.

---

## 11. Schermate

| Schermata | Trigger | Contenuto |
|---|---|---|
| **Boot** | Avvio con dati salvati | "Continua" / "Ricomincia", sprite del pet |
| **Setup** | Primo avvio / Ricomincia | Scelta Fire o Water (Botamon / Punimon) |
| **Main** | Default Idle/Session/Training/etc | Sprite centrato 112×112, label stato, timer sessione, escrementi, icona notifica, icona BT |
| **Menu** | Tasto A | 5 voci: Status, Feed, Clean, Heal, Registro |
| **Status** | Menu → Status | Barre colorate STR/INT/ENG/HAP, nome Digimon, sessioni, evolution progress |
| **Clock** | B in Idle | Orologio CEST, sprite, modifica ora con A/B |
| **Battle** | Long-press B con notifica | Intro sprint → clash cursore → risultato V/L |
| **Evolving** | Soglia sessioni raggiunta | Barra di progresso, nome nuovo Digimon |
| **Registro** | Menu → Registro | Lista 32 Digimon, scorrimento con A, silhouette se non ottenuto |

---

## 12. Companion App (PC)

Applicazione Python che gira sul PC del giocatore e invia `NotifPacket` al PetCube via BLE.

### 12.1 Architettura

```
main.py
  └── CompanionEngine
        ├── PluginManager          (asyncio loop, polling plugin)
        │     ├── DiscordPlugin
        │     ├── GmailPlugin
        │     ├── CalendarPlugin
        │     ├── HacknPlanPlugin
        │     ├── TelegramPlugin
        │     └── WhatsAppPlugin
        ├── Sentiment analyzer     (spaCy + dizionari IT/EN)
        └── BleSender              (bleak, BLE GATT write)
```

### 12.2 Pipeline notifica

1. Il plugin rileva un evento (nuovo messaggio, email, PR, evento calendario)
2. Il testo viene analizzato dal modulo `sentiment.py`:
   - Sentiment: positive / neutral / negative / question
   - Urgenza: low / high
   - → `NotifCategory` (8 valori)
3. Viene costruito un `NotifPacket` (64 byte)
4. Il `BleSender` lo invia via BLE GATT write al PetCube

### 12.3 Analisi sentimentale

Approccio ibrido (senza ML pesante):
- Tokenizzazione e lemmatizzazione via **spaCy**
- Dizionari keyword IT/EN per sentiment positivo/negativo/urgenza
- Detection domande via `?`, verbo iniziale, pronomi interrogativi

### 12.4 Plugin attivi

| Plugin | Source | Note |
|---|---|---|
| Discord | SRC_DISCORD | Webhook o polling |
| Gmail | SRC_GMAIL | OAuth2 |
| Google Calendar | SRC_CALENDAR | OAuth2; giorno della settimana → elemento nemico |
| HacknPlan | SRC_TRELLO | API HacknPlan (usa enum SRC_TRELLO per compat. wire) |
| Telegram | SRC_TELEGRAM | Telethon, session salvata in file |
| WhatsApp | SRC_WHATSAPP | Filtro chat/DM configurabile |

---

## 13. BLE GATT

| Campo | Valore |
|---|---|
| Device name | `PetCube` |
| Service UUID | `12345678-1234-5678-1234-56789abcdef0` |
| Characteristic UUID | `12345678-1234-5678-1234-56789abcdef1` |
| Payload | 64 byte fissi (`NotifPacket`) |
| Advertising | Solo durante stato Idle (risparmio batteria) |

---

## 14. Persistenza NVS

| Namespace | Chiavi | Sopravvive al reset? |
|---|---|---|
| `petcube` | statSTR, statINT, statENG, statHAP, evoStage, lineVariant, finalVariant, sessTotal, sessActive, battlesWon, battlesLost, battleStreak, sickEpisodes, poopCount, clockOffsetSec, gElement... | No — cancellato con "Ricomincia" |
| `registro` | r0…r31 (contatori ottenuto per Digimon) | **Sì** — persistente tra reset |

Migrazione automatica NVS tramite `FW_VERSION` (attuale: 14).

---

## 15. Roadmap futura (non implementato)

- **Multiplayer locale** via BLE peer-to-peer (due PetCube che si scambiano NotifPacket direttamente)
- **Plugin aggiuntivi**: GitHub Issues, Slack thread-level, Jira
- **Impostazione ora automatica** (NTP via WiFi o sync dalla Companion)
- **Evoluzioni speciali** legate a streak di vittorie o combinazioni di stat particolari
- **Suoni di evoluzione** differenziati per variante Light / Dark
