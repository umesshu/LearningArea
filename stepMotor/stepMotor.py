import RPi.GPIO as GPIO
import time

# --- 引腳定義 ---
STEP_PIN = 17
DIR_PIN = 18
ENABLE_PIN = 14

# --- 參數設定 ---
# 17HS1352-P413 在全步進模式下為 200 步一圈
# 若你已將 MS1, MS2, MS3 接上 3.3V (1/16 微步)，請將此值改為 3200
STEPS_PER_REVOLUTION = 200 

# 目標：2秒轉一圈 (360度)
SECONDS_PER_REV = 2.0
# 計算每一步的延遲時間 (秒)
# 因為一個脈衝包含 HIGH 與 LOW 兩個動作，所以 sleep 時間要除以 2
step_delay = (SECONDS_PER_REV / STEPS_PER_REVOLUTION) / 2

# --- GPIO 初始化 ---
GPIO.setmode(GPIO.BCM)
GPIO.setup(STEP_PIN, GPIO.OUT)
GPIO.setup(DIR_PIN, GPIO.OUT)
GPIO.setup(ENABLE_PIN, GPIO.OUT)

# 設定旋轉方向 (HIGH 或 LOW 可切換順逆時針)
GPIO.output(DIR_PIN, GPIO.HIGH)

try:
    # 1. 啟動驅動器 (A4988 的 ENABLE 是低電位致能)
    GPIO.output(ENABLE_PIN, GPIO.LOW)
    print(f"馬達開始持續旋轉 (速度: {SECONDS_PER_REV}s/rev)...")
    print("按下 Ctrl+C 可停止並關閉馬達電源")

    while True:
        # 執行步進脈衝
        GPIO.output(STEP_PIN, GPIO.HIGH)
        time.sleep(step_delay)
        GPIO.output(STEP_PIN, GPIO.LOW)
        time.sleep(step_delay)

except KeyboardInterrupt:
    print("\n偵測到停止指令")

finally:
    # 2. 徹底解決抖動：在結束時將 ENABLE 設為 HIGH (切斷馬達線圈電流)
    GPIO.output(ENABLE_PIN, GPIO.HIGH)
    print("驅動器已關閉，馬達動力已釋放")
    
    # 清除 GPIO 狀態
    #GPIO.cleanup()

