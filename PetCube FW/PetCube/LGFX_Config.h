// ═══════════════════════════════════════════════════════════════
//  LGFX_Config.h — Configurazione LovyanGFX
//  Driver: GC9A01  240×240 round TFT
//  Board:  XIAO ESP32-S3
//
//  Sostituisce TFT_eSPI/User_Setup.h: TFT_eSPI causa un crash
//  StoreProhibited al boot (begin_tft_write/spi.beginTransaction)
//  su questa combinazione ESP32-S3 + GC9A01.
// ═══════════════════════════════════════════════════════════════

#pragma once

#include <LovyanGFX.hpp>

class LGFX : public lgfx::LGFX_Device {
  lgfx::Panel_GC9A01 _panel_instance;
  lgfx::Bus_SPI      _bus_instance;
  lgfx::Light_PWM    _light_instance;

public:
  LGFX(void) {
    {
      auto cfg = _bus_instance.config();
      cfg.spi_host    = SPI2_HOST;
      cfg.spi_mode    = 0;
      cfg.freq_write  = 10000000;  // 10 MHz — conservativo
      cfg.freq_read   = 5000000;
      cfg.spi_3wire   = false;
      cfg.use_lock    = true;
      cfg.dma_channel = SPI_DMA_CH_AUTO;
      cfg.pin_sclk    = 7;   // D8  / GPIO7
      cfg.pin_mosi    = 9;   // D10 / GPIO9
      cfg.pin_miso    = -1;  // non usato (display write-only)
      cfg.pin_dc      = 3;   // D2  / GPIO3
      _bus_instance.config(cfg);
      _panel_instance.setBus(&_bus_instance);
    }

    {
      auto cfg = _panel_instance.config();
      cfg.pin_cs           = 2;   // D1 / GPIO2
      cfg.pin_rst          = -1;  // reset software (RES collegato a 3V3)
      cfg.pin_busy         = -1;
      cfg.panel_width      = 240;
      cfg.panel_height     = 240;
      cfg.offset_x         = 0;
      cfg.offset_y         = 0;
      cfg.offset_rotation  = 0;
      cfg.dummy_read_pixel = 8;
      cfg.dummy_read_bits  = 1;
      cfg.readable         = false;
      cfg.invert           = true;
      cfg.rgb_order        = false; // RGB (GC9A01 BGR causava swap R/B + colori invertiti)
      cfg.dlen_16bit       = false;
      cfg.bus_shared       = false;
      _panel_instance.config(cfg);
    }

    {
      auto cfg = _light_instance.config();
      cfg.pin_bl     = 43;  // D6 / GPIO43 — controllo backlight
      cfg.invert     = false; // HIGH = backlight acceso
      cfg.freq       = 44100;
      cfg.pwm_channel = 7;
      _light_instance.config(cfg);
      _panel_instance.setLight(&_light_instance);
    }

    setPanel(&_panel_instance);
  }
};
