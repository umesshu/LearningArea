#include <Arduino.h>
#include <ESP8266WiFi.h>
#include <Wire.h>
#include <VL53L1X.h>
#include <arduino_homekit_server.h>

// ==================== 使用者設定 ====================
const char *ssid     = "Jimmy-Wifi6";      // ← 改成你家 2.4GHz Wi-Fi
const char *password = "0988178308";      // ← 改成你的密碼

// ---- 腳位(用絲印符號,核心自動對應正確 GPIO)----
#define PIN_OPEN    D5   // 開門(上)  → GPIO14
#define PIN_CLOSE   D6   // 關門(下)  → GPIO12
#define PIN_PAUSE   D7   // 暫停(停/開) → GPIO13
// VL53L1X 走 I2C:SDA=D14, SCL=D15(ESP8266 預設 I2C 腳)

#define PULSE_MS       400       // 模擬按一下的脈衝長度
#define TIMEOUT_MS     25000UL   // 逾時保護:25 秒沒到位 → 障礙警示
#define SENSOR_PERIOD  200       // 感測器輪詢週期(毫秒)

// ---- 距離→百分比校準(單位 mm,務必依實測調整!)----
// DIST_CLOSED:門全關時,感測器量到的距離(門板最遠,約車庫高)
// DIST_OPEN  :門全開時,感測器量到的距離(門板捲到頂,最近)
#define DIST_CLOSED  2300        // ← 全關距離,實測後改
#define DIST_OPEN     300        // ← 全開距離,實測後改
#define POS_TOLERANCE  3         // 到位判定容差(%)

// ==================== 全域物件 ====================
VL53L1X sensor;
bool sensorOK = false;

extern "C" homekit_server_config_t config;
extern "C" homekit_characteristic_t cha_current_position;
extern "C" homekit_characteristic_t cha_target_position;
extern "C" homekit_characteristic_t cha_position_state;
extern "C" homekit_characteristic_t cha_obstruction;
extern "C" homekit_characteristic_t cha_pause_on;

unsigned long moveStart = 0;     // 動作計時起點(0=未計時)
uint8_t lastPos = 0;             // 上次回報的百分比

// ==================== 工具函式 ====================
void pulse(uint8_t pin) {
  digitalWrite(pin, HIGH);
  delay(PULSE_MS);
  digitalWrite(pin, LOW);
}

// 距離(mm)換算成開門百分比(0~100)
uint8_t distanceToPercent(uint16_t mm) {
  long span = (long)DIST_CLOSED - (long)DIST_OPEN;   // 全關到全開的距離差
  if (span == 0) return 0;
  long pct = (long)(DIST_CLOSED - mm) * 100 / span;  // 距離越短→開越多
  if (pct < 0)   pct = 0;
  if (pct > 100) pct = 100;
  return (uint8_t)pct;
}

// ==================== HomeKit 回呼 ====================
// 家庭 App 拖動位置滑桿(0~100)
void target_position_setter(const homekit_value_t value) {
  cha_target_position.value = value;
  uint8_t target = value.uint8_value;
  uint8_t cur = cha_current_position.value.uint8_value;

  if (target > cur) {            // 目標比現在高 → 開門
    pulse(PIN_OPEN);
    cha_position_state.value = HOMEKIT_UINT8_CPP(1);   // 開啟中
  } else if (target < cur) {     // 目標比現在低 → 關門
    pulse(PIN_CLOSE);
    cha_position_state.value = HOMEKIT_UINT8_CPP(0);   // 關閉中
  }
  cha_obstruction.value = HOMEKIT_BOOL_CPP(false);
  homekit_characteristic_notify(&cha_position_state, cha_position_state.value);
  homekit_characteristic_notify(&cha_obstruction, cha_obstruction.value);
  moveStart = millis();
}

// 暫停開關:按下觸發暫停脈衝,自動彈回 OFF
void pause_setter(const homekit_value_t value) {
  if (value.bool_value) {
    pulse(PIN_PAUSE);
    cha_pause_on.value = HOMEKIT_BOOL_CPP(false);
    homekit_characteristic_notify(&cha_pause_on, cha_pause_on.value);
    // 暫停後門會停在半路,清掉計時避免誤報逾時
    moveStart = 0;
    cha_position_state.value = HOMEKIT_UINT8_CPP(2);   // 靜止
    homekit_characteristic_notify(&cha_position_state, cha_position_state.value);
  }
}

// ==================== setup ====================
void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.println("\n[車庫門控制器啟動]");

  pinMode(PIN_OPEN, OUTPUT);   digitalWrite(PIN_OPEN, LOW);
  pinMode(PIN_CLOSE, OUTPUT);  digitalWrite(PIN_CLOSE, LOW);
  pinMode(PIN_PAUSE, OUTPUT);  digitalWrite(PIN_PAUSE, LOW);

  // ---- 初始化 VL53L1X ----
  Wire.begin();          // ESP8266 預設 SDA=D14, SCL=D15
  Wire.setClock(400000);
  sensor.setTimeout(500);
  if (sensor.init()) {
    sensor.setDistanceMode(VL53L1X::Long);        // 長距模式(~4m)
    sensor.setMeasurementTimingBudget(50000);     // 50ms,穩定度佳
    sensor.startContinuous(SENSOR_PERIOD);
    sensorOK = true;
    Serial.println("[VL53L1X] 初始化成功");
  } else {
    Serial.println("[VL53L1X] 初始化失敗!檢查接線(SDA=D14 SCL=D15 VIN=3V3)");
  }

  // ---- 綁定 HomeKit 回呼 ----
  cha_target_position.setter = target_position_setter;
  cha_pause_on.setter = pause_setter;

  // ---- 連 Wi-Fi ----
  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid, password);
  Serial.print("[WiFi] 連線中");
  while (WiFi.status() != WL_CONNECTED) { delay(300); Serial.print("."); }
  Serial.print("\n[WiFi] 已連線 IP=");
  Serial.println(WiFi.localIP());

  arduino_homekit_setup(&config);
  Serial.println("[HomeKit] 就緒,配對碼 111-11-111");
}

// ==================== loop ====================
void loop() {
  arduino_homekit_loop();

  static unsigned long lastCheck = 0;
  if (millis() - lastCheck < SENSOR_PERIOD) return;
  lastCheck = millis();

  if (!sensorOK) return;

  uint16_t mm = sensor.read(false);
  if (sensor.timeoutOccurred()) {
    Serial.println("[VL53L1X] 讀取逾時");
    return;
  }

  uint8_t pos = distanceToPercent(mm);
  Serial.printf("[量測] 距離=%u mm  開門=%u%%\n", mm, pos);

  // 位置有明顯變化才更新 HomeKit(避免抖動洗頻)
  if (abs((int)pos - (int)lastPos) >= 1) {
    cha_current_position.value = HOMEKIT_UINT8_CPP(pos);
    homekit_characteristic_notify(&cha_current_position, cha_current_position.value);
    lastPos = pos;
  }

  // ---- 到位判定:接近目標則視為靜止,停止計時 ----
  uint8_t target = cha_target_position.value.uint8_value;
  if (abs((int)pos - (int)target) <= POS_TOLERANCE) {
    if (cha_position_state.value.uint8_value != 2) {
      cha_position_state.value = HOMEKIT_UINT8_CPP(2);   // 靜止
      homekit_characteristic_notify(&cha_position_state, cha_position_state.value);
    }
    if (cha_obstruction.value.bool_value) {
      cha_obstruction.value = HOMEKIT_BOOL_CPP(false);
      homekit_characteristic_notify(&cha_obstruction, cha_obstruction.value);
    }
    moveStart = 0;
  }

  // ---- 25 秒逾時保護 ----
  if (moveStart != 0 && (millis() - moveStart > TIMEOUT_MS)) {
    cha_position_state.value = HOMEKIT_UINT8_CPP(2);       // 靜止
    cha_obstruction.value = HOMEKIT_BOOL_CPP(true);        // 障礙警示
    homekit_characteristic_notify(&cha_position_state, cha_position_state.value);
    homekit_characteristic_notify(&cha_obstruction, cha_obstruction.value);
    Serial.println("[逾時] 25 秒未到位 → 觸發障礙警示");
    moveStart = 0;
  }
}
