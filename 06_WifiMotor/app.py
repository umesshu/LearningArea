import lgpio
from flask import Flask, render_template
from flask_socketio import SocketIO

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

h = lgpio.gpiochip_open(0)

# GPIO 腳位定義
L_IN1 = 17
L_IN2 = 27
L_ENA = 18  # PWM

R_IN3 = 23
R_IN4 = 24
R_ENB = 13  # PWM

PWM_FREQ = 1000  # Hz

for pin in [L_IN1, L_IN2, R_IN3, R_IN4, L_ENA, R_ENB]:
    lgpio.gpio_claim_output(h, pin)

def set_motor(in1, in2, ena, speed):
    """
    speed: -100 ~ +100
    正值 = 正轉，負值 = 反轉，0 = 停止
    """
    duty = abs(speed)

    if speed > 0:
        lgpio.gpio_write(h, in1, 1)
        lgpio.gpio_write(h, in2, 0)
    elif speed < 0:
        lgpio.gpio_write(h, in1, 0)
        lgpio.gpio_write(h, in2, 1)
    else:
        lgpio.gpio_write(h, in1, 0)
        lgpio.gpio_write(h, in2, 0)

    lgpio.tx_pwm(h, ena, PWM_FREQ, duty)

@app.route("/")
def index():
    return render_template("index.html")

@socketio.on("move")
def handle_move(data):
    left  = int(data.get("L", 0))
    right = int(data.get("R", 0))

    # 右輪鏡像安裝，方向反相
    set_motor(L_IN1, L_IN2, L_ENA,  left)
    set_motor(R_IN3, R_IN4, R_ENB, -right)

@socketio.on("disconnect")
def handle_disconnect():
    set_motor(L_IN1, L_IN2, L_ENA, 0)
    set_motor(R_IN3, R_IN4, R_ENB, 0)

if __name__ == "__main__":
    try:
        socketio.run(app, host="0.0.0.0", port=5000)
    finally:
        set_motor(L_IN1, L_IN2, L_ENA, 0)
        set_motor(R_IN3, R_IN4, R_ENB, 0)
        lgpio.gpiochip_close(h)
