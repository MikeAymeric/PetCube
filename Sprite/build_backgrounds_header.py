#!/usr/bin/env python3
"""
Genera PetCube FW/PetCube/petcube_backgrounds.h a partire dagli sfondi
in Sprite/BG_*.png (120x120, sfondo opaco).

Ogni sfondo viene scalato 2x (nearest-neighbor) a 240x240 ed esportato come
array PROGMEM uint16_t in formato RGB565 (stesso encoding di rgb565() usato
per le sprite, da disegnare con canvas.setSwapBytes(true) + pushImage()).
"""

import os
import numpy as np
from PIL import Image

SPRITE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR   = os.path.dirname(SPRITE_DIR)
HEADER_OUT = os.path.join(ROOT_DIR, "PetCube FW", "PetCube", "petcube_backgrounds.h")

DISP_SIZE = 240

# display name (Sprite/BG_<Nome>.png) -> identificatore C (BG_<NOME>)
BACKGROUNDS = [
    ("Normal", "NORMAL"),
    ("Sleep", "SLEEP"),
    ("Work", "WORK"),
    ("Study", "STUDY"),
    ("Training", "TRAINING"),
]


def rgb565(r, g, b):
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


def load_bg(display_name):
    path = os.path.join(SPRITE_DIR, f"BG_{display_name}.png")
    img = Image.open(path).convert("RGB")
    img = img.resize((DISP_SIZE, DISP_SIZE), Image.NEAREST)
    arr = np.array(img)
    px = []
    for y in range(DISP_SIZE):
        for x in range(DISP_SIZE):
            r, g, b = arr[y, x]
            px.append(rgb565(int(r), int(g), int(b)))
    return px


def format_array(name, px):
    lines = [f"static const uint16_t BG_{name}[{DISP_SIZE * DISP_SIZE}] PROGMEM = {{"]
    for i in range(0, len(px), 12):
        chunk = px[i:i+12]
        lines.append("  " + ", ".join(f"0x{v:04X}" for v in chunk) + ",")
    lines.append("};")
    return "\n".join(lines)


def main():
    out = []
    out.append("// ══════════════════════════════════════════════════════")
    out.append("//  PetCube — Sfondi RGB565 240x240 (upscale 2x da Sprite/BG_*.png)")
    out.append("//  Disegnare con canvas.setSwapBytes(true) + canvas.pushImage(0,0,240,240,BG_x)")
    out.append("// ══════════════════════════════════════════════════════")
    out.append("")

    for display_name, name in BACKGROUNDS:
        px = load_bg(display_name)
        out.append(format_array(name, px))
        out.append("")
        print(f"BG_{name}: convertito da BG_{display_name}.png")

    with open(HEADER_OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(out).rstrip() + "\n")

    print(f"\nScritto {HEADER_OUT}")


if __name__ == "__main__":
    main()
