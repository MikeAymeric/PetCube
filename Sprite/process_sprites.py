#!/usr/bin/env python3
"""
PetCube sprite processing:
- Remove magenta background
- Detect 6x2 grid, extract 12 frames
- Crop empty space, ground sprites
- Convert residual magenta/purple to orange (fire) or blue (water)
- Scale so longest side = 130px (uniform across all frames per sprite)
- Generate individual previews
"""

import numpy as np
from PIL import Image, ImageDraw
import os, sys

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
    'idle1','idle2','attack1','attack2','angry1','angry2',
    'happy1','happy2','sick1','sick2','sleep1','sleep2'
]
TARGET_LONG = 130
EDGE_TRIM   = 4   # strip N px from each cell edge to remove border lines


# ── Helpers ──────────────────────────────────────────────────────────────────

def _bg_mask(r, g, b):
    """Pixels that are the magenta background."""
    ri, gi, bi = r.astype(np.int32), g.astype(np.int32), b.astype(np.int32)
    return (ri > 160) & (bi > 160) & (gi < 110) & ((ri + bi - 2*gi) > 120)

def _border_mask(r, g, b):
    """Pixels that are black separator lines."""
    return (r < 35) & (g < 35) & (b < 35)


def find_separators(arr):
    """
    Detect row/column bands that are background or border.
    Returns h_ranges, v_ranges: list of (start, end) int tuples.
    """
    h, w = arr.shape[:2]
    r, g, b = arr[:,:,0], arr[:,:,1], arr[:,:,2]

    sep = _bg_mask(r, g, b) | _border_mask(r, g, b)

    row_ratio = sep.mean(axis=1)
    col_ratio = sep.mean(axis=0)

    def to_ranges(indices, gap=4):
        if len(indices) == 0:
            return []
        groups, start, prev = [], int(indices[0]), int(indices[0])
        for i in map(int, indices[1:]):
            if i - prev <= gap:
                prev = i
            else:
                groups.append((start, prev))
                start = prev = i
        groups.append((start, prev))
        return groups

    h_ranges = to_ranges(np.where(row_ratio > 0.80)[0])
    v_ranges = to_ranges(np.where(col_ratio > 0.80)[0])
    return h_ranges, v_ranges



def frame_regions(h_ranges, v_ranges, img_h, img_w):
    """
    Compute pixel (r1, r2, c1, c2) for each of the 12 content cells.
    """
    def between(ranges, total):
        edges = [(0, 0)] + list(ranges) + [(total - 1, total - 1)]
        result = []
        for i in range(len(edges) - 1):
            s = edges[i][1] + 1
            e = edges[i + 1][0] - 1
            if e >= s:
                result.append((s, e))
        return result

    rows = between(h_ranges, img_h)
    cols = between(v_ranges, img_w)

    regions = []
    for r in rows:
        for c in cols:
            regions.append((r[0], r[1], c[0], c[1]))
    return regions


def remove_bg(cell):
    """Make magenta and black-border pixels transparent. Returns RGBA."""
    r, g, b = cell[:,:,0], cell[:,:,1], cell[:,:,2]
    a = cell[:,:,3].copy()
    a[_bg_mask(r, g, b) | _border_mask(r, g, b)] = 0
    out = cell.copy(); out[:,:,3] = a
    return out


def convert_residual(rgba, is_fire):
    """
    Shift pixels with magenta/purple hue (270-350°) to orange or cyan,
    preserving HSV value and saturation.
    Fully vectorised.
    """
    r = rgba[:,:,0].astype(np.float32) / 255
    g = rgba[:,:,1].astype(np.float32) / 255
    b = rgba[:,:,2].astype(np.float32) / 255
    visible = rgba[:,:,3] > 30

    maxc = np.maximum(np.maximum(r, g), b)
    minc = np.minimum(np.minimum(r, g), b)
    delta = maxc - minc
    sat = np.where(maxc > 1e-6, delta / maxc, 0.0)

    hue = np.zeros_like(r)
    eps = 1e-6
    m_r = (delta > eps) & (maxc == r)
    m_g = (delta > eps) & (maxc == g)
    m_b = (delta > eps) & (maxc == b)
    hue[m_r] = ((g[m_r] - b[m_r]) / delta[m_r]) % 6
    hue[m_g] = (b[m_g] - r[m_g]) / delta[m_g] + 2
    hue[m_b] = (r[m_b] - g[m_b]) / delta[m_b] + 4
    hue = (hue / 6.0) % 1.0

    # Magenta/purple hue band: ~0.73–0.97 (265–350°)
    is_mag = visible & (sat > 0.20) & (hue >= 0.73) & (hue <= 0.97)
    if not np.any(is_mag):
        return rgba

    # Target hue: 30° orange = 0.0833, 200° cyan = 0.5556
    th = 0.0833 if is_fire else 0.5556
    h6  = th * 6
    hi  = int(h6) % 6
    f   = h6 - int(h6)

    v_m = maxc[is_mag]
    s_m = sat[is_mag]
    p = v_m * (1 - s_m)
    q = v_m * (1 - f * s_m)
    t = v_m * (1 - (1 - f) * s_m)

    hsv_rgb = [(v_m,t,p),(q,v_m,p),(p,v_m,t),(p,q,v_m),(t,p,v_m),(v_m,p,q)]
    nr, ng, nb = hsv_rgb[hi]

    res = rgba.copy()
    res[is_mag, 0] = np.clip(nr * 255, 0, 255).astype(np.uint8)
    res[is_mag, 1] = np.clip(ng * 255, 0, 255).astype(np.uint8)
    res[is_mag, 2] = np.clip(nb * 255, 0, 255).astype(np.uint8)
    return res


def crop_content(rgba):
    """Tight crop to non-transparent bounding box."""
    a = rgba[:,:,3]
    rows = np.where(np.any(a > 0, axis=1))[0]
    cols = np.where(np.any(a > 0, axis=0))[0]
    if len(rows) == 0 or len(cols) == 0:
        return rgba
    return rgba[rows[0]:rows[-1]+1, cols[0]:cols[-1]+1]


def scale_to(rgba, long_side):
    """Scale so longest dimension == long_side (LANCZOS)."""
    h, w = rgba.shape[:2]
    if max(h, w) == 0:
        return rgba
    s = long_side / max(h, w)
    nw, nh = max(1, round(w * s)), max(1, round(h * s))
    img = Image.fromarray(rgba, 'RGBA').resize(
        (nw, nh), Image.Resampling.LANCZOS)
    return np.array(img)


# ── Preview ───────────────────────────────────────────────────────────────────

def make_preview(name, frames, is_fire):
    CELL, PAD = 150, 24
    W = 6 * CELL
    H = 2 * CELL + PAD

    bg = (28, 28, 28)
    preview = Image.new('RGB', (W, H), bg)
    draw = ImageDraw.Draw(preview)

    for i, fa in enumerate(frames):
        row, col = divmod(i, 6)
        fname = FRAME_NAMES[i] if i < 12 else f"f{i}"
        cx, cy = col * CELL, row * CELL

        if fa is not None and fa.shape[0] > 0 and fa.shape[1] > 0:
            fh, fw = fa.shape[:2]
            # Center horizontally, ground vertically
            xo = cx + (CELL - fw) // 2
            yo = cy + CELL - fh
            fi = Image.fromarray(fa, 'RGBA')
            preview.paste(fi, (xo, yo), fi)

        # Cell grid
        draw.rectangle([cx, cy, cx + CELL - 1, cy + CELL - 1],
                       outline=(70, 70, 70))
        # Label bar at bottom of cell
        draw.rectangle([cx + 1, cy + CELL - 14, cx + CELL - 2, cy + CELL - 1],
                       fill=(0, 0, 0))
        draw.text((cx + 3, cy + CELL - 13), fname, fill=(170, 170, 170))

    # Footer
    lc = (255, 130, 0) if is_fire else (64, 200, 255)
    draw.text((4, H - PAD + 4), f"{name}  [{'FIRE' if is_fire else 'WATER'}]", fill=lc)

    preview.save(os.path.join(PREVIEW_DIR, f"{name}_preview.png"), 'PNG')


# ── Main processing ───────────────────────────────────────────────────────────

def process_file(filename):
    name      = os.path.splitext(filename)[0]
    nl        = name.lower()
    is_fire   = nl in FIRE_NAMES
    is_water  = nl in WATER_NAMES
    if not is_fire and not is_water:
        print(f"  SKIP (unknown): {filename}")
        return

    img_path = os.path.join(SPRITE_DIR, filename)
    img      = Image.open(img_path).convert('RGBA')
    arr      = np.array(img)
    ih, iw   = arr.shape[:2]

    h_sep, v_sep = find_separators(arr)
    regions      = frame_regions(h_sep, v_sep, ih, iw)

    print(f"  {name}: {iw}x{ih} | h_sep={len(h_sep)} v_sep={len(v_sep)} -> {len(regions)} frames")

    if len(regions) != 12:
        print(f"  ERROR: expected 12 frames, got {len(regions)} - check separators and skip")
        return

    # Extract + process each frame (trim cell edges to remove border lines)
    t = EDGE_TRIM
    raw_frames = []
    for (r1, r2, c1, c2) in regions:
        cell = arr[max(0, r1+t) : min(ih, r2+1-t),
                   max(0, c1+t) : min(iw, c2+1-t)].copy()
        cell = remove_bg(cell)
        cell = convert_residual(cell, is_fire)
        cell = crop_content(cell)
        raw_frames.append(cell)

    # Uniform scale: find longest side across all frames
    max_side = max((max(f.shape[0], f.shape[1]) for f in raw_frames
                    if f.shape[0] > 0 and f.shape[1] > 0), default=1)
    scale_f  = TARGET_LONG / max_side

    scaled = []
    for f in raw_frames:
        if f.shape[0] == 0 or f.shape[1] == 0:
            scaled.append(np.zeros((1, 1, 4), dtype=np.uint8))
        else:
            h, w = f.shape[:2]
            nw = max(1, round(w * scale_f))
            nh = max(1, round(h * scale_f))
            out = Image.fromarray(f, 'RGBA').resize(
                (nw, nh), Image.Resampling.LANCZOS)
            scaled.append(np.array(out))

    # Save individual frame PNGs
    out_dir = os.path.join(OUTPUT_DIR, name)
    os.makedirs(out_dir, exist_ok=True)
    for i, (fa, fn) in enumerate(zip(scaled, FRAME_NAMES)):
        Image.fromarray(fa, 'RGBA').save(os.path.join(out_dir, f"{fn}.png"))

    # Preview
    make_preview(name, scaled, is_fire)
    print(f"  ✓ saved to processed/{name}/ + previews/{name}_preview.png")


if __name__ == '__main__':
    files = sorted(f for f in os.listdir(SPRITE_DIR)
                   if f.lower().endswith('.png')
                   and 'preview' not in f.lower()
                   and f != 'process_sprites.py')

    print(f"Found {len(files)} spritesheet(s)\n")
    for fn in files:
        print(f"Processing: {fn}")
        try:
            process_file(fn)
        except Exception as e:
            import traceback
            print(f"  ✗ ERROR: {e}")
            traceback.print_exc()
        print()

    print("All done. Previews →", PREVIEW_DIR)
