#include <Arduino.h>
#include <ESP8266WiFi.h>
#include <WiFiUdp.h>
#include <arduino_homekit_server.h>
#include <Wire.h>
#include <VL53L1X.h>

// ==================== 使用者設定 ====================
// WiFi 帳密放在同目錄的 secrets.h(已被 .gitignore 排除,不會進版控)。
// 第一次使用:複製 secrets.h.example 成 secrets.h,填入自己的 SSID / 密碼。
#include "secrets.h"

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

// ==================== VL53L1X 車庫門位置感測 ====================
// 接線:VL53L1X → 3V3 / GND / SDA / SCL
// ⚠️ 本板為 WeMos D1 R1(FQBN d1),D1=GPIO1(TX)、D2=GPIO16,不可當 I2C!
//    使用板子內建 SDA=GPIO4、SCL=GPIO5 常數,接到板上標示 SDA/SCL 的腳。
#define PIN_SDA    SDA    // GPIO4
#define PIN_SCL    SCL    // GPIO5

// ---- 距離→百分比校正(務必實測後填入)----
// 分別量測「門全關」與「門全開」時感測器讀到的距離(mm),填入下面兩格。
// 兩種安裝方向都支援:DIST_OPEN_MM 比 DIST_CLOSED_MM 大或小都可以。
// 校正做法:先把 VL53_CALIBRATION 設為 1 燒錄,序列埠會持續印出目前距離,
//          手動開/關門讀出兩端數值,填回下面,再把 VL53_CALIBRATION 改回 0。
#define VL53_CALIBRATION  1        // 1=校正模式(只印距離,不更新HomeKit位置)
#define DIST_CLOSED_MM    400      // ← 門「全關」時的距離(mm),實測填入
#define DIST_OPEN_MM      2000     // ← 門「全開」時的距離(mm),實測填入

#define VL53_READ_INTERVAL_MS  500UL   // 讀取/更新位置的間隔
#define POS_CHANGE_THRESHOLD   2       // 位置變化超過幾 % 才更新(去抖動)

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
extern "C" homekit_characteristic_t cha_cover_current;   // 目前位置 0~100%
extern "C" homekit_characteristic_t cha_cover_target;    // 目標位置 0~100%
extern "C" homekit_characteristic_t cha_cover_state;     // 0=關閉中 1=開啟中 2=停止

VL53L1X tofSensor;
bool tofReady = false;
unsigned long lastTofRead = 0;
uint16_t lastDistanceMm = 0;   // 最近一次 VL53L1X 量測到的距離(mm)

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

// ---- 距離(mm)換算成開啟百分比 0~100(自動支援兩種安裝方向)----
uint8_t distance_to_position(uint16_t mm) {
  long lo = DIST_CLOSED_MM, hi = DIST_OPEN_MM;
  long pos = (long)(mm - lo) * 100 / (hi - lo);   // 線性內插(hi<lo 時比例自動反向)
  if (pos < 0)   pos = 0;
  if (pos > 100) pos = 100;
  return (uint8_t)pos;
}

// 定期送一包 JSON 遙測給樹莓派儀表板(以 '{' 開頭,收集器藉此和文字 log 區分)
void sendTelemetry() {
#if UDP_LOG_ENABLE
  if (!udpLogReady || WiFi.status() != WL_CONNECTED) return;
  char buf[256];
  // 校正模式下 HomeKit 位置不會更新,這裡仍即時換算一份給網頁看
  uint8_t pos = tofReady ? distance_to_position(lastDistanceMm) : 0;
  int n = snprintf(buf, sizeof(buf),
      "{\"t\":\"tel\",\"dist\":%u,\"pos\":%u,\"hkpos\":%d,\"target\":%d,"
      "\"state\":%d,\"rssi\":%d,\"heap\":%u,\"up\":%lu,\"tof\":%d,\"cal\":%d}",
      lastDistanceMm, pos,
      cha_cover_current.value.int_value, cha_cover_target.value.int_value,
      cha_cover_state.value.int_value, WiFi.RSSI(),
      (unsigned)ESP.getFreeHeap(), millis() / 1000UL,
      tofReady ? 1 : 0, VL53_CALIBRATION);
  if (n > 0) udpSend(buf, (size_t)min(n, (int)sizeof(buf) - 1));
#endif
}

// 顯示WiFi訊號強度(連線中/已連線皆可呼叫)
void printWifiRssi() {
  // 即時讀一次距離(連線迴圈在 setup 內、loop 尚未跑,不能只靠 lastDistanceMm)
  if (tofReady) {
    uint16_t mm = tofSensor.read(false);
    if (!tofSensor.timeoutOccurred()) lastDistanceMm = mm;
  }
  if (WiFi.status() == WL_CONNECTED) {
    if (tofReady) {
      netlogf("[WiFi] 已連線,訊號強度 RSSI=%d dBm,距離=%u mm\n",
              WiFi.RSSI(), lastDistanceMm);
    } else {
      netlogf("[WiFi] 已連線,訊號強度 RSSI=%d dBm,距離=N/A(感測器未就緒)\n",
              WiFi.RSSI());
    }
  } else {
    if (tofReady) {
      netlogf("[WiFi] 連線中,尚無訊號強度資料,距離=%u mm\n", lastDistanceMm);
    } else {
      netlogf("[WiFi] 連線中,尚無訊號強度資料,距離=N/A(感測器未就緒)\n");
    }
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
    }
  }
}

// ==================== HomeKit 回呼 ====================
void open_setter(const homekit_value_t value)  { trigger_switch(0, value); }
void close_setter(const homekit_value_t value) { trigger_switch(1, value); }
void pause_setter(const homekit_value_t value) { trigger_switch(2, value); }

// ---- 窗簾:使用者拖動滑桿設定目標位置 → 觸發開/關脈衝 ----
// 車庫門無法精準停在中間,策略:目標比目前高 → 開門;比目前低 → 關門。
void cover_target_setter(const homekit_value_t value) {
  int target = value.int_value;
  int current = cha_cover_current.value.int_value;
  cha_cover_target.value = value;   // 記住目標
  if (target > current + POS_CHANGE_THRESHOLD) {
    netlogf("[窗簾] 目標 %d%% > 目前 %d%% → 開門\n", target, current);
    trigger_switch(0, HOMEKIT_BOOL_CPP(true));
    cha_cover_state.value = HOMEKIT_UINT8_CPP(1);   // 開啟中
    homekit_characteristic_notify(&cha_cover_state, cha_cover_state.value);
  } else if (target < current - POS_CHANGE_THRESHOLD) {
    netlogf("[窗簾] 目標 %d%% < 目前 %d%% → 關門\n", target, current);
    trigger_switch(1, HOMEKIT_BOOL_CPP(true));
    cha_cover_state.value = HOMEKIT_UINT8_CPP(0);   // 關閉中
    homekit_characteristic_notify(&cha_cover_state, cha_cover_state.value);
  }
}

// ---- 讀取感測器並更新 HomeKit 位置 / 狀態 ----
void update_door_position() {
  if (!tofReady) return;
  if (millis() - lastTofRead < VL53_READ_INTERVAL_MS) return;
  lastTofRead = millis();

  uint16_t mm = tofSensor.read(false);   // 非阻塞讀取最新一次連續量測值
  if (tofSensor.timeoutOccurred()) {
    netlogf("[VL53] 讀取逾時\n");
    return;
  }
  lastDistanceMm = mm;   // 供 printWifiRssi() 一併顯示

#if VL53_CALIBRATION
  // 校正模式:只印距離,方便讀出門全關 / 全開兩端的 mm 值
  netlogf("[VL53 校正] 距離 = %u mm\n", mm);
  return;
#else
  uint8_t pos = distance_to_position(mm);
  int prev = cha_cover_current.value.int_value;

  if (abs((int)pos - prev) >= POS_CHANGE_THRESHOLD) {
    cha_cover_current.value = HOMEKIT_UINT8_CPP(pos);
    homekit_characteristic_notify(&cha_cover_current, cha_cover_current.value);
    netlogf("[VL53] 距離 %u mm → 位置 %u%%\n", mm, pos);

    // 依位置變化方向更新狀態(開啟中/關閉中);變化很小視為停止
    uint8_t state = (pos > prev + 1) ? 1 : (pos < prev - 1) ? 0 : 2;
    if (state != cha_cover_state.value.int_value) {
      cha_cover_state.value = HOMEKIT_UINT8_CPP(state);
      homekit_characteristic_notify(&cha_cover_state, cha_cover_state.value);
    }
  } else {
    // 位置穩定 → 標記為停止,並讓目標追上目前值(避免滑桿殘留)
    if (cha_cover_state.value.int_value != 2) {
      cha_cover_state.value = HOMEKIT_UINT8_CPP(2);
      homekit_characteristic_notify(&cha_cover_state, cha_cover_state.value);
      cha_cover_target.value = HOMEKIT_UINT8_CPP(cha_cover_current.value.int_value);
      homekit_characteristic_notify(&cha_cover_target, cha_cover_target.value);
    }
  }
#endif
}

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
  cha_cover_target.setter = cover_target_setter;

  // ---- 初始化 VL53L1X(I2C)----
  Wire.begin(PIN_SDA, PIN_SCL);
  Wire.setClock(400000);
  tofSensor.setTimeout(500);
  if (tofSensor.init()) {
    tofSensor.setDistanceMode(VL53L1X::Long);       // 長距模式(最遠約 4m)
    tofSensor.setMeasurementTimingBudget(50000);    // 50ms/次
    tofSensor.startContinuous(50);                  // 每 50ms 連續量測
    tofReady = true;
    Serial.println("[VL53] 初始化成功,開始量測車庫門位置");
#if VL53_CALIBRATION
    Serial.println("[VL53] *** 校正模式:請手動全開/全關門,記下兩端 mm 值 ***");
#endif
  } else {
    Serial.println("[VL53] 初始化失敗!請檢查接線(SDA=GPIO4, SCL=GPIO5, 3V3, GND)");
  }

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
  netlogf("[系統] 開機完成,韌體 = 車庫門控制器 · 測距版\n");

  arduino_homekit_setup(&config);
  netlogf("[HomeKit] 就緒,配對碼 111-11-111\n");
}

// ==================== loop ====================
void loop() {
  arduino_homekit_loop();
  update_pulse_switches();
  update_door_position();

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
