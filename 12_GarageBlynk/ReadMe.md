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

| 動作 | Datastream (Virtual Pin) | 資料型態 | 範圍 |
|---|---|---|---|
| 開門 | V0 | Integer | 0~1 |
| 關門 | V1 | Integer | 0~1 |

建立路徑：Blynk Console → Templates → 點進該 Template → Datastreams 分頁 → New/Add Datastream → 選 Virtual Pin → 填上表對應設定 → Create。

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

| 動作 | Virtual Pin | ESP32 GPIO | 輸出方式 |
|---|---|---|---|
| 開門 | V0 | GPIO16 | 板載 Relay 1 |
| 關門 | V1 | GPIO17 | 板載 Relay 2 |

### 觸發方式：HTTP API（不依賴 Blynk App）

```
開：https://blynk.cloud/external/api/update?token={你的Token}&V0=1
關：https://blynk.cloud/external/api/update?token={你的Token}&V1=1
```

可用 iOS 捷徑、自架網頁按鈕、或任何能發 HTTP request 的工具觸發。**Token 等同密碼，不可外流**（放在 `secrets.h`，已加入 `.gitignore`，不要 commit）。

## 執行步驟

- [x] 1. 專案目錄建立、板型號選定（ESP32_Relay_AC_X2 / 303E32AC210）
- [x] 2. 樹莓派上安裝 ESP32 開發板套件（`arduino-cli core install esp32:esp32`）與 Blynk IoT 函式庫（`arduino-cli lib install Blynk`，1.3.5，Blynk IoT 2.0 相容版）
- [x] 3. 撰寫韌體骨架（WiFi 連線、Blynk 連線、2 組 BLYNK_WRITE handler）
- [ ] 4. 註冊 Blynk 帳號並建立 Template
- [ ] 5. 在 Template 中設定 2 個 Datastream（V0/V1，如上表）
- [ ] 6. 建立 Device，取得 Auth Token，填入 `secrets.h`
- [ ] 7. 試編譯/試燒錄（板上無 USB，需外接 6-pin USB-TTL 轉接板）
- [ ] 8. 準備硬體：接線到遙控器 開/關 2 個按鈕觸點（含下拉電阻），焊接前用電表確認極性
- [ ] 9. 用 Blynk App 建立 2 個測試按鈕，先接 LED 測試訊號流程（勿直接接車庫門電路）
- [ ] 10. 確認無誤後，改用 HTTP API（iOS 捷徑/網頁按鈕）作為正式觸發方式，並正式接上車庫門電路
- [ ] 11. 補上斷線自動重連邏輯

## 尚待確認/決定的事項

- [ ] 開/關兩個按鈕觸點的實際接線腳位對應（板子的 GPIO16/GPIO17 已定，車庫遙控器端焊點位置待現場量測）
- [ ] 是否需要車庫門狀態回報（例如加裝感測器回傳目前開/關狀態），目前僅規劃「觸發」單向控制
- [ ] WiFi 斷線重連、裝置離線通知等穩定性細節尚未實作

## 開發環境

開發機就是樹莓派本身（同 `10_GarageHomekit`），不需要 scp 或遠端連線。
