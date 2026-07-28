# 車庫門遙測監控(樹莓派端)

Wemos 把除錯訊息與遙測用 **UDP 廣播**送出，樹莓派收下來，並提供一個網頁儀表板，
手機或電腦連進去就能即時看到車庫門狀態。

```
Wemos D1 R1 (192.168.0.222)
  └─ UDP 廣播 192.168.0.255:5514
        └─ 樹莓派 server.py
              ├─ 環形緩衝(遙測 240 筆 / log 500 筆)
              └─ HTTP :8080  ──  瀏覽器(手機 / 電腦)
                    ├─ /          儀表板
                    ├─ /events    Server-Sent Events 即時推播
                    └─ /api/state 目前狀態快照(JSON)
```

## 為什麼是 UDP 廣播 + SSE

- **UDP**：送出即忘，樹莓派關機也不會阻塞 Wemos。韌體的 `arduino_homekit_loop()`
  不能被卡住，用 TCP（MQTT/HTTP）在對方無回應時可能阻塞數百毫秒，導致 HomeKit 逾時。
- **廣播**：韌體不必寫死樹莓派 IP，樹莓派換 IP 也不用重燒。
- **SSE**：資料只需伺服器→瀏覽器單向流動，SSE 用標準函式庫就能做，且瀏覽器會自動重連。
  **樹莓派完全不需要 `pip install` 任何套件。**

## 安裝

需要 Python 3.7 以上，樹莓派系統內建即可。

本專案的開發機**就是樹莓派本身**（Pi 5，192.168.0.113），所以不必 scp，直接跑：

```bash
cd ~/gemini_workspace/LearningArea/10_GarageHomekit/pi_monitor
python3 server.py
```

看到 `[UDP] 監聽 0.0.0.0:5514` 和 `[HTTP] 儀表板 ...` 兩行就代表起來了，
然後用瀏覽器打開 <http://192.168.0.113:8080/>。

## 設成開機自動啟動

`garage-monitor.service` 裡的路徑與 `User` 已經填好上面那台機器的實際值；
換機器的話記得先改。

```bash
sudo cp garage-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload            # 讓 systemd 重新掃描,不然它不知道多了新檔案
sudo systemctl enable --now garage-monitor
systemctl status garage-monitor
journalctl -u garage-monitor -f         # 看即時輸出
```

日後修改設定：**改版控裡這一份**，再重新 `cp` 過去，然後
`sudo systemctl daemon-reload && sudo systemctl restart garage-monitor`。
不要直接編輯 `/etc/systemd/system/` 底下那份，否則兩邊會走鐘。

完全移除（可逆，不留痕跡）：

```bash
sudo systemctl disable --now garage-monitor
sudo rm /etc/systemd/system/garage-monitor.service
sudo systemctl daemon-reload
```

## 參數

```bash
python3 server.py --port 8080 --udp-port 5514 --bind 0.0.0.0
```

`--udp-port` 必須與韌體裡的 `UDP_LOG_PORT` 一致（預設 5514）。

## 儀表板看得到什麼

| 區塊 | 內容 |
|---|---|
| 頂部狀態列 | 連線狀態、來源 IP、Wemos 運行時間、距離上次封包幾秒、累計封包數 |
| 車庫門位置 | 捲門開合示意圖 + 開啟百分比 + 雷射量測路徑、HomeKit 目標值、校正模式警示 |
| 數值磚 | WiFi 訊號（含強度格）、可用記憶體、量測距離、遙測抵達頻率 |
| 趨勢圖 | 距離與 RSSI 的歷史折線，滑鼠/手指移過去可讀單點數值 |
| 歷史數值表 | 最近 20 筆的原始數值，方便對照 |
| 除錯訊息 | 即時 log 串流，可依 指令 / VL53 / WiFi / 系統 篩選，可暫停自動捲動 |

網頁會跟隨系統的深色／淺色主題，手機直式與電腦寬螢幕都做過排版。

## 存取範圍：目前只限區域網路

`192.168.0.113` 是私有位址，**只有連著家裡 WiFi 的裝置看得到**。手機切到行動網路就連不上。
（HomeKit 配件在外面能用是靠 HomePod 當家庭中樞轉送；這個儀表板不是 HomeKit 配件，沒有人幫它轉送。）

要從外面連，方向有三種：

| 方案 | 網址 | 費用 | 要動路由器嗎 |
|---|---|---|---|
| Tailscale 等 VPN | 私有位址 | 免費（個人） | 不用 |
| Cloudflare Tunnel + 自有網域 | 固定，自動 HTTPS | 網域年費 | 不用 |
| 連接埠轉發 + DDNS | 固定 | 免費 | 要，且會暴露對外 IP |

⚠️ **`server.py` 目前沒有任何身分驗證**，是照「只在區網內使用」設計的。
若要讓它能從公網存取，請先加上驗證機制 —— 否則等於把車庫門的即時狀態、
WiFi 資訊與完整 log 公開廣播。VPN 方案不需要改程式，因為網路層已經限定了來源。

## 疑難排解

**網頁顯示「示範資料」**
表示瀏覽器連不上 `/events`。如果你是直接用檔案總管開 `index.html`，這是正常的
（頁面刻意保留這個模式，方便單機預覽外觀）。若是連到樹莓派卻出現這個，檢查
`server.py` 有沒有在跑、防火牆有沒有擋 8080。

**網頁開得起來但一直「等待資料」**
UDP 沒收到。依序檢查：

1. Wemos 序列埠有沒有印出 `[UDP] 除錯訊息廣播至 192.168.0.255:5514`
2. 兩台是否在**同一個網段**（廣播不會跨路由器，也常被 AP 隔離／訪客網路擋掉）
3. 樹莓派防火牆：`sudo ufw allow 5514/udp`
4. 直接驗證：`sudo tcpdump -i any -n udp port 5514`

**想不靠 Wemos 先測試**
用這行灌一筆假資料進去：

```bash
echo '{"t":"tel","dist":1200,"pos":57,"hkpos":57,"target":57,"state":2,"rssi":-58,"heap":28000,"up":3600,"tof":1,"cal":0}' \
  | socat - UDP-DATAGRAM:127.0.0.1:5514
```

沒有 `socat` 的話用 `nc -u -w1 127.0.0.1 5514`。

## 韌體端對應的設定

`10_GarageHomekit.ino` 裡：

```cpp
#define UDP_LOG_ENABLE       1       // 關成 0 就完全不送 UDP
#define UDP_LOG_PORT         5514    // 要與 server.py 的 --udp-port 一致
#define UDP_TELEMETRY_MS     2000UL  // 每隔多久送一包 JSON 遙測
```

所有原本的 `Serial.printf` 都改成了 `netlogf`，格式字串完全相同，
訊息會**同時**進序列埠與 UDP，序列埠除錯方式一如既往。
