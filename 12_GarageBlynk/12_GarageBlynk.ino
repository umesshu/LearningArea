// 車庫門遙控器（ESP32 + Blynk IoT 2.0）
// 已驗證：燒錄、WiFi/Blynk 連線、開/關繼電器觸發皆測試成功
//
// 監控走「Blynk Cloud 中繼」而非本地 UDP：連線狀態靠 isHardwareConnected API，
// 操作紀錄靠單一 String 虛擬腳位 V0（外部寫入 "open"/"close" 觸發，裝置執行完
// 寫回 "open:N"/"close:N" 當作回報），這樣不管樹莓派實際在哪個網路都收得到，
// 不像本地 UDP 廣播/單播那樣依賴「兩台裝置在同一個網段」。
//
// 曾經一度以為 String Datastream 在 Blynk 後端會被誤判成數值型態（送字串一律
// 400），一路懷疑到是平台 bug；後來才發現真正原因是 Blynk Console 編輯完
// Datastream 之後要按「Save and Apply」才會真的套用，光是編輯畫面看起來改好了
// 不代表後端已生效。按下 Save and Apply 之後這個單一 String 腳位設計完全正常。

#include "secrets.h"

#define BLYNK_PRINT Serial
#include <WiFi.h>
#include <BlynkSimpleEsp32.h>

// ESP32_Relay_AC_X2 (303E32AC210 / SZHJW-ESP32-WROOM-32E) 板載腳位：
//   Relay 1 = GPIO16, Relay 2 = GPIO17, 板載按鈕 = GPIO0, 板載狀態燈 = GPIO23（低電位觸發）
// 只做開/關，剛好對應板載 2 個繼電器，不需外接光耦合模組
#define PIN_OPEN  16  // 板載 Relay 1
#define PIN_CLOSE 17  // 板載 Relay 2

// 斷線（WiFi 或 Blynk 任一失去連線）累積超過這個時間就重開機
#define RECONNECT_TIMEOUT_MS (10UL * 60UL * 1000UL)  // 10 分鐘

unsigned long lastConnectedMillis = 0;
unsigned long opCounter = 0;   // 每次開/關動作遞增，樹莓派靠這個數字變化偵測新事件

void pulseGPIO(int pin) {
  digitalWrite(pin, HIGH);
  delay(300);
  digitalWrite(pin, LOW);
}

// V0（String）:
//   外部 → 裝置：寫入 "open" 或 "close" 觸發動作(iOS 捷徑打 ...&V0=open / ...&V0=close)
//   裝置 → 雲端：執行完動作後，寫回 "open:N" / "close:N" 當作回報，
//                裝置自己 virtualWrite() 不會觸發自己的 BLYNK_WRITE,不會無窮迴圈
BLYNK_WRITE(V0) {
  String cmd = param.asStr();
  const char *action = nullptr;
  if (cmd == "open") { pulseGPIO(PIN_OPEN); action = "open"; }
  else if (cmd == "close") { pulseGPIO(PIN_CLOSE); action = "close"; }
  else return;   // 不認得的內容（例如裝置自己寫回的狀態字串）就忽略

  opCounter++;
  Blynk.virtualWrite(V0, String(action) + ":" + String(opCounter));
}

// 統一的斷線監控：連上就把計時歸零，斷線超過 10 分鐘就重開機
void checkReconnectWatchdog() {
  if (WiFi.status() == WL_CONNECTED && Blynk.connected()) {
    lastConnectedMillis = millis();
  } else if (millis() - lastConnectedMillis > RECONNECT_TIMEOUT_MS) {
    Serial.println("WiFi/Blynk 斷線已超過 10 分鐘，重新啟動裝置...");
    Serial.flush();
    delay(100);
    ESP.restart();
  }
}

void setup() {
  Serial.begin(115200);

  pinMode(PIN_OPEN, OUTPUT);
  pinMode(PIN_CLOSE, OUTPUT);
  digitalWrite(PIN_OPEN, LOW);
  digitalWrite(PIN_CLOSE, LOW);

  lastConnectedMillis = millis(); // 開機當下當作起始基準，避免一開機就被判定逾時

  WiFi.mode(WIFI_STA);   // 明確指定 STA 模式，否則 WiFi.begin() 可能不會真的開始連線
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Blynk.config(BLYNK_AUTH_TOKEN);

  // 開機當下也受同一個 10 分鐘逾時保護：連不上的話不會無限卡住，會自動重開機重試
  bool wifiWasConnected = false;
  while (WiFi.status() != WL_CONNECTED || !Blynk.connected()) {
    Blynk.run();
    checkReconnectWatchdog();
    if (!wifiWasConnected && WiFi.status() == WL_CONNECTED) {
      wifiWasConnected = true;
      Serial.print("WiFi 已連線，IP=");
      Serial.println(WiFi.localIP());
    }
    delay(100);
  }
}

void loop() {
  Blynk.run();
  checkReconnectWatchdog();
}
