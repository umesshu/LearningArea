# 11_tofCameraEnvSenser - ToF 深度相機 3D 串流伺服器

用 Arducam ToF (Time-of-Flight) 深度相機接在樹莓派上，跑一個 Flask 網頁伺服器，
手機或電腦瀏覽器連進去就能即時看到：

1. **2D 深度圖**（彩色熱力圖，MJPEG 串流）
2. **3D 立體網格**（把深度圖轉成三角網格，Three.js 渲染，可用手指/滑鼠拖曳旋轉、縮放）

專案目標是先把「即時 3D 檢視」這個基礎打穩，之後再疊加真正的應用邏輯
（例如車庫環境感測、距離觸發等 —— 資料夾名稱裡的 `EnvSenser` 是預留給那個方向的）。

## 硬體

| 項目 | 內容 |
|---|---|
| 主機 | 樹莓派（本身就是開發機，開發跟部署是同一台） |
| 深度相機 | Arducam ToF Camera（HQVGA，240×180），CSI 排線連接 |
| 感測原理 | 相位式 ToF：VCSEL 發射調制紅外光，量測反射相位差換算距離 |
| 量測範圍 | 支援 2M / 4M 兩種模式，網頁上可即時切換（不用重開相機），誤差約 ±2cm |
| 供電 | 需要獨立 5V/2A，USB 供電可能不穩 |
| SDK 位置 | `~/Arducam_tof_camera`（官方 repo，含 C/C++/Python 範例、`ArducamDepthCamera.h` API 文件） |

CSI 排線接妥後，`rpicam-hello --list-cameras` 看不到這顆是正常的——它用的是
`arducam-pivariety` 驅動，一般 libcamera 應用程式不認得，要透過 `ArducamDepthCamera`
這個廠商自己的 SDK 存取。可以用 `i2cdetect -y 10` 看到位址 `0x0c` 顯示 `UU`
（代表核心驅動已抓到並綁定）來確認硬體有正常連接。

## 軟體技術棧

| 層 | 技術 |
|---|---|
| 相機存取 | `ArducamDepthCamera`（官方 Python SDK，0.1.24） |
| 後端 | Python 3.13 + Flask 3.1（開發用內建伺服器） |
| 影像/數值運算 | OpenCV (`opencv-python-headless`) + NumPy |
| 前端 3D 渲染 | Three.js r128（CDN 載入，UMD 版，非 ES module） |
| 前端框架 | 無，純手寫 HTML/CSS/JS，用 Python `render_template_string` 直接內嵌在 `.py` 裡 |
| 開機常駐 | systemd service（`tof-stream.service`） |

Python 套件安裝在專案獨立的 venv（`venv/`，不進版控），因為系統 Python
是「externally managed」（PEP 668），不能直接 `pip install`。

```bash
cd 11_tofCameraEnvSenser
python3 -m venv venv
./venv/bin/pip install ArducamDepthCamera opencv-python-headless "numpy<2.0.0" flask
```

## 檔案

- `tof_stream_server.py` — 主程式，唯一的原始碼檔案（Flask 路由 + 相機處理執行緒 + 前端頁面字串都在這裡）
- `tof-stream.service` — systemd unit 檔範本，複製到 `/etc/systemd/system/` 即可開機自動啟動
- `venv/` — 專案獨立虛擬環境（**不進版控**）

## 架構

```
┌─────────────────────────┐        ┌──────────────────────────┐
│   camera_worker (背景執行緒)  │        │      Flask 主執行緒         │
│                          │        │                          │
│ 迴圈:                     │        │  GET /            深度圖頁面 │
│  1. 檢查有沒有活躍連線         │  共用   │  GET /video_feed  MJPEG串流│
│  2. 沒人看→關閉相機省電       │  全域   │  GET /pointcloud  3D頁面   │
│  3. 讀一幀深度資料            │◄──────►│  GET /mesh_data   網格二進位│
│  4. 輻射深度→平面深度修正      │  變數   │  GET /camera_info 相機參數 │
│  5. 時間平滑(EMA)           │  (加鎖) │  GET /settings    目前設定 │
│  6. 九宮格平滑 + Laplacian鬆弛│        │  GET /set_temporal_frames │
│  7. 建三角網格               │        │  GET /set_range   切換2M/4M│
│  8. 打包 JPEG(2D) / 二進位(3D)│        │  GET /calibrate_flat (後門)│
└─────────────────────────┘        └──────────────────────────┘
```

背景執行緒（`camera_worker`）負責所有跟相機/運算相關的事，Flask 路由只負責讀取
背景執行緒算好、放在全域變數裡的最新結果，兩者用 `threading.Lock` 保護共用資料。

## 功能與設計決策（開發過程中討論出來的取捨）

### 1. 深度圖 → 3D 網格，而不是點雲

ToF 相機輸出的原始資料本質上是「一張長得像圖片、內容是距離值的 2D 陣列」（深度圖），
不是點雲。因為它本身就是規則網格（每個像素是一個格點），所以直接把相鄰 2×2 格點
兜成兩個三角形，就能組出連續的面，不需要對無序點雲做三角化。

轉換公式（針孔相機模型，套用相機內參 fx/fy/cx/cy）：
```
x = (u - cx) * depth / fx
y = (v - cy) * depth / fy
z = depth
```

（這裡的 `depth` 已經是下面第 2 節做完輻射→平面修正之後的值，不是感測器原始輸出。）

### 2. 輻射距離 → 平面深度修正 + 橫帶自動校正（`DEPTH_IS_RADIAL`）

ToF 感測器量到的其實是「相機光心沿著每條射線的直線距離」（輻射距離，radial），
不是「垂直於鏡頭光軸的平面深度」（planar）。同一片正對相機的牆，畫面邊角的
光路比正中央斜、也比較長，如果直接把這個距離值當成針孔相機公式裡的 `depth`
代入，會把平面重建成一個「往外凸的弧面」（邊角被算得比實際更遠）。

**理論修正公式**：
```
nx = (u - cx) / fx
ny = (v - cy) / fy
z_planar = R / sqrt(1 + nx² + ny²)
```
其中 `R` 是感測器輸出的輻射距離。這個公式本身只能大致拉平，2M 模式下靠近
畫面角落仍殘留明顯弧形失真（推測是鏡頭實際光學失真不是理想針孔模型）。

**橫帶自動校正**（`calibrate_flat_wall()`）：關鍵要求是「只把牆面拉平，不要
把整個畫面都拉平」——所以**只取畫面下方 1/5~2/5 高度的一條橫帶**
（`CALIB_BAND_BOTTOM_FRAC`/`CALIB_BAND_TOP_FRAC`），假設這裡拍到的是一片
真正的平面（牆或地板），對這條帶子做 SVD 平面擬合，反推出「修正係數 vs
r²（=nx²+ny²）」的二次多項式關係，再把這條曲線套用到**全畫面**（不管實際
量到的深度是什麼，修正曲線只跟像素在畫面上的角度位置有關，跟鏡頭離牆多遠
無關）。2M / 4M 分開校正、分開存修正係數表（`_correction_maps`，key 是量測
範圍 mm）。

開發時抓到兩個非顯而易見的演算法 bug：
1. **收斂到錯誤局部解**：迭代擬合平面若從 `factor=1`（直接拿 R 當 Z）開始，
   窄橫帶只看得到弧面的一小塊，SVD 會收斂到一個局部自洽但整體方向錯誤的
   切平面，疊代也跳不出來。修法：初始猜測改用理論公式
   `sqrt(1+nx²+ny²)`，讓資料一開始就已經接近打平，SVD 才擬合得到真正的
   牆面方向。
2. **尺度不定**：即使修好①，仍有約 2% 系統性偏差——整體縮放 `factor` 仍能
   讓點落在同一平面上（形狀對），RMS 誤差看不出這個偏差。修法：用「光軸
   中心 `r²=0` 處輻射距離必然等於平面深度」這個物理事實錨定尺度，把整條
   修正曲線除以 `factor(r²=0)`。

兩個 bug 都是靠自己寫 numpy 模擬（理想情況/雜訊情況/含未建模殘餘失真情況/
場景中混入不同深度的近物）主動驗證找出來的，不是使用者回報的。

**目前狀態**：使用者確認校正效果可接受後，把校正參數**寫死**進程式碼
（`_CALIBRATED_MAPS_B64`，2M/4M 各一組 base64 編碼的 float32 修正係數表，
形狀 `[90,120]`），開機時 `_decode_calibrated_maps()` 自動解碼套用，不需
每次重新校正、也不需 UI 操作。原本「自動校正」「扭曲修正強度」「扭曲修正
衰減率」三個 UI 區塊和對應路由已全部移除；`calibrate_flat_wall()` 與
`/calibrate_flat`、`/clear_calibration` 路由仍保留在後端（沒有 UI 按鈕），
作為以後相機實際被移動、需要重新校正時的維護後門，可用 curl 直接呼叫（見
「網頁路由一覽」）。

### 3. 前後景邊緣不連成一片「拖影」

物體邊緣的地方，前景跟背景深度差很大，如果無腦把相鄰格點都連成三角形，會出現
一片從物體邊緣「拉伸到背景」的假面。解法：相鄰格點深度差超過 `MAX_DEPTH_JUMP_MM`
(150mm) 就跳過，不連成面。

### 4. 抗雜訊：三層平滑疊加

ToF 感測器每個像素有隨機幾公分的雜訊，直接建網格會呈現「刺蝟狀」尖刺
（法線在雜訊影響下劇烈跳動，方向光一打就很明顯）。試過幾種方案，最後三層疊加使用：

| 方案 | 做法 | 參數 |
|---|---|---|
| 時間平滑 (Temporal EMA) | 跟過去幾幀的深度做指數移動平均，抵消隨機雜訊；只用這一幀信心足夠的像素更新，避免用雜訊污染歷史值 | `TEMPORAL_FRAMES`，網頁上有拖桿可調，預設 5 |
| 九宮格平均 (Box Blur) | 建網格前，把每個像素的深度換成周圍 3×3 有效像素的平均值 | `BOX_BLUR_KERNEL = 3` |
| 頂點鬆弛 (Laplacian Smoothing) | 每個頂點跟上下左右鄰居的平均位置做加權混合，重複幾次 | `SMOOTH_ITERATIONS = 4`, `SMOOTH_ALPHA = 0.5` |

**踩過的坑**：一開始試過「雙邊濾波 (Bilateral Filter)」，效果不理想（刺蝟感沒有明顯改善），
換成上面這個組合後才滿意。程式碼裡還留著一個做好但**目前關閉**的「自適應網格化
(Adaptive Meshing，四元樹遞迴合併平坦區域)」——技術上可行（面數可減少 50%+），
但使用者評估後覺得視覺效果不是想要的，用 `ADAPTIVE_MESH_ENABLED = False` 關閉，
程式碼保留供未來參考。

### 5. 3D 檢視操作邏輯，抄自己之前寫的 obj-workbench 工具

相機控制（支點旋轉、平移、縮放、ViewCube 視角方塊）直接參考另一個自製的
OBJ 網格編輯工具的做法，核心是**繞任意支點的剛體旋轉**：拖曳畫面時，先用
raycast 找出你點到的那個點當支點，之後旋轉時相機位置+朝向一起繞著這個支點轉，
而不是永遠繞畫面正中央轉。

### 6. 相機圖示 + 拍攝視錐 (Frustum)

場景原點畫一個極簡線框相機圖示，並依照相機內參 + 目前設定的量測範圍
（`/camera_info` API 讀 `cam.getControl(RANGE)` 得知目前是 2M 或 4M）畫出四角錐
視錐線框，標示「相機在哪裡、看得到多遠」。

### 7. 淺色主題

3D 檢視頁面的顏色配置、材質（`0x8e9aa6` 淺灰、`flatShading:true`）刻意跟
obj-workbench 那個工具一致，深度不再用彩虹色上色（那是 2D 深度圖頁面才有的），
3D 網格改用純粹的燈光明暗表現立體感。

### 8. 更新率調校

發現兩個各自獨立的瓶頸，疊加起來把畫面卡在 ~5.5fps：

1. **相機的 `AUTO_FRAME_RATE`**：預設為了遠距量測的訊噪比自動降到 ~6fps。
   實測發現不用犧牲量測範圍，維持 4M 模式、只關掉這個控制項，就能拿到
   原生 ~30fps 擷取率。
2. **`/mesh_data` 重複做序列化**：原本每次 HTTP 請求都重新序列化一次上萬個
   頂點，在高頻輪詢下跟背景執行緒互搶 GIL，拖慢整體速度。改成**只在背景
   執行緒每次新影格時打包一次**，之後所有請求都直接回傳同一份現成的
   bytes。

兩者一起修，實測更新率從 ~5.5fps 提升到 ~18fps。

之後又把 `/mesh_data` 的格式從 JSON 換成純二進位（見 `mesh_to_binary()`）：
JSON 需要把每個數字轉成文字、下載、再解析回數字，對上萬個頂點來說這幾步
在手機瀏覽器上很花時間。改成直接傳原始 `float32`/`uint32` bytes
（`[uint32 頂點數][uint32 索引數][positions bytes][indices bytes]`），
網頁端用 `ArrayBuffer`/`DataView`/TypedArray 直接讀記憶體、不用解析，手機端
FPS 又進一步提升。

### 9. 閒置省電：伺服器常駐，相機才會休眠

網頁伺服器（Flask）本身**不休眠**，隨時能回應請求；真正會開關的是**相機硬體**。
用一個全域時間戳記錄「最後一次有請求進來的時間」，各路由（`/video_feed` 串流中
每一幀、`/mesh_data` 每次輪詢）都會更新它。背景執行緒每輪迴圈檢查：超過
`IDLE_TIMEOUT_SEC`（5 秒）沒人存取，就完整呼叫 `cam.stop()` + `cam.close()`。

**關鍵發現**：只是不呼叫 `requestFrame()` 是不夠的——Arducam SDK 內部有自己的
原生擷取/深度運算執行緒，只要呼叫過 `cam.start()`，就算應用端完全不讀取資料，
那條執行緒依然會持續佔用約 18% CPU。必須真正呼叫 `close()` 才能讓那條執行緒停下來。

實測效果：
| 狀態 | CPU 使用率（樹莓派 4 核心） |
|---|---|
| 有人連線觀看 | ~126% |
| 閒置（相機已關閉）| **0%** |
| 重新連線 | 約 1 秒內恢復（`close→open→start` 完整重啟循環，~0.22 秒） |

好處：除了省 CPU，也能減少 ToF 感測器（尤其是雷射發射器 VCSEL）長時間運轉的損耗，
延長硬體壽命。

**踩過的坑**：一開始想用比較輕量的 `cam.stop()` + `cam.start()`（不整個 close/open）
循環，但實測 SDK 回傳錯誤代碼、狀態不明確；改用完整的 `close()` → `open()` →
`start()` 才是可靠的做法。

### 10. 一個真實的 Python 坑：`global` 不會自動穿透巢狀函式

改寫 `camera_worker` 支援閒置開關相機時，把開相機的邏輯搬進一個巢狀函式
`open_camera()`。外層函式宣告了 `global _camera_info`，但**巢狀函式不會繼承這個宣告**——
在 `open_camera()` 裡面對 `_camera_info` 賦值，其實是建立了一個函式內的區域變數，
從來沒真的寫回模組全域變數。結果就是相機明明正常運作、`mesh_data` 也有資料，
但 `/camera_info` 一直回傳空的 `{}`，導致前端的相機圖示/視錐畫不出來。
修法：在巢狀函式裡也加一行 `global _camera_info`。

## 網頁路由一覽

| 路由 | 說明 |
|---|---|
| `GET /` | 2D 深度圖頁面（彩虹色深度圖 + MJPEG） |
| `GET /video_feed` | MJPEG 串流本體 |
| `GET /pointcloud` | 3D 立體網格檢視頁面 |
| `GET /mesh_data` | 網格資料，二進位格式（見上方「更新率調校」） |
| `GET /camera_info` | 相機內參與目前量測範圍（給前端畫視錐用） |
| `GET /settings` | 目前的時間平滑/量測範圍設定、是否已有校正資料 |
| `GET /set_temporal_frames?frames=N` | 調整時間平滑的影格數（1~20） |
| `GET /set_range?mm=2000\|4000` | 切換量測範圍（2M/4M），相機不用重開 |
| `GET /calibrate_flat` | **維護後門**（無 UI 按鈕）：對目前畫面下方橫帶重新做一次自動校正，寫入該量測範圍的修正係數表 |
| `GET /clear_calibration?mm=2000\|4000` | **維護後門**（無 UI 按鈕）：清除該量測範圍的校正係數，回退成只用理論公式修正 |

目前網頁上的風琴式設定面板只剩三區塊：統計（頂點/面/FPS）、量測範圍
（2M/4M 切換）、時間平滑（滑桿）。原本的「自動校正」「扭曲修正強度」
「扭曲修正衰減率」UI 已移除（見上方第 2 節），對應的校正功能只能用
`/calibrate_flat`、`/clear_calibration` 這兩個後門路由（例如用 curl）操作。

## 如何啟動

### 手動啟動（開發/測試用）
```bash
cd ~/gemini_workspace/LearningArea/11_tofCameraEnvSenser
./venv/bin/python3 tof_stream_server.py
```

手機/電腦瀏覽器打開：
```
http://<樹莓派IP>:5000/            深度圖
http://<樹莓派IP>:5000/pointcloud  3D 網格
```

在家用區網用 `192.168.x.x`；在外面用 Tailscale 連，要用樹莓派的 **Tailscale IP**
（`tailscale ip -4` 查得到），因為這台機器沒有開 subnet router，`192.168.x.x`
在 tailnet 外連不到。

### 常駐服務（正式使用）
```bash
sudo cp tof-stream.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable tof-stream.service
sudo systemctl start tof-stream.service
sudo systemctl status tof-stream.service
```

`Restart=always` 會在程式當掉時自動重啟；`WantedBy=multi-user.target` 讓它開機自動啟動。

## 已知限制 / 之後可以做的事

- Flask 內建的開發伺服器不是給正式環境用的（`app.run()` 會印警告），流量大時建議
  換成 `waitress` 或 `gunicorn`
- 網頁完全沒有身分驗證，設計上假設只在自己的 Tailscale/區網內使用
- 自適應網格化（四元樹合併平坦區域）程式碼已寫好但關閉中，之後有需要可以重新打開評估
- `EnvSenser`（環境感測）這個資料夾名稱暗示的功能還沒做：例如依距離判斷有沒有人靠近、
  距離門檻觸發事件等，都還只是點子階段
- 討論中、尚未實作：移動/旋轉時持續掃描並拼接環境 3D（簡易 SLAM）。純陀螺儀/
  加速度計積分位移不可行（誤差隨時間平方成長）；純深度幀對幀 ICP 面對大片平坦
  牆面容易沿平面內滑移飄走。建議路線：先加一顆便宜、內建感測融合的 IMU（例如
  BNO055）只做旋轉融合，轉動時場景即時轉正；再用 Open3D 的 RGBD odometry，
  IMU 旋轉當初始猜測去約束幀對幀 ICP，估出完整 6-DoF pose 做拼接；長時間漂移
  明顯的話再考慮 TSDF 融合或迴路偵測（評估是否換底層到 RTAB-Map）
