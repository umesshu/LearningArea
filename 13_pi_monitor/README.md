# 車庫門裝置共用監控(樹莓派端)

同時監控兩台獨立的車庫門裝置，共用**同一個 process、同一個網頁埠**：

- **GARAGE-01**：Wemos D1 R1 + Apple HomeKit（見 `10_GarageHomekit`，開/關/暫停三個開關）
- **GARAGE-02**：ESP32_Relay_AC_X2 + Blynk IoT（見 `12_GarageBlynk`，開/關）

兩台裝置的**控制路徑完全不同**（HomeKit 走 Apple 生態系、Blynk 走雲端 API，互不相干），
但**監控**這件事兩邊都採用同一套做法：韌體額外連一個 Blynk Device（純粹回報用，
不影響原本的控制邏輯），樹莓派輪詢 Blynk Cloud 的 HTTP API 取得狀態。

```
Wemos ── (網際網路) ──┐
                      ├── Blynk Cloud
ESP32 ── (網際網路) ──┘        ▲
                                │ 輪詢 isHardwareConnected /
                                │ get V0(String,格式 "動作:計數器")
                                │
                      樹莓派 server.py
                            ├─ DeviceHub × 2(各自的連線階段 / 操作紀錄)
                            └─ HTTP :8080
                                  ├─ /          合併儀表板
                                  ├─ /events    SSE(每筆事件帶 device 欄位)
                                  └─ /api/state {"garage01":..., "garage02":...}
```

刻意只用 Python 標準函式庫，樹莓派不必安裝任何套件（含打 HTTP 請求都用 `urllib`）。

## 為什麼兩邊都改成輪詢 Blynk Cloud

`10_GarageHomekit` 原本是用 UDP 廣播回報遙測給樹莓派，但這要求兩台裝置在同一個
網段——後來家裡網路架構換過，樹莓派跟 Wemos/ESP32 分處不同網段，UDP 就收不到了。
輪詢 Blynk Cloud 不管樹莓派實際在哪個網路，只要能上網就能監控，不受這個限制。

**重要**：GARAGE-01 連上 Blynk **純粹是為了監控**，跟 HomeKit 控制邏輯完全獨立、
互不影響——韌體裡刻意不用 `Blynk.begin()`（那會搶走 WiFi 連線管理權），也沒有
「連不上 Blynk 就重開機」這種邏輯，Blynk 掛了頂多監控看不到資料，車庫門本身完全
不受影響。詳見 `10_GarageHomekit/ReadMe.md`。

## 安裝與啟動

需要先準備兩個裝置各自的 Blynk Token：複製 `secrets.py.example` 為 `secrets.py`，
分別填入 `10_GarageHomekit/secrets.h` 跟 `12_GarageBlynk/secrets.h` 裡各自的
`BLYNK_AUTH_TOKEN`（兩台裝置是不同的 Token，即使共用同一個 Blynk Template）。

```bash
cd ~/gemini_workspace/LearningArea/13_pi_monitor
cp secrets.py.example secrets.py   # 然後編輯填入兩個 Token
python3 server.py
```

看到 `[HTTP] 儀表板 ...` 就代表起來了，然後用瀏覽器打開
<http://192.168.50.68:8080/>，兩個裝置的區塊會顯示在同一個頁面上。

## 設成開機自動啟動

```bash
sudo cp garage-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now garage-monitor
systemctl status garage-monitor
journalctl -u garage-monitor -f
```

日後修改設定：**改版控裡這一份**，重新 `cp` 過去，再
`sudo systemctl daemon-reload && sudo systemctl restart garage-monitor`。

完全移除：

```bash
sudo systemctl disable --now garage-monitor
sudo rm /etc/systemd/system/garage-monitor.service
sudo systemctl daemon-reload
```

## 參數

```bash
python3 server.py --port 8080 --blynk-poll-interval 10 --bind 0.0.0.0
```

`--blynk-poll-interval` 是打 Blynk Cloud API 的輪詢間隔秒數（預設 10 秒；免費額度
是每裝置每天 50 萬次請求，10 秒一次一天約 8,640 次，兩台裝置合計都還很寬裕）。

## 儀表板內容

兩個裝置區塊格式完全一樣：

| 項目 | 說明 |
|---|---|
| 連線狀態 | 來自 Blynk 的 `isHardwareConnected` |
| 連線階段記錄（最近 50 筆） | 伺服器端從輪詢結果推算：連上算一段開始，`isHardwareConnected` 變 false 就收尾 |
| 操作記錄（最近 50 筆） | 解析 V0 的 `"動作:計數器"` 格式，計數器變化就記一筆。GARAGE-01 多一種 `pause`(暫停) |

**⚠️ 操作紀錄沒有來源裝置/IP**：不管是 iOS 捷徑打 Blynk Cloud，還是 HomeKit App
觸發，都不經過這台樹莓派，兩邊都無法取得原始請求的來源資訊，只記錄「何時發生了什麼」。

## 存取範圍

`server.py` 沒有任何身分驗證，是照「只在區網/Tailscale 內使用」設計的。
`192.168.50.68` 只有連著家裡網路的裝置看得到；要從外面連，用 `setup_tailscale.sh`
裝好 Tailscale 之後，透過私有位址/MagicDNS 存取即可，不要對外開放連接埠轉發。
