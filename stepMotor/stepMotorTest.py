import RPi.GPIO as GPIO
import time

# 引腳定義
STEP_PIN = 17
DIR_PIN = 18
ENABLE_PIN = 14 # 連接到驅動器的 EN 引腳

GPIO.setmode(GPIO.BCM)
GPIO.setup(STEP_PIN, GPIO.OUT)
GPIO.setup(DIR_PIN, GPIO.OUT)
GPIO.setup(ENABLE_PIN, GPIO.OUT)

# 初始狀態：致能驅動器 (通常 A4988 是低電平致能)
GPIO.output(ENABLE_PIN, GPIO.LOW) 

# 設定方向 (1為順時針, 0為逆時針)
GPIO.output(DIR_PIN, GPIO.HIGH)

# 每步間隔：2秒 / 200步 = 0.01秒
delay = 0.01 / 2

try:
    print("NEMA 17 開始旋轉：每2秒360度... (按 Ctrl+C 停止)")
    while True:
        # 轉一圈 (200步)
        for _ in range(200):
            GPIO.output(STEP_PIN, GPIO.HIGH)
            time.sleep(delay)
            GPIO.output(STEP_PIN, GPIO.LOW)
            time.sleep(delay)
        
        time.sleep(1) # 停頓一秒後繼續下一圈

except KeyboardInterrupt:
    print("\n偵測到停止訊號")
finally:
    # 關鍵：結束前停用驅動器並確保引腳為低電平，防止馬達抖動
    GPIO.output(ENABLE_PIN, GPIO.HIGH) 
    GPIO.output(STEP_PIN, GPIO.LOW)
    GPIO.output(DIR_PIN, GPIO.LOW)
    GPIO.cleanup()
    print("已清理 GPIO 並關閉馬達電源，防止抖動。")
