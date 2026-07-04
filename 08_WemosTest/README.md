# 08_WemosTest - Wemos D1 燒錄環境

## 安裝清單

### arduino-cli
| 項目 | 內容 |
|------|------|
| 版本 | 1.5.1 |
| 執行檔位置 | `~/bin/arduino-cli` |
| 設定檔 | `~/.arduino15/arduino-cli.yaml` |
| PATH 設定 | 已寫入 `~/.bashrc` |

### ESP8266 開發板核心
| 項目 | 內容 |
|------|------|
| 套件 ID | `esp8266:esp8266` |
| 版本 | 3.1.2 |
| 安裝位置 | `~/.arduino15/packages/esp8266/` |
| Board Manager URL | `http://arduino.esp8266.com/stable/package_esp8266com_index.json` |

### 草稿碼位置
| 檔案 | 路徑 |
|------|------|
| LED 閃爍程式 | `~/gemini_workspace/LearningArea/08_WemosTest/08_WemosTest.ino` |
| 空白程式 | `~/gemini_workspace/LearningArea/08_WemosTest/blank/blank.ino` |

---

## 常用指令備忘

```bash
# 編譯
~/bin/arduino-cli compile --fqbn esp8266:esp8266:d1 <草稿碼資料夾>

# 上傳
~/bin/arduino-cli upload -p /dev/ttyUSB0 --fqbn esp8266:esp8266:d1 <草稿碼資料夾>

# Serial Monitor
~/bin/arduino-cli monitor -p /dev/ttyUSB0 --config baudrate=115200
```
