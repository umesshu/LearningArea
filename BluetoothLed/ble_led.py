from bluezero import peripheral, adapter
from gpiozero import LED
from gi.repository import GLib
import sys

# --- 1. 硬體與藍牙配置 ---
LED_PIN = 17
# UUID 保持不變，方便您的 iPhone 識別
SERVICE_UUID = '12345678-1234-5678-1234-567812345678'
CHAR_UUID    = '87654321-4321-8765-4321-876543210987'

# 初始化 LED
led = LED(LED_PIN)

# --- 2. 指令處理邏輯 ---
def led_control_callback(value, options):
    """處理來自 iPhone 的寫入數據"""
    try:
        # 轉換數據格式 (Python 3.13 有時會將數據傳為整數列表 [49])
        if isinstance(value, list):
            cmd = "".join([chr(n) for n in value])
        else:
            cmd = bytes(value).decode('utf-8')
        
        # 清理字串
        cmd = cmd.strip()
        print(f"收到 iPhone 指令: [{cmd}]")

        # 執行硬體動作
        if '1' in cmd:
            led.on()
            print(">>> 執行動作: 開燈 (GPIO 17 ON)")
        elif '0' in cmd:
            led.off()
            print(">>> 執行動作: 關燈 (GPIO 17 OFF)")
        else:
            print(f"無法辨識的指令: {cmd}")

    except Exception as e:
        print(f"數據解析發生錯誤: {e}")

# --- 3. 啟動藍牙伺服器 ---
def start_ble():
    # A. 取得藍牙適配器
    adps = list(adapter.Adapter.available())
    if not adps:
        print("錯誤：找不到藍牙適配器，請確認藍牙是否開啟")
        sys.exit(1)
    
    adp_address = adps[0].address
    
    # B. 初始化外設物件 (Peripheral)
    # local_name 是 iPhone 搜尋時看到的名稱
    my_pi = peripheral.Peripheral(adp_address, local_name='Pi_LED_Remote')
    
    # C. 建立服務 (Service)
    my_pi.add_service(srv_id=1, uuid=SERVICE_UUID, primary=True)
    
    # D. 建立特徵值 (Characteristic)
    # 補足所有必要參數，包含 notifying、read_callback 等
    my_pi.add_characteristic(srv_id=1, chr_id=1, uuid=CHAR_UUID,
                            value=[], 
                            notifying=False,           # 核心修正：避免 TypeError
                            flags=['read', 'write'],
                            read_callback=None,
                            write_callback=led_control_callback, # 綁定動作
                            notify_callback=None)
    
    # E. 發布並開始廣播
    print(f"🚀 藍牙伺服器已就緒！")
    print(f"請在 LightBlue 搜尋：'Pi_LED_Remote'")
    my_pi.publish()
    
    # F. 啟動 GLib 事件迴圈，這能防止程式跑完後自動結束(斷線)
    loop = GLib.MainLoop()
    try:
        loop.run()
    except KeyboardInterrupt:
        print("\n使用者停止程式，正在清理資源...")
        loop.quit()
        led.off()

if __name__ == '__main__':
    start_ble()