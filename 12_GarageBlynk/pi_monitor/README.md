# 車庫門 Blynk 監控（樹莓派端）

> ⚠️ **已被取代**：這份監控已經整合進 [`../../13_pi_monitor/`](../../13_pi_monitor/README.md)，
> 跟 `10_GarageHomekit` 的裝置合併成同一個 process、同一個網頁埠(8080)一起顯示，
> 不用再另外佔用 8081。這份還沒裝過系統服務，可以直接改用新的那份，
> 這裡的檔案之後可以整個刪除。

跟 `10_GarageHomekit/pi_monitor` 是同一套「UDP 廣播 + SSE 網頁」設計理念，但監控的是
另一台獨立的裝置（ESP32 + Blynk），資料型態也不同，所以獨立一份、用不同的埠，
兩邊完全互不干擾、可以同時跑。

```
ESP32_Relay_AC_X2 (WiFi)
  └─ UDP 廣播 <本機廣播位址>:5515
        └─ 樹莓派 server.py
              ├─ 連線階段記錄(最近 50 筆，逾時 15 秒沒心跳視為離線)
              ├─ 開/關操作記錄(最近 50 筆)
              └─ HTTP :8081 ──  瀏覽器(手機 / 電腦)
                    ├─ /          儀表板
                    ├─ /events    Server-Sent Events 即時推播
                    └─ /api/state 目前狀態快照(JSON)
```

## 監控項目

| 項目 | 怎麼來的 |
|---|---|
| 目前是否連線 | ESP32 每 5 秒送一次心跳(UDP 廣播)，超過 15 秒沒收到就判定離線 |
| 連線階段記錄（最近 50 筆） | 伺服器端從心跳的時間間隔推算：心跳正常視為同一段，逾時就把上一段收尾，下一個心跳進來開新的一段 |
| 開/關操作記錄（最近 50 筆） | ESP32 在 `BLYNK_WRITE(V0)`/`BLYNK_WRITE(V1)` 執行完 `pulseGPIO` 後，額外送一個 `{"type":"op","action":"open"/"close"}` 封包過來 |

## ⚠️ 已知限制：操作紀錄沒有來源裝置/IP

觸發流程是「iOS 捷徑 → 直接打 Blynk Cloud → ESP32」，**不經過這台樹莓派**。
Blynk Cloud 把指令轉發給 ESP32 時，不會夾帶原始 HTTP 請求的來源 IP／裝置資訊，
ESP32 自己也無從得知是誰觸發的。所以操作紀錄只有「什麼時候發生了開/關」，
沒有「誰觸發的」。

如果之後真的需要這項資訊，唯一做法是把觸發路徑改成先經過樹莓派（樹莓派收到請求、
記下來源 IP，再用存在本機的 Token 轉發給 Blynk），但這樣一來樹莓派沒開機/斷線時
就不能觸發車庫門了，是刻意用「觸發可靠性」換來的取捨，目前決定維持現況。

## 安裝與啟動

跟 `10_GarageHomekit/pi_monitor` 一樣，只用 Python 標準函式庫，不必 `pip install`。

```bash
cd ~/gemini_workspace/LearningArea/12_GarageBlynk/pi_monitor
python3 server.py
```

看到 `[UDP] 監聽 0.0.0.0:5515` 和 `[HTTP] 儀表板 ...` 兩行就代表起來了，
然後用瀏覽器打開 <http://192.168.0.113:8081/>（跟既有的 HomeKit 監控 8080 不衝突）。

## 設成開機自動啟動

```bash
sudo cp garage-blynk-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now garage-blynk-monitor
systemctl status garage-blynk-monitor
journalctl -u garage-blynk-monitor -f
```

日後修改設定：**改版控裡這一份**，重新 `cp` 過去，再
`sudo systemctl daemon-reload && sudo systemctl restart garage-blynk-monitor`。

完全移除：

```bash
sudo systemctl disable --now garage-blynk-monitor
sudo rm /etc/systemd/system/garage-blynk-monitor.service
sudo systemctl daemon-reload
```

## 參數

```bash
python3 server.py --port 8081 --udp-port 5515 --bind 0.0.0.0
```

`--udp-port` 必須與韌體裡的 `MONITOR_UDP_PORT` 一致（預設 5515）。

## 存取範圍

跟既有監控一樣，`server.py` 沒有身分驗證，是照「只在區網/Tailscale 內使用」設計的。
`192.168.0.113` 只有連著家裡 WiFi 的裝置看得到；要從外面連，用既有已設定好的
Tailscale（見 `10_GarageHomekit/pi_monitor/setup_tailscale.sh`），不要對外開放連接埠轉發。
