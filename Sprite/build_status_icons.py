#!/usr/bin/env python3
"""
Genera PetCube FW/PetCube/petcube_status_icons.h a partire da
Sprite/Bluetooth_Icon.png (32x32 RGBA).

Produce due varianti 32x32 RGB565 (PROGMEM):
  - ICON_BT:      icona a colori (connessione BLE attiva)
  - ICON_BT_GRAY: stessa icona in scala di grigio (advertising, lampeggiante)

Pixel trasparenti (alpha < 128) -> 0x0000; pixel neri opachi -> 0x0001
(per non collidere con il colore trasparente). Disegnare con
canvas.setSwapBytes(true) + canvas.pushImage(x, y, ICON_BT_SIZE, ICON_BT_SIZE, ICON_x, 0x0000).
"""

import os
import numpy as np
from PIL import Image

SPRITE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR   = os.path.dirname(SPRITE_DIR)
HEADER_OUT = os.path.join(ROOT_DIR, "PetCube FW", "PetCube", "petcube_status_icons.h")

SRC_FILE  = "Bluetooth_Icon.png"
ICON_SIZE = 24


def rgb565(r, g, b):
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


def load_icon(filename, grayscale=False):
    path = os.path.join(SPRITE_DIR, filename)
    img = Image.open(path).convert("RGBA")
    img = img.resize((ICON_SIZE, ICON_SIZE), Image.LANCZOS)
    arr = np.array(img)
    px = []
    for y in range(ICON_SIZE):
        for x in range(ICON_SIZE):
            r, g, b, a = arr[y, x]
            if a < 128:
                px.append(0x0000)
                continue
            if grayscale:
                lum = int(0.299 * r + 0.587 * g + 0.114 * b)
                r = g = b = lum
            v = rgb565(int(r), int(g), int(b))
            px.append(v if v != 0x0000 else 0x0001)
    return px


def format_array(name, px):
    lines = [f"static const uint16_t {name}[{ICON_SIZE * ICON_SIZE}] PROGMEM = {{"]
    for i in range(0, len(px), 12):
        chunk = px[i:i+12]
        lines.append("  " + ", ".join(f"0x{v:04X}" for v in chunk) + ",")
    lines.append("};")
    return "\n".join(lines)


def main():
    out = []
    out.append("// ══════════════════════════════════════════════════════")
    out.append(f"//  PetCube — Icona stato BLE RGB565 {ICON_SIZE}x{ICON_SIZE} (da Sprite/Bluetooth_Icon.png)")
    out.append("//  0x0000 = trasparente. Disegnare con canvas.setSwapBytes(true) +")
    out.append("//  canvas.pushImage(x, y, ICON_BT_SIZE, ICON_BT_SIZE, ICON_x, 0x0000)")
    out.append("// ══════════════════════════════════════════════════════")
    out.append("")
    out.append(f"#define ICON_BT_SIZE {ICON_SIZE}")
    out.append("")

    out.append(format_array("ICON_BT", load_icon(SRC_FILE, grayscale=False)))
    out.append("")
    print("ICON_BT: convertita da", SRC_FILE)

    out.append(format_array("ICON_BT_GRAY", load_icon(SRC_FILE, grayscale=True)))
    out.append("")
    print("ICON_BT_GRAY: convertita da", SRC_FILE)

    with open(HEADER_OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(out).rstrip() + "\n")

    print(f"\nScritto {HEADER_OUT}")


if __name__ == "__main__":
    main()
