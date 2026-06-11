#!/usr/bin/env python3
"""
Genera PetCube FW/PetCube/petcube_sprites.h a partire dagli spritesheet
a colori in Sprite/<Nome>.png (48x64, griglia 4x3 di celle 16x16,
sfondo trasparente).

Per ogni stato viene generata una coppia di array PROGMEM:
  - spr_<name>_<state>_px[256]   uint16_t RGB565, row-major 16x16
  - spr_<name>_<state>_mask[32]  unsigned char, 2 byte/riga, bit=1 = pixel visibile

I 4 mostri "Light" (mitamamon, lucemon, vikemon, ryugumon) non hanno uno
spritesheet a colori: la maschera viene riusata dalle bitmap XBM
monocromatiche esistenti e i pixel vengono impostati a bianco (0xFFFF)
dove il bit e' acceso.
"""

import os
import re
import numpy as np
from PIL import Image

SPRITE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR   = os.path.dirname(SPRITE_DIR)
HEADER_OUT = os.path.join(ROOT_DIR, "PetCube FW", "PetCube", "petcube_sprites.h")

FRAME_NAMES = [
    'idle1', 'idle2', 'idle3',
    'happy1', 'sleep1', 'sleep2',
    'atk1', 'happy2', 'angry1',
    'sick1', 'sick2', 'atk2',
]

# Ordine e nomi dei 32 mostri (display name -> spr_<name> identifier)
MONSTERS = [
    ("Pyruff", "pyruff"),
    ("Kindlekin", "kindlekin"),
    ("Lightfin", "lightfin"),
    ("Fanglure", "fanglure"),
    ("Riptalon", "riptalon"),
    ("Blazebrand", "blazebrand"),
    ("Emberpaw", "emberpaw"),
    ("Leviacrush", "leviacrush"),
    ("Mightforge", "mightforge"),
    ("Noxfortress", "noxfortress"),
    ("Seraphyre", "seraphyre"),
    ("Drowsea", "drowsea"),
    ("Nightmare", "nightmare"),
    ("Gloomfin", "gloomfin"),
    ("Flameforge", "flameforge"),
    ("Maulstream", "maulstream"),
    ("Shieldmane", "shieldmane"),
    ("Fortifire", "fortifire"),
    ("Citadellion", "citadellion"),
    ("Mitamamon", "mitamamon"),
    ("Aurovulp", "aurovulp"),
    ("Vulpyre", "vulpyre"),
    ("Eldervulp", "eldervulp"),
    ("Lucemon", "lucemon"),
    ("Baleguard", "baleguard"),
    ("Bulwhark", "bulwhark"),
    ("Tidenaught", "tidenaught"),
    ("Vikemon", "vikemon"),
    ("Sirenlure", "sirenlure"),
    ("Abyssibyl", "abyssibyl"),
    ("Thalassibyl", "thalassibyl"),
    ("Ryugumon", "ryugumon"),
]

# Mostri senza nuovo spritesheet a colori: si riusa la maschera mono esistente
LIGHT_MONSTERS = {"mitamamon", "lucemon", "vikemon", "ryugumon"}

ALPHA_THRESHOLD = 128
CELL = 16
COLS, ROWS = 3, 4


def rgb565(r, g, b):
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


def find_spritesheet(display_name):
    """Cerca Sprite/<display_name>.png case-insensitive."""
    for f in os.listdir(SPRITE_DIR):
        if f.lower() == f"{display_name.lower()}.png":
            return os.path.join(SPRITE_DIR, f)
    return None


def cells_from_sheet(path):
    """Ritorna dict {frame_name: (px[256], mask[32])} da uno spritesheet 48x64."""
    img = Image.open(path).convert("RGBA")
    if img.size != (CELL * COLS, CELL * ROWS):
        raise ValueError(f"{path}: dimensione {img.size} != {(CELL*COLS, CELL*ROWS)}")
    arr = np.array(img)

    out = {}
    for i, fname in enumerate(FRAME_NAMES):
        row, col = divmod(i, COLS)
        cell = arr[row*CELL:(row+1)*CELL, col*CELL:(col+1)*CELL]
        px = []
        mask = []
        for r in range(CELL):
            rowbits = 0
            for c in range(CELL):
                rr, gg, bb, aa = cell[r, c]
                if aa >= ALPHA_THRESHOLD:
                    rowbits |= (1 << c)
                    px.append(rgb565(int(rr), int(gg), int(bb)))
                else:
                    px.append(0)
            mask.append(rowbits & 0xFF)
            mask.append((rowbits >> 8) & 0xFF)
        out[fname] = (px, mask)
    return out


def parse_existing_masks(name, existing_text):
    """Estrae le maschere mono esistenti (32 byte per stato) per un mostro.

    Funziona sia sull'header originale (array spr_<name>_<state>[]) sia su
    una versione gia' rigenerata (array spr_<name>_<state>_mask[]).
    """
    out = {}
    for fname in FRAME_NAMES:
        m = re.search(
            r"spr_" + re.escape(name) + r"_" + re.escape(fname) +
            r"(?:_mask)?\[\d*\]\s*PROGMEM\s*=\s*\{([^}]*)\};",
            existing_text)
        if not m:
            raise ValueError(f"Array spr_{name}_{fname} non trovato nell'header esistente")
        bytes_ = [int(x.strip(), 16) for x in m.group(1).split(',') if x.strip()]
        if len(bytes_) != 32:
            raise ValueError(f"spr_{name}_{fname}: attesi 32 byte, trovati {len(bytes_)}")
        mask = bytes_
        px = []
        for r in range(CELL):
            b0 = bytes_[r*2]
            b1 = bytes_[r*2 + 1]
            rowbits = b0 | (b1 << 8)
            for c in range(CELL):
                px.append(0xFFFF if (rowbits >> c) & 1 else 0x0000)
        out[fname] = (px, mask)
    return out


def format_px_array(name, fname, px):
    lines = [f"static const uint16_t spr_{name}_{fname}_px[256] PROGMEM = {{"]
    for i in range(0, len(px), 8):
        chunk = px[i:i+8]
        lines.append("  " + ", ".join(f"0x{v:04X}" for v in chunk) + ",")
    lines.append("};")
    return "\n".join(lines)


def format_mask_array(name, fname, mask):
    lines = [f"static const unsigned char spr_{name}_{fname}_mask[32] PROGMEM = {{"]
    for r in range(CELL):
        b0, b1 = mask[r*2], mask[r*2+1]
        lines.append(f"  0x{b0:02X}, 0x{b1:02X},")
    lines.append("};")
    return "\n".join(lines)


# Sezione finale dell'header, invariata rispetto all'originale: icone 12x12
# XBM per le notifiche (Telegram, WhatsApp, ecc.), usate da PetCube.ino.
ICON_SECTION = """// ══════════════════════════════════════════════════════════════
//  📡 ICONE NOTIFICATION SOURCE 12×12 (XBM, LSB-first)
// ══════════════════════════════════════════════════════════════
// Disegnate da Mike. 12×12 pixel, formato XBM compatibile con u8g2.drawXBM().
// Vengono renderizzate nel main screen come piccola icona accanto al pet
// quando una notifica è pendente in Idle.

static const unsigned char ICON_CALENDAR[] PROGMEM = {
  0xFE, 0x07, 0xFF, 0x0F, 0xFF, 0x0F, 0xFF, 0x0F,
  0x01, 0x08, 0x9D, 0x09, 0x51, 0x0A, 0x49, 0x0A,
  0x51, 0x0A, 0x9D, 0x09, 0x01, 0x08, 0xFE, 0x07
};

static const unsigned char ICON_GMAIL[] PROGMEM = {
  0x00, 0x00, 0x00, 0x00, 0xFE, 0x07, 0x03, 0x0C,
  0x05, 0x0A, 0x99, 0x09, 0x61, 0x08, 0x01, 0x08,
  0x01, 0x08, 0xFE, 0x07, 0x00, 0x00, 0x00, 0x00
};

// NOTA: questa icona rappresenta HacknPlan. Visto che HacknPlan invia
// notifiche con source=SRC_TRELLO (riusando l'enum esistente), questa icona
// viene mostrata quando arriva una notifica TRELLO. Se in futuro userai
// veramente Trello, dovremo aggiungere SRC_HACKNPLAN al firmware.
static const unsigned char ICON_HACKNPLAN[] PROGMEM = {
  0x60, 0x00, 0xF8, 0x01, 0xFE, 0x07, 0x07, 0x0E,
  0xF7, 0x0E, 0xF7, 0x0E, 0xF7, 0x0E, 0x07, 0x0E,
  0xE7, 0x0F, 0xFE, 0x07, 0xF8, 0x01, 0x60, 0x00
};

// Aeroplano di carta puntato a destra (logo Telegram).
// Muso: pixel singolo in alto-destra → ala larga → timone che si assottiglia in basso.
static const unsigned char ICON_TELEGRAM[] PROGMEM = {
  0x00, 0x08,  // . . . . . . . . . . . X  ← punta muso
  0x00, 0x0C,  // . . . . . . . . . . X X
  0xFE, 0x0F,  // . X X X X X X X X X X X  ← ala superiore
  0xFF, 0x0F,  // X X X X X X X X X X X X  ← corpo
  0xFF, 0x0F,  // X X X X X X X X X X X X  ← corpo
  0xFE, 0x0F,  // . X X X X X X X X X X X  ← ala inferiore
  0xF0, 0x0F,  // . . . . X X X X X X X X  ← timone
  0x00, 0x0F,  // . . . . . . . . X X X X
  0x00, 0x0C,  // . . . . . . . . . . X X
  0x00, 0x08,  // . . . . . . . . . . . X  ← punta coda
  0x00, 0x00,
  0x00, 0x00
};

// Fumetto di chat con tre puntini di digitazione (logo WhatsApp).
// Contorno aperto, coda in basso-sinistra.
static const unsigned char ICON_WHATSAPP[] PROGMEM = {
  0xFC, 0x03,  // . . X X X X X X X X . .   (arco superiore)
  0x02, 0x04,  // . X . . . . . . . . X .   (lati)
  0x01, 0x08,  // X . . . . . . . . . . X
  0x25, 0x09,  // X . X . . X . . X . . X  ← tre puntini (. . .)
  0x01, 0x08,  // X . . . . . . . . . . X
  0x01, 0x08,  // X . . . . . . . . . . X
  0x02, 0x04,  // . X . . . . . . . . X .   (lati)
  0xFC, 0x03,  // . . X X X X X X X X . .   (arco inferiore)
  0x0C, 0x00,  // . . X X . . . . . . . .   (coda fumetto)
  0x04, 0x00,  // . . X . . . . . . . . .   (punta coda)
  0x00, 0x00,
  0x00, 0x00
};

// Clyde semplificato: blob arrotondato, due occhi 2×2, sorriso, due piedi.
static const unsigned char ICON_DISCORD[] PROGMEM = {
  0xFC, 0x03,  // . . X X X X X X X X . .
  0xFE, 0x07,  // . X X X X X X X X X X .
  0x03, 0x0C,  // X X . . . . . . . . X X
  0x9B, 0x0D,  // X X . X X . . X X . X X  ← occhi
  0x9B, 0x0D,  // X X . X X . . X X . X X  ← occhi
  0x03, 0x0C,  // X X . . . . . . . . X X
  0x0B, 0x0D,  // X X . X . . . . X . X X  ← angoli bocca
  0xF3, 0x0C,  // X X . . X X X X . . X X  ← bocca
  0xFE, 0x07,  // . X X X X X X X X X X .
  0x9C, 0x03,  // . . X X X . . X X X . .  ← piedi
  0x00, 0x00,  // . . . . . . . . . . . .
  0x00, 0x00   // . . . . . . . . . . . .
};"""


def main():
    with open(HEADER_OUT, "r", encoding="utf-8") as f:
        existing_text = f.read()

    out = []
    out.append("// ══════════════════════════════════════════════════════")
    out.append("//  PetCube — Sprite a colori RGB565 16x16")
    out.append("//  Layout: spr_<name>_<state>_px (256x uint16 RGB565)")
    out.append("//          spr_<name>_<state>_mask (32 byte, 2/riga, bit=1=visibile)")
    out.append("//  Totale: 32 creature × 12 stati = 384 sprite")
    out.append("// ══════════════════════════════════════════════════════")
    out.append("")

    for display_name, name in MONSTERS:
        out.append(f"// ── {display_name} " + "─" * 50)
        out.append("")

        if name in LIGHT_MONSTERS:
            frames = parse_existing_masks(name, existing_text)
            print(f"{display_name}: riuso maschera mono esistente (placeholder bianco)")
        else:
            sheet = find_spritesheet(display_name)
            if sheet is None:
                raise FileNotFoundError(f"Spritesheet non trovato per {display_name}")
            frames = cells_from_sheet(sheet)
            print(f"{display_name}: convertito da {os.path.basename(sheet)}")

        for fname in FRAME_NAMES:
            px, mask = frames[fname]
            out.append(format_px_array(name, fname, px))
            out.append("")
            out.append(format_mask_array(name, fname, mask))
            out.append("")

    out.append(ICON_SECTION)
    out.append("")

    with open(HEADER_OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(out).rstrip() + "\n")

    print(f"\nScritto {HEADER_OUT}")


if __name__ == "__main__":
    main()
