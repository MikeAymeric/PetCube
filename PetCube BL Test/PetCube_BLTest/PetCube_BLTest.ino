// Minimal test: solo backlight blink — niente TFT, niente librerie
// Se questo funziona: ESP è OK, il crash è dentro tft.init()

#define PIN_BL D6

void setup() {
    pinMode(PIN_BL, OUTPUT);
}

void loop() {
    digitalWrite(PIN_BL, HIGH);
    delay(500);
    digitalWrite(PIN_BL, LOW);
    delay(500);
}
