# 車庫門遙控器（ESP32 + Blynk 方案）

> 與 `10_GarageHomekit`（ESP8266 + Apple HomeKit）是**平行、獨立**的專案，不要混合。

## 專案背景

不用 HomeKit，改成 ESP32 + WiFi 直連 Blynk 雲端服務，讓手機可以在任何地方（不限家中 WiFi）遠端觸發車庫門動作。

硬體邏輯沿用 HomeKit 版本：用光耦合模組（PC817）隔離訊號，模擬「按下遙控器按鈕」的動作，觸發車庫遙控器上的觸點焊盤。

**已知重要教訓（沿用自 HomeKit 版）**：觸點訊號腳位要加下拉電阻，避免開機瞬間浮動觸發誤動作。

## 硬體規格：開/關 2 個獨立按鈕

車庫門遙控器上有開、關、暫停 3 個實體按鈕，但本專案**捨棄暫停功能**，只做開/關：

| 動作 | 對應遙控器按鈕 |
|---|---|
| 開門 | 開 按鈕 |
| 關門 | 關 按鈕 |

因此只需要 **2 組獨立通道**，剛好對應下面選用板子的 2 個板載繼電器，不需要額外的光耦合模組。

- 焊接前務必用三用電表確認每組按鈕觸點的正確極性/接點位置，避免焊錯

## 軟體方案：Blynk IoT（免費方案）

選擇 Blynk 作為雲端中繼，理由：ESP32 和手機都是「主動連出去」連上 Blynk.Cloud，不需要對家用路由器做 Port Forwarding，也不受動態 IP 影響。

**Blynk 免費方案額度**（對此專案用量遠遠足夠）：
- 最多 5 個裝置
- 每個 Template 最多 10 個 Datastream
- 每個裝置每天最多 50 萬次 HTTP API 更新請求
- 每個裝置每秒最多 50 次請求

**重要**：要用 **Blynk IoT / Blynk 2.0** 版本的函式庫與 API，不要用舊版 Legacy Blynk，兩者不相容。

### Datastream（虛擬腳位）對應規劃

**2026-09-14 改版**：為了讓「連線狀態」跟「開關操作記錄」不管樹莓派在哪個網路都查得到（見下方樹莓派端監控），把 V0/V1 合併成單一 String 腳位，V0/V1（Integer 版）已刪除：

| Datastream (Virtual Pin) | 資料型態 | 方向 | 內容 |
|---|---|---|---|
| V0 | **String** | 外部 → 裝置（觸發） | `"open"` 或 `"close"` |
| V0 | **String** | 裝置 → 雲端（回報） | `"open:N"` / `"close:N"`，N 是每次動作遞增的計數器，裝置執行完動作後自己 `Blynk.virtualWrite()` 寫回 |

原理：裝置自己 `virtualWrite()` 不會觸發自己的 `BLYNK_WRITE`（只有外部 App/API 寫入才會），所以同一個腳位可以雙向使用，不會無窮迴圈。連線狀態不佔用任何腳位，改查獨立的 `isHardwareConnected` API（見下方）。

**⚠️ 除錯教訓**：中間一度以為 String 型態在 Blynk 後端會被誤判成數值型態（送字串一律 400 "Value doesn't match the Datastream data type"，懷疑是平台 bug），花了不少力氣排查、甚至規劃改回 Integer 方案。**真正原因其實是 Blynk Console 編輯完 Datastream 之後要按「Save and Apply」才會真的套用**——光是編輯畫面顯示改好了不代表後端生效，難怪怎麼改都沒用。按下 Save and Apply 之後，最一開始的單一 String 腳位設計完全正常，不需要退回 Integer。**改完 Datastream 設定務必記得按 Save and Apply。**

建立路徑：Blynk Console → Templates → 點進該 Template → Datastreams 分頁 → New/Add Datastream → 選 Virtual Pin → 填上表對應設定 → Create → **Save and Apply**。

### 硬體對應規劃

選用板子：**ESP32_Relay_AC_X2（型號 303E32AC210）**，一塊整合 ESP32-WROOM-32E、AC 電源轉換、2 個板載繼電器的成品板（SZHJW/LC Technology 系列常見於 AliExpress，命名規則類似 `303E32AC111` 為單通道 AC 版、`210` 為雙通道版本）。

板載腳位（多方 ESPHome/Cirkit 文件交叉確認一致）：

| 功能 | GPIO |
|---|---|
| 板載 Relay 1 | GPIO16 |
| 板載 Relay 2 | GPIO17 |
| 板載按鈕 | GPIO0 |
| 板載狀態 LED | GPIO23（低電位觸發） |

電源：90-250VAC 輸入，板上內建變壓/整流，直接輸出穩壓後電源給 ESP32，不需另外接 5V。
Relay 規格：每組 COM/NO/NC 端子，額定最大 10A（250VAC/30VDC）。
燒錄：板上無 USB，需外接 6-pin 排針的 USB-TTL 轉接板（TX/RX 為 3.3V 邏輯），進燒錄模式需將 GPIO0 拉到 GND。

捨棄暫停功能後，板載 2 個繼電器剛好對應開/關，不需要額外的光耦合模組：

| 動作 | V0 內容 | ESP32 GPIO | 輸出方式 |
|---|---|---|---|
| 開門 | 寫入 `"open"` | GPIO16 | 板載 Relay 1 |
| 關門 | 寫入 `"close"` | GPIO17 | 板載 Relay 2 |

### 觸發方式：HTTP API，用 iOS 捷徑發送（已決定）

```
開：https://blynk.cloud/external/api/update?token={你的Token}&V0=open
關：https://blynk.cloud/external/api/update?token={你的Token}&V0=close
```

**採用 iOS 捷徑 App**，各建一個捷徑、加「取得URL內容」(GET) 動作打上面的網址。捷徑內容只存在自己手機本機（頂多 iCloud 同步到自己其他裝置），Token 不會外流。

**⚠️ 已評估過、決定捨棄「自架 HTML 網頁按鈕」方案**：Token 這種需要保密的字串只要寫進會送到瀏覽器執行的 HTML/JS，就等於半公開——view-source、開發者工具都能直接複製走，就算放在私有 GitHub repo，GitHub Pages 免費方案也要求 repo 公開，一樣會外流。如果之後真的想要網頁介面，正確做法是「後端代理」：網頁呼叫自己樹莓派上的服務，Token 留在伺服器端（例如比照 `10_GarageHomekit/pi_monitor`，只透過 Tailscale 存取），網頁本身完全不含 Token。

## 執行步驟

- [x] 1. 專案目錄建立、板型號選定（ESP32_Relay_AC_X2 / 303E32AC210）
- [x] 2. 樹莓派上安裝 ESP32 開發板套件（`arduino-cli core install esp32:esp32`）與 Blynk IoT 函式庫（`arduino-cli lib install Blynk`，1.3.5，Blynk IoT 2.0 相容版）
- [x] 3. 撰寫韌體骨架（WiFi 連線、Blynk 連線、2 組 BLYNK_WRITE handler）
- [x] 4. 註冊 Blynk 帳號並建立 Template「Garage Door」
- [x] 5. 在 Template 中設定 2 個 Datastream（V0/V1，如上表）
- [x] 6. 建立 Device，取得 Auth Token，填入 `secrets.h`
- [x] 7. 試編譯/試燒錄（用 blkbox BB-CHB USB-TTL 轉接板，`arduino-cli upload` 燒錄成功）
- [x] 7.5 用 HTTP API（curl）直接測試 V0/V1，板載 Relay 1/2 皆正常動作（喀一聲）
- [x] 11. 補上斷線自動重連邏輯（WiFi/Blynk 任一斷線累積超過 10 分鐘即 `ESP.restart()`，開機階段也受同一個逾時保護）
- [ ] 8. 準備硬體：接線到遙控器 開/關 2 個按鈕觸點（含下拉電阻），焊接前用電表確認極性
- [ ] 9. 用 Blynk App 建立 2 個測試按鈕，先接 LED 測試訊號流程（勿直接接車庫門電路）—— 目前用 HTTP API 直測已跳過此步
- [ ] 10. 確認無誤後，改用 HTTP API（iOS 捷徑/網頁按鈕）作為正式觸發方式，並正式接上車庫門電路
- [ ] 12. 板子改回 AC 供電做長期穩定性測試（目前用 USB 供電測試）
- [x] 13. 樹莓派端監控：連線狀態、連線階段記錄、開/關操作記錄，已跟 `10_GarageHomekit` 整合成同一個共用監控（見下方）

## 樹莓派端監控

跟 `10_GarageHomekit` 共用同一套監控，見 [`../13_pi_monitor/README.md`](../13_pi_monitor/README.md)（同一個 process、同一個網頁埠 8080，兩個裝置分區塊顯示，不用另外佔用 port）。`12_GarageBlynk/pi_monitor/` 底下原本獨立的那份已經**被取代**，留著只是保留歷史，之後會整個刪除。

**2026-09-14 改版——不再用本地 UDP，改走 Blynk Cloud 輪詢**：原本韌體會用 UDP 送心跳/操作事件給樹莓派，但這要求兩台裝置在同一個網段，樹莓派換了網路（或只是換了 IP）就收不到。現在改成：

- 連線狀態：樹莓派定時打 Blynk 的 `isHardwareConnected` API 查詢，不需要韌體額外做任何事
- 操作紀錄：韌體執行完開/關動作後，把 `"open:N"` / `"close:N"`（N 是遞增計數器）寫回 V0，樹莓派輪詢 V0 偵測計數器變化來判斷「有新操作發生」

好處是不管樹莓派實際在哪個網路，只要能上網就能監控到；代價是**操作紀錄的時間戳記精確度受輪詢間隔影響**（預設 10 秒輪詢一次），且一樣**沒有來源裝置/IP**（跟之前一樣，觸發不經過樹莓派，技術上做不到）。


## 尚待確認/決定的事項

- [ ] 開/關兩個按鈕觸點的實際接線腳位對應（板子的 GPIO16/GPIO17 已定，車庫遙控器端焊點位置待現場量測）
- [ ] 是否需要車庫門狀態回報（例如加裝感測器回傳目前開/關狀態），目前僅規劃「觸發」單向控制

## 開發環境

開發機就是樹莓派本身（同 `10_GarageHomekit`），不需要 scp 或遠端連線。
