# 車庫門 HomeKit 控制器(Wemos D1 R2 / ESP8266)

## 檔案
- `garage.ino` — 主程式
- `accessory.c` — HomeKit 配件定義(車庫門 Window Covering + 暫停 Switch)

## 一次性安裝
```bash
# ESP8266 核心
arduino-cli config add board_manager.additional_urls https://arduino.esp8266.com/stable/package_esp8266com_index.json
arduino-cli core update-index
arduino-cli core install esp8266:esp8266

# 函式庫
arduino-cli lib install --git-url https://github.com/Mixiaoxiao/Arduino-HomeKit-ESP8266.git
arduino-cli lib install "VL53L1X"   # Pololu 版
```

## 編譯 + 燒錄(先 cd 進本資料夾)
```bash
arduino-cli compile --fqbn esp8266:esp8266:d1 . \
  && arduino-cli upload -p /dev/ttyUSB0 --fqbn esp8266:esp8266:d1 .
```

## 序列埠監控
```bash
arduino-cli monitor -p /dev/ttyUSB0 -c baudrate=115200
```

## 燒錄前務必修改
1. `ssid` / `password` → 你家 2.4GHz Wi-Fi
2. `DIST_CLOSED` / `DIST_OPEN` → 實測門全關 / 全開時感測器的距離(mm)

## 校準方法
1. 先燒錄,開序列埠看 `[量測] 距離=xxxx mm`
2. 門「全關」時記下距離 → 填 `DIST_CLOSED`
3. 門「全開」時記下距離 → 填 `DIST_OPEN`
4. 重新燒錄,百分比就準了

## 加入 Apple 家庭
家庭 App → 加入配件 → 沒有代碼 → 選「車庫門」→ 輸入 `111-11-111` → 仍要加入
