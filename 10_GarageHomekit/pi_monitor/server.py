#!/usr/bin/env python3
"""車庫門遙測收集器 —— 收 Wemos 的 UDP 封包,並以網頁即時呈現。

兩個角色跑在同一支程式裡:
  1. UDP 監聽緒:收 Wemos 廣播過來的文字 log 與 JSON 遙測,存進環形緩衝
  2. HTTP 伺服器:提供儀表板網頁 + Server-Sent Events 即時推播

刻意只用 Python 標準函式庫,樹莓派不必安裝任何套件。
SSE 而非 WebSocket:資料只需 伺服器→瀏覽器 單向流動,SSE 剛好夠用且斷線會自動重連。

用法:
    python3 server.py                 # UDP 5514, 網頁 8080
    python3 server.py --port 9000     # 換網頁埠
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

# 保留多少筆歷史。240 筆遙測 ≈ 8 分鐘(Wemos 每 2 秒送一包)
MAX_LOGS = 500
MAX_HISTORY = 240

# 超過這麼久沒收到任何封包就視為離線(Wemos 每 2 秒送一次遙測)
OFFLINE_AFTER_S = 12


class Hub:
    """所有狀態的單一存放處,並負責把新事件推播給每個網頁連線。"""

    def __init__(self):
        self.lock = threading.Lock()
        self.logs = deque(maxlen=MAX_LOGS)
        self.history = deque(maxlen=MAX_HISTORY)
        self.latest = None
        self.last_seen = 0.0
        self.packets = 0
        self.subscribers = []          # list[queue.Queue]

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

    # ---- 收到封包 ----
    def add_telemetry(self, data, src):
        now = time.time()
        data["ts"] = now
        data["src"] = src
        with self.lock:
            self.latest = data
            self.last_seen = now
            self.packets += 1
            self.history.append({
                "ts": now,
                "dist": data.get("dist", 0),
                "pos": data.get("pos", 0),
                "rssi": data.get("rssi", 0),
                "heap": data.get("heap", 0),
            })
        self.broadcast({"type": "tel", "data": data})

    def add_log(self, line, src):
        now = time.time()
        entry = {"ts": now, "line": line, "src": src}
        with self.lock:
            self.logs.append(entry)
            self.last_seen = now
            self.packets += 1
        self.broadcast({"type": "log", "data": entry})

    # ---- 網頁剛連上時的初始快照 ----
    def snapshot(self):
        with self.lock:
            return {
                "type": "snapshot",
                "latest": self.latest,
                "history": list(self.history),
                "logs": list(self.logs),
                "last_seen": self.last_seen,
                "packets": self.packets,
                "offline_after": OFFLINE_AFTER_S,
                "server_time": time.time(),
            }


HUB = Hub()


def udp_listener(port):
    """收 Wemos 廣播。以 '{' 開頭視為 JSON 遙測,其餘視為文字 log。"""
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

        if text.startswith("{"):
            try:
                HUB.add_telemetry(json.loads(text), src)
                continue
            except json.JSONDecodeError:
                pass               # 解不開就當成一般 log 收下,不要靜靜丟掉

        # 一個封包可能含多行(例如格式字串裡有 \n)
        for line in text.splitlines():
            if line.strip():
                HUB.add_log(line.rstrip(), src)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "GarageMonitor/1.0"

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
    parser = argparse.ArgumentParser(description="車庫門遙測收集器")
    parser.add_argument("--udp-port", type=int, default=5514,
                        help="接收 Wemos 廣播的 UDP 埠(需與韌體 UDP_LOG_PORT 一致)")
    parser.add_argument("--port", type=int, default=8080, help="網頁埠")
    parser.add_argument("--bind", default="0.0.0.0", help="網頁監聽位址")
    args = parser.parse_args()

    threading.Thread(target=udp_listener, args=(args.udp_port,), daemon=True).start()

    httpd = ThreadingHTTPServer((args.bind, args.port), Handler)
    httpd.daemon_threads = True
    print(f"[HTTP] 儀表板 http://{args.bind}:{args.port}/")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[系統] 收到 Ctrl-C,結束")


if __name__ == "__main__":
    main()
