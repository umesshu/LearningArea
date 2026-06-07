from gpiozero import LED
from time import sleep

# 定義 LED 連接在 GPIO 17 (BCM 編號)
led = LED(17)

print("LED 開始閃爍... 按下 Ctrl+C 可停止")

try:
    while True:
        led.on()       # 點亮 LED
        sleep(0.5)     # 持續 0.5 秒
        led.off()      # 關閉 LED
        sleep(0.5)     # 持續 0.5 秒
except KeyboardInterrupt:
    print("\n程式已停止")