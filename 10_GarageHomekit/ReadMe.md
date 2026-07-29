# 車庫門 HomeKit 控制器 · 測距版(Wemos D1 R1)

Home App 裡有四個配件:三個獨立開關「開門」「關門」「暫停」,按下(On)後觸發一次脈衝再自動彈回 Off;
外加一個窗簾配件「車庫門」,用 VL53L1X 雷射測距回報門的開啟百分比,也可以拖滑桿下指令。

另外所有除錯訊息會用 UDP 廣播送到樹莓派,可以用手機或電腦開網頁看即時狀態(見 `pi_monitor/`)。

## 檔案
- `10_GarageHomekit.ino` — 主程式
- `accessary.c` — HomeKit 配件(開門 / 關門 / 暫停三個 Switch + 車庫門 Window Covering)
- `secrets.h` — WiFi 帳密(**不進版控**,由 `.gitignore` 排除)
- `secrets.h.example` — 上面那份的範本,第一次使用時複製改名
- `log_serial.py` — 序列埠記錄工具
- `pi_monitor/` — 樹莓派端的遙測收集器與網頁儀表板,含 Tailscale 遠端存取(見該資料夾的 README)

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

### VL53L1X 測距感測器(I2C)
| VL53L1X | Wemos |
|---|---|
| VIN | 3V3 |
| GND | GND |
| SDA | 板上標示 `SDA`(GPIO4) |
| SCL | 板上標示 `SCL`(GPIO5) |

⚠️ **不要用 D1 / D2 當 I2C**,本板 `D1`=GPIO1 是 UART TX,詳見下方 Lesson Learned。

## 安裝
```bash
arduino-cli config add board_manager.additional_urls https://arduino.esp8266.com/stable/package_esp8266com_index.json
arduino-cli core update-index
arduino-cli core install esp8266:esp8266
arduino-cli lib install --git-url https://github.com/Mixiaoxiao/Arduino-HomeKit-ESP8266.git
arduino-cli lib install VL53L1X
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

## 網頁遠端監控(pi_monitor/)
韌體裡所有 `netlogf()` 的訊息會**同時**進序列埠與 UDP 廣播,另外每 2 秒送一包 JSON 遙測
(距離 / 位置 / 狀態 / RSSI / 可用記憶體 / 運行時間)。樹莓派收下來後提供一個網頁儀表板,
手機或電腦連進去就能即時看門的狀態、趨勢圖與 log 串流。

```cpp
#define UDP_LOG_ENABLE       1       // 關成 0 就完全不送 UDP
#define UDP_LOG_PORT         5514    // 要與 server.py 的 --udp-port 一致
#define UDP_TELEMETRY_MS     2000UL  // 每隔多久送一包 JSON 遙測
```

用 UDP 而不是 MQTT / HTTP,是因為 `arduino_homekit_loop()` 不能被阻塞 ——
UDP 送出即忘,樹莓派關機也不會拖慢韌體;TCP 在對方無回應時可能卡住數百毫秒導致 HomeKit 逾時。
廣播位址由 IP 與遮罩自動算出,樹莓派換 IP 不必重燒韌體。

儀表板預設只在區域網路內看得到。要從外面連，`pi_monitor/setup_tailscale.sh` 會把
Tailscale 裝起來，手機在行動網路下也能開 —— 不需要動路由器，也不會把服務暴露在公網上。

⚠️ 儀表板**沒有身分驗證**,是照「只在自己的網路內使用」設計的。
不要用 `tailscale funnel` 或連接埠轉發把它開到公網,除非先加上密碼。
安全性要點見 [`pi_monitor/README.md`](pi_monitor/README.md#安全性要點)。

安裝與疑難排解見 [`pi_monitor/README.md`](pi_monitor/README.md)。

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
1. **WiFi 帳密** → 複製 `secrets.h.example` 成 `secrets.h`,填入你家 2.4GHz Wi-Fi 的 SSID 與密碼。
   `secrets.h` 已被 `.gitignore` 排除,不會進版控;沒有這個檔案編譯會失敗
   (`secrets.h: No such file or directory`),這是預期行為。
2. `staticIP` / `gateway` → 改成你家網段(見下方 DHCP 的 Lesson Learned)
3. `DIST_CLOSED_MM` / `DIST_OPEN_MM` → 實測後填入(見下方校正步驟)

## 距離校正(第一次安裝必做)
窗簾配件的百分比是用「門全關」與「門全開」兩端的距離線性內插算出來的,兩個數字沒填對就不準。
兩種安裝方向都支援,`DIST_OPEN_MM` 比 `DIST_CLOSED_MM` 大或小都可以。

1. 把 `VL53_CALIBRATION` 設為 `1` → 編譯燒錄。此時**只印距離,不更新 HomeKit 位置**。
2. 手動把門全關,記下距離;再全開,記下距離。
   (看網頁儀表板的「距離趨勢」圖比盯序列埠方便,而且校正模式下頁面會跳出紅色標籤提醒。)
3. 把兩個數字填回:
   ```cpp
   #define DIST_CLOSED_MM    400      // 門全關時的距離
   #define DIST_OPEN_MM      2000     // 門全開時的距離
   ```
4. **把 `VL53_CALIBRATION` 改回 `0` → 再燒錄一次**(不改回去的話 HomeKit 位置永遠是 0%)。

## 運作說明
- 三個開關各自獨立,按下(On)就對應腳位輸出一個 PULSE_MS 脈衝,然後自動彈回 Off。
  脈衝結束由 loop 非阻塞計時處理,不用 `delay()`,避免卡住 HomeKit 迴圈。
- 窗簾配件每 500ms 讀一次 VL53L1X,換算成 0~100% 回報目前位置與開合狀態
  (開啟中 / 關閉中 / 停止),位置變化超過 2% 才更新以去抖動。
- 拖動滑桿設定目標位置時:目標比目前高 → 觸發開門,比目前低 → 觸發關門。
  車庫門無法精準停在中間,所以這是「往哪個方向動」而非真正的定位控制。

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
家庭 App → 加入配件 → 沒有代碼 → 依序加入「開門」「關門」「車庫門暫停」「車庫門」四個配件
→ 輸入 `111-11-111` → 仍要加入

前三個是觸發用的開關,第四個「車庫門」是窗簾配件,會顯示開啟百分比。

> 若手機上已經配對過舊版(車庫門控制器)裝置,燒錄新版後請先在家庭 App 移除舊配件,再重新掃描加入,避免快取的配件資料與新結構不一致。
