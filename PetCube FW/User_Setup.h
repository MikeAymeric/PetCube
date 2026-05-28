// ═══════════════════════════════════════════════════════════════
//  TFT_eSPI — User_Setup.h
//  Driver: GC9A01  240×240 round TFT
//  Board:  XIAO ESP32-S3
//
//  !! VERIFICA i pin CS, DC, RST con il tuo schema PCB !!
//  I pin SCK e MOSI sono quelli hardware SPI dell'ESP32-S3.
// ═══════════════════════════════════════════════════════════════

#define GC9A01_DRIVER

#define TFT_WIDTH  240
#define TFT_HEIGHT 240

// ── Pin SPI XIAO ESP32-S3 ─────────────────────────────────────
//   D8 = GPIO7  → SCK
//   D10 = GPIO9 → MOSI
#define TFT_MOSI  9    // D10 / GPIO9
#define TFT_SCLK  7    // D8  / GPIO7

#define TFT_CS    2    // D1 / GPIO2
#define TFT_DC    1    // D0 / GPIO1
#define TFT_RST   3    // D2 / GPIO3
// #define TFT_BL  xx  // decommentare se backlight è su un GPIO

// ── SPI frequency ─────────────────────────────────────────────
#define SPI_FREQUENCY      27000000
#define SPI_READ_FREQUENCY 20000000

// ── Ordine colore (GC9A01 usa BGR) ───────────────────────────
#define TFT_RGB_ORDER TFT_BGR
