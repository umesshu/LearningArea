// 車庫門遙控器（ESP32 + Blynk IoT 2.0）
// 骨架尚未實測，實際接線前務必用三用電表確認觸點極性

#include "secrets.h"

#define BLYNK_PRINT Serial
#include <WiFi.h>
#include <BlynkSimpleEsp32.h>

// ESP32_Relay_AC_X2 (303E32AC210 / SZHJW-ESP32-WROOM-32E) 板載腳位：
//   Relay 1 = GPIO16, Relay 2 = GPIO17, 板載按鈕 = GPIO0, 板載狀態燈 = GPIO23（低電位觸發）
// 只做開/關，剛好對應板載 2 個繼電器，不需外接光耦合模組
#define PIN_OPEN  16  // 板載 Relay 1
#define PIN_CLOSE 17  // 板載 Relay 2

void pulseGPIO(int pin) {
  digitalWrite(pin, HIGH);
  delay(300);
  digitalWrite(pin, LOW);
}

BLYNK_WRITE(V0) { // 開
  if (param.asInt() == 1) pulseGPIO(PIN_OPEN);
}
BLYNK_WRITE(V1) { // 關
  if (param.asInt() == 1) pulseGPIO(PIN_CLOSE);
}

void setup() {
  Serial.begin(115200);

  pinMode(PIN_OPEN, OUTPUT);
  pinMode(PIN_CLOSE, OUTPUT);
  digitalWrite(PIN_OPEN, LOW);
  digitalWrite(PIN_CLOSE, LOW);

  Blynk.begin(BLYNK_AUTH_TOKEN, WIFI_SSID, WIFI_PASSWORD);
}

void loop() {
  Blynk.run();
  // TODO: 斷線自動重連邏輯
}
