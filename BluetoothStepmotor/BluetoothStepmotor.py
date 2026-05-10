from bluezero import gatt_server
from bluezero import adapter
from gpiozero import OutputDevice
from time import sleep

# 1. 初始化馬達引腳 (使用 OutputDevice 以獲得更好的控制)
step_pin = OutputDevice(17)
dir_pin = OutputDevice(27)

# 定義藍牙 UUID (可延用之前的)
SERVICE_UUID = '12345678-1234-5678-1234-567812345678'
CHAR_UUID = '87654321-4321-8765-4321-876543210987'

def rotate_motor(steps, direction_val, speed=0.001):
    """
    控制馬達旋轉的輔助函式
    steps: 走多少步 (200步通常是一圈)
    direction_val: 1 為順時針, 0 為逆時針
    speed: 脈波間隔，越小轉越快
    """
    dir_pin.value = direction_val
    for _ in range(steps):
        step_pin.on()
        sleep(speed)
        step_pin.off()
        sleep(speed)

def motor_control_callback(value, options):
    """當 iPhone 發送指令時觸發"""
    cmd = bytes(value).decode('utf-8')
    
    if cmd == '1':
        print("iPhone 指令：順時針轉一圈")
        rotate_motor(200, 1) # 200 步
    elif cmd == '0':
        print("iPhone 指令：逆時針轉一圈")
        rotate_motor(200, 0)
    elif cmd == 's':
        print("iPhone 指令：緊急停止 (雖然此範例為步進完成即停)")
        # 步進馬達不給脈波就會自動鎖死或停止

def start_ble_server():
    # 設置藍牙適配器
    ble_adapter = list(adapter.Adapter.available())[0]
    
    # 建立 GATT 服務與特徵值
    srv = gatt_server.Service(SERVICE_UUID, True)
    char = gatt_server.Characteristic(CHAR_UUID, srv)
    
    # 綁定回呼函數
    char.add_write_value(motor_control_callback)
    
    # 啟動應用
    app = gatt_server.Application()
    app.add_service(srv)
    app.register()
    
    print("藍牙馬達控制器已就緒...")
    app.run()

if __name__ == '__main__':
    start_ble_server()
