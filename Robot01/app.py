import lgpio
from flask import Flask, render_template
from flask_socketio import SocketIO

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

# ── lgpio 初始化 ───────────────────────────────────────────
# gpiochip_open(0) 開啟 Pi 5 的第一個 GPIO 晶片（RP1）
# 回傳一個 handle（控制代號），之後所有操作都要帶著它
h = lgpio.gpiochip_open(0)

# ── GPIO 腳位定義 ──────────────────────────────────────────
# 左輪
L_IN1 = 17
L_IN2 = 27
L_ENA = 18   # 硬體 PWM

# 右輪
R_IN3 = 23
R_IN4 = 24
R_ENB = 13   # 硬體 PWM

PWM_FREQ = 1000   # Hz

# 設定方向腳位為輸出模式，初始值為 0（低電位）
# gpio_claim_output(handle, pin, 初始值)
for pin in [L_IN1, L_IN2, R_IN3, R_IN4, L_ENA, R_ENB]:
    lgpio.gpio_claim_output(h, pin, 0)


# ── 馬達控制函式 ───────────────────────────────────────────
def set_motor(in1, in2, ena, speed):
    """
    speed: -100 ~ +100（整數）
      正值 = 正轉（前進方向）
      負值 = 反轉（後退方向）
      0    = 停止

    lgpio.tx_pwm() 的 duty cycle 單位是 0.0 ~ 100.0（百分比）
    與 pigpiod 的 0~1,000,000 不同，這裡直接用 abs(speed) 即可
    """
    duty = float(abs(speed))   # 0.0 ~ 100.0

    if speed > 0:
        lgpio.gpio_write(h, in1, 1)
        lgpio.gpio_write(h, in2, 0)
    elif speed < 0:
        lgpio.gpio_write(h, in1, 0)
        lgpio.gpio_write(h, in2, 1)
    else:
        lgpio.gpio_write(h, in1, 0)
        lgpio.gpio_write(h, in2, 0)

    # tx_pwm(handle, pin, 頻率Hz, duty百分比)
    lgpio.tx_pwm(h, ena, PWM_FREQ, duty)


def stop_all():
    """緊急停止兩顆馬達"""
    set_motor(L_IN1, L_IN2, L_ENA, 0)
    set_motor(R_IN3, R_IN4, R_ENB, 0)


# ── Flask 路由 ────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


# ── SocketIO 事件處理 ──────────────────────────────────────
@socketio.on("move")
def handle_move(data):
    """
    接收來自手機搖桿的指令
    data 格式：{"L": 整數(-100~100), "R": 整數(-100~100)}
    """
    left  = int(data.get("L", 0))
    right = int(data.get("R", 0))

    left  = max(-100, min(100, left))
    right = max(-100, min(100, right))

    # 右輪鏡像安裝，方向訊號反相
    set_motor(L_IN1, L_IN2, L_ENA,  left)
    set_motor(R_IN3, R_IN4, R_ENB, -right)


@socketio.on("stop")
def handle_stop():
    stop_all()


@socketio.on("disconnect")
def handle_disconnect():
    """連線中斷時自動停車，避免失控"""
    stop_all()


# ── 啟動伺服器 ────────────────────────────────────────────
if __name__ == "__main__":
    try:
        socketio.run(app, host="0.0.0.0", port=5000, debug=False)
    finally:
        stop_all()
        # 釋放所有已申請的 GPIO 腳位，歸還給系統
        for pin in [L_IN1, L_IN2, R_IN3, R_IN4, L_ENA, R_ENB]:
            lgpio.gpio_free(h, pin)
        lgpio.gpiochip_close(h)