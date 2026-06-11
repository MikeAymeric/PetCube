#!/usr/bin/env python3
"""
PetCube sprite processing — logica semplificata:
  - Separatori neri esattamente 4px tra i frame (verticali)
  - Eventuale bordo nero in alto/basso rimosso
  - Ogni frame: rimozione sfondo magenta + conversione residui viola
  - Frame NON tagliati ulteriormente: l'utente li ha già posizionati
    con l'origine in basso (grounded). La larghezza/altezza è quella
    definita nello spritesheet.
  - Scala uniforme: lato lungo = 130px uguale per tutti i frame
  - Preview 6×2
"""

import numpy as np
from PIL import Image, ImageDraw
import os

SPRITE_DIR  = os.path.dirname(os.path.abspath(__file__))
PREVIEW_DIR = os.path.join(SPRITE_DIR, "previews")
OUTPUT_DIR  = os.path.join(SPRITE_DIR, "processed")

os.makedirs(PREVIEW_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR,  exist_ok=True)

FIRE_NAMES = {
    'kindlekin','emberpaw','pyruff','blazebrand','mightforge','flameforge',
    'shieldmane','fortifire','citadellion','aurovulp','vulpyre','eldervulp',
    'seraphyre','noxfortress'
}
WATER_NAMES = {
    'drowsea','gloomfin','fanglure','riptalon','maulstream','leviacrush',
    'baleguard','bulwhark','tidenaught','sirenlure','abyssibyl','thalassibyl',
    'lightfin','nightmare'
}

FRAME_NAMES = [
    'idle1','idle2','angry1','angry2',
    'happy1','happy2','sick1','sick2','sleep1','sleep2'
]
TARGET_LONG = 130


# ── Maschere colore ───────────────────────────────────────────────────────────

def _bg_mask(r, g, b):
    """Pixel magenta (sfondo)."""
    ri, gi, bi = r.astype(np.int32), g.astype(np.int32), b.astype(np.int32)
    return (ri > 160) & (bi > 160) & (gi < 110) & ((ri + bi - 2*gi) > 120)

def _black_mask(r, g, b):
    """Pixel neri (separatori)."""
    return (r < 35) & (g < 35) & (b < 35)


# ── Rilevamento separatori neri ───────────────────────────────────────────────

def _find_black_bands(ratios, threshold=0.90):
    """
    Data una serie di ratios (0..1) di pixel neri per riga o colonna,
    restituisce lista di (start, end) per ogni banda continua > threshold.
    """
    bands = []
    in_band = False
    start = 0
    for i, v in enumerate(ratios):
        if v > threshold:
            if not in_band:
                start = i
                in_band = True
        else:
            if in_band:
                bands.append((start, i - 1))
                in_band = False
    if in_band:
        bands.append((start, len(ratios) - 1))
    return bands


def detect_frame_columns(arr, threshold=0.90, min_frame_width=8):
    """
    Trova i range di colonne di ogni frame leggendo i separatori neri
    verticali. Ignora fasce troppo strette (< min_frame_width px).

    Restituisce lista di (col_start, col_end) per ogni frame,
    e (row_start, row_end) per il contenuto (esclusi bordi orizzontali).
    """
    ih, iw = arr.shape[:2]
    r, g, b = arr[:,:,0], arr[:,:,1], arr[:,:,2]
    is_black = _black_mask(r, g, b)

    col_ratio = is_black.mean(axis=0)
    row_ratio = is_black.mean(axis=1)

    v_bands = _find_black_bands(col_ratio, threshold)
    h_bands = _find_black_bands(row_ratio, threshold)

    # Bounds riga: escludi bordi neri in alto e in basso
    row_start = h_bands[0][1] + 1 if h_bands and h_bands[0][0] == 0 else 0
    row_end   = h_bands[-1][0] - 1 if h_bands and h_bands[-1][1] >= ih - 2 else ih - 1

    # Colonne frame: contenuto tra i separatori verticali
    frame_cols = []
    prev = 0
    for (s, e) in v_bands:
        if s > prev and (s - prev) >= min_frame_width:
            frame_cols.append((prev, s - 1))
        prev = e + 1
    if prev < iw and (iw - prev) >= min_frame_width:
        frame_cols.append((prev, iw - 1))

    return frame_cols, row_start, row_end


# ── Rimozione sfondo e conversione residui ────────────────────────────────────

def remove_bg(cell):
    """Rende trasparenti i pixel magenta e i separatori neri."""
    r, g, b = cell[:,:,0], cell[:,:,1], cell[:,:,2]
    a = cell[:,:,3].copy()
    a[_bg_mask(r, g, b) | _black_mask(r, g, b)] = 0
    out = cell.copy()
    out[:,:,3] = a
    return out


def convert_residual(rgba, is_fire):
    """
    Pixel con tonalità magenta/viola residua (hue 265°–350°) →
    arancio (Fire) o ciano (Water), preservando luminosità e saturazione.
    """
    r = rgba[:,:,0].astype(np.float32) / 255
    g = rgba[:,:,1].astype(np.float32) / 255
    b = rgba[:,:,2].astype(np.float32) / 255
    visible = rgba[:,:,3] > 30

    maxc  = np.maximum(np.maximum(r, g), b)
    minc  = np.minimum(np.minimum(r, g), b)
    delta = maxc - minc
    sat   = np.where(maxc > 1e-6, delta / maxc, 0.0)

    hue = np.zeros_like(r)
    eps = 1e-6
    m_r = (delta > eps) & (maxc == r)
    m_g = (delta > eps) & (maxc == g)
    m_b = (delta > eps) & (maxc == b)
    hue[m_r] = ((g[m_r] - b[m_r]) / delta[m_r]) % 6
    hue[m_g] = (b[m_g] - r[m_g]) / delta[m_g] + 2
    hue[m_b] = (r[m_b] - g[m_b]) / delta[m_b] + 4
    hue = (hue / 6.0) % 1.0

    is_mag = visible & (sat > 0.20) & (hue >= 0.73) & (hue <= 0.97)
    if not np.any(is_mag):
        return rgba

    th = 0.0833 if is_fire else 0.5556
    h6 = th * 6
    hi = int(h6) % 6
    f  = h6 - int(h6)

    v_m = maxc[is_mag];  s_m = sat[is_mag]
    p = v_m * (1 - s_m)
    q = v_m * (1 - f * s_m)
    t = v_m * (1 - (1 - f) * s_m)

    nr, ng, nb = [(v_m,t,p),(q,v_m,p),(p,v_m,t),(p,q,v_m),(t,p,v_m),(v_m,p,q)][hi]

    res = rgba.copy()
    res[is_mag, 0] = np.clip(nr * 255, 0, 255).astype(np.uint8)
    res[is_mag, 1] = np.clip(ng * 255, 0, 255).astype(np.uint8)
    res[is_mag, 2] = np.clip(nb * 255, 0, 255).astype(np.uint8)
    return res


# ── Preview ───────────────────────────────────────────────────────────────────

def make_preview(name, frames, is_fire):
    CELL, PAD = 150, 24
    W, H = 5 * CELL, 2 * CELL + PAD
    preview = Image.new('RGB', (W, H), (28, 28, 28))
    draw = ImageDraw.Draw(preview)

    for i, fa in enumerate(frames):
        row, col = divmod(i, 5)
        fname = FRAME_NAMES[i] if i < 12 else f"f{i}"
        cx, cy = col * CELL, row * CELL

        if fa is not None and fa.shape[0] > 0 and fa.shape[1] > 0:
            fh, fw = fa.shape[:2]
            xo = cx + (CELL - fw) // 2
            yo = cy + CELL - fh          # grounded
            preview.paste(Image.fromarray(fa, 'RGBA'), (xo, yo),
                          Image.fromarray(fa, 'RGBA'))

        draw.rectangle([cx, cy, cx+CELL-1, cy+CELL-1], outline=(70, 70, 70))
        draw.rectangle([cx+1, cy+CELL-14, cx+CELL-2, cy+CELL-1], fill=(0,0,0))
        draw.text((cx+3, cy+CELL-13), fname, fill=(170,170,170))

    lc = (255, 130, 0) if is_fire else (64, 200, 255)
    draw.text((4, H-PAD+4), f"{name}  [{'FIRE' if is_fire else 'WATER'}]", fill=lc)
    preview.save(os.path.join(PREVIEW_DIR, f"{name}_preview.png"), 'PNG')


# ── Processing principale ─────────────────────────────────────────────────────

def process_file(filename):
    name = os.path.splitext(filename)[0]
    nl   = name.lower()
    is_fire  = nl in FIRE_NAMES
    is_water = nl in WATER_NAMES
    if not is_fire and not is_water:
        print(f"  SKIP (unknown): {filename}")
        return

    arr = np.array(Image.open(os.path.join(SPRITE_DIR, filename)).convert('RGBA'))
    ih, iw = arr.shape[:2]

    frame_cols, row_start, row_end = detect_frame_columns(arr)

    print(f"  {name}: {iw}x{ih} | bordi riga {row_start}..{row_end} "
          f"| {len(frame_cols)} frame rilevati")

    if len(frame_cols) != 10:
        print(f"  ERROR: attesi 10 frame, trovati {len(frame_cols)} — controlla i separatori")
        return

    # Processa ogni frame: solo rimozione sfondo, nessun crop
    raw_frames = []
    for c1, c2 in frame_cols:
        cell = arr[row_start:row_end+1, c1:c2+1].copy()
        cell = remove_bg(cell)
        cell = convert_residual(cell, is_fire)
        raw_frames.append(cell)

    # Scala uniforme: lato lungo = TARGET_LONG, uguale per tutti i frame
    max_side = max((max(f.shape[0], f.shape[1]) for f in raw_frames
                    if f.shape[0] > 0 and f.shape[1] > 0), default=1)
    scale_f  = TARGET_LONG / max_side

    out_dir = os.path.join(OUTPUT_DIR, name)
    os.makedirs(out_dir, exist_ok=True)
    scaled = []
    for f, fn in zip(raw_frames, FRAME_NAMES):
        h, w = f.shape[:2]
        nw = max(1, round(w * scale_f))
        nh = max(1, round(h * scale_f))
        img_out = Image.fromarray(f, 'RGBA').resize((nw, nh), Image.Resampling.LANCZOS)
        img_out.save(os.path.join(out_dir, f"{fn}.png"))
        scaled.append(np.array(img_out))

    make_preview(name, scaled, is_fire)
    print(f"  OK  scale={scale_f:.3f} | saved to processed/{name}/")


if __name__ == '__main__':
    files = sorted(f for f in os.listdir(SPRITE_DIR)
                   if f.lower().endswith('.png')
                   and 'preview' not in f.lower()
                   and os.path.basename(f) not in ('process_sprites.py',))

    print(f"Found {len(files)} spritesheet(s)\n")
    for fn in files:
        print(f"Processing: {fn}")
        try:
            process_file(fn)
        except Exception as e:
            import traceback
            print(f"  ERROR: {e}")
            traceback.print_exc()
        print()
    print("Done. Previews ->", PREVIEW_DIR)
