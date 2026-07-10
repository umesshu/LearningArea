#include <Arduino.h>
#include <ESP8266WiFi.h>
#include <arduino_homekit_server.h>

// ==================== 使用者設定 ====================
const char *ssid     = "Jimmy-Wifi6-2-4G";      // ← 改成你家 2.4GHz Wi-Fi
const char *password = "0988178308";      // ← 改成你的密碼

// ---- 腳位(用絲印符號,核心自動對應正確 GPIO)----
#define PIN_OPEN    D5   // 開門(上)  → GPIO14
#define PIN_CLOSE   D6   // 關門(下)  → GPIO12
#define PIN_PAUSE   D7   // 暫停(停/開) → GPIO13

#define PULSE_MS      400        // 模擬按一下的脈衝長度
#define TRAVEL_MS     20000UL    // 假設門開/關全程約 20 秒(依實測調整)

// ==================== HomeKit 特性 ====================
extern "C" homekit_server_config_t config;
extern "C" homekit_characteristic_t cha_current_door_state;
extern "C" homekit_characteristic_t cha_target_door_state;
extern "C" homekit_characteristic_t cha_obstruction;
extern "C" homekit_characteristic_t cha_pause_on;

unsigned long moveStart = 0;   // 動作起點(0=靜止)
uint8_t movingTo = 1;          // 這次動作的目標:0=開 1=關

// ==================== 工具 ====================
void pulse(uint8_t pin) {
  digitalWrite(pin, HIGH);
  delay(PULSE_MS);
  digitalWrite(pin, LOW);
}

// ==================== HomeKit 回呼 ====================
// 家庭 App 按開 / 關
void target_door_setter(const homekit_value_t value) {
  cha_target_door_state.value = value;
  uint8_t t = value.uint8_value;   // 0=開 1=關

  if (t == 0) {                    // 要開
    pulse(PIN_OPEN);
    cha_current_door_state.value = HOMEKIT_UINT8_CPP(2);   // 開啟中
  } else {                         // 要關
    pulse(PIN_CLOSE);
    cha_current_door_state.value = HOMEKIT_UINT8_CPP(3);   // 關閉中
  }
  cha_obstruction.value = HOMEKIT_BOOL_CPP(false);
  homekit_characteristic_notify(&cha_current_door_state, cha_current_door_state.value);
  homekit_characteristic_notify(&cha_obstruction, cha_obstruction.value);

  movingTo = t;
  moveStart = millis();
  Serial.printf("[指令] 目標=%s\n", t == 0 ? "開" : "關");
}

// 暫停開關:觸發後自動彈回,並把門標成 STOPPED
void pause_setter(const homekit_value_t value) {
  if (value.bool_value) {
    pulse(PIN_PAUSE);
    cha_pause_on.value = HOMEKIT_BOOL_CPP(false);
    homekit_characteristic_notify(&cha_pause_on, cha_pause_on.value);

    moveStart = 0;   // 停止計時
    cha_current_door_state.value = HOMEKIT_UINT8_CPP(4);   // STOPPED
    homekit_characteristic_notify(&cha_current_door_state, cha_current_door_state.value);
    Serial.println("[指令] 暫停 → STOPPED");
  }
}

// ==================== setup ====================
void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.println("\n[車庫門控制器 · 無感測三鍵版]");

  pinMode(PIN_OPEN, OUTPUT);   digitalWrite(PIN_OPEN, LOW);
  pinMode(PIN_CLOSE, OUTPUT);  digitalWrite(PIN_CLOSE, LOW);
  pinMode(PIN_PAUSE, OUTPUT);  digitalWrite(PIN_PAUSE, LOW);

  cha_target_door_state.setter = target_door_setter;
  cha_pause_on.setter = pause_setter;

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

  // 無感測:用計時器模擬「門走完全程」,時間到就把狀態設為已開/已關
  if (moveStart != 0 && (millis() - moveStart >= TRAVEL_MS)) {
    if (movingTo == 0) {
      cha_current_door_state.value = HOMEKIT_UINT8_CPP(0);   // OPEN
    } else {
      cha_current_door_state.value = HOMEKIT_UINT8_CPP(1);   // CLOSED
    }
    homekit_characteristic_notify(&cha_current_door_state, cha_current_door_state.value);
    Serial.printf("[狀態] 假定已%s(計時 %lu 秒到)\n",
                  movingTo == 0 ? "開" : "關", TRAVEL_MS / 1000);
    moveStart = 0;
  }
}
