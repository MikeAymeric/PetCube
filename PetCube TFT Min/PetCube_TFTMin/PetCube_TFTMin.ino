// TFT minimal test con LovyanGFX + GC9A01 su XIAO ESP32-S3

#define LGFX_USE_V1
#include <LovyanGFX.hpp>

// ── Configurazione display ────────────────────────────────────
class LGFX : public lgfx::LGFX_Device {
    lgfx::Panel_GC9A01  _panel;
    lgfx::Bus_SPI       _bus;
    lgfx::Light_PWM     _light;

public:
    LGFX() {
        // SPI bus
        {
            auto cfg = _bus.config();
            cfg.spi_host    = SPI2_HOST;
            cfg.spi_mode    = 0;
            cfg.freq_write  = 40000000;
            cfg.freq_read   = 16000000;
            cfg.spi_3wire   = false;
            cfg.use_lock    = true;
            cfg.dma_channel = SPI_DMA_CH_AUTO;
            cfg.pin_sclk    =  7;   // D8  GPIO7
            cfg.pin_mosi    =  9;   // D10 GPIO9
            cfg.pin_miso    = -1;   // non usato
            cfg.pin_dc      =  3;   // D2  GPIO3
            _bus.config(cfg);
            _panel.setBus(&_bus);
        }
        // Panel
        {
            auto cfg = _panel.config();
            cfg.pin_cs      =  2;   // D1  GPIO2
            cfg.pin_rst     = -1;   // RES collegato a 3V3
            cfg.pin_busy    = -1;
            cfg.panel_width  = 240;
            cfg.panel_height = 240;
            cfg.invert       = true;
            cfg.rgb_order    = false;
            cfg.readable     = false;
            _panel.config(cfg);
        }
        // Backlight PWM
        {
            auto cfg = _light.config();
            cfg.pin_bl      = 43;   // D6 GPIO43
            cfg.invert      = false;
            cfg.freq        = 44100;
            cfg.pwm_channel = 7;
            _light.config(cfg);
            _panel.setLight(&_light);
        }
        setPanel(&_panel);
    }
};

LGFX tft;

// ── Color helper ──────────────────────────────────────────────
// Converte RGB888 → uint16_t RGB565.
// LovyanGFX interpreta int/uint32_t come colori 24-bit: passare
// sempre uint16_t per avere il colore corretto sul display.
uint16_t fc(uint8_t r, uint8_t g, uint8_t b) {
    return ((uint16_t)(r >> 3) << 11)
         | ((uint16_t)(g >> 2) << 5)
         | (b >> 3);
}

// ─────────────────────────────────────────────────────────────

void setup() {
    Serial.begin(115200);
    delay(1500);
    Serial.println("Init LovyanGFX...");

    // Forza backlight ON manualmente prima dell'init
    pinMode(43, OUTPUT);
    digitalWrite(43, HIGH);

    tft.init();
    tft.setBrightness(255);
    tft.setRotation(0);
    Serial.println("Init OK");

    Serial.println("Starting color loop...");
}

void loop() {
    tft.fillScreen(fc(255,   0,   0)); Serial.println("RED");   delay(1000);
    tft.fillScreen(fc(  0, 255,   0)); Serial.println("GREEN"); delay(1000);
    tft.fillScreen(fc(  0,   0, 255)); Serial.println("BLUE");  delay(1000);
    tft.fillScreen(fc(255, 255, 255)); Serial.println("WHITE"); delay(1000);
}
