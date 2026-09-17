#!/usr/bin/env python3
"""兩台車庫門裝置的共用監控(樹莓派端)。

  - GARAGE-01:10_GarageHomekit(Wemos + HomeKit,開/關/暫停三個開關)
  - GARAGE-02:12_GarageBlynk(ESP32_Relay_AC_X2 + Blynk,開/關)

兩台裝置各自的控制路徑完全不同(HomeKit 走 Apple 生態系、Blynk 走雲端 API),
但監控這件事兩邊都改成**輪詢 Blynk Cloud**,用同一套邏輯:

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

為什麼兩邊都改成輪詢雲端 API,而不是本地 UDP:UDP 廣播/單播都要求兩台裝置在
同一個網段,樹莓派換了網路(甚至只是換了 IP)就收不到了。輪詢 Blynk Cloud
不管樹莓派實際在哪個網路都能用 —— 只要能上網。GARAGE-01 原本走 UDP 廣播,
現在也一併改成跟 GARAGE-02 一樣的做法,不再需要本地網路 UDP 監聽。

刻意只用 Python 標準函式庫,樹莓派不必安裝任何套件(含 HTTP 請求都用 urllib)。
"""

import argparse
import json
import queue
import re
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parent / "static"


class Bus:
    """所有裝置共用的推播管道:一個網頁連線,同時收多個裝置的事件。"""

    def __init__(self):
        self.lock = threading.Lock()
        self.subscribers = []          # list[queue.Queue]

    def subscribe(self):
        q = queue.Queue(maxsize=400)
        with self.lock:
            self.subscribers.append(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            if q in self.subscribers:
                self.subscribers.remove(q)

    def broadcast(self, event):
        payload = json.dumps(event, ensure_ascii=False)
        with self.lock:
            targets = list(self.subscribers)
        for q in targets:
            try:
                q.put_nowait(payload)
            except queue.Full:
                pass               # 這個瀏覽器跟不上,丟掉這筆比拖垮全部好


BUS = Bus()


# ============================================================
# 裝置監控狀態:連線階段記錄 + 開/關/暫停操作紀錄
# 資料來源是輪詢 Blynk Cloud API,不是本地封包,所以沒有「src IP」這種欄位
# ============================================================
MAX_SESSIONS = 50
MAX_OPS = 50
OFFLINE_AFTER_S = 30   # 輪詢間隔的安全網,正常情況下由 isHardwareConnected 直接判定


class DeviceHub:
    """一台裝置的監控狀態。GARAGE-01/GARAGE-02 各自一個實例,邏輯完全共用。"""

    def __init__(self, device_key):
        self.device_key = device_key   # 事件裡的 "device" 欄位,例如 "garage01"
        self.lock = threading.RLock()  # 可重入:snapshot() 會在持鎖時呼叫 is_online()
        self.sessions = deque(maxlen=MAX_SESSIONS)
        self.ops = deque(maxlen=MAX_OPS)
        self.last_seen = 0.0
        self.session_start = None
        self.polls = 0

    def _close_session_locked(self, end_ts):
        if self.session_start is not None:
            self.sessions.append({
                "start": self.session_start,
                "end": end_ts,
                "duration": max(0.0, end_ts - self.session_start),
            })
            self.session_start = None

    def mark_connected(self):
        """輪詢到 isHardwareConnected=true 時呼叫,開新的一段(如果原本是離線的話)。"""
        now = time.time()
        with self.lock:
            if self.session_start is None:
                self.session_start = now
            self.last_seen = now
            self.polls += 1
        BUS.broadcast({"type": "hb", "device": self.device_key, "data": {"ts": now}})

    def mark_disconnected(self):
        """輪詢到 isHardwareConnected=false 時呼叫,立刻收尾這一段(不用等逾時)。"""
        now = time.time()
        with self.lock:
            self.polls += 1
            went_offline = self.session_start is not None
            if went_offline:
                self._close_session_locked(now)
        if went_offline:
            BUS.broadcast({"type": "offline", "device": self.device_key})

    def add_op(self, action, operator=""):
        now = time.time()
        entry = {"ts": now, "action": action, "operator": operator}
        with self.lock:
            self.ops.append(entry)
        BUS.broadcast({"type": "op", "device": self.device_key, "data": entry})

    def check_timeout(self):
        """輪詢本身失敗(例如樹莓派沒網路)太久的安全網,免得畫面卡在「線上」不動。"""
        now = time.time()
        with self.lock:
            if self.session_start is not None and self.last_seen and (now - self.last_seen) > OFFLINE_AFTER_S:
                self._close_session_locked(self.last_seen)
                went_offline = True
            else:
                went_offline = False
        if went_offline:
            BUS.broadcast({"type": "offline", "device": self.device_key})

    def is_online(self):
        with self.lock:
            return self.session_start is not None

    def snapshot(self):
        with self.lock:
            return {
                "online": self.is_online(),
                "last_seen": self.last_seen,
                "sessions": list(self.sessions),
                "ops": list(self.ops),
                "polls": self.polls,
                "offline_after": OFFLINE_AFTER_S,
            }


GARAGE01 = DeviceHub("garage01")   # 10_GarageHomekit(Wemos + HomeKit)
GARAGE02 = DeviceHub("garage02")   # 12_GarageBlynk(ESP32 + Blynk)


def combined_snapshot():
    return {
        "type": "snapshot",
        "garage01": GARAGE01.snapshot(),
        "garage02": GARAGE02.snapshot(),
        "server_time": time.time(),
    }


# ============================================================
# 輪詢 Blynk Cloud:兩台裝置共用同一套邏輯,只是 token/hub 不同
# ============================================================
BLYNK_API_BASE = "https://blynk.cloud/external/api"
# 韌體端 V0 回報的是「最近幾筆」歷史,逗號分隔、新的在前,例如:
#   "12:close:iPhone15,11:open:,10:close:iPad"
# 操作者欄位是後來加的,用 (?:...)? 包成可選,舊格式("12:close",沒有操作者)
# 一樣解得出來,只是 operator 抓到空字串。用 findall 抓出全部,不要求整串只有一筆。
BLYNK_OP_RE = re.compile(r"(\d+):(open|close|pause)(?::([^,]*))?")


def _blynk_api_get(path_and_query, timeout=6):
    """打一次 Blynk HTTP API,回傳回應文字;失敗回傳 None(呼叫端自己決定怎麼處理)。"""
    url = f"{BLYNK_API_BASE}/{path_and_query}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace").strip()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"[Blynk] API 請求失敗({path_and_query}):{exc}")
        return None


def _parse_blynk_get_value(raw):
    """Blynk 的 get API 回傳格式是 JSON 陣列,例如 ["open:7"];解不開就當原始字串用。"""
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list) and parsed:
            return str(parsed[0])
    except json.JSONDecodeError:
        pass
    return raw.strip().strip('"')


def blynk_poll_loop(hub, token, interval_s):
    """定時輪詢 Blynk Cloud:isHardwareConnected 判斷連線狀態,V0 判斷有沒有新操作。

    V0 是韌體的單一 String 腳位,裝置端維護「最近幾筆」歷史(逗號分隔、新的在
    前,例如 "12:close:iPhone15,11:open:,10:close:iPad")而不是只回報最新
    一筆,這樣就算輪詢間隔內連續操作好幾次,一次輪詢也能把中間漏掉的全部補
    回來,不會漏記。第三個欄位(操作者)是選填,舊格式沒有這欄也解得出來。

    做法:每次輪詢把整串解析成 (counter, action, operator) 列表,只處理
    counter 比上次看過的最大值還大的那些,依 counter 由小到大依序記錄,
    確保操作順序正確。若新一批的最大 counter 反而比上次還小(裝置重開機、
    counter 歸零重算),视為全部都是新事件,重新從這批開始追蹤。
    """
    last_op_counter = None   # 記住上次看過、已經處理過的最大 counter

    while True:
        connected_raw = _blynk_api_get(f"isHardwareConnected?token={token}")
        if connected_raw is not None:
            if connected_raw.strip().lower() == "true":
                hub.mark_connected()

                v0_raw = _blynk_api_get(f"get?token={token}&v0")
                if v0_raw is not None:
                    value = _parse_blynk_get_value(v0_raw)
                    entries = sorted(
                        ((int(c), a, op or "") for c, a, op in BLYNK_OP_RE.findall(value)),
                        key=lambda triple: triple[0],
                    )
                    if entries:
                        newest_counter = entries[-1][0]
                        if last_op_counter is None:
                            pass   # 開機第一次看到,只記基準,不補報「開機前」的歷史
                        elif newest_counter < last_op_counter:
                            for counter, action, operator in entries:   # 裝置重開機,counter 歸零重算
                                hub.add_op(action, operator)
                        else:
                            for counter, action, operator in entries:
                                if counter > last_op_counter:
                                    hub.add_op(action, operator)
                        last_op_counter = newest_counter
            else:
                hub.mark_disconnected()

        time.sleep(interval_s)


def timeout_watcher():
    """每秒巡一次,讓「目前是否連線」在輪詢本身失敗太久時也會反映出來。"""
    while True:
        GARAGE01.check_timeout()
        GARAGE02.check_timeout()
        time.sleep(1)


# ============================================================
# HTTP:合併儀表板 + SSE + JSON 快照
# ============================================================
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "GarageMonitor/3.0"

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._serve_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
        elif path == "/api/state":
            self._serve_json(combined_snapshot())
        elif path == "/events":
            self._serve_events()
        else:
            self.send_error(404, "Not Found")

    def _serve_file(self, path, content_type):
        try:
            body = path.read_bytes()
        except OSError:
            self.send_error(404, f"找不到 {path.name}")
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_json(self, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_events(self):
        q = BUS.subscribe()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            self._send_sse(json.dumps(combined_snapshot(), ensure_ascii=False))
            while True:
                try:
                    payload = q.get(timeout=5)
                    self._send_sse(payload)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            BUS.unsubscribe(q)

    def _send_sse(self, payload):
        self.wfile.write(b"data: " + payload.encode("utf-8") + b"\n\n")
        self.wfile.flush()


def load_blynk_tokens():
    """從 secrets.py 讀兩個裝置各自的 Token(範本見 secrets.py.example),故意不進版控。"""
    try:
        import secrets as _secrets_module  # 這個資料夾自己的 secrets.py,不是標準庫的 secrets
        token_g01 = getattr(_secrets_module, "BLYNK_TOKEN_GARAGE01", "").strip()
        token_g02 = getattr(_secrets_module, "BLYNK_TOKEN_GARAGE02", "").strip()
        if not token_g01 or not token_g02:
            raise AttributeError("BLYNK_TOKEN_GARAGE01 或 BLYNK_TOKEN_GARAGE02 是空的")
        return token_g01, token_g02
    except Exception as exc:
        print(f"[錯誤] 讀不到 Blynk Token:{exc}")
        print("請複製 secrets.py.example 為 secrets.py,分別填入兩個裝置各自的 BLYNK_AUTH_TOKEN")
        print("(10_GarageHomekit/secrets.h 跟 12_GarageBlynk/secrets.h 裡各自的那個)")
        raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser(description="車庫門裝置共用監控收集器")
    parser.add_argument("--blynk-poll-interval", type=int, default=10,
                        help="輪詢 Blynk Cloud API 的間隔秒數(2 支 API/次,一天約 1.7 萬次/裝置,遠低於 Blynk 免費額度 50 萬次/裝置/天;10 秒內操作歷史緩衝區還吃得下 8 筆,夠用)")
    parser.add_argument("--port", type=int, default=8080, help="網頁埠")
    parser.add_argument("--bind", default="0.0.0.0", help="網頁監聽位址")
    args = parser.parse_args()

    token_g01, token_g02 = load_blynk_tokens()

    threading.Thread(target=blynk_poll_loop, args=(GARAGE01, token_g01, args.blynk_poll_interval), daemon=True).start()
    threading.Thread(target=blynk_poll_loop, args=(GARAGE02, token_g02, args.blynk_poll_interval), daemon=True).start()
    threading.Thread(target=timeout_watcher, daemon=True).start()

    httpd = ThreadingHTTPServer((args.bind, args.port), Handler)
    httpd.daemon_threads = True
    print(f"[HTTP] 儀表板 http://{args.bind}:{args.port}/")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[系統] 收到 Ctrl-C,結束")


if __name__ == "__main__":
    main()
