#!/usr/bin/env python3
"""
Genera PetCube FW/PetCube/petcube_battery_icons.h a partire dalle icone
32x32 RGBA in Sprite/Battery_*.png (stati batteria).

Ogni icona viene esportata come array PROGMEM uint16_t in formato RGB565
(stesso encoding di rgb565() usato per gli sfondi/icone notifica, da
disegnare con canvas.setSwapBytes(true) +
canvas.pushImage(x, y, 32, 32, ICON_x, 0x0000)).
Pixel trasparenti (alpha < 128) -> 0x0000; pixel neri opachi -> 0x0001
(per non collidere con il colore trasparente).
"""

import os
import numpy as np
from PIL import Image

SPRITE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR   = os.path.dirname(SPRITE_DIR)
HEADER_OUT = os.path.join(ROOT_DIR, "PetCube FW", "PetCube", "petcube_battery_icons.h")

ICON_SIZE = 32

# Sprite/<file>.png -> identificatore C (ICON_BATTERY_<NAME>)
ICONS = [
    ("Battery_0_25.png",            "0_25"),
    ("Battery_26_50.png",           "26_50"),
    ("Battery_51_75.png",           "51_75"),
    ("Battery_76_100.png",          "76_100"),
    ("Battery_Charging.png",        "CHARGING"),
    ("Battery_Charge complete.png", "FULL"),
]

def rgb565(r, g, b):
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)

def load_icon(filename):
    path = os.path.join(SPRITE_DIR, filename)
    img = Image.open(path).convert("RGBA")
    if img.size != (ICON_SIZE, ICON_SIZE):
        img = img.resize((ICON_SIZE, ICON_SIZE), Image.NEAREST)
    arr = np.array(img)
    px = []
    for y in range(ICON_SIZE):
        for x in range(ICON_SIZE):
            r, g, b, a = arr[y, x]
            if a < 128:
                px.append(0x0000)
            else:
                v = rgb565(int(r), int(g), int(b))
                px.append(v if v != 0x0000 else 0x0001)
    return px

def format_array(name, px):
    lines = [f"static const uint16_t ICON_BATTERY_{name}[{ICON_SIZE * ICON_SIZE}] PROGMEM = {{"]
    for i in range(0, len(px), 12):
        chunk = px[i:i+12]
        lines.append("  " + ", ".join(f"0x{v:04X}" for v in chunk) + ",")
    lines.append("};")
    return "\n".join(lines)

def main():
    out = []
    out.append("// ══════════════════════════════════════════════════════")
    out.append("//  PetCube — Icone batteria RGB565 32x32 (da Sprite/Battery_*.png)")
    out.append("//  0x0000 = trasparente. Disegnare con canvas.setSwapBytes(true) +")
    out.append("//  canvas.pushImage(x, y, ICON_BATTERY_SIZE, ICON_BATTERY_SIZE, ICON_x, 0x0000)")
    out.append("// ══════════════════════════════════════════════════════")
    out.append("")
    out.append(f"#define ICON_BATTERY_SIZE {ICON_SIZE}")
    out.append("")

    for filename, name in ICONS:
        px = load_icon(filename)
        out.append(format_array(name, px))
        out.append("")
        print(f"ICON_BATTERY_{name}: convertita da {filename}")

    with open(HEADER_OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(out).rstrip() + "\n")

    print(f"\nScritto {HEADER_OUT}")

if __name__ == "__main__":
    main()
