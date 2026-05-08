import RPi.GPIO as GPIO
import time

# 引腳定義
STEP_PIN = 17
DIR_PIN = 18

GPIO.setmode(GPIO.BCM)
GPIO.setup(STEP_PIN, GPIO.OUT)
GPIO.setup(DIR_PIN, GPIO.OUT)

# 設定方向 (1為順時針, 0為逆時針)
GPIO.output(DIR_PIN, GPIO.HIGH)

# 每步間隔：2秒 / 200步 = 0.01秒
# 因為訊號要有高低電平，所以 sleep 時間要除以 2
delay = 0.01 / 2

try:
    print("NEMA 17 開始旋轉：每2秒360度...")
    while True:
        # 轉一圈 (200步)
        for _ in range(200):
            GPIO.output(STEP_PIN, GPIO.HIGH)
            time.sleep(delay)
            GPIO.output(STEP_PIN, GPIO.LOW)
            time.sleep(delay)
        
        time.sleep(1) # 停頓一秒後繼續下一圈

except KeyboardInterrupt:
    print("停止控制")
finally:
    GPIO.cleanup()