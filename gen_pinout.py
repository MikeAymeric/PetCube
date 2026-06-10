#!/usr/bin/env python3
"""Generate a printable PetCube pinout PDF."""

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

OUT = r"C:\Users\mikma\Desktop\Progetti Personali\PetCube\PetCube_Pinout.pdf"
W, H = A4

# ── Palette (light / print-friendly) ─────────────────────────────────────────
BG        = colors.white
PANEL     = colors.HexColor("#eef2f7")
ORANGE    = colors.HexColor("#c0430a")
CYAN      = colors.HexColor("#0077a8")
GREEN     = colors.HexColor("#047857")
YELLOW    = colors.HexColor("#92640a")
RED       = colors.HexColor("#b91c1c")
GRAY      = colors.HexColor("#6b7280")
LGRAY     = colors.HexColor("#555555")
WHITE     = colors.white
BLACK     = colors.HexColor("#111827")
XIAO_G    = colors.HexColor("#2d6a4f")
XIAO_DG   = colors.HexColor("#1b4332")
ROW_A     = colors.HexColor("#f1f5f9")
ROW_B     = colors.white

c = canvas.Canvas(OUT, pagesize=A4)
c.setTitle("PetCube — Pinout XIAO ESP32-S3")

# Background
c.setFillColor(BG)
c.rect(0, 0, W, H, fill=1, stroke=0)

# ═══ TITLE ════════════════════════════════════════════════════════════════════
c.setFillColor(BLACK)
c.setFont("Helvetica-Bold", 18)
c.drawCentredString(W/2, H - 14*mm, "PetCube — Pinout XIAO ESP32-S3")
c.setFont("Helvetica", 8)
c.setFillColor(GRAY)
c.drawCentredString(W/2, H - 20*mm, "Firmware v0.14  ·  tutti i pin occupati  ·  BAT+ → TP4056 OUT+")

# ═══ BOARD DIAGRAM ════════════════════════════════════════════════════════════
# Board geometry — compact
bw, bh = 30*mm, 50*mm
bx = W/2 - bw/2
by = H - 80*mm          # board bottom-left corner

# Body
c.setFillColor(XIAO_G)
c.setStrokeColor(colors.HexColor("#40916c"))
c.setLineWidth(1.0)
c.roundRect(bx, by, bw, bh, 2.5*mm, fill=1, stroke=1)

# USB-C notch top-centre
uw, uh = 10*mm, 4*mm
c.setFillColor(XIAO_DG)
c.roundRect(bx+(bw-uw)/2, by+bh-1.5*mm, uw, uh, 1.2*mm, fill=1, stroke=0)
c.setFillColor(LGRAY); c.setFont("Helvetica-Bold", 5)
c.drawCentredString(bx+bw/2, by+bh+1.2*mm, "USB-C")

# Board label
c.setFillColor(WHITE); c.setFont("Helvetica-Bold", 6)
c.drawCentredString(bx+bw/2, by+bh/2+2*mm, "XIAO")
c.setFont("Helvetica", 5); c.drawCentredString(bx+bw/2, by+bh/2-2*mm, "ESP32-S3")

# Bottom pad: BAT+ only
pad_w = 20*mm
c.setFillColor(YELLOW)
c.roundRect(bx+bw/2-pad_w/2, by-5.5*mm, pad_w, 4*mm, 1*mm, fill=1, stroke=0)
c.setFillColor(XIAO_DG); c.setFont("Helvetica-Bold", 5)
c.drawCentredString(bx+bw/2, by-2.8*mm, "BAT+  →  TP4056 OUT+")

# ── Pin definitions (top of board = near USB-C) ───────────────────────────────
# Left side: D0 at top (near USB), D6 at bottom
left_pins = [
    ("D0",  "GPIO1",  "BUZZER",         ORANGE),
    ("D1",  "GPIO2",  "TFT CS",         CYAN),
    ("D2",  "GPIO3",  "TFT DC",         CYAN),
    ("D3",  "GPIO4",  "BTN_C",          GREEN),
    ("D4",  "GPIO5",  "MPU SDA",        YELLOW),
    ("D5",  "GPIO6",  "MPU SCL",        YELLOW),
    ("D6",  "GPIO43", "TFT BLK",        CYAN),
]
# Right side: VUSB at top (near USB), D7 at bottom
right_pins = [
    ("VUSB", None,    "—",              RED),
    ("GND",  None,    "—",              GRAY),
    ("3V3",  None,    "TFT VCC · MPU VCC", RED),
    ("D10",  "GPIO9", "TFT MOSI",       CYAN),
    ("D9",   "GPIO8", "BTN_A",          GREEN),
    ("D8",   "GPIO7", "TFT SCK",        CYAN),
    ("D7",   "GPIO44","BTN_B",          GREEN),
]

n = 7
spacing = (bh - 7*mm) / (n - 1)
py0 = by + bh - 5*mm       # y of first pin (top)

DOT_R  = 1.8*mm
LBL_W  = 13*mm   # width of pin-name box
DESC_W = 24*mm   # width of description box
GAP    = 2*mm    # gap between board edge and first element

def draw_left(i, pin, gpio, desc, col):
    py = py0 - i * spacing
    px = bx                       # board left edge

    # horizontal line board → label area
    line_end = px - GAP - DOT_R
    line_start = px - GAP - DOT_R - LBL_W - 2*mm - DESC_W - 1*mm
    c.setStrokeColor(col); c.setLineWidth(0.6)
    c.line(px, py, line_end, py)

    # dot on board edge
    c.setFillColor(col); c.circle(px, py, DOT_R, fill=1, stroke=0)

    # description box (leftmost)
    dx = line_start
    c.setFillColor(col)
    c.roundRect(dx, py-3*mm, DESC_W, 5.5*mm, 0.8*mm, fill=1, stroke=0)
    c.setFillColor(XIAO_DG); c.setFont("Helvetica-Bold", 5.8)
    c.drawCentredString(dx + DESC_W/2, py-1.2*mm, desc)

    # pin name
    nx = dx + DESC_W + 1*mm
    c.setFillColor(BLACK); c.setFont("Helvetica-Bold", 6.5)
    c.drawRightString(nx + LBL_W - 1*mm, py - 2*mm, pin)
    if gpio:
        c.setFillColor(LGRAY); c.setFont("Helvetica", 5.2)
        c.drawRightString(nx + LBL_W - 1*mm, py + 1.2*mm, gpio)

def draw_right(i, pin, gpio, desc, col):
    py = py0 - i * spacing
    px = bx + bw

    c.setStrokeColor(col); c.setLineWidth(0.6)
    c.line(px, py, px + GAP + DOT_R, py)
    c.setFillColor(col); c.circle(px, py, DOT_R, fill=1, stroke=0)

    # pin name
    nx = px + GAP + DOT_R + 1*mm
    c.setFillColor(BLACK); c.setFont("Helvetica-Bold", 6.5)
    c.drawString(nx, py - 2*mm, pin)
    if gpio:
        c.setFillColor(LGRAY); c.setFont("Helvetica", 5.2)
        c.drawString(nx, py + 1.2*mm, gpio)

    # description box
    dx = nx + LBL_W + 1*mm
    c.setFillColor(col)
    c.roundRect(dx, py-3*mm, DESC_W, 5.5*mm, 0.8*mm, fill=1, stroke=0)
    c.setFillColor(XIAO_DG); c.setFont("Helvetica-Bold", 5.8)
    c.drawCentredString(dx + DESC_W/2, py-1.2*mm, desc)

for i, (p,g,d,co) in enumerate(left_pins):  draw_left(i,p,g,d,co)
for i, (p,g,d,co) in enumerate(right_pins): draw_right(i,p,g,d,co)

# ═══ WIRING TABLE ══════════════════════════════════════════════════════════════
table_top = by - 10*mm      # start just below board/BAT pad area
ROW_H     = 5.4*mm
rows = [
    ("3.3V",  "—",        "TFT VCC · MPU6050 VCC · TFT RES",         RED),
    ("GND",   "—",        "TFT GND · MPU6050 GND · TP4056 OUT− · Buzzer − · Pulsanti −", GRAY),
    ("BAT+",  "—",        "TP4056 OUT+",                              YELLOW),
    ("D0",    "GPIO1",    "Buzzer +",                                  ORANGE),
    ("D1",    "GPIO2",    "TFT CS",                                    CYAN),
    ("D2",    "GPIO3",    "TFT DC",                                    CYAN),
    ("D3",    "GPIO4",    "Pulsante C  (→ GND)",                      GREEN),
    ("D4",    "GPIO5",    "MPU6050 SDA",                               YELLOW),
    ("D5",    "GPIO6",    "MPU6050 SCL",                               YELLOW),
    ("D6",    "GPIO43",   "TFT BLK  (backlight)",                     CYAN),
    ("D7",    "GPIO44",   "Pulsante B  (→ GND)",                      GREEN),
    ("D8",    "GPIO7",    "TFT SCK  (SPI clock)",                     CYAN),
    ("D9",    "GPIO8",    "Pulsante A  (→ GND)",                      GREEN),
    ("D10",   "GPIO9",    "TFT SDA / MOSI",                           CYAN),
    ("TFT RES", "—",      "→ 3V3  (reset software)",                  GRAY),
]

# Panel background
panel_h = ROW_H * (len(rows) + 1) + 6*mm
mx = 10*mm
c.setFillColor(PANEL)
c.setStrokeColor(colors.HexColor("#c8d8e8"))
c.setLineWidth(0.5)
c.roundRect(mx, table_top - panel_h, W - 2*mx, panel_h, 2*mm, fill=1, stroke=1)

# Section title
c.setFillColor(BLACK); c.setFont("Helvetica-Bold", 9)
c.drawString(mx+3*mm, table_top - 4.5*mm, "Cablaggio completo")

# Column positions
cx = [mx+3*mm, mx+30*mm, mx+55*mm]
# Header
hy = table_top - ROW_H - 3*mm
c.setFillColor(colors.HexColor("#d0e4ef"))
c.rect(mx, hy, W-2*mx, ROW_H, fill=1, stroke=0)
for j, h in enumerate(["Pin XIAO", "GPIO", "Collega a"]):
    c.setFillColor(CYAN); c.setFont("Helvetica-Bold", 7)
    c.drawString(cx[j], hy+1.5*mm, h)

for i, (pin, gpio, dest, col) in enumerate(rows):
    ry = hy - ROW_H*(i+1)
    c.setFillColor(ROW_A if i%2==0 else ROW_B)
    c.rect(mx, ry, W-2*mx, ROW_H, fill=1, stroke=0)
    c.setFillColor(col);  c.setFont("Helvetica-Bold", 7); c.drawString(cx[0], ry+1.5*mm, pin)
    c.setFillColor(LGRAY); c.setFont("Helvetica", 6.5);   c.drawString(cx[1], ry+1.5*mm, gpio)
    c.setFillColor(col);  c.setFont("Helvetica", 6.8);    c.drawString(cx[2], ry+1.5*mm, dest)

# ═══ NOTES ════════════════════════════════════════════════════════════════════
ny = table_top - panel_h - 5*mm
notes = [
    ("⚠", ORANGE, "TFT alimentato a 3.3V — non usare 5V."),
    ("⚠", ORANGE, "TFT SDA/SCL sono pin SPI, non I²C — non collegare a D4/D5."),
    ("⚠", ORANGE, "Non collegare USB-C del XIAO mentre il TP4056 sta caricando."),
    ("⚠", ORANGE, "TFT RES: collegare a 3V3 (non a un GPIO) — reset gestito via software."),
    ("✓", GREEN,  "Pulsanti: pin → GND, nessuna resistenza (INPUT_PULLUP)."),
    ("✓", GREEN,  "Ordine saldatura: componenti → test USB → ultima: BAT+ a TP4056 OUT+."),
]
for i, (icon, col, txt) in enumerate(notes):
    c.setFillColor(col);  c.setFont("Helvetica-Bold", 8); c.drawString(mx+1*mm, ny-i*5*mm, icon)
    c.setFillColor(BLACK); c.setFont("Helvetica", 7);     c.drawString(mx+7*mm, ny-i*5*mm, txt)

# ═══ FOOTER ═══════════════════════════════════════════════════════════════════
c.setFillColor(GRAY); c.setFont("Helvetica", 6)
c.drawCentredString(W/2, 7*mm, "PetCube  ·  github.com/MikeAymeric/PetCube  ·  CC BY-NC-SA 4.0")

c.save()
print(f"Salvato: {OUT}")
