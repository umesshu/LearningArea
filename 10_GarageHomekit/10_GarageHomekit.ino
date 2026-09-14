#include <Arduino.h>
#include <ESP8266WiFi.h>
#include <WiFiUdp.h>
#include <arduino_homekit_server.h>

// ==================== 使用者設定 ====================
// WiFi 帳密放在同目錄的 secrets.h(已被 .gitignore 排除,不會進版控)。
// 第一次使用:複製 secrets.h.example 成 secrets.h,填入自己的 SSID / 密碼。
#include "secrets.h"

// ==================== Blynk(純監控用,不是控制的一部分)====================
// 跟 12_GarageBlynk 共用同一個 Template(Datastream V0 已經定義好,直接沿用),
// 但這是獨立的 Device、獨立的 Token。這裡刻意不呼叫 Blynk.begin()(那會搶走
// WiFi 連線流程),只用 Blynk.config() + loop() 裡的 Blynk.run(),完全不影響
// 既有的 WiFi/HomeKit 連線邏輯。Blynk 連不上最多就是監控看不到資料,
// **絕對不能因為 Blynk 而重開機或延誤 HomeKit**,所以這裡沒有比照 ESP32 版本
// 做斷線重開機的 watchdog。
#include <BlynkSimpleEsp8266.h>
unsigned long blynkOpCounter = 0;   // 每次開/關/暫停動作遞增

void reportOpToBlynk(const char *action) {
  blynkOpCounter++;
  Blynk.virtualWrite(V0, String(action) + ":" + String(blynkOpCounter));
}

const char *ssid     = WIFI_SSID;       // 與 iPhone/HomePod 同網段(192.168.0.x)
const char *password = WIFI_PASSWORD;

// ---- 靜態 IP(繞過不穩的 DHCP,避免拿到 169.254.x.x 孤立位址)----
// 設 USE_STATIC_IP 為 1 啟用;IP 要在路由器同網段、且盡量避開 DHCP 配發範圍。
#define USE_STATIC_IP  1
IPAddress staticIP(192, 168, 0, 222);   // 裝置固定 IP
IPAddress gateway  (192, 168, 0, 1);    // 路由器(閘道)
IPAddress subnet   (255, 255, 255, 0);  // 子網路遮罩
IPAddress dns1     (192, 168, 0, 1);    // DNS(用路由器即可)

// ---- 腳位(用絲印符號,核心自動對應正確 GPIO)----
#define PIN_OPEN    D5   // 開門(上)  → GPIO14
#define PIN_CLOSE   D6   // 關門(下)  → GPIO12
#define PIN_PAUSE   D7   // 暫停(停/開) → GPIO13

#define PULSE_MS        400      // 「上」「下」模擬按一下的脈衝長度
#define PULSE_MS_PAUSE  1200     // 「暫停」模擬按一下的脈衝長度

// ---- 輸出模組極性 ----
// 1 = 低電位觸發(8 路繼電器模組:IN 拉低 → 繼電器吸合)
// 0 = 高電位觸發(舊的光耦板)
// 接線:IN1/IN2/IN3 → D5/D6/D7,GND → GND,VCC → 3V3,
//       拔掉 VCC–JD-VCC 的 jumper,JD-VCC 另接 5V(線圈電源)。
//       VCC 吃 3V3 是為了讓 ESP 輸出的 3.3V 高電位能完全關斷光耦,避免繼電器放不乾淨。
// ⚠️ 低電位觸發時,建議每支 IN 腳對 3V3 加 10kΩ 上拉電阻:
//    ESP8266 開機前幾十毫秒腳位仍是浮接輸入,沒有上拉可能誤觸發一次脈衝。
#define RELAY_ACTIVE_LOW  1

#if RELAY_ACTIVE_LOW
  #define RELAY_ON   LOW     // 相當於「按下按鈕」
  #define RELAY_OFF  HIGH    // 相當於「放開按鈕」
#else
  #define RELAY_ON   HIGH
  #define RELAY_OFF  LOW
#endif

// ==================== 遠端監控(UDP → 樹莓派)====================
// 所有除錯訊息除了走序列埠,也會以 UDP 廣播送到同網段的樹莓派收集器。
// 用「廣播」而非固定 IP:樹莓派換 IP 也不必重燒韌體。
// UDP 是 fire-and-forget,即使樹莓派關機也不會阻塞 HomeKit 迴圈。
#define UDP_LOG_ENABLE       1
#define UDP_LOG_PORT         5514     // 樹莓派收集器監聽的埠(需與 server.py 一致)
#define UDP_TELEMETRY_MS     2000UL   // 每隔多久送一包 JSON 遙測

WiFiUDP udpLog;
IPAddress udpLogTarget;
bool udpLogReady = false;
unsigned long lastTelemetry = 0;

// VL53L1X 距離感測(車庫門位置偵測)已移除:實測這顆感測器裝在這個位置量出來的
// 距離不穩定、不合用。現在只保留開/關/暫停三個獨立開關,不追蹤門的精確位置。

// ==================== 一次性配對重置 ====================
// 若在 iOS 家庭 App 找不到配件(裝置殘留舊配對),把下面設為 1 燒錄開機一次,
// 待 log 顯示配對已清除後,再改回 0 重新燒錄,然後用配對碼重新加入。
// 注意:維持 1 的話每次開機都會清掉配對,將永遠無法配對成功!
#define RESET_HOMEKIT_PAIRING  0

// ==================== HomeKit 特性 ====================
extern "C" homekit_server_config_t config;
extern "C" homekit_characteristic_t cha_open_on;
extern "C" homekit_characteristic_t cha_close_on;
extern "C" homekit_characteristic_t cha_pause_on;

#define WIFI_RSSI_INTERVAL_MS  5000UL   // WiFi訊號強度顯示間隔
unsigned long lastRssiPrint = 0;

// WiFi 若持續斷線超過此時間就自動重開機(涵蓋開機時連不上 & 執行中掉線兩種情況)
#define WIFI_REBOOT_TIMEOUT_MS  (15UL * 60UL * 1000UL)   // 15 分鐘
unsigned long lastWifiOkMs = 0;   // 最近一次 WiFi 為已連線的時間點

// ==================== UDP 遠端 log ====================
// 依目前 IP 與子網路遮罩算出廣播位址(例:192.168.0.222/24 → 192.168.0.255)
void udpLogBegin() {
#if UDP_LOG_ENABLE
  uint32_t ip   = (uint32_t)WiFi.localIP();
  uint32_t mask = (uint32_t)WiFi.subnetMask();
  udpLogTarget = IPAddress(ip | ~mask);
  udpLog.begin(UDP_LOG_PORT + 1);   // 本機來源埠,收不收都無所謂
  udpLogReady = true;
  Serial.print("[UDP] 除錯訊息廣播至 ");
  Serial.print(udpLogTarget);
  Serial.printf(":%d\n", UDP_LOG_PORT);
#endif
}

void udpSend(const char *data, size_t len) {
#if UDP_LOG_ENABLE
  if (!udpLogReady || WiFi.status() != WL_CONNECTED) return;
  if (udpLog.beginPacket(udpLogTarget, UDP_LOG_PORT)) {
    udpLog.write((const uint8_t *)data, len);
    udpLog.endPacket();
  }
#endif
}

// 取代 Serial.printf:同一份訊息同時進序列埠與 UDP。
// 格式字串與原本完全相同,呼叫端只需改函式名。
void netlogf(const char *fmt, ...) {
  char buf[220];
  va_list ap;
  va_start(ap, fmt);
  int n = vsnprintf(buf, sizeof(buf), fmt, ap);
  va_end(ap);
  if (n < 0) return;
  if (n >= (int)sizeof(buf)) n = sizeof(buf) - 1;   // 過長就截斷
  Serial.print(buf);
  udpSend(buf, n);
}

// 定期送一包 JSON 遙測給樹莓派儀表板(以 '{' 開頭,收集器藉此和文字 log 區分)
void sendTelemetry() {
#if UDP_LOG_ENABLE
  if (!udpLogReady || WiFi.status() != WL_CONNECTED) return;
  char buf[128];
  int n = snprintf(buf, sizeof(buf),
      "{\"t\":\"tel\",\"rssi\":%d,\"heap\":%u,\"up\":%lu}",
      WiFi.RSSI(), (unsigned)ESP.getFreeHeap(), millis() / 1000UL);
  if (n > 0) udpSend(buf, (size_t)min(n, (int)sizeof(buf) - 1));
#endif
}

// 顯示WiFi訊號強度(連線中/已連線皆可呼叫)
void printWifiRssi() {
  if (WiFi.status() == WL_CONNECTED) {
    netlogf("[WiFi] 已連線,訊號強度 RSSI=%d dBm\n", WiFi.RSSI());
  } else {
    netlogf("[WiFi] 連線中,尚無訊號強度資料\n");
  }
}

// ==================== 工具 ====================
// 三個開關共用:按下(On)就啟動脈衝,由 loop() 非阻塞地在 PULSE_MS 後收尾,
// 避免 delay() 卡住 HomeKit 處理迴圈導致其他請求逾時。
struct PulseSwitch {
  homekit_characteristic_t *cha;
  uint8_t pin;
  const char *label;
  unsigned long pulse_ms;
  bool active;
  unsigned long start;
};

// action:給 Blynk 回報用的英文代號,樹莓派端解析用,跟 label(中文,給序列埠/UDP log 用)分開
const char *pulseActions[3] = { "open", "close", "pause" };

PulseSwitch pulseSwitches[3] = {
  { &cha_open_on,  PIN_OPEN,  "開門", PULSE_MS,       false, 0 },
  { &cha_close_on, PIN_CLOSE, "關門", PULSE_MS,       false, 0 },
  { &cha_pause_on, PIN_PAUSE, "暫停", PULSE_MS_PAUSE, false, 0 },
};

void trigger_switch(uint8_t idx, const homekit_value_t value) {
  PulseSwitch &sw = pulseSwitches[idx];
  if (value.bool_value && !sw.active) {
    digitalWrite(sw.pin, RELAY_ON);
    sw.active = true;
    sw.start = millis();
    netlogf("[指令] %s\n", sw.label);
  }
}

void update_pulse_switches() {
  for (uint8_t i = 0; i < 3; i++) {
    PulseSwitch &sw = pulseSwitches[i];
    if (sw.active && millis() - sw.start >= sw.pulse_ms) {
      digitalWrite(sw.pin, RELAY_OFF);
      sw.active = false;
      sw.cha->value = HOMEKIT_BOOL_CPP(false);
      homekit_characteristic_notify(sw.cha, sw.cha->value);
      reportOpToBlynk(pulseActions[i]);   // 純回報監控用,失敗也不影響上面已經做完的動作
    }
  }
}

// ==================== HomeKit 回呼 ====================
void open_setter(const homekit_value_t value)  { trigger_switch(0, value); }
void close_setter(const homekit_value_t value) { trigger_switch(1, value); }
void pause_setter(const homekit_value_t value) { trigger_switch(2, value); }

// ==================== setup ====================
void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.println("\n[車庫門控制器 · 三開關版]");

  // 先寫入「放開」電位再切成輸出,避免 pinMode 當下輸出一個反向的短脈衝
  // (低電位觸發時那一瞬間就等於按了一下按鈕,車庫門會自己動)。
  digitalWrite(PIN_OPEN,  RELAY_OFF);  pinMode(PIN_OPEN,  OUTPUT);  digitalWrite(PIN_OPEN,  RELAY_OFF);
  digitalWrite(PIN_CLOSE, RELAY_OFF);  pinMode(PIN_CLOSE, OUTPUT);  digitalWrite(PIN_CLOSE, RELAY_OFF);
  digitalWrite(PIN_PAUSE, RELAY_OFF);  pinMode(PIN_PAUSE, OUTPUT);  digitalWrite(PIN_PAUSE, RELAY_OFF);

  cha_open_on.setter = open_setter;
  cha_close_on.setter = close_setter;
  cha_pause_on.setter = pause_setter;

#if RESET_HOMEKIT_PAIRING
  // 清除配對移到 WiFi 連線「之前」,確保即使 WiFi 連不上也能清乾淨、恢復可發現
  Serial.println("[HomeKit] *** 清除舊配對資料(RESET_HOMEKIT_PAIRING=1)***");
  Serial.println("[HomeKit] *** 完成後請把 RESET_HOMEKIT_PAIRING 改回 0 再燒一次!***");
  homekit_storage_reset();
#endif

  WiFi.mode(WIFI_STA);

#if USE_STATIC_IP
  if (WiFi.config(staticIP, gateway, subnet, dns1)) {
    Serial.print("[WiFi] 使用靜態 IP=");
    Serial.println(staticIP);
  } else {
    Serial.println("[WiFi] 靜態 IP 設定失敗,改用 DHCP");
  }
#endif

  // ---- 暫時診斷:掃描附近WiFi,確認SSID是否存在 ----
  Serial.println("[WiFi] 掃描附近網路...");
  int n = WiFi.scanNetworks();
  for (int i = 0; i < n; i++) {
    Serial.printf("  %2d: %-32s  RSSI=%d  %s\n",
                  i + 1, WiFi.SSID(i).c_str(), WiFi.RSSI(i),
                  WiFi.encryptionType(i) == ENC_TYPE_NONE ? "開放" : "加密");
  }
  Serial.println("[WiFi] 掃描結束");

  WiFi.begin(ssid, password);
  Serial.print("[WiFi] 連線中");
  int retry = 0;
  lastRssiPrint = millis();
  unsigned long connectStart = millis();   // 開始嘗試連線的時間
  while (WiFi.status() != WL_CONNECTED) {
    delay(300);
    Serial.print(".");
    if (++retry > 40) {   // 12秒還沒連上就印出狀態碼並重試,避免看不出卡在哪
      Serial.printf("\n[WiFi] 逾時,status=%d,重新嘗試...\n", WiFi.status());
      WiFi.begin(ssid, password);
      retry = 0;
    }
    if (millis() - lastRssiPrint >= WIFI_RSSI_INTERVAL_MS) {
      Serial.println();
      printWifiRssi();
      lastRssiPrint = millis();
    }
    // 開機後持續連不上超過 15 分鐘 → 重開機從頭再試
    if (millis() - connectStart >= WIFI_REBOOT_TIMEOUT_MS) {
      Serial.println("\n[系統] WiFi 連續 15 分鐘連不上,重新開機...");
      Serial.flush();
      delay(100);
      ESP.restart();
    }
  }
  Serial.print("\n[WiFi] 已連線 IP=");
  Serial.println(WiFi.localIP());
  lastRssiPrint = millis();
  lastWifiOkMs = millis();   // 記錄已連線的時間點,供 loop() 判斷掉線

  udpLogBegin();             // WiFi 就緒後才算得出廣播位址
  netlogf("[系統] 開機完成,韌體 = 車庫門控制器 · 三開關版(無測距)\n");

  Blynk.config(BLYNK_AUTH_TOKEN);   // 不呼叫 Blynk.begin(),不搶 WiFi 連線流程;
                                     // 之後交給 loop() 的 Blynk.run() 背景連線

  arduino_homekit_setup(&config);
  netlogf("[HomeKit] 就緒,配對碼 111-11-111\n");
}

// ==================== loop ====================
void loop() {
  arduino_homekit_loop();
  update_pulse_switches();
  Blynk.run();   // 純監控用的背景連線;WiFi 沒連上時 Blynk.run() 內部會自己跳過

  // 每隔約5秒顯示一次WiFi訊號強度
  if (millis() - lastRssiPrint >= WIFI_RSSI_INTERVAL_MS) {
    printWifiRssi();
    lastRssiPrint = millis();
  }

  // 定期送 JSON 遙測給樹莓派儀表板
  if (millis() - lastTelemetry >= UDP_TELEMETRY_MS) {
    sendTelemetry();
    lastTelemetry = millis();
  }

  // WiFi 掉線監控:連線正常就更新時間戳;連續斷線超過 15 分鐘就重開機
  if (WiFi.status() == WL_CONNECTED) {
    lastWifiOkMs = millis();
  } else if (millis() - lastWifiOkMs >= WIFI_REBOOT_TIMEOUT_MS) {
    netlogf("[系統] WiFi 掉線超過 15 分鐘,重新開機...\n");
    Serial.flush();
    delay(100);
    ESP.restart();
  }
}
