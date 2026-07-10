# 車庫門 HomeKit 控制器 · 無感測三鍵版(Wemos D1 R2)

僅開/關/暫停三路控制,不含任何距離或位置感測。

## 檔案
- `garage3.ino` — 主程式
- `accessory.c` — HomeKit 配件(車庫門 + 暫停 Switch)

## 電路
| 功能 | Wemos 腳 | GPIO | 接遙控器按鈕 |
|---|---|---|---|
| 開門 | D5 | 14 | 上 |
| 關門 | D6 | 12 | 下 |
| 暫停 | D7 | 13 | 停/開 |

- 每路:GPIO → 220Ω → 光耦 IN;光耦 IN 另端 → GND
- 每路 GPIO 對 GND 加 10kΩ 下拉(防漏電 / 開機誤觸)
- 光耦輸出並接遙控器按鈕兩焊點;接遙控器後兩側不共地
- 「鎖」不接。電源用 USB 或 HLK-5M05

## 安裝
```bash
arduino-cli config add board_manager.additional_urls https://arduino.esp8266.com/stable/package_esp8266com_index.json
arduino-cli core update-index
arduino-cli core install esp8266:esp8266
arduino-cli lib install --git-url https://github.com/Mixiaoxiao/Arduino-HomeKit-ESP8266.git
```

## 編譯燒錄(cd 進本資料夾)
```bash
arduino-cli compile --fqbn esp8266:esp8266:d1 . \
  && arduino-cli upload -p /dev/ttyUSB0 --fqbn esp8266:esp8266:d1 .
```

## 監控
```bash
arduino-cli monitor -p /dev/ttyUSB0 -c baudrate=115200
```

## 燒錄前修改
1. `ssid` / `password` → 你家 2.4GHz Wi-Fi
2. `TRAVEL_MS` → 門開/關全程實際秒數(預設 20 秒)

## 運作說明
- 因無感測,狀態靠「計時」模擬:按開/關後,經過 TRAVEL_MS 就假定門已到位。
- 暫停會把門標成 STOPPED。
- 這只是狀態顯示的估計,實際門位置以肉眼為準。

## 加入 Apple 家庭
家庭 App → 加入配件 → 沒有代碼 → 選「車庫門」→ 輸入 `111-11-111` → 仍要加入
