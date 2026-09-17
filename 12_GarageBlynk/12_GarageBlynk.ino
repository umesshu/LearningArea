// 車庫門遙控器（ESP32 + Blynk IoT 2.0）
// 已驗證：燒錄、WiFi/Blynk 連線、開/關繼電器觸發皆測試成功
//
// 監控走「Blynk Cloud 中繼」而非本地 UDP：連線狀態靠 isHardwareConnected API，
// 操作紀錄靠單一 String 虛擬腳位 V0，這樣不管樹莓派實際在哪個網路都收得到，
// 不像本地 UDP 廣播/單播那樣依賴「兩台裝置在同一個網段」。
//
// 曾經一度以為 String Datastream 在 Blynk 後端會被誤判成數值型態（送字串一律
// 400），一路懷疑到是平台 bug；後來才發現真正原因是 Blynk Console 編輯完
// Datastream 之後要按「Save and Apply」才會真的套用，光是編輯畫面看起來改好了
// 不代表後端已生效。按下 Save and Apply 之後這個單一 String 腳位設計完全正常。
//
// 【2026-09-17 修正】V0 原本只存「最新一筆」("open:N")，如果樹莓派輪詢間隔內
// 連續操作兩次以上，中間那幾筆會被直接覆寫掉、Pi 端永遠看不到。現在改成把
// 「最近 OP_HISTORY_SIZE 筆」一起塞進同一個 String，格式 "counter:action" 用
// 逗號分隔、新的在前，例如 "12:close,11:open,10:close"。樹莓派端一次輪詢
// 解析整串，把所有「還沒處理過的 counter」都補回去，不會再漏記。
//
// 【2026-09-17 加上操作者資訊】iOS 捷徑用「取得裝置詳細資訊」拿到手機/裝置名稱，
// 跟動作一起塞進同一個字串送過來，格式改成 "動作|操作者"，例如
// "open|iPhone 15(阿明)"。韌體解析出動作照舊觸發繼電器，操作者則一起記進歷史，
// 格式變成 "counter:action:operator"，例如 "12:close:iPhone15,11:open:iPad"。
// 操作者字串裡如果剛好出現 "," 或 ":" 會壞了這個簡易格式，所以送進歷史前會先
// 用 sanitizeOperator() 把這兩個字元換成空白。

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

unsigned long opCounter = 0;   // 每次開/關動作遞增，樹莓派靠這個數字變化偵測新事件

// 最近 N 筆操作的環狀緩衝區，供樹莓派一次輪詢就能補齊間隔內發生的所有動作
// （不只是最新一筆），避免短時間內連續操作被覆寫掉、Pi 端漏記。
#define OP_HISTORY_SIZE 8
unsigned long opHistoryCounter[OP_HISTORY_SIZE];
String opHistoryAction[OP_HISTORY_SIZE];
String opHistoryOperator[OP_HISTORY_SIZE];
int opHistoryCount = 0;   // 目前緩衝區裡有效筆數（< OP_HISTORY_SIZE 代表還沒補滿）

unsigned long lastConnectedMillis = 0;

void pulseGPIO(int pin) {
  digitalWrite(pin, HIGH);
  delay(300);
  digitalWrite(pin, LOW);
}

// 操作者字串裡的 "," ":" 會弄壞歷史字串的簡易格式，換成空白；順便限制長度。
String sanitizeOperator(String s) {
  s.replace(",", " ");
  s.replace(":", " ");
  s.trim();
  if (s.length() > 24) s = s.substring(0, 24);
  return s;
}

// 記一筆新操作進歷史緩衝區（新的放最前面），再把整個緩衝區組成字串回報給 Blynk。
void reportOp(const char *action, const String &operatorName) {
  opCounter++;

  int fillCount = min(opHistoryCount + 1, OP_HISTORY_SIZE);
  for (int i = fillCount - 1; i > 0; i--) {
    opHistoryCounter[i] = opHistoryCounter[i - 1];
    opHistoryAction[i]  = opHistoryAction[i - 1];
    opHistoryOperator[i] = opHistoryOperator[i - 1];
  }
  opHistoryCounter[0] = opCounter;
  opHistoryAction[0]  = action;
  opHistoryOperator[0] = operatorName;
  opHistoryCount = fillCount;

  String combined;
  for (int i = 0; i < opHistoryCount; i++) {
    if (i) combined += ",";
    combined += String(opHistoryCounter[i]) + ":" + opHistoryAction[i] + ":" + opHistoryOperator[i];
  }
  Blynk.virtualWrite(V0, combined);
}

// V0（String）:
//   外部 → 裝置：寫入 "動作" 或 "動作|操作者" 觸發動作
//                (iOS 捷徑打 ...&V0=open / ...&V0=open|iPhone15)
//   裝置 → 雲端：執行完動作後，寫回歷史緩衝區字串當作回報，
//                裝置自己 virtualWrite() 不會觸發自己的 BLYNK_WRITE,不會無窮迴圈
BLYNK_WRITE(V0) {
  String cmd = param.asStr();
  int sep = cmd.indexOf('|');
  String action = (sep < 0) ? cmd : cmd.substring(0, sep);
  String operatorName = (sep < 0) ? "" : sanitizeOperator(cmd.substring(sep + 1));

  if (action == "open") { pulseGPIO(PIN_OPEN); reportOp("open", operatorName); }
  else if (action == "close") { pulseGPIO(PIN_CLOSE); reportOp("close", operatorName); }
  // 不認得的內容（例如裝置自己寫回的歷史字串）就忽略
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
