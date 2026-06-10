from bluezero import gatt_server
from bluezero import adapter
from gpiozero import LED

# 初始化 LED (GPIO 17)
led = LED(17)

# 定義藍牙服務的 UUID (隨機生成，確保 iPhone 能認出)
SERVICE_UUID = '12345678-1234-5678-1234-567812345678'
CHAR_UUID = '87654321-4321-8765-4321-876543210987'

def led_control_callback(value, options):
    """當 iPhone 寫入數據時觸發此函數"""
    cmd = bytes(value).decode('utf-8')
    if cmd == '1':
        led.on()
        print("iPhone 指令：開燈")
    elif cmd == '0':
        led.off()
        print("iPhone 指令：關燈")

def start_ble():
    # 1. 設定藍牙適配器
    ble_adapter = list(adapter.Adapter.available())[0]
    
    # 2. 建立 GATT 服務
    srv = gatt_server.Service(SERVICE_UUID, True)
    
    # 3. 建立特徵值 (可讀、可寫)
    char = gatt_server.Characteristic(CHAR_UUID, srv)
    char.add_write_value(led_control_callback)
    
    # 4. 開始廣播
    app = gatt_server.Application()
    app.add_service(srv)
    app.register()
    
    print("藍牙 LED 伺服器啟動中，請用 iPhone 連線...")
    app.run()

if __name__ == '__main__':
    start_ble()
