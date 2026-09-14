# 車庫門 HomeKit 控制器 · 三開關版(Wemos D1 R1)

Home App 裡有三個獨立開關「開門」「關門」「暫停」,按下(On)後觸發一次脈衝再自動彈回 Off。

**2026-09-14 更新**:原本還有一個窗簾配件「車庫門」,用 VL53L1X 雷射測距回報門的開啟
百分比——實測這顆感測器裝在這個位置量出來的距離不穩定、不合用,**已整個移除**,包含
`accessary.c` 裡的 Window Covering 服務、校正邏輯、I2C 接線都不需要了,現在只做開/關/暫停。

監控走**輪詢 Blynk Cloud**(另外連一個純監控用的 Blynk Device,跟 HomeKit 控制邏輯
完全獨立、互不影響),取代原本要求兩台裝置同網段的 UDP 廣播(見下方「網頁遠端監控」)。

## 檔案
- `10_GarageHomekit.ino` — 主程式
- `accessary.c` — HomeKit 配件(開門 / 關門 / 暫停三個 Switch)
- `secrets.h` — WiFi 帳密 + Blynk Token(**不進版控**,由 `.gitignore` 排除)
- `secrets.h.example` — 上面那份的範本,第一次使用時複製改名
- `log_serial.py` — 序列埠記錄工具
- `pi_monitor/` — 已被取代,見 `../13_pi_monitor/`

## 電路
| 功能 | Wemos 腳 | GPIO | 接遙控器按鈕 |
|---|---|---|---|
| 開門 | D5 | 14 | 上 |
| 關門 | D6 | 12 | 下 |
| 暫停 | D7 | 13 | 停/開 |

輸出級目前用 **8 路繼電器模組(SRD-05VDC-SL-C,低電位觸發)**,只用其中 3 路:

| 模組腳 | 接到 |
|---|---|
| IN1 / IN2 / IN3 | D5 / D6 / D7 |
| GND(IN 側排針) | GND |
| VCC(IN 側排針) | **3V3** |
| JD-VCC | 5V(線圈電源) |
| GND(JD-VCC 側三針) | GND |

- **拔掉 VCC–JD-VCC 的藍色 jumper**。控制側 VCC 吃 3V3,ESP 輸出的 3.3V 高電位才能完全關斷光耦;若維持 jumper(控制側 5V),3.3V 關不乾淨會讓繼電器抖動或放不掉。
- 每支 IN 腳對 3V3 加 10kΩ **上拉**(低電位觸發:開機瞬間腳位浮接會誤觸發)。
- 每路繼電器的 **COM + NO** 並接遙控器按鈕兩焊點(乾接點,等同按一下);NC 不接。
- 線圈約 70mA/路,3 路約 210mA,USB 供電可承受;要同時吸合更多路請把 JD-VCC 改外接 5V。
- 極性由 `.ino` 的 `RELAY_ACTIVE_LOW` 控制:`1`=繼電器模組(低觸發),`0`=舊的光耦板(高觸發)。
- 「鎖」不接。電源用 USB 或 HLK-5M05

<details>
<summary>舊版:光耦板接法</summary>

- 每路:GPIO → 220Ω → 光耦 IN;光耦 IN 另端 → GND
- 每路 GPIO 對 GND 加 10kΩ 下拉(防漏電 / 開機誤觸)
- 光耦輸出並接遙控器按鈕兩焊點;接遙控器後兩側不共地
- 程式需把 `RELAY_ACTIVE_LOW` 設為 `0`
</details>

## 安裝
```bash
arduino-cli config add board_manager.additional_urls https://arduino.esp8266.com/stable/package_esp8266com_index.json
arduino-cli core update-index
arduino-cli core install esp8266:esp8266
arduino-cli lib install --git-url https://github.com/Mixiaoxiao/Arduino-HomeKit-ESP8266.git
arduino-cli lib install Blynk
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

## 網頁遠端監控(../13_pi_monitor/)
韌體裡的 `netlogf()` 訊息、每 2 秒一包的 JSON 遙測(RSSI / 可用記憶體 / 運行時間)
還是照舊用 UDP 廣播送出(`UDP_LOG_ENABLE`/`UDP_LOG_PORT`/`UDP_TELEMETRY_MS`,見主程式頂端),
但這條路依賴「樹莓派跟裝置在同一個網段」,家裡網路架構換過之後已經不保證收得到。

**正式的監控資料來源改成輪詢 Blynk Cloud**:韌體另外連一個 Blynk Device(純粹回報用,
`secrets.h` 裡的 `BLYNK_AUTH_TOKEN`,**跟控制邏輯完全獨立**——不呼叫 `Blynk.begin()`,
不會因為 Blynk 連不上就重開機或延誤 HomeKit),每次開/關/暫停動作完成後把
`"動作:計數器"` 寫進 Blynk 的 V0(String)腳位。樹莓派輪詢 `isHardwareConnected` 判斷
連線狀態、輪詢 V0 判斷有沒有新操作,這樣不管樹莓派實際在哪個網路都收得到。

儀表板跟 `12_GarageBlynk` 合併成同一個共用的 `../13_pi_monitor/`(同一個 process、
同一個網頁埠 8080),不再各自獨立。安裝、Tailscale 遠端存取、安全性要點都在
[`../13_pi_monitor/README.md`](../13_pi_monitor/README.md)。這個資料夾底下原本
獨立的 `pi_monitor/` 已被取代,留著只是保留歷史。

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

## 燒錄前修改
1. **WiFi 帳密 + Blynk Token** → 複製 `secrets.h.example` 成 `secrets.h`,填入你家
   2.4GHz Wi-Fi 的 SSID/密碼,以及 Blynk Console 那個「Garage HomeKit」Device 的
   Auth Token。`secrets.h` 已被 `.gitignore` 排除,不會進版控;沒有這個檔案編譯會
   失敗(`secrets.h: No such file or directory`),這是預期行為。
2. `staticIP` / `gateway` → 改成你家網段(見下方 DHCP 的 Lesson Learned)

## 運作說明
- 三個開關各自獨立,按下(On)就對應腳位輸出一個 PULSE_MS 脈衝,然後自動彈回 Off。
  脈衝結束由 loop 非阻塞計時處理,不用 `delay()`,避免卡住 HomeKit 迴圈。
- 脈衝結束的同時,把動作(open/close/pause)+ 遞增計數器寫進 Blynk 的 V0,純粹讓
  樹莓派監控用,不影響上面已經完成的動作。

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
家庭 App → 加入配件 → 沒有代碼 → 依序加入「開門」「關門」「車庫門暫停」三個配件
→ 輸入 `111-11-111` → 仍要加入

> 若手機上已經配對過舊版(含窗簾配件的測距版)裝置,燒錄新版後請先在家庭 App 移除舊配件
> (含已經不存在的「車庫門」窗簾配件),再重新掃描加入,避免快取的配件資料與新結構不一致。
