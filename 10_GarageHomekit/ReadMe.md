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

## 序列埠記錄工具(log_serial.py)
把 D1 mini 的 Serial 輸出**加上時間戳**存進 `logs/` 資料夾,方便事後排查(WiFi 連線、HomeKit 配對過程、重置原因都會記下來)。只用 Python 標準函式庫,不需安裝 pyserial。

```bash
./log_serial.py                  # 開始記錄(Ctrl+C 停止),同時顯示在畫面上
./log_serial.py --quiet          # 只寫檔案,不在畫面上顯示
./log_serial.py --port /dev/ttyUSB1
./log_serial.py --stats          # 不記錄,改為分析既有 log 檔
```

- 記錄檔存於 `logs/serial_YYYYMMDD_HHMMSS.log`,每行即時寫入。
- 啟動時會透過 DTR/RTS 觸發板子重置一次,剛好能從頭記錄到開機訊息。
- **執行中會佔用序列埠**,要重新上傳韌體前請先按 Ctrl+C 停止。

## 清除舊配對(找不到裝置時)
若在 iOS 家庭 App 找不到配件,通常是裝置快閃記憶體裡**殘留舊配對資料**——裝置以為自己已配對,不再廣播成可新增的配件(log 會出現 `Found admin pairing ... disabling pair setup`)。此時要清掉舊配對,讓它重新變回未配對狀態。

主程式頂端有一個一次性開關:
```cpp
#define RESET_HOMEKIT_PAIRING  0   // 平時保持 0
```

步驟:
1. 先在 iOS 家庭 App 移除舊配件(若還看得到)。
2. 把 `RESET_HOMEKIT_PAIRING` 改為 `1` → 編譯燒錄 → 開機一次。
   log 會顯示 `*** 清除舊配對資料 ***` 與 `HomeKit: Resetting HomeKit storage`,並產生全新 accessory ID。
3. **把 `RESET_HOMEKIT_PAIRING` 改回 `0` → 再燒錄一次**(這步不可省;維持 1 的話每次開機都會清掉配對,將永遠無法配對成功)。
4. 回家庭 App 重新加入,輸入配對碼 `111-11-111`。

> 驗證是否清乾淨:重開機後 log 應顯示 `Using existing accessory ID`,而**不再**出現 `Resetting` 或 `Found admin pairing`。

## 找不到裝置的排查順序
1. **先確認裝置拿到「正確網段」的 IP**(最重要,見下方 Lesson Learned)。
2. **確認裝置有在廣播**:在同網段的電腦上查 mDNS —— 應看到 `_hap._tcp` 服務、TXT 內含 `sf=1`(未配對、可被發現)。
3. **iPhone 必須和裝置在同一網段**:ESP8266 只支援 2.4GHz;iPhone 若連 5GHz 且路由器把兩頻段切成不同子網,就收不到 mDNS 廣播 → iPhone 改連與裝置同一個 2.4GHz SSID(可在 Wi-Fi (i) 裡確認 IP 是同一網段,例如都是 `192.168.0.x`)。
4. 仍找不到:路由器可能開了 **AP 隔離 / 客戶端隔離** 或擋多播(IGMP snooping),進後台關掉。
5. 訊號太弱(RSSI 低於約 -75 dBm)也會配對不穩,讓裝置靠近路由器再試。

## ⭐ Lesson Learned:DHCP 拿不到 IP → 169.254.x.x 孤島(害配件永遠找不到)

**症狀**:裝置看起來「有連上 WiFi」(WiFi 有關聯、序列埠也印出 IP),但在家庭 App 就是找不到,其他同網段電腦也 ping 不到、掃不到它的 mDNS。

**關鍵判斷**:看它拿到的 IP。若是 **`169.254.x.x`**,那是 **APIPA / link-local 自動私有位址**——代表**第 2 層關聯成功,但 DHCP 沒分配到 IP**,裝置只好自己亂給一個。這種位址在區網裡**完全孤立**:不在 `192.168.0.x` 網段,誰都連不到它,自然無法被發現、無法配對。

> 特別容易發生在「智慧合併頻段(2.4G+5G 同名 SSID)」的路由器上:關聯成功但 DHCP 交握不穩。

**解法:改用靜態 IP,直接繞過不穩的 DHCP。** 本專案已內建開關(主程式頂端):
```cpp
#define USE_STATIC_IP  1
IPAddress staticIP(192, 168, 0, 222);   // 裝置固定 IP(同網段、避開 DHCP 配發範圍)
IPAddress gateway  (192, 168, 0, 1);    // 路由器(閘道)
IPAddress subnet   (255, 255, 255, 0);
IPAddress dns1     (192, 168, 0, 1);
```
- `staticIP` 必須和你家路由器同網段(前三段一樣,如 `192.168.0.x`),最後一段挑一個沒被別的裝置用、也盡量在 DHCP 配發範圍外的號碼。
- `gateway` = 路由器 IP(通常是 `192.168.0.1`,可從手機 Wi-Fi (i) 或路由器後台確認)。
- 設好後裝置每次開機都用固定 IP,連上就穩定可見。要改回 DHCP 把 `USE_STATIC_IP` 設 `0` 即可。

**驗證**:燒錄後在同網段電腦 `ping 192.168.0.222` 應會通,mDNS 也查得到 `sf=1`。

## ⭐ Lesson Learned:本板 I2C 腳位(WeMos D1 R1 ≠ D1 mini)
FQBN 用 `esp8266:esp8266:d1`(WeMos D1 R1)時,`D1`=GPIO1(**UART TX!**)、`D2`=GPIO16,**不能拿來當 I2C**——否則 `Wire.begin()` 會把 TX 腳搶走,序列埠整個沒輸出、開機像當機。I2C 要用板子內建 `SDA`(GPIO4)/ `SCL`(GPIO5) 常數,接到板上標示 SDA/SCL 的腳。

## 燒錄前修改
1. `ssid` / `password` → 你家 2.4GHz Wi-Fi
2. `staticIP` / `gateway` → 改成你家網段(見下方 DHCP 的 Lesson Learned)

## 運作說明
- 三個開關各自獨立,按下(On)就對應腳位輸出一個 PULSE_MS 脈衝,然後自動彈回 Off。
- 不會回報門的實際開/關/移動中狀態,純粹是三顆觸發按鈕。

## WiFi 逾時自動重開機
裝置若**持續連不上 WiFi 超過 15 分鐘**就會自動 `ESP.restart()` 重開機,避免卡在斷線狀態需要手動拔電。涵蓋兩種情況:

- **開機時連不上**:setup 內連續嘗試 15 分鐘仍失敗 → 重開機從頭再試。
- **執行中掉線**:loop 內只要連著就更新時間戳,一旦斷線且連續超過 15 分鐘沒恢復 → 重開機。

重開機前會 `Serial.flush()` 把訊息送完,log 會留下 `[系統] WiFi ... 重新開機...` 方便追查。

門檻由主程式頂端的常數控制,要改分鐘數只需改這一處:
```cpp
#define WIFI_REBOOT_TIMEOUT_MS  (15UL * 60UL * 1000UL)   // 15 分鐘
```

## 加入 Apple 家庭
家庭 App → 加入配件 → 沒有代碼 → 依序加入「開門」「關門」「暫停」三個配件 → 輸入 `111-11-111` → 仍要加入

> 若手機上已經配對過舊版(車庫門控制器)裝置,燒錄新版後請先在家庭 App 移除舊配件,再重新掃描加入,避免快取的配件資料與新結構不一致。
