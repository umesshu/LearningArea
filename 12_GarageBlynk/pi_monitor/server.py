#!/usr/bin/env python3
"""ESP32+Blynk 車庫門監控(樹莓派端)。

跟 10_GarageHomekit/pi_monitor 是同一套「UDP 廣播 + SSE 網頁」設計理念，
但資料型態不同，所以獨立一份，用不同的 UDP/HTTP 埠,兩邊互不干擾:

    ESP32 (WiFi 廣播)
      └─ UDP 廣播 <本機廣播位址>:5515
            └─ 樹莓派 server.py
                  ├─ 連線階段記錄(最近 50 次)
                  ├─ 開/關操作記錄(最近 50 次)
                  └─ HTTP :8081 ── 瀏覽器
                        ├─ /          儀表板
                        ├─ /events    Server-Sent Events 即時推播
                        └─ /api/state 目前狀態快照(JSON)

「連線階段」是伺服器端從心跳封包的時間間隔推算出來的:心跳正常間隔內視為
同一階段;超過 OFFLINE_AFTER_S 沒收到心跳,就把上一階段收尾、視為離線,
下一個心跳進來時開新的一段。ESP32 不需要,也做不到,主動送「我要斷線了」
的封包(斷電/斷網當下不會有機會送),所以完全交給伺服器端用逾時去推算。

操作紀錄只有時間與動作(open/close),**沒有來源裝置/IP**——因為觸發是
iOS 捷徑直接打 Blynk Cloud,不經過這台樹莓派,ESP32 收到 Blynk 轉發的
指令時也无従得知原始發request的來源,這是目前架構下的已知限制。

刻意只用 Python 標準函式庫,樹莓派不必安裝任何套件。
"""

import argparse
import json
import queue
import socket
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parent / "static"

MAX_SESSIONS = 50
MAX_OPS = 50

# 心跳每 5 秒送一次(見韌體 HEARTBEAT_INTERVAL_MS);超過這麼久沒收到就視為離線
OFFLINE_AFTER_S = 15


class Hub:
    """所有狀態的單一存放處,並負責把新事件推播給每個網頁連線。"""

    def __init__(self):
        self.lock = threading.RLock()  # 可重入:snapshot() 會在持鎖時呼叫 is_online()
        self.sessions = deque(maxlen=MAX_SESSIONS)   # 已結束的連線階段
        self.ops = deque(maxlen=MAX_OPS)             # 開/關操作紀錄
        self.latest_hb = None       # 最新一筆心跳內容(rssi/ip/uptime)
        self.last_seen = 0.0        # 最近一次收到「任何」封包的時間
        self.session_start = None   # 目前這段連線的起點;None 代表目前判定離線
        self.packets = 0
        self.subscribers = []       # list[queue.Queue]

    # ---- 訂閱 / 退訂 ----
    def subscribe(self):
        q = queue.Queue(maxsize=200)
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

    # ---- 內部:結束目前連線階段(呼叫端要自己拿著 lock)----
    def _close_session_locked(self, end_ts):
        if self.session_start is not None:
            self.sessions.append({
                "start": self.session_start,
                "end": end_ts,
                "duration": max(0.0, end_ts - self.session_start),
            })
            self.session_start = None

    # ---- 收到心跳 ----
    def add_heartbeat(self, data, src):
        now = time.time()
        with self.lock:
            gap = (now - self.last_seen) if self.last_seen else None
            if self.session_start is None or (gap is not None and gap > OFFLINE_AFTER_S):
                # 上一段已經逾時斷線在先(或這是第一次收到),先收尾再開新的一段
                if self.session_start is not None:
                    self._close_session_locked(self.last_seen)
                self.session_start = now
            self.latest_hb = {**data, "src": src}
            self.last_seen = now
            self.packets += 1
        self.broadcast({"type": "hb", "data": self.latest_hb})

    # ---- 收到操作事件 ----
    def add_op(self, data, src):
        now = time.time()
        entry = {"ts": now, "action": data.get("action", "?"), "src": src}
        with self.lock:
            self.ops.append(entry)
            self.last_seen = now
            self.packets += 1
        self.broadcast({"type": "op", "data": entry})

    # ---- 背景巡邏:逾時沒心跳就自動判定離線、收尾這一段 ----
    def check_timeout(self):
        now = time.time()
        with self.lock:
            if self.session_start is not None and (now - self.last_seen) > OFFLINE_AFTER_S:
                self._close_session_locked(self.last_seen)
                went_offline = True
            else:
                went_offline = False
        if went_offline:
            self.broadcast({"type": "offline"})

    # ---- 目前是否在線 ----
    def is_online(self):
        with self.lock:
            return (self.session_start is not None
                    and (time.time() - self.last_seen) <= OFFLINE_AFTER_S)

    # ---- 網頁剛連上時的初始快照 ----
    def snapshot(self):
        with self.lock:
            return {
                "type": "snapshot",
                "online": self.is_online(),
                "latest_hb": self.latest_hb,
                "last_seen": self.last_seen,
                "sessions": list(self.sessions),
                "ops": list(self.ops),
                "packets": self.packets,
                "offline_after": OFFLINE_AFTER_S,
                "server_time": time.time(),
            }


HUB = Hub()


def udp_listener(port):
    """收 ESP32 廣播。依 type 欄位分成心跳(hb)或操作事件(op)。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.bind(("", port))
    print(f"[UDP] 監聽 0.0.0.0:{port}")

    while True:
        try:
            raw, addr = sock.recvfrom(2048)
        except OSError as exc:
            print(f"[UDP] 接收失敗:{exc}")
            time.sleep(1)
            continue

        text = raw.decode("utf-8", errors="replace").strip()
        if not text:
            continue
        src = addr[0]

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            print(f"[UDP] 收到無法解析的封包,來自 {src}: {text!r}")
            continue

        kind = data.get("type")
        if kind == "hb":
            HUB.add_heartbeat(data, src)
        elif kind == "op":
            HUB.add_op(data, src)
        else:
            print(f"[UDP] 收到不認識的 type={kind!r},來自 {src}")


def timeout_watcher():
    """每秒巡一次,讓「目前是否連線」不用等下一個封包才會更新。"""
    while True:
        HUB.check_timeout()
        time.sleep(1)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "GarageBlynkMonitor/1.0"

    def log_message(self, fmt, *args):
        pass                       # 關掉每個請求一行的預設 log,畫面才清爽

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._serve_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
        elif path == "/api/state":
            self._serve_json(HUB.snapshot())
        elif path == "/events":
            self._serve_events()
        else:
            self.send_error(404, "Not Found")

    # ---- 各種回應 ----
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
        q = HUB.subscribe()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            self._send_sse(json.dumps(HUB.snapshot(), ensure_ascii=False))
            while True:
                try:
                    payload = q.get(timeout=5)
                    self._send_sse(payload)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")   # 心跳,避免中間設備切斷閒置連線
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass                   # 瀏覽器關掉分頁,正常情況
        finally:
            HUB.unsubscribe(q)

    def _send_sse(self, payload):
        self.wfile.write(b"data: " + payload.encode("utf-8") + b"\n\n")
        self.wfile.flush()


def main():
    parser = argparse.ArgumentParser(description="ESP32+Blynk 車庫門監控收集器")
    parser.add_argument("--udp-port", type=int, default=5515,
                        help="接收 ESP32 廣播的 UDP 埠(需與韌體 MONITOR_UDP_PORT 一致)")
    parser.add_argument("--port", type=int, default=8081, help="網頁埠")
    parser.add_argument("--bind", default="0.0.0.0", help="網頁監聽位址")
    args = parser.parse_args()

    threading.Thread(target=udp_listener, args=(args.udp_port,), daemon=True).start()
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
