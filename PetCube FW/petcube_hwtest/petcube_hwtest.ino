// ═══════════════════════════════════════════════════════════════
//  PetCube — Hardware Test
//  Testa: TFT GC9A01 · MPU6050 · Buzzer · Pulsanti A/B/C
//
//  All'avvio:
//    1. TFT color test (rosso / verde / blu / bianco / nero)
//    2. Beep di conferma
//    3. Dashboard live con stato pulsanti + dati MPU6050
//
//  Durante il test:
//    BTN_A → beep singolo
//    BTN_B → melodia
//    BTN_C → toggle backlight
//
//  Pin (vedi User_Setup.h per i pin SPI del TFT):
//    D0  → BUZZER
//    D3  → BTN_C
//    D6  → TFT BLK (backlight)
//    D7  → BTN_B
//    D9  → BTN_A
//    D4/D5 → I2C SDA/SCL (MPU6050, default Wire)
// ═══════════════════════════════════════════════════════════════

#include <Wire.h>
#include <TFT_eSPI.h>
#include <Adafruit_MPU6050.h>
#include <Adafruit_Sensor.h>

// ── Pin ───────────────────────────────────────────────────────
#define BTN_A    D9
#define BTN_B    D7
#define BTN_C    D3
#define BUZZER   D0
#define PIN_BL   D6   // backlight (TFT_BL definito anche in User_Setup.h)

// ── Oggetti ───────────────────────────────────────────────────
TFT_eSPI       tft;
TFT_eSprite    canvas(&tft);
Adafruit_MPU6050 mpu;

bool mpuOK = false;

// ── Colori RGB565 ─────────────────────────────────────────────
#define C_BG      0x0000
#define C_WHITE   0xFFFF
#define C_GRAY    0x7BEF
#define C_DGRAY   0x39E7
#define C_GREEN   0x07E0
#define C_RED     0xF800
#define C_BLUE    0x001F
#define C_YELLOW  0xFFE0
#define C_ORANGE  0xFD20
#define C_CYAN    0x07FF

// ─────────────────────────────────────────────────────────────

void beep(int freq, int dur) {
    tone(BUZZER, freq, dur);
    delay(dur + 20);
    noTone(BUZZER);
}

void melodyStartup() {
    beep(523, 80);
    beep(659, 80);
    beep(784, 150);
}

void melodyB() {
    beep(523, 70); beep(659, 70);
    beep(784, 70); beep(1047, 180);
}

// ── TFT color test all'avvio ──────────────────────────────────
void tftColorTest() {
    struct { uint16_t bg; uint16_t fg; const char* label; } seq[] = {
        { TFT_RED,   TFT_WHITE, "RED"   },
        { TFT_GREEN, TFT_BLACK, "GREEN" },
        { TFT_BLUE,  TFT_WHITE, "BLUE"  },
        { TFT_WHITE, TFT_BLACK, "WHITE" },
        { TFT_BLACK, TFT_WHITE, "OK"    },
    };
    for (auto& s : seq) {
        tft.fillScreen(s.bg);
        tft.setTextColor(s.fg, s.bg);
        tft.setTextDatum(MC_DATUM);
        tft.setTextSize(3);
        tft.drawString(s.label, 120, 120);
        delay(350);
    }
}

// ── Dashboard ─────────────────────────────────────────────────
void drawDashboard(float ax, float ay, float az,
                   float gx, float gy, float gz,
                   bool bA, bool bB, bool bC, bool bl) {

    canvas.fillSprite(C_BG);

    // ── Titolo ─────────────────────────────────────────────
    canvas.setTextDatum(TC_DATUM);
    canvas.setTextSize(2);
    canvas.setTextColor(C_WHITE, C_BG);
    canvas.drawString("PetCube HW Test", 120, 4);
    canvas.drawLine(8, 24, 232, 24, C_DGRAY);

    // ── Pulsanti ───────────────────────────────────────────
    canvas.setTextDatum(TL_DATUM);
    canvas.setTextSize(1);
    canvas.setTextColor(C_GRAY, C_BG);
    canvas.drawString("BUTTONS", 10, 30);

    const char* bLabels[3] = {"A", "B", "C"};
    bool bStates[3] = { bA, bB, bC };
    int  bX[3]      = { 18, 93, 168 };

    for (int i = 0; i < 3; i++) {
        uint16_t bg = bStates[i] ? C_GREEN  : C_DGRAY;
        uint16_t fg = bStates[i] ? C_BG     : C_GRAY;
        canvas.fillRoundRect(bX[i], 40, 64, 28, 6, bg);
        canvas.setTextColor(fg, bg);
        canvas.setTextDatum(MC_DATUM);
        canvas.setTextSize(2);
        canvas.drawString(bLabels[i], bX[i] + 32, 54);
    }

    // Backlight indicator
    canvas.setTextDatum(TL_DATUM);
    canvas.setTextSize(1);
    canvas.setTextColor(bl ? C_YELLOW : C_DGRAY, C_BG);
    canvas.drawString(bl ? "BL: ON" : "BL: OFF", 10, 74);

    canvas.drawLine(8, 82, 232, 82, C_DGRAY);

    // ── MPU6050 ────────────────────────────────────────────
    if (!mpuOK) {
        canvas.setTextColor(C_RED, C_BG);
        canvas.setTextSize(1);
        canvas.setTextDatum(TL_DATUM);
        canvas.drawString("MPU6050: NOT FOUND", 10, 88);
        canvas.setTextColor(C_GRAY, C_BG);
        canvas.drawString("Controlla cablaggio SDA/SCL", 10, 100);
    } else {
        canvas.setTextColor(C_GREEN, C_BG);
        canvas.setTextSize(1);
        canvas.setTextDatum(TL_DATUM);
        canvas.drawString("MPU6050: OK", 10, 88);

        char buf[24];

        // Accel
        canvas.setTextColor(C_YELLOW, C_BG);
        canvas.drawString("ACCEL (m/s2)", 10, 100);
        canvas.setTextColor(C_WHITE, C_BG);
        snprintf(buf, sizeof(buf), "X: %+6.2f", ax); canvas.drawString(buf, 10, 112);
        snprintf(buf, sizeof(buf), "Y: %+6.2f", ay); canvas.drawString(buf, 10, 122);
        snprintf(buf, sizeof(buf), "Z: %+6.2f", az); canvas.drawString(buf, 10, 132);

        // Gyro
        canvas.setTextColor(C_ORANGE, C_BG);
        canvas.drawString("GYRO (deg/s)", 125, 100);
        canvas.setTextColor(C_WHITE, C_BG);
        snprintf(buf, sizeof(buf), "X: %+6.1f", gx); canvas.drawString(buf, 125, 112);
        snprintf(buf, sizeof(buf), "Y: %+6.1f", gy); canvas.drawString(buf, 125, 122);
        snprintf(buf, sizeof(buf), "Z: %+6.1f", gz); canvas.drawString(buf, 125, 132);

        // Tilt bar (basato su az)
        canvas.drawLine(8, 146, 232, 146, C_DGRAY);
        canvas.setTextColor(C_GRAY, C_BG);
        canvas.drawString("TILT Z", 10, 150);
        int barVal = constrain((int)(az / 20.0f * 100), -100, 100);
        canvas.drawRect(18, 162, 204, 10, C_DGRAY);
        if (barVal > 0) canvas.fillRect(120, 162, barVal * 102 / 100, 10, C_GREEN);
        else            canvas.fillRect(120 + barVal * 102 / 100, 162, -barVal * 102 / 100, 10, C_RED);
        canvas.drawLine(120, 160, 120, 174, C_WHITE); // zero reference
    }

    canvas.drawLine(8, 178, 232, 178, C_DGRAY);

    // ── Legenda ────────────────────────────────────────────
    canvas.setTextColor(C_DGRAY, C_BG);
    canvas.setTextSize(1);
    canvas.setTextDatum(TL_DATUM);
    canvas.drawString("A: beep  B: melodia  C: backlight", 10, 183);

    // ── Footer ─────────────────────────────────────────────
    canvas.drawLine(8, 200, 232, 200, C_DGRAY);
    canvas.setTextColor(C_DGRAY, C_BG);
    canvas.setTextDatum(TC_DATUM);
    canvas.drawString("petcube_hwtest.ino", 120, 205);

    canvas.pushSprite(0, 0);
}

// ─────────────────────────────────────────────────────────────

void setup() {
    Serial.begin(115200);

    pinMode(BTN_A,  INPUT_PULLUP);
    pinMode(BTN_B,  INPUT_PULLUP);
    pinMode(BTN_C,  INPUT_PULLUP);
    pinMode(BUZZER, OUTPUT);
    pinMode(PIN_BL, OUTPUT);
    digitalWrite(PIN_BL, HIGH);

    // TFT
    tft.init();
    tft.setRotation(0);
    tft.fillScreen(TFT_BLACK);
    canvas.createSprite(240, 240);

    // Color test
    tftColorTest();

    // MPU6050
    Wire.begin();
    mpuOK = mpu.begin();
    if (mpuOK) {
        mpu.setAccelerometerRange(MPU6050_RANGE_2_G);
        mpu.setGyroRange(MPU6050_RANGE_250_DEG);
        mpu.setFilterBandwidth(MPU6050_BAND_21_HZ);
    }

    // Startup beep
    melodyStartup();
}

// ─────────────────────────────────────────────────────────────

bool lastA = true, lastB = true, lastC = true;
bool blOn  = true;

void loop() {
    bool btnA = !digitalRead(BTN_A);
    bool btnB = !digitalRead(BTN_B);
    bool btnC = !digitalRead(BTN_C);

    // Fronti di discesa (press)
    if (btnA && !lastA) beep(880, 80);








    
    if (btnB && !lastB) melodyB();
    if (btnC && !lastC) {
        blOn = !blOn;
        digitalWrite(PIN_BL, blOn ? HIGH : LOW);
    }

    lastA = btnA; lastB = btnB; lastC = btnC;

    // Lettura MPU6050
    float ax=0, ay=0, az=0, gx=0, gy=0, gz=0;
    if (mpuOK) {
        sensors_event_t a, g, temp;
        mpu.getEvent(&a, &g, &temp);
        ax = a.acceleration.x;
        ay = a.acceleration.y;
        az = a.acceleration.z;
        gx = g.gyro.x * 57.2958f;
        gy = g.gyro.y * 57.2958f;
        gz = g.gyro.z * 57.2958f;
    }

    drawDashboard(ax, ay, az, gx, gy, gz, btnA, btnB, btnC, blOn);
    delay(50); // ~20 FPS
}
