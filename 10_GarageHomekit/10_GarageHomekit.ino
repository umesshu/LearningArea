#include <Arduino.h>
#include <ESP8266WiFi.h>
#include <arduino_homekit_server.h>

// ==================== 使用者設定 ====================
const char *ssid     = "Jimmy-Wifi6";      // ← 改成你家 2.4GHz Wi-Fi
const char *password = "0988178308";      // ← 改成你的密碼

// ---- 腳位(用絲印符號,核心自動對應正確 GPIO)----
#define PIN_OPEN    D5   // 開門(上)  → GPIO14
#define PIN_CLOSE   D6   // 關門(下)  → GPIO12
#define PIN_PAUSE   D7   // 暫停(停/開) → GPIO13

#define PULSE_MS        400      // 「上」「下」模擬按一下的脈衝長度
#define PULSE_MS_PAUSE  1200     // 「暫停」模擬按一下的脈衝長度

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

// 顯示WiFi訊號強度(連線中/已連線皆可呼叫)
void printWifiRssi() {
  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("[WiFi] 已連線,訊號強度 RSSI=%d dBm\n", WiFi.RSSI());
  } else {
    Serial.println("[WiFi] 連線中,尚無訊號強度資料...");
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

PulseSwitch pulseSwitches[3] = {
  { &cha_open_on,  PIN_OPEN,  "開門", PULSE_MS,       false, 0 },
  { &cha_close_on, PIN_CLOSE, "關門", PULSE_MS,       false, 0 },
  { &cha_pause_on, PIN_PAUSE, "暫停", PULSE_MS_PAUSE, false, 0 },
};

void trigger_switch(uint8_t idx, const homekit_value_t value) {
  PulseSwitch &sw = pulseSwitches[idx];
  if (value.bool_value && !sw.active) {
    digitalWrite(sw.pin, HIGH);
    sw.active = true;
    sw.start = millis();
    Serial.printf("[指令] %s\n", sw.label);
  }
}

void update_pulse_switches() {
  for (uint8_t i = 0; i < 3; i++) {
    PulseSwitch &sw = pulseSwitches[i];
    if (sw.active && millis() - sw.start >= sw.pulse_ms) {
      digitalWrite(sw.pin, LOW);
      sw.active = false;
      sw.cha->value = HOMEKIT_BOOL_CPP(false);
      homekit_characteristic_notify(sw.cha, sw.cha->value);
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

  pinMode(PIN_OPEN, OUTPUT);   digitalWrite(PIN_OPEN, LOW);
  pinMode(PIN_CLOSE, OUTPUT);  digitalWrite(PIN_CLOSE, LOW);
  pinMode(PIN_PAUSE, OUTPUT);  digitalWrite(PIN_PAUSE, LOW);

  cha_open_on.setter = open_setter;
  cha_close_on.setter = close_setter;
  cha_pause_on.setter = pause_setter;

  WiFi.mode(WIFI_STA);

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
  }
  Serial.print("\n[WiFi] 已連線 IP=");
  Serial.println(WiFi.localIP());
  lastRssiPrint = millis();

#if RESET_HOMEKIT_PAIRING
  Serial.println("[HomeKit] *** 清除舊配對資料(RESET_HOMEKIT_PAIRING=1)***");
  Serial.println("[HomeKit] *** 完成後請把 RESET_HOMEKIT_PAIRING 改回 0 再燒一次!***");
  homekit_storage_reset();
#endif

  arduino_homekit_setup(&config);
  Serial.println("[HomeKit] 就緒,配對碼 111-11-111");
}

// ==================== loop ====================
void loop() {
  arduino_homekit_loop();
  update_pulse_switches();

  // 每隔約5秒顯示一次WiFi訊號強度
  if (millis() - lastRssiPrint >= WIFI_RSSI_INTERVAL_MS) {
    printWifiRssi();
    lastRssiPrint = millis();
  }
}
