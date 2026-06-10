// ═══════════════════════════════════════════════════════════════
//  PetCube — Hardware Test completo
//  TFT GC9A01 240×240 round · MPU6050 · Buzzer · Tasti A/B/C
//  Animazione Abyssibyl — cicla stati con BTN_B
//
//  BTN_A : beep
//  BTN_B : stato successivo (idle→attack→angry→happy→sick→sleep)
//  BTN_C : toggle backlight
// ═══════════════════════════════════════════════════════════════

#define LGFX_USE_V1
#include <LovyanGFX.hpp>
#include <Wire.h>
#include <Adafruit_MPU6050.h>
#include <Adafruit_Sensor.h>
#include "abyssibyl_all.h"

// ── Pin ───────────────────────────────────────────────────────
#define BTN_A   D9
#define BTN_B   D7
#define BTN_C   D3
#define BUZZER  D0

// ── LovyanGFX ─────────────────────────────────────────────────
class LGFX : public lgfx::LGFX_Device {
    lgfx::Panel_GC9A01  _panel;
    lgfx::Bus_SPI       _bus;
    lgfx::Light_PWM     _light;
public:
    LGFX() {
        { auto cfg = _bus.config();
          cfg.spi_host = SPI2_HOST; cfg.spi_mode = 0;
          cfg.freq_write = 40000000; cfg.spi_3wire = false;
          cfg.use_lock = true; cfg.dma_channel = SPI_DMA_CH_AUTO;
          cfg.pin_sclk = 7; cfg.pin_mosi = 9;
          cfg.pin_miso = -1; cfg.pin_dc = 3;
          _bus.config(cfg); _panel.setBus(&_bus); }
        { auto cfg = _panel.config();
          cfg.pin_cs = 2; cfg.pin_rst = -1; cfg.pin_busy = -1;
          cfg.panel_width = 240; cfg.panel_height = 240;
          cfg.invert = true; cfg.rgb_order = false;
          cfg.readable = false;
          _panel.config(cfg); }
        { auto cfg = _light.config();
          cfg.pin_bl = 43; cfg.invert = false;
          cfg.freq = 44100; cfg.pwm_channel = 7;
          _light.config(cfg); _panel.setLight(&_light); }
        setPanel(&_panel);
    }
};

LGFX             tft;
LGFX_Sprite      canvas(&tft);
Adafruit_MPU6050 mpu;
bool mpuOK = false;
bool blOn  = true;

// ── Color helper ──────────────────────────────────────────────
uint16_t fc(uint8_t r, uint8_t g, uint8_t b) {
    return ((uint16_t)(r >> 3) << 11) | ((uint16_t)(g >> 2) << 5) | (b >> 3);
}
const uint16_t C_BG    = fc( 12,  12,  28);
const uint16_t C_WHITE = fc(255, 255, 255);
const uint16_t C_GRAY  = fc(110, 110, 110);
const uint16_t C_DGRAY = fc( 45,  45,  55);
const uint16_t C_GREEN = fc(  0, 210,  80);
const uint16_t C_RED   = fc(220,  50,  50);
const uint16_t C_CYAN  = fc( 60, 200, 255);

// ── Stati animazione ──────────────────────────────────────────
// Ogni stato ha 2 frame; il frame alterna ogni FRAME_MS ms
#define N_STATES    6
#define FRAME_MS  500

const char* STATE_NAMES[N_STATES] = {
    "IDLE", "ATTACK", "ANGRY", "HAPPY", "SICK", "SLEEP"
};

// frame[state][0/1] = puntatore dati, width, height
struct FrameInfo { const uint16_t* data; int16_t w, h; };

const FrameInfo FRAMES[N_STATES][2] = {
    { {abyssibyl_idle1,   ABYSSIBYL_IDLE1_W,   ABYSSIBYL_IDLE1_H},
      {abyssibyl_idle2,   ABYSSIBYL_IDLE2_W,   ABYSSIBYL_IDLE2_H} },
    { {abyssibyl_attack1, ABYSSIBYL_ATTACK1_W, ABYSSIBYL_ATTACK1_H},
      {abyssibyl_attack2, ABYSSIBYL_ATTACK2_W, ABYSSIBYL_ATTACK2_H} },
    { {abyssibyl_angry1,  ABYSSIBYL_ANGRY1_W,  ABYSSIBYL_ANGRY1_H},
      {abyssibyl_angry2,  ABYSSIBYL_ANGRY2_W,  ABYSSIBYL_ANGRY2_H} },
    { {abyssibyl_happy1,  ABYSSIBYL_HAPPY1_W,  ABYSSIBYL_HAPPY1_H},
      {abyssibyl_happy2,  ABYSSIBYL_HAPPY2_W,  ABYSSIBYL_HAPPY2_H} },
    { {abyssibyl_sick1,   ABYSSIBYL_SICK1_W,   ABYSSIBYL_SICK1_H},
      {abyssibyl_sick2,   ABYSSIBYL_SICK2_W,   ABYSSIBYL_SICK2_H} },
    { {abyssibyl_sleep1,  ABYSSIBYL_SLEEP1_W,  ABYSSIBYL_SLEEP1_H},
      {abyssibyl_sleep2,  ABYSSIBYL_SLEEP2_W,  ABYSSIBYL_SLEEP2_H} },
};

// ── Beep ──────────────────────────────────────────────────────
void beep(int hz, int ms) { tone(BUZZER,hz,ms); delay(ms+10); noTone(BUZZER); }

// ── Disegna sprite PROGMEM con trasparenza 0x0000 ─────────────
void drawSprite(int x, int y, const uint16_t* data, int16_t w, int16_t h) {
    for (int py = 0; py < h; py++)
        for (int px = 0; px < w; px++) {
            uint16_t c = data[py * w + px];
            if (c) canvas.writePixel(x + px, y + py, c);
        }
}

// ── Render ────────────────────────────────────────────────────
void render(float ax, float ay, float az,
            float gx, float gy, float gz,
            bool bA, bool bB, bool bC,
            int state, int frame) {

    canvas.fillSprite(C_BG);

    // ── Sprite — centrata orizzontalmente, poggiata a y=192 ──
    const FrameInfo& fi = FRAMES[state][frame];
    int sx = (240 - fi.w) / 2;
    int sy = 192 - fi.h;
    drawSprite(sx, sy, fi.data, fi.w, fi.h);

    // ── Nome stato — centrato, y=198 (dentro cerchio) ─────────
    canvas.setTextDatum(lgfx::bottom_center);
    canvas.setTextSize(1);
    canvas.setTextColor(C_CYAN);
    canvas.drawString(STATE_NAMES[state], 120, 200);

    // ── MPU6050 — centrato, y=35..62 ─────────────────────────
    canvas.setTextDatum(lgfx::top_center);
    canvas.setTextSize(1);
    if (!mpuOK) {
        canvas.setTextColor(C_RED);
        canvas.drawString("MPU: NOT FOUND", 120, 38);
    } else {
        char buf[32];
        canvas.setTextColor(C_CYAN);
        canvas.drawString("MPU6050", 120, 36);
        canvas.setTextColor(C_WHITE);
        snprintf(buf, sizeof(buf), "%.1f  %.1f  %.1f", ax, ay, az);
        canvas.drawString(buf, 120, 48);
        canvas.setTextColor(C_GRAY);
        snprintf(buf, sizeof(buf), "%.0f  %.0f  %.0f", gx, gy, gz);
        canvas.drawString(buf, 120, 58);
    }

    // ── Pulsanti A/B/C — centrati, y=208..232 ────────────────
    // Al cerchio y=220: larghezza ≈159px → 3×44 + 2×7 = 146px → x 47..193
    const char* lbl[3] = {"A","B","C"};
    bool        prs[3] = {bA, bB, bC};
    int         bxs[3] = {47, 98, 149};
    for (int i = 0; i < 3; i++) {
        uint16_t bg = prs[i] ? C_GREEN : C_DGRAY;
        canvas.fillRoundRect(bxs[i], 208, 44, 24, 5, bg);
        canvas.setTextColor(prs[i] ? C_BG : C_GRAY);
        canvas.setTextDatum(lgfx::middle_center);
        canvas.setTextSize(2);
        canvas.drawString(lbl[i], bxs[i]+22, 220);
    }

    canvas.pushSprite(0, 0);
}

// ─────────────────────────────────────────────────────────────
void setup() {
    Serial.begin(115200);
    pinMode(BTN_A, INPUT_PULLUP);
    pinMode(BTN_B, INPUT_PULLUP);
    pinMode(BTN_C, INPUT_PULLUP);
    pinMode(BUZZER, OUTPUT);

    tft.init();
    tft.setBrightness(255);
    tft.setRotation(0);
    tft.fillScreen(fc(0,0,0));

    canvas.createSprite(240, 240);
    canvas.setColorDepth(16);

    Wire.begin();
    mpuOK = mpu.begin();
    if (mpuOK) {
        mpu.setAccelerometerRange(MPU6050_RANGE_2_G);
        mpu.setGyroRange(MPU6050_RANGE_250_DEG);
        mpu.setFilterBandwidth(MPU6050_BAND_21_HZ);
    }

    beep(523,80); beep(659,80); beep(784,150);
}

// ─────────────────────────────────────────────────────────────
bool lastA = true, lastB = true, lastC = true;
int  curState = 0, curFrame = 0;
unsigned long lastSwap = 0;

void loop() {
    bool bA = !digitalRead(BTN_A);
    bool bB = !digitalRead(BTN_B);
    bool bC = !digitalRead(BTN_C);

    if (bA && !lastA) beep(880, 80);

    if (bB && !lastB) {
        curState = (curState + 1) % N_STATES;
        curFrame = 0;
        beep(660, 40);
    }

    if (bC && !lastC) {
        blOn = !blOn;
        tft.setBrightness(blOn ? 255 : 0);
    }

    lastA = bA; lastB = bB; lastC = bC;

    // Alterna frame ogni FRAME_MS ms
    if (millis() - lastSwap > FRAME_MS) {
        curFrame ^= 1;
        lastSwap = millis();
    }

    float ax=0,ay=0,az=0,gx=0,gy=0,gz=0;
    if (mpuOK) {
        sensors_event_t a, g, temp;
        mpu.getEvent(&a, &g, &temp);
        ax=a.acceleration.x; ay=a.acceleration.y; az=a.acceleration.z;
        gx=g.gyro.x*57.3f;   gy=g.gyro.y*57.3f;  gz=g.gyro.z*57.3f;
    }

    render(ax, ay, az, gx, gy, gz, bA, bB, bC, curState, curFrame);
    delay(30);
}
