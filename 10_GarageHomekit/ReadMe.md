# 車庫門 HomeKit 控制器 · 三開關版(Wemos D1 R1)

Home App 裡是三個獨立的開關配件:「開門」「關門」「暫停」,各自按下(On)後觸發一次脈衝,再自動彈回 Off,不含任何門狀態顯示或位置感測。

## 檔案
- `10_GarageHomekit.ino` — 主程式
- `accessary.c` — HomeKit 配件(開門 / 關門 / 暫停,各為獨立 Switch 配件)

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

## 運作說明
- 三個開關各自獨立,按下(On)就對應腳位輸出一個 PULSE_MS 脈衝,然後自動彈回 Off。
- 不會回報門的實際開/關/移動中狀態,純粹是三顆觸發按鈕。

## 加入 Apple 家庭
家庭 App → 加入配件 → 沒有代碼 → 依序加入「開門」「關門」「暫停」三個配件 → 輸入 `111-11-111` → 仍要加入

> 若手機上已經配對過舊版(車庫門控制器)裝置,燒錄新版後請先在家庭 App 移除舊配件,再重新掃描加入,避免快取的配件資料與新結構不一致。
