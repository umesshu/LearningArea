#!/bin/bash
# 一鍵啟動腳本（lgpio 版本，不需要 pigpiod）

cd ~/gemini_workspace/LearningArea/Robot01

# 背景啟動 mediamtx 攝影機串流
./mediamtx &
MEDIAMTX_PID=$!
echo "mediamtx 已啟動 (PID: $MEDIAMTX_PID)"

# 前景啟動 Flask，Ctrl+C 可停止
python app.py

# Flask 停止後，一併關閉 mediamtx
kill $MEDIAMTX_PID
echo "mediamtx 已關閉"