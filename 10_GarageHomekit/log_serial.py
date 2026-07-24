#!/usr/bin/env python3
"""
車庫門 HomeKit 控制器 — 序列埠記錄工具

把 D1 mini 的 Serial 輸出加上時間戳,存進 logs/ 資料夾。
只用 Python 標準函式庫(termios),不需要安裝 pyserial。

用法:
    ./log_serial.py                  # 開始記錄(Ctrl+C 停止)
    ./log_serial.py --quiet          # 只寫檔案,不在畫面上顯示
    ./log_serial.py --port /dev/ttyUSB1
    ./log_serial.py --stats          # 不記錄,改為分析既有 log 檔

注意:
    * 執行中會佔用序列埠,要重新上傳韌體前請先按 Ctrl+C 停止。
    * 開始記錄時會觸發板子重置一次(這是序列埠 DTR 訊號的正常行為),
      剛好可以順便記錄到開機訊息與重置原因。
"""

import argparse
import fcntl
import glob
import os
import re
import struct
import sys
import termios
import time
from datetime import datetime

DEFAULT_PORT = "/dev/ttyUSB0"
DEFAULT_BAUD = 115200
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def open_serial(port: str, baud: int) -> int:
    """開啟序列埠並設定為 raw 模式 115200 8N1。回傳 file descriptor。"""
    fd = os.open(port, os.O_RDWR | os.O_NOCTTY)   # RDWR 才能操作 DTR/RTS 做重置

    speed = getattr(termios, f"B{baud}", None)
    if speed is None:
        os.close(fd)
        raise ValueError(f"不支援的 baud rate: {baud}")

    iflag, oflag, cflag, lflag, ispeed, ospeed, cc = termios.tcgetattr(fd)

    # raw 輸入:不做任何字元轉換
    iflag &= ~(termios.IGNBRK | termios.BRKINT | termios.PARMRK | termios.ISTRIP |
               termios.INLCR | termios.IGNCR | termios.ICRNL | termios.IXON)
    oflag &= ~termios.OPOST
    lflag &= ~(termios.ECHO | termios.ECHONL | termios.ICANON |
               termios.ISIG | termios.IEXTEN)

    # 8 資料位元、無同位、1 停止位元、無硬體流控
    cflag &= ~(termios.CSIZE | termios.PARENB | termios.CSTOPB | termios.CRTSCTS)
    cflag |= termios.CS8 | termios.CLOCAL | termios.CREAD
    cflag &= ~termios.HUPCL          # 關閉時不要拉低 DTR(避免多餘的重置)

    ispeed = ospeed = speed
    cc[termios.VMIN] = 1             # 至少讀到 1 byte 才返回
    cc[termios.VTIME] = 0

    termios.tcsetattr(fd, termios.TCSANOW,
                      [iflag, oflag, cflag, lflag, ispeed, ospeed, cc])
    return fd


def pulse_reset(fd: int) -> None:
    """透過 DTR/RTS 重置 ESP8266,好從頭捕捉開機訊息(含 WiFi 掃描結果)。

    Wemos D1 mini 的自動重置電路:RTS 接 RST、DTR 接 GPIO0。
    保持 DTR 未觸發(GPIO0 為高 = 正常開機),只脈衝 RTS 拉低 RST。
    """
    dtr = struct.pack("I", termios.TIOCM_DTR)
    rts = struct.pack("I", termios.TIOCM_RTS)
    fcntl.ioctl(fd, termios.TIOCMBIC, dtr)   # DTR 放開 → GPIO0 高 → 正常開機(非燒錄模式)
    fcntl.ioctl(fd, termios.TIOCMBIS, rts)   # RTS 觸發 → RST 拉低
    time.sleep(0.1)
    fcntl.ioctl(fd, termios.TIOCMBIC, rts)   # 放開 RST → 開機
    time.sleep(0.05)


def record(port: str, baud: int, quiet: bool, do_reset: bool = False) -> None:
    os.makedirs(LOG_DIR, exist_ok=True)
    logfile = os.path.join(LOG_DIR, f"serial_{datetime.now():%Y%m%d_%H%M%S}.log")

    print(f"[logger] 記錄中 -> {logfile}")
    print(f"[logger] 埠={port}  baud={baud}   (Ctrl+C 停止)")
    print("-" * 60)

    with open(logfile, "a", encoding="utf-8", buffering=1) as log:

        def emit(text: str) -> None:
            line = f"{now()} {text}"
            log.write(line + "\n")      # buffering=1 → 每行即時寫入磁碟
            if not quiet:
                print(line, flush=True)

        while True:
            # 等待序列埠出現(板子重開機 / USB 重新枚舉時)
            if not os.path.exists(port):
                emit(f"[logger] 找不到 {port},等待裝置接上...")
                while not os.path.exists(port):
                    time.sleep(1)

            try:
                fd = open_serial(port, baud)
            except PermissionError:
                emit(f"[logger] 沒有權限開啟 {port}"
                     f"(請確認使用者在 dialout 群組,或該埠被其他程式佔用)")
                time.sleep(3)
                continue
            except OSError as exc:
                emit(f"[logger] 開啟 {port} 失敗: {exc},3 秒後重試...")
                time.sleep(3)
                continue

            emit("[logger] 開始讀取序列埠")
            if do_reset:
                do_reset = False           # 只在第一次連上時重置
                emit("[logger] 重置板子,捕捉開機訊息...")
                try:
                    pulse_reset(fd)
                except OSError as exc:
                    emit(f"[logger] 重置失敗(此埠可能不支援 DTR/RTS): {exc}")

            buf = b""
            partial = True      # 從資料流中途接入,第一行通常是半截的,丟掉
            try:
                while True:
                    chunk = os.read(fd, 4096)
                    if not chunk:                     # EOF:裝置消失
                        break
                    buf += chunk
                    *lines, buf = buf.split(b"\n")
                    for raw in lines:
                        if partial:                   # 略過接入瞬間的殘缺行
                            partial = False
                            continue
                        emit(raw.rstrip(b"\r").decode("utf-8", errors="replace"))
            except OSError as exc:
                emit(f"[logger] 讀取中斷: {exc}")
            finally:
                os.close(fd)

            emit("[logger] 序列埠中斷,1 秒後重新連線...")
            time.sleep(1)


def stats() -> None:
    """分析既有 log 檔:統計 WiFi 斷線次數、原因碼、HomeKit 連線事件。"""
    files = sorted(glob.glob(os.path.join(LOG_DIR, "serial_*.log")))
    if not files:
        print(f"找不到任何 log 檔(目錄: {LOG_DIR})")
        return

    reasons: dict[str, int] = {}
    wifi_events, hk_events, reboots = [], [], []

    for path in files:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.rstrip("\n")
                if "[WiFi事件]" in line and "斷線" in line:
                    wifi_events.append(line)
                    m = re.search(r"原因碼=(\d+)", line)
                    if m:
                        reasons[m.group(1)] = reasons.get(m.group(1), 0) + 1
                elif "[HomeKit事件]" in line:
                    hk_events.append(line)
                elif "重置原因" in line:
                    reboots.append(line)

    reason_desc = {
        "1": "未指定", "2": "認證過期", "3": "被 AP 踢出", "4": "關聯逾時(閒置)",
        "8": "AP 離開", "15": "4-way 握手逾時", "200": "Beacon Timeout(收不到 AP 信標)",
        "201": "找不到 AP", "202": "認證失敗", "203": "關聯失敗",
    }

    print(f"分析 {len(files)} 個 log 檔\n")
    print(f"== WiFi 斷線: 共 {len(wifi_events)} 次 ==")
    for code, count in sorted(reasons.items(), key=lambda kv: -kv[1]):
        print(f"  原因碼 {code:>3} × {count:<4} {reason_desc.get(code, '未知')}")
    for line in wifi_events[-10:]:
        print(f"  {line}")

    print(f"\n== HomeKit 事件: 共 {len(hk_events)} 筆 ==")
    for line in hk_events[-10:]:
        print(f"  {line}")

    print(f"\n== 開機/重置紀錄: 共 {len(reboots)} 次 ==")
    for line in reboots[-10:]:
        print(f"  {line}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="D1 mini 序列埠記錄工具(附時間戳)",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", default=DEFAULT_PORT, help=f"序列埠(預設 {DEFAULT_PORT})")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD, help=f"鮑率(預設 {DEFAULT_BAUD})")
    parser.add_argument("--quiet", action="store_true", help="只寫檔案,畫面不顯示")
    parser.add_argument("--stats", action="store_true", help="分析既有 log 檔,不進行記錄")
    parser.add_argument("--reset", action="store_true",
                        help="開始記錄時重置板子,以完整捕捉開機訊息(含 WiFi 掃描結果)")
    args = parser.parse_args()

    if args.stats:
        stats()
        return 0

    try:
        record(args.port, args.baud, args.quiet, args.reset)
    except KeyboardInterrupt:
        print("\n[logger] 已停止記錄,序列埠已釋放。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
