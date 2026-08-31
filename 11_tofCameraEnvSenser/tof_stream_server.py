#!/usr/bin/env python3
"""
Arducam ToF Camera 深度影像 / 3D 點雲串流伺服器

啟動一個 Flask 網頁伺服器：
  - "/"            深度彩色圖 MJPEG 串流（給手機/電腦瀏覽器看）
  - "/pointcloud"  即時 3D 立體網格檢視頁面（Three.js，可用手指拖曳旋轉/縮放）
  - "/mesh_data"   立體網格資料 JSON API（給 /pointcloud 頁面輪詢用）

使用方式:
    ./venv/bin/python3 tof_stream_server.py

手機瀏覽器打開:
    http://<樹莓派IP>:5000/            深度圖
    http://<樹莓派IP>:5000/pointcloud  3D 點雲
"""

import json
import threading
import time

import cv2
import numpy as np
from flask import Flask, Response, render_template_string, request

import ArducamDepthCamera as ac

# ---- 可調參數 ----
MAX_DISTANCE = 4000  # 量測範圍上限 (mm)，可選 2000 或 4000
CONFIDENCE_VALUE = 30  # 低於此信心值的像素視為不可信，顯示為黑色/濾除
JPEG_QUALITY = 80
HOST = "0.0.0.0"
PORT = 5000

# 點雲降採樣間隔 (每隔幾個像素取一點)，數字越大點越少、手機端渲染越輕鬆
POINTCLOUD_STRIDE = 2

# 相鄰網格點深度差超過這個值 (mm) 就視為前景/背景的邊緣，不連成面
# (避免前景物體邊緣被一片「拉伸的布」黏到後面的背景上)
MAX_DEPTH_JUMP_MM = 150.0

# 頂點鬆弛 (Laplacian smoothing) 參數：讓每個頂點跟鄰居的位置互相拉近，
# 撫平逐像素雜訊造成的尖刺。ALPHA 是每次迭代跟鄰居平均值的混合比例，
# 太高會讓表面過度收縮/磨圓，太低則平滑效果不明顯。
SMOOTH_ITERATIONS = 4
SMOOTH_ALPHA = 0.5

# 九宮格平均 (3x3 box blur) 參數：在建網格前，先把每個像素的深度值換成
# 周圍 3x3 (含自己共9個) 有效像素的平均值，等於在原始解析度上先做一次
# 低通濾波，跟 Laplacian 頂點鬆弛疊加使用效果更平滑。
BOX_BLUR_ENABLED = True
BOX_BLUR_KERNEL = 3

# 跨影格時間平滑 (Temporal EMA) 參數：把這一幀的深度跟過去幾幀做加權平均，
# 隨機雜訊會因為在時間軸上互相抵消而消失。TEMPORAL_FRAMES 概念上是「平滑
# 橫跨幾個影格」，數字越大畫面越穩定乾淨，但物體移動時延遲感也越明顯。
# 5 是預設建議值 (低雜訊與低延遲間的折衷)，可用網頁上的拖桿即時調整，
# 1 等於關閉時間平滑 (只看當下這一幀)。
TEMPORAL_FRAMES_DEFAULT = 5
TEMPORAL_FRAMES_MIN = 1
TEMPORAL_FRAMES_MAX = 20

# 自適應網格化 (adaptive meshing)：把幾乎共平面的一大塊區域合併成一個大
# 四邊形 (2 個三角形)，只有物體邊緣、曲面變化大的地方才保留高密度的小
# 三角形，藉此在不犧牲細節的前提下大幅減少三角面數量。
# 用四元樹遞迴實作：從 ADAPTIVE_MAX_BLOCK x ADAPTIVE_MAX_BLOCK 的區塊開始，
# 平坦就合併，不夠平坦就切成 4 塊繼續判斷，直到縮小到 1 格為止。
ADAPTIVE_MESH_ENABLED = False  # 使用者評估後覺得效果不是想要的，先關閉保留程式碼
ADAPTIVE_MAX_BLOCK = 8  # 最大合併區塊邊長 (以降採樣後的網格為單位)，需為 2 的冪
ADAPTIVE_FLAT_THRESHOLD_MM = 15.0  # 區塊內深度偏離雙線性預測值超過這個值就視為不夠平坦

# 沒有瀏覽器連進來的時候，暫停讀取影格與所有運算 (平滑、建網格、編碼)，
# 省下 CPU；超過這麼多秒沒有任何請求，就視為「沒人在看」。
IDLE_TIMEOUT_SEC = 5.0

app = Flask(__name__)

# 全域共享的最新資料，供多個瀏覽器連線共用
_frame_lock = threading.Lock()
_latest_jpeg = None
_latest_mesh = None  # 已編碼好的 JSON bytes: {"positions":[[x,y,z],...],"indices":[i0,i1,i2,...]}
_camera_ready = threading.Event()
_camera_info = {}  # {"fx","fy","cx","cy","width","height","range_mm"}，相機開好後才會有值

# 用戶端活動偵測：/video_feed、/mesh_data 等路由每次被存取都會更新這個時間戳，
# camera_worker 依此判斷目前有沒有人在看，沒人看就跳過整條處理流程。
_activity_lock = threading.Lock()
_last_client_time = 0.0


def touch_activity():
    global _last_client_time
    with _activity_lock:
        _last_client_time = time.time()


def has_active_clients():
    with _activity_lock:
        last = _last_client_time
    return (time.time() - last) <= IDLE_TIMEOUT_SEC

# 時間平滑的可調狀態：影格數 (拖桿控制) 與上一幀的 EMA 深度值
_temporal_lock = threading.Lock()
_temporal_frames = TEMPORAL_FRAMES_DEFAULT
_ema_depth = None


def get_preview_rgb(preview: np.ndarray, confidence: np.ndarray) -> np.ndarray:
    preview = np.nan_to_num(preview)
    preview[confidence < CONFIDENCE_VALUE] = (0, 0, 0)
    return preview


def temporal_smooth_depth(depth_buf, confidence_buf):
    """跨影格時間平滑 (EMA)：跟過去幾幀的深度做加權平均，抵消隨機雜訊。

    frames 數字換算成每幀更新權重 alpha = 2/(frames+1)，frames 越大 alpha
    越小、新的一幀影響越小，畫面越穩定但延遲也越明顯。這一幀信心不足的
    像素不拿來更新 EMA (避免用雜訊/黑洞污染歷史平均值)，維持上次的值。
    """
    global _ema_depth

    with _temporal_lock:
        frames = _temporal_frames
    alpha = 2.0 / (frames + 1.0)

    valid = (confidence_buf >= CONFIDENCE_VALUE) & (depth_buf > 0)
    depth_f = depth_buf.astype(np.float32)

    with _temporal_lock:
        if _ema_depth is None or _ema_depth.shape != depth_f.shape:
            _ema_depth = depth_f.copy()
        else:
            _ema_depth = np.where(
                valid, alpha * depth_f + (1 - alpha) * _ema_depth, _ema_depth
            )
        result = _ema_depth.copy()

    return result


def box_blur_depth(depth_buf, confidence_buf, kernel_size):
    """把每個像素的深度值換成周圍 kernel_size x kernel_size (預設 3x3=9 點)
    有效像素的平均值，等於在原始解析度上先做一次低通濾波去雜訊。

    只用「有效」像素 (信心值足夠、深度>0) 參與平均，避免無效的黑洞/背景
    像素把周圍真實表面的深度拉偏。
    """
    valid = (confidence_buf >= CONFIDENCE_VALUE) & (depth_buf > 0)
    valid_f = valid.astype(np.float32)
    masked = np.where(valid, depth_buf, 0).astype(np.float32)

    kernel = np.ones((kernel_size, kernel_size), dtype=np.float32)
    sum_depth = cv2.filter2D(masked, -1, kernel, borderType=cv2.BORDER_CONSTANT)
    sum_valid = cv2.filter2D(valid_f, -1, kernel, borderType=cv2.BORDER_CONSTANT)

    avg = np.zeros_like(depth_buf, dtype=np.float32)
    np.divide(sum_depth, sum_valid, out=avg, where=sum_valid > 0)
    # 平均不到任何有效鄰居的地方 (通常本身就是無效點) 維持原值即可，
    # 反正後面仍會用原始的信心值/深度判斷有效性，這裡不影響結果。
    return np.where(sum_valid > 0, avg, depth_buf).astype(depth_buf.dtype)


def laplacian_smooth_grid(pos_grid, valid, iterations, alpha):
    """對規則網格上的頂點位置做 Laplacian 鬆弛：

    每次迭代把每個頂點的位置，跟它上下左右四個有效鄰居的平均位置
    做加權混合，重複幾次後尖銳的雜訊起伏會被拉平成平滑的表面。
    只在 valid 為 True 的格點上進行，無效格點不參與、也不被更新。
    """
    pos = pos_grid.copy()
    valid_f = valid.astype(np.float64)[..., None]

    for _ in range(iterations):
        up = np.zeros_like(pos); up[:-1] = pos[1:]
        down = np.zeros_like(pos); down[1:] = pos[:-1]
        left = np.zeros_like(pos); left[:, :-1] = pos[:, 1:]
        right = np.zeros_like(pos); right[:, 1:] = pos[:, :-1]

        v_up = np.zeros_like(valid_f); v_up[:-1] = valid_f[1:]
        v_down = np.zeros_like(valid_f); v_down[1:] = valid_f[:-1]
        v_left = np.zeros_like(valid_f); v_left[:, :-1] = valid_f[:, 1:]
        v_right = np.zeros_like(valid_f); v_right[:, 1:] = valid_f[:, :-1]

        neighbor_sum = up * v_up + down * v_down + left * v_left + right * v_right
        neighbor_count = np.maximum(v_up + v_down + v_left + v_right, 1e-6)
        neighbor_avg = neighbor_sum / neighbor_count

        blended = pos * (1 - alpha) + neighbor_avg * alpha
        pos = np.where(valid[..., None], blended, pos)

    return pos


def _bilinear_grid(z00, z10, z01, z11, size):
    """回傳 (size+1)x(size+1) 網格上，由四個角落深度值雙線性內插出的預測值。"""
    t = np.linspace(0.0, 1.0, size + 1)
    tx, ty = np.meshgrid(t, t)
    top = z00 * (1 - tx) + z10 * tx
    bottom = z01 * (1 - tx) + z11 * tx
    return top * (1 - ty) + bottom * ty


def _emit_leaf_rect(r0, r1, c0, c1, valid, jump_depth, idx_map, tris):
    """退回最基本的逐格處理：矩形範圍內每一格分別判斷、各自兜成 2 個三角形。"""
    for r in range(r0, r1):
        for c in range(c0, c1):
            if not (valid[r, c] and valid[r, c + 1] and valid[r + 1, c] and valid[r + 1, c + 1]):
                continue
            zs = (jump_depth[r, c], jump_depth[r, c + 1], jump_depth[r + 1, c], jump_depth[r + 1, c + 1])
            if max(zs) - min(zs) > MAX_DEPTH_JUMP_MM:
                continue
            v00, v10 = idx_map[r, c], idx_map[r, c + 1]
            v01, v11 = idx_map[r + 1, c], idx_map[r + 1, c + 1]
            tris.append((v00, v10, v01))
            tris.append((v10, v11, v01))


def _adaptive_block(r0, c0, size, valid, jump_depth, flat_depth, idx_map, tris):
    """四元樹遞迴：這個區塊夠平坦就合併成一個大四邊形，否則切成 4 塊繼續判斷。

    jump_depth 用原始未平滑的深度做「是否為真實物體邊緣」的判斷 (門檻較大，
    150mm，雜訊不太會誤判)；flat_depth 用平滑過的深度做「夠不夠平坦可以合併」
    的判斷 (門檻較小，才不會被平滑前的逐像素雜訊誤判成處處都不平坦)。
    """
    if size == 1:
        _emit_leaf_rect(r0, r0 + 1, c0, c0 + 1, valid, jump_depth, idx_map, tris)
        return

    if not valid[r0:r0 + size + 1, c0:c0 + size + 1].all():
        h = size // 2
        _adaptive_block(r0, c0, h, valid, jump_depth, flat_depth, idx_map, tris)
        _adaptive_block(r0, c0 + h, h, valid, jump_depth, flat_depth, idx_map, tris)
        _adaptive_block(r0 + h, c0, h, valid, jump_depth, flat_depth, idx_map, tris)
        _adaptive_block(r0 + h, c0 + h, h, valid, jump_depth, flat_depth, idx_map, tris)
        return

    block_jump = jump_depth[r0:r0 + size + 1, c0:c0 + size + 1]
    if (block_jump.max() - block_jump.min()) > MAX_DEPTH_JUMP_MM:
        h = size // 2
        _adaptive_block(r0, c0, h, valid, jump_depth, flat_depth, idx_map, tris)
        _adaptive_block(r0, c0 + h, h, valid, jump_depth, flat_depth, idx_map, tris)
        _adaptive_block(r0 + h, c0, h, valid, jump_depth, flat_depth, idx_map, tris)
        _adaptive_block(r0 + h, c0 + h, h, valid, jump_depth, flat_depth, idx_map, tris)
        return

    block_flat = flat_depth[r0:r0 + size + 1, c0:c0 + size + 1]
    z00, z10 = flat_depth[r0, c0], flat_depth[r0, c0 + size]
    z01, z11 = flat_depth[r0 + size, c0], flat_depth[r0 + size, c0 + size]
    predicted = _bilinear_grid(z00, z10, z01, z11, size)

    if np.abs(block_flat - predicted).max() <= ADAPTIVE_FLAT_THRESHOLD_MM:
        v00, v10 = idx_map[r0, c0], idx_map[r0, c0 + size]
        v01, v11 = idx_map[r0 + size, c0], idx_map[r0 + size, c0 + size]
        tris.append((v00, v10, v01))
        tris.append((v10, v11, v01))
        return

    h = size // 2
    _adaptive_block(r0, c0, h, valid, jump_depth, flat_depth, idx_map, tris)
    _adaptive_block(r0, c0 + h, h, valid, jump_depth, flat_depth, idx_map, tris)
    _adaptive_block(r0 + h, c0, h, valid, jump_depth, flat_depth, idx_map, tris)
    _adaptive_block(r0 + h, c0 + h, h, valid, jump_depth, flat_depth, idx_map, tris)


def adaptive_triangulate(valid, jump_depth, flat_depth, idx_map):
    """把整張網格切成 ADAPTIVE_MAX_BLOCK 大小的區塊，各自跑四元樹合併；
    邊界湊不滿一整個區塊的地方直接退回逐格處理 (只影響最右/最下一小條)。
    """
    nr, nc = valid.shape
    tris = []
    size = ADAPTIVE_MAX_BLOCK

    r0 = 0
    while r0 + size <= nr - 1:
        c0 = 0
        while c0 + size <= nc - 1:
            _adaptive_block(r0, c0, size, valid, jump_depth, flat_depth, idx_map, tris)
            c0 += size
        if c0 < nc - 1:
            _emit_leaf_rect(r0, r0 + size, c0, nc - 1, valid, jump_depth, idx_map, tris)
        r0 += size
    if r0 < nr - 1:
        _emit_leaf_rect(r0, nr - 1, 0, nc - 1, valid, jump_depth, idx_map, tris)

    return tris


def depth_to_mesh(depth_buf, confidence_buf, fx, fy, cx, cy):
    """把深度圖 (240x180) 依相機內參轉成三角網格 (頂點 + 面)。

    深度圖本身就是規則網格 (每個像素是一個格點)，所以直接把相鄰 2x2
    格點兜成兩個三角形即可，不需要額外做點雲三角化。深度差太大的地方
    (物體邊緣) 會被跳過，避免產生連接前景與背景的「拖影」面；頂點位置
    會先做九宮格平均 (box blur)，再做 Laplacian 鬆弛，兩層疊加撫平
    逐像素雜訊造成的尖刺。
    """
    h, w = depth_buf.shape

    depth_for_pos = depth_buf
    if BOX_BLUR_ENABLED:
        depth_for_pos = box_blur_depth(depth_buf, confidence_buf, BOX_BLUR_KERNEL)

    rows = np.arange(0, h, POINTCLOUD_STRIDE)
    cols = np.arange(0, w, POINTCLOUD_STRIDE)

    # sub_depth 用「原始未平滑」的深度：用來判斷有效性跟物體邊緣才準確；
    # sub_depth_pos 用「平滑過」的深度：實際拿來計算頂點的 3D 座標。
    sub_depth = depth_buf[np.ix_(rows, cols)]
    sub_depth_pos = depth_for_pos[np.ix_(rows, cols)]
    sub_conf = confidence_buf[np.ix_(rows, cols)]

    valid = (sub_conf >= CONFIDENCE_VALUE) & (sub_depth > 0)

    uu, vv = np.meshgrid(cols, rows)  # 都是 shape (nr, nc)
    x = (uu - cx) * sub_depth_pos / fx
    y = (vv - cy) * sub_depth_pos / fy

    # mm -> 公尺，y 軸反轉讓畫面方向直覺
    pos_grid = np.stack([x / 1000.0, -y / 1000.0, sub_depth_pos / 1000.0], axis=-1)
    pos_grid = laplacian_smooth_grid(pos_grid, valid, SMOOTH_ITERATIONS, SMOOTH_ALPHA)

    idx_map = -np.ones(valid.shape, dtype=np.int64)
    idx_map[valid] = np.arange(int(valid.sum()))

    positions = pos_grid[valid]

    if ADAPTIVE_MESH_ENABLED:
        # 自適應網格化：平坦區域合併成大三角形，只有邊緣/曲面保留細節
        tris = adaptive_triangulate(valid, sub_depth, sub_depth_pos, idx_map)
        indices = (
            np.array(tris, dtype=np.int64).reshape(-1)
            if tris
            else np.zeros(0, dtype=np.int64)
        )
    else:
        # 找出四個角都有效、且深度差在容許範圍內的格子，兜成兩個三角形
        # (深度差門檻仍用原始未平滑的深度值判斷，才能準確抓到真實邊緣)
        quad_valid = valid[:-1, :-1] & valid[:-1, 1:] & valid[1:, :-1] & valid[1:, 1:]
        z00, z10 = sub_depth[:-1, :-1], sub_depth[:-1, 1:]
        z01, z11 = sub_depth[1:, :-1], sub_depth[1:, 1:]
        zs = np.stack([z00, z10, z01, z11])
        jump_ok = (zs.max(axis=0) - zs.min(axis=0)) <= MAX_DEPTH_JUMP_MM
        quad_ok = quad_valid & jump_ok

        ri, ci = np.nonzero(quad_ok)
        v00 = idx_map[ri, ci]
        v10 = idx_map[ri, ci + 1]
        v01 = idx_map[ri + 1, ci]
        v11 = idx_map[ri + 1, ci + 1]

        tri1 = np.stack([v00, v10, v01], axis=1)
        tri2 = np.stack([v10, v11, v01], axis=1)
        indices = np.concatenate([tri1, tri2], axis=0).reshape(-1)

    # 壓縮：合併大四邊形後，很多內部格點不會被任何三角形引用到，
    # 這裡只留下真正用到的頂點，減少要傳給網頁的資料量。
    if indices.size:
        used = np.unique(indices)
        remap = -np.ones(positions.shape[0], dtype=np.int64)
        remap[used] = np.arange(used.shape[0])
        positions = positions[used]
        indices = remap[indices]

    return {
        "positions": positions.tolist(),
        "indices": indices.tolist(),
    }


def camera_worker():
    """背景執行緒：持續從 ToF 相機讀取深度資料，更新畫面與立體網格。

    沒有瀏覽器連進來時，會把相機整個關掉 (stop+close)，不只是不讀取資料——
    因為實測發現 Arducam SDK 內部有自己的擷取/深度運算執行緒，只要呼叫過
    cam.start()，即使應用端完全不呼叫 requestFrame()，那條執行緒依然會
    持續佔用 CPU (約 18%)。真正要省下這筆資源，必須整個關閉相機。
    """
    global _latest_jpeg, _latest_mesh, _camera_info

    cam = None
    fx = fy = cx = cy = max_range = None

    def open_camera():
        nonlocal cam, fx, fy, cx, cy, max_range
        global _camera_info

        print("正在開啟 ToF 相機 (CSI)...")
        new_cam = ac.ArducamCamera()
        ret = new_cam.open(ac.Connection.CSI, 0)
        if ret != 0:
            print(f"開啟相機失敗，錯誤代碼: {ret}")
            return False

        ret = new_cam.start(ac.FrameType.DEPTH)
        if ret != 0:
            print(f"啟動相機失敗，錯誤代碼: {ret}")
            new_cam.close()
            return False

        new_cam.setControl(ac.Control.RANGE, MAX_DISTANCE)
        max_range = new_cam.getControl(ac.Control.RANGE)

        # 關掉相機的自動幀率：預設會為了遠距量測的訊噪比自動把幀率降到約
        # 6fps，關閉後即使維持 4M 遠距模式，實測也能拿到約 30fps 的原生擷取率。
        new_cam.setControl(ac.Control.AUTO_FRAME_RATE, 0)

        # 相機內參 (實際值需除以 100，見官方 API 說明)
        fx = new_cam.getControl(ac.Control.INTRINSIC_FX) / 100.0
        fy = new_cam.getControl(ac.Control.INTRINSIC_FY) / 100.0
        cx = new_cam.getControl(ac.Control.INTRINSIC_CX) / 100.0
        cy = new_cam.getControl(ac.Control.INTRINSIC_CY) / 100.0

        info = new_cam.getCameraInfo()
        print(f"相機解析度: {info.width}x{info.height}，量測範圍: {max_range}mm")
        print(f"內參: fx={fx:.2f} fy={fy:.2f} cx={cx:.2f} cy={cy:.2f}")

        _camera_info = {
            "fx": fx, "fy": fy, "cx": cx, "cy": cy,
            "width": info.width, "height": info.height,
            "range_mm": max_range,
        }

        cam = new_cam
        return True

    def close_camera():
        nonlocal cam
        if cam is not None:
            print("閒置超過時間，關閉相機省電...")
            cam.stop()
            cam.close()
            cam = None

    if not open_camera():
        return
    _camera_ready.set()

    try:
        while True:
            if not has_active_clients():
                # 沒有瀏覽器在看：把相機整個關掉，定期醒來檢查一次有沒有人回來。
                close_camera()
                time.sleep(0.3)
                continue

            if cam is None:
                print("偵測到有人連進來，重新開啟相機...")
                if not open_camera():
                    time.sleep(1.0)
                    continue

            frame = cam.requestFrame(2000)
            if frame is not None and isinstance(frame, ac.DepthData):
                depth_buf = frame.depth_data
                confidence_buf = frame.confidence_data

                result_image = (depth_buf * (255.0 / max_range)).astype(np.uint8)
                result_image = cv2.applyColorMap(result_image, cv2.COLORMAP_RAINBOW)
                result_image = get_preview_rgb(result_image, confidence_buf)

                depth_temporal = temporal_smooth_depth(depth_buf, confidence_buf)
                mesh = depth_to_mesh(depth_temporal, confidence_buf, fx, fy, cx, cy)

                # 240x180 太小，放大方便手機觀看
                preview_image = cv2.resize(
                    result_image, (720, 540), interpolation=cv2.INTER_NEAREST
                )

                ok, jpeg = cv2.imencode(
                    ".jpg", preview_image, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]
                )

                # JSON 序列化在這裡 (背景執行緒) 只做一次，不管等一下有幾個
                # 瀏覽器來輪詢 /mesh_data，都直接回傳同一份編碼好的 bytes，
                # 避免大量重複的 JSON 編碼跟這個執行緒互搶 GIL、拖慢擷取速度。
                mesh_json = json.dumps(mesh).encode("utf-8")

                with _frame_lock:
                    if ok:
                        _latest_jpeg = jpeg.tobytes()
                    _latest_mesh = mesh_json

                cam.releaseFrame(frame)
            else:
                time.sleep(0.01)
    finally:
        close_camera()


def mjpeg_generator():
    """回傳 multipart/x-mixed-replace 的 MJPEG 串流，讓 <img> 標籤直接播放。"""
    boundary = b"--frame"
    while True:
        # 只要這個串流還在被讀取 (瀏覽器分頁還開著)，就持續回報「有人在看」，
        # 讓 camera_worker 保持運算，不會播到一半突然斷掉。
        touch_activity()
        with _frame_lock:
            jpeg = _latest_jpeg
        if jpeg is None:
            time.sleep(0.05)
            continue
        yield (
            boundary + b"\r\n"
            b"Content-Type: image/jpeg\r\n"
            b"Content-Length: " + str(len(jpeg)).encode() + b"\r\n\r\n"
            + jpeg + b"\r\n"
        )
        time.sleep(0.03)  # 約 30fps 上限，避免過度佔用頻寬


DEPTH_PAGE = """
<!DOCTYPE html>
<html lang="zh-Hant">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>ToF Camera 即時串流</title>
    <style>
        body {
            margin: 0;
            background: #111;
            color: #eee;
            font-family: -apple-system, sans-serif;
            display: flex;
            flex-direction: column;
            align-items: center;
        }
        h1 { font-size: 1.1rem; margin: 0.8rem 0; }
        img {
            width: 100%;
            max-width: 720px;
            height: auto;
            background: #000;
        }
        .status { font-size: 0.85rem; color: #8f8; margin-bottom: 0.5rem; }
        a { color: #6cf; }
    </style>
</head>
<body>
    <h1>Arducam ToF Camera 深度影像串流</h1>
    <div class="status">量測範圍上限: {{ max_distance }}mm ・ <a href="/pointcloud">切換到 3D 點雲檢視 →</a></div>
    <img src="/video_feed" alt="ToF stream">
</body>
</html>
"""

POINTCLOUD_PAGE = """
<!DOCTYPE html>
<html lang="zh-Hant">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
    <title>ToF Camera 3D 點雲</title>
    <style>
        html, body {
            margin: 0;
            height: 100%;
            background: #eef0f3;
            color: #1d2328;
            font-family: -apple-system, sans-serif;
            overflow: hidden;
            -webkit-user-select: none; user-select: none; -webkit-touch-callout: none;
        }
        #stage { position: relative; width: 100%; height: 100%; }
        #stage canvas { display: block; position: absolute; inset: 0; width: 100%; height: 100%; touch-action: none; }
        #hud {
            position: fixed;
            top: 0; left: 0; right: 0;
            padding: 0.6rem 0.9rem;
            font-size: 0.8rem;
            color: #6b7680;
            background: linear-gradient(rgba(238,240,243,.9), transparent);
            z-index: 10;
            display: flex;
            justify-content: space-between;
            pointer-events: none;
        }
        #hud a { color: #2e7bb8; pointer-events: auto; }
        #cube {
            position: absolute; top: 12px; right: 10px; z-index: 10;
            width: 96px; height: 96px; touch-action: none; cursor: grab;
        }
        #cube:active { cursor: grabbing; }
        #fitBtn {
            position: absolute; left: 12px; bottom: 16px; z-index: 10;
            background: rgba(255,255,255,.85); border: 1px solid #d7dce2;
            color: #1d2328; border-radius: 8px; padding: 8px 12px; font-size: 0.8rem;
            box-shadow: 0 3px 14px rgba(29,35,40,.12);
            backdrop-filter: blur(4px);
        }
        #fitBtn:active { background: #e7eaee; }
        #settings {
            position: absolute; left: 12px; bottom: 60px; z-index: 10; width: 210px;
            background: rgba(255,255,255,.92); border: 1px solid #d7dce2; border-radius: 9px;
            padding: 10px 12px; box-shadow: 0 3px 14px rgba(29,35,40,.12);
            font-size: 0.78rem; backdrop-filter: blur(4px);
        }
        #settings .lbl { display: flex; justify-content: space-between; color: #6b7680; margin-bottom: 4px; }
        #settings .lbl b { color: #1d2328; font-weight: 600; }
        #settings input[type=range] {
            width: 100%; -webkit-appearance: none; appearance: none; height: 2px;
            background: #d7dce2; border-radius: 2px; outline: none; margin: 0;
        }
        #settings input[type=range]::-webkit-slider-thumb {
            -webkit-appearance: none; width: 14px; height: 14px; border-radius: 50%;
            background: #2e7bb8; border: 2px solid #fff; cursor: pointer;
            box-shadow: 0 1px 3px rgba(29,35,40,.25);
        }
        #settings .hint { color: #9aa5b0; margin-top: 5px; line-height: 1.4; }
    </style>
</head>
<body>
    <div id="stage">
        <canvas id="cube" aria-label="視角方塊"></canvas>
        <div id="settings">
            <div class="lbl"><span>時間平滑 (影格數)</span><b id="temporalVal">5</b></div>
            <input type="range" id="temporalSlider" min="1" max="20" step="1" value="5">
            <div class="hint">數字越大畫面越穩定乾淨，但移動中的物體延遲感也越明顯；1 為關閉。</div>
        </div>
        <button id="fitBtn">重新對正視角</button>
    </div>
    <div id="hud">
        <span id="stats">連線中...</span>
        <a href="/">← 切換到 2D 深度圖</a>
    </div>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
    <script>
    (function(){
    "use strict";

    var stage = document.getElementById("stage");
    var scene = new THREE.Scene();
    scene.background = new THREE.Color(0xeef0f3);

    var camera = new THREE.PerspectiveCamera(50, 1, 0.01, 100);
    var renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    stage.appendChild(renderer.domElement);

    scene.add(new THREE.HemisphereLight(0xffffff, 0x30303a, 0.9));
    var key = new THREE.DirectionalLight(0xffffff, 0.7); key.position.set(0.6, 1, -0.4); scene.add(key);
    var rim = new THREE.DirectionalLight(0x88aaff, 0.35); rim.position.set(-0.6, -0.3, 0.8); scene.add(rim);

    var grid = new THREE.GridHelper(4, 8, 0x9aa5b0, 0xc4ccd4);
    scene.add(grid);
    scene.add(new THREE.AxesHelper(0.2));

    var surfaceObj = null;
    var surfaceGeo = null;
    // 淺灰色實體材質 (跟 obj-workbench 的網格檢視一致)，不用深度上色，
    // 靠燈光的明暗變化來呈現立體感；正反兩面都畫出來才不會從某些角度看穿變透明
    var surfaceMat = new THREE.MeshStandardMaterial({
        color: 0x8e9aa6, roughness: 0.58, metalness: 0.05, flatShading: true, side: THREE.DoubleSide
    });

    /* ---- 相機圖示 + 拍攝範圍視錐 (在原點，也就是 ToF 相機本身的位置) ---- */
    var frustumGroup = new THREE.Group();
    scene.add(frustumGroup);

    function buildCameraIcon() {
        var g = new THREE.Group();
        var lineMat = new THREE.LineBasicMaterial({ color: 0x252c33 });

        // 極簡機身：一個小方塊的線框，鏡頭朝 +Z (跟深度方向一致)
        var body = new THREE.LineSegments(
            new THREE.EdgesGeometry(new THREE.BoxGeometry(0.05, 0.035, 0.03)),
            lineMat
        );
        body.position.set(0, 0, -0.015);
        g.add(body);

        // 鏡頭：一個朝前的小圓錐線框
        var lens = new THREE.LineSegments(
            new THREE.EdgesGeometry(new THREE.ConeGeometry(0.016, 0.02, 16)),
            lineMat
        );
        lens.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), new THREE.Vector3(0, 0, 1));
        lens.position.set(0, 0, 0.01);
        g.add(lens);

        return g;
    }

    // info: {fx, fy, cx, cy, width, height, range_mm}
    function farCorner(info, u, v, z) {
        var x = (u - info.cx) * z / info.fx;
        var y = -(v - info.cy) * z / info.fy;
        return new THREE.Vector3(x, y, z);
    }

    function buildFrustum(info) {
        var g = new THREE.Group();
        g.add(buildCameraIcon());

        var z = info.range_mm / 1000.0;
        var c00 = farCorner(info, 0, 0, z);
        var c10 = farCorner(info, info.width, 0, z);
        var c01 = farCorner(info, 0, info.height, z);
        var c11 = farCorner(info, info.width, info.height, z);
        var origin = new THREE.Vector3(0, 0, 0);

        var edgeMat = new THREE.LineBasicMaterial({ color: 0x2e7bb8, transparent: true, opacity: 0.55 });

        // 四條從相機拉到最遠面四個角落的線 (四角錐的稜)
        var edgeGeo = new THREE.BufferGeometry().setFromPoints([
            origin, c00, origin, c10, origin, c01, origin, c11
        ]);
        g.add(new THREE.LineSegments(edgeGeo, edgeMat));

        // 最遠面的矩形外框
        var farGeo = new THREE.BufferGeometry().setFromPoints([
            c00, c10, c10, c11, c11, c01, c01, c00
        ]);
        g.add(new THREE.LineSegments(farGeo, edgeMat));

        // 最遠面上淡淡鋪一層半透明的面，比較容易看出「範圍到這裡為止」
        var fillGeo = new THREE.BufferGeometry();
        fillGeo.setAttribute("position", new THREE.Float32BufferAttribute(
            [c00, c10, c01, c10, c11, c01].flatMap(function (p) { return [p.x, p.y, p.z]; }), 3
        ));
        var fillMat = new THREE.MeshBasicMaterial({
            color: 0x2e7bb8, transparent: true, opacity: 0.06, side: THREE.DoubleSide, depthWrite: false
        });
        g.add(new THREE.Mesh(fillGeo, fillMat));

        return g;
    }

    function loadCameraFrustum() {
        fetch("/camera_info").then(function (res) { return res.json(); }).then(function (info) {
            if (!info || !info.width) { setTimeout(loadCameraFrustum, 1000); return; }
            while (frustumGroup.children.length) frustumGroup.remove(frustumGroup.children[0]);
            frustumGroup.add(buildFrustum(info));
        }).catch(function () { setTimeout(loadCameraFrustum, 1000); });
    }

    /* ---- 相機控制：以「網格上被點到的那一點」為支點旋轉，仿照 obj-workbench 的做法 ---- */
    var target = new THREE.Vector3(0, 0, 1.0);
    var camQuat = new THREE.Quaternion().setFromEuler(new THREE.Euler(-0.15, 0, 0, "YXZ"));
    var camR = 1.6;
    var pivotMarker = new THREE.Mesh(
        new THREE.SphereGeometry(1, 16, 12),
        new THREE.MeshBasicMaterial({ color: 0xff6a3d, depthTest: false })
    );
    pivotMarker.visible = false;
    pivotMarker.renderOrder = 9;
    scene.add(pivotMarker);
    var pivotRay = new THREE.Raycaster();

    function applyCam() {
        camR = Math.max(0.05, camR);
        var back = new THREE.Vector3(0, 0, 1).applyQuaternion(camQuat).multiplyScalar(camR);
        camera.position.copy(target).add(back);
        camera.quaternion.copy(camQuat);
        syncCube();
    }

    // 剛體繞固定支點旋轉相機 (位置與朝向一起轉)，支點在畫面上的投影位置全程不變
    function rotateAroundPivot(P, dx, dy) {
        var angle = Math.sqrt(dx * dx + dy * dy) * 0.007;
        if (angle < 1e-9) return;
        var right = new THREE.Vector3(1, 0, 0).applyQuaternion(camQuat);
        var up = new THREE.Vector3(0, 1, 0).applyQuaternion(camQuat);
        var axis = new THREE.Vector3().addScaledVector(right, -dy).addScaledVector(up, -dx).normalize();
        var dq = new THREE.Quaternion().setFromAxisAngle(axis, angle);
        var offset = camera.position.clone().sub(P).applyQuaternion(dq);
        camera.position.copy(P).add(offset);
        camQuat.premultiply(dq).normalize();
        camera.quaternion.copy(camQuat);
        target.copy(P);
        camR = offset.length();
        syncCube();
    }

    function pan(dx, dy) {
        var s = camR * 0.0022;
        var right = new THREE.Vector3(1, 0, 0).applyQuaternion(camQuat);
        var up2 = new THREE.Vector3(0, 1, 0).applyQuaternion(camQuat);
        target.addScaledVector(right, -dx * s).addScaledVector(up2, dy * s);
    }

    function findPivot(cx, cy) {
        if (!surfaceObj) return null;
        var r = renderer.domElement.getBoundingClientRect();
        var ndc = new THREE.Vector2(((cx - r.left) / r.width) * 2 - 1, -((cy - r.top) / r.height) * 2 + 1);
        pivotRay.setFromCamera(ndc, camera);
        var hits = pivotRay.intersectObject(surfaceObj, false);
        if (!hits.length) return null;
        var p = hits[0].point.clone();
        pivotMarker.scale.setScalar(0.012);
        pivotMarker.position.copy(p);
        pivotMarker.visible = true;
        return p;
    }

    (function () {
        var el = renderer.domElement, ptr = {}, mode = null, last = null, lastDist = 0, lastMid = null;
        var pivot = null;
        function pos(e) { return { x: e.clientX, y: e.clientY }; }
        function ids() { return Object.keys(ptr); }
        el.addEventListener("pointerdown", function (e) {
            e.preventDefault();
            el.setPointerCapture(e.pointerId); ptr[e.pointerId] = pos(e);
            if (ids().length === 1) {
                mode = (e.button === 2 || e.shiftKey) ? "pan" : "rot"; last = pos(e);
                if (mode === "rot") pivot = findPivot(e.clientX, e.clientY) || target.clone();
            } else if (ids().length === 2) {
                mode = "multi"; var p = twoPointer(); lastDist = p.d; lastMid = p.m;
            }
        });
        el.addEventListener("pointermove", function (e) {
            if (!(e.pointerId in ptr)) return;
            ptr[e.pointerId] = pos(e);
            if (mode === "rot" && last) {
                var p = pos(e);
                rotateAroundPivot(pivot, p.x - last.x, p.y - last.y);
                last = p;
            } else if (mode === "pan" && last) {
                var q = pos(e); pan(q.x - last.x, q.y - last.y); last = q; applyCam();
            } else if (mode === "multi") {
                var t = twoPointer();
                camR *= lastDist / Math.max(1, t.d);
                pan(t.m.x - lastMid.x, t.m.y - lastMid.y);
                lastDist = t.d; lastMid = t.m; applyCam();
            }
        });
        function up(e) {
            delete ptr[e.pointerId];
            if (ids().length === 0) { mode = null; last = null; pivot = null; pivotMarker.visible = false; }
            else if (ids().length === 1) { mode = "rot"; last = ptr[ids()[0]]; }
        }
        el.addEventListener("pointerup", up);
        el.addEventListener("pointercancel", up);
        el.addEventListener("contextmenu", function (e) { e.preventDefault(); });
        el.addEventListener("wheel", function (e) {
            e.preventDefault(); camR *= Math.pow(1.0016, e.deltaY); applyCam();
        }, { passive: false });
        function twoPointer() {
            var k = ids(), a = ptr[k[0]], b = ptr[k[1]];
            return { d: Math.hypot(a.x - b.x, a.y - b.y), m: { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 } };
        }
    })();

    function resize() {
        var w = stage.clientWidth, h = stage.clientHeight;
        if (!w || !h) return;
        renderer.setSize(w, h, true);
        camera.aspect = w / h; camera.updateProjectionMatrix();
    }
    window.addEventListener("resize", resize);

    function fitCamera() {
        if (!surfaceObj || !surfaceGeo) return;
        surfaceGeo.computeBoundingSphere();
        var s = surfaceGeo.boundingSphere;
        if (!s) return;
        target.copy(s.center);
        camR = Math.max(0.2, s.radius * 2.2);
        applyCam();
    }
    document.getElementById("fitBtn").addEventListener("click", fitCamera);

    /* ---- 時間平滑拖桿：讀取伺服器目前設定值，拖動時即時送給後端 ---- */
    var temporalSlider = document.getElementById("temporalSlider");
    var temporalVal = document.getElementById("temporalVal");

    fetch("/settings").then(function (res) { return res.json(); }).then(function (s) {
        temporalSlider.min = s.min; temporalSlider.max = s.max;
        temporalSlider.value = s.temporal_frames;
        temporalVal.textContent = s.temporal_frames;
    }).catch(function () {});

    temporalSlider.addEventListener("input", function () {
        temporalVal.textContent = temporalSlider.value;
        fetch("/set_temporal_frames?frames=" + temporalSlider.value).catch(function () {});
    });

    /* ---- 立體網格資料輪詢 ---- */
    var statsEl = document.getElementById("stats");
    var fitted = false;

    async function fetchMesh() {
        try {
            var res = await fetch("/mesh_data");
            var data = await res.json();
            var verts = data.positions, idx = data.indices;
            var n = verts.length;
            var positions = new Float32Array(n * 3);
            for (var i = 0; i < n; i++) {
                positions[i * 3] = verts[i][0];
                positions[i * 3 + 1] = verts[i][1];
                positions[i * 3 + 2] = verts[i][2];
            }

            if (surfaceObj) { scene.remove(surfaceObj); surfaceGeo.dispose(); }
            surfaceGeo = new THREE.BufferGeometry();
            surfaceGeo.setAttribute("position", new THREE.BufferAttribute(positions, 3));
            surfaceGeo.setIndex(idx);
            surfaceGeo.computeVertexNormals();
            surfaceObj = new THREE.Mesh(surfaceGeo, surfaceMat);
            scene.add(surfaceObj);

            statsEl.textContent = "頂點: " + n + " ・ 面: " + (idx.length / 3);

            if (!fitted && n > 0) { fitted = true; fitCamera(); }
        } catch (e) {
            statsEl.textContent = "連線失敗，重試中...";
        }
        setTimeout(fetchMesh, 40);
    }

    /* ---- 視角方塊 (ViewCube)：拖曳自由旋轉，點面快速定位到正視圖 ---- */
    var cubeCanvas = document.getElementById("cube");
    var cRenderer = new THREE.WebGLRenderer({ canvas: cubeCanvas, antialias: true, alpha: true });
    cRenderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    cRenderer.setSize(96, 96, true);

    var cScene = new THREE.Scene();
    var cCam = new THREE.OrthographicCamera(-1.42, 1.42, 1.42, -1.42, 0.1, 20);

    function faceTex(label) {
        var c = document.createElement("canvas"); c.width = c.height = 128;
        var x = c.getContext("2d");
        x.fillStyle = "#fafbfc"; x.fillRect(0, 0, 128, 128);
        x.strokeStyle = "#c2cad3"; x.lineWidth = 6; x.strokeRect(3, 3, 122, 122);
        x.fillStyle = "#252c33";
        x.font = "600 26px system-ui, sans-serif";
        x.textAlign = "center"; x.textBaseline = "middle";
        x.fillText(label, 64, 66);
        var t = new THREE.CanvasTexture(c);
        return t;
    }
    var cubeMats = ["右", "左", "上", "下", "前", "後"].map(function (s) {
        return new THREE.MeshBasicMaterial({ map: faceTex(s) });
    });
    var cubeMesh = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), cubeMats);
    cScene.add(cubeMesh);
    cScene.add(new THREE.LineSegments(
        new THREE.EdgesGeometry(new THREE.BoxGeometry(1.002, 1.002, 1.002)),
        new THREE.LineBasicMaterial({ color: 0x8d97a1 })
    ));

    function syncCube() {
        cCam.position.copy(new THREE.Vector3(0, 0, 1).applyQuaternion(camQuat).multiplyScalar(6));
        cCam.quaternion.copy(camQuat);
    }

    function quatFromSpherical(theta, phi) {
        var back = new THREE.Vector3(
            Math.sin(phi) * Math.sin(theta), Math.cos(phi), Math.sin(phi) * Math.cos(theta)
        );
        var up = new THREE.Vector3(
            -Math.cos(phi) * Math.sin(theta), Math.sin(phi), -Math.cos(phi) * Math.cos(theta)
        );
        var right = new THREE.Vector3().crossVectors(up, back).normalize();
        up.crossVectors(back, right).normalize();
        var m = new THREE.Matrix4().makeBasis(right, up, back);
        return new THREE.Quaternion().setFromRotationMatrix(m);
    }

    function glideTo(theta, phi) {
        var qStart = camQuat.clone();
        var qEnd = quatFromSpherical(theta, phi);
        var t0ms = performance.now(), dur = 300;
        (function step(now) {
            var k = Math.min(1, (now - t0ms) / dur);
            var e = k < .5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2;
            camQuat.copy(qStart).slerp(qEnd, e);
            applyCam();
            if (k < 1) requestAnimationFrame(step);
        })(t0ms);
    }

    (function () {
        var cRay = new THREE.Raycaster(), cNdc = new THREE.Vector2();
        var down = null, dragging = false, last = null;
        cubeCanvas.addEventListener("pointerdown", function (e) {
            e.stopPropagation();
            cubeCanvas.setPointerCapture(e.pointerId);
            down = { x: e.clientX, y: e.clientY }; last = { x: e.clientX, y: e.clientY }; dragging = false;
        });
        cubeCanvas.addEventListener("pointermove", function (e) {
            if (!down) return;
            e.stopPropagation();
            if (!dragging && Math.hypot(e.clientX - down.x, e.clientY - down.y) > 4) dragging = true;
            if (dragging) {
                var dx = e.clientX - last.x, dy = e.clientY - last.y;
                rotateAroundPivot(target, dx * 1.57, dy * 1.57);
            }
            last = { x: e.clientX, y: e.clientY };
        });
        cubeCanvas.addEventListener("pointerup", function (e) {
            e.stopPropagation();
            if (down && !dragging) snapTo(e.clientX, e.clientY);
            down = null; dragging = false;
        });
        cubeCanvas.addEventListener("pointercancel", function () { down = null; dragging = false; });
        cubeCanvas.addEventListener("wheel", function (e) { e.stopPropagation(); });

        function snapTo(cx, cy) {
            var r = cubeCanvas.getBoundingClientRect();
            cNdc.x = ((cx - r.left) / r.width) * 2 - 1;
            cNdc.y = -((cy - r.top) / r.height) * 2 + 1;
            cRay.setFromCamera(cNdc, cCam);
            var hit = cRay.intersectObject(cubeMesh, false)[0];
            if (!hit) return;
            var p = hit.point, lim = 0.34;
            var v = new THREE.Vector3(
                Math.abs(p.x) > lim ? Math.sign(p.x) : 0,
                Math.abs(p.y) > lim ? Math.sign(p.y) : 0,
                Math.abs(p.z) > lim ? Math.sign(p.z) : 0
            );
            if (v.lengthSq() === 0) return;
            v.normalize();
            var isPole = Math.abs(v.x) < 1e-6 && Math.abs(v.z) < 1e-6;
            var curBack = new THREE.Vector3(0, 0, 1).applyQuaternion(camQuat);
            var curTheta = Math.atan2(curBack.x, curBack.z);
            var targetTheta = isPole ? Math.round(curTheta / (Math.PI / 2)) * (Math.PI / 2) : Math.atan2(v.x, v.z);
            glideTo(targetTheta, Math.acos(Math.max(-1, Math.min(1, v.y))));
        }
    })();

    function animate() {
        requestAnimationFrame(animate);
        renderer.render(scene, camera);
        cRenderer.render(cScene, cCam);
    }

    resize(); applyCam(); syncCube(); animate(); fetchMesh(); loadCameraFrustum();
    })();
    </script>
</body>
</html>
"""


@app.route("/")
def index():
    touch_activity()
    return render_template_string(DEPTH_PAGE, max_distance=MAX_DISTANCE)


@app.route("/video_feed")
def video_feed():
    touch_activity()
    return Response(
        mjpeg_generator(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


@app.route("/pointcloud")
def pointcloud_page():
    touch_activity()
    return render_template_string(POINTCLOUD_PAGE)


@app.route("/mesh_data")
def mesh_data():
    touch_activity()
    # _latest_mesh 已經是背景執行緒編碼好的 JSON bytes，這裡不用再序列化一次
    with _frame_lock:
        mesh_json = _latest_mesh or b'{"positions": [], "indices": []}'
    return Response(mesh_json, mimetype="application/json")


@app.route("/camera_info")
def camera_info():
    return Response(json.dumps(_camera_info), mimetype="application/json")


@app.route("/settings")
def get_settings():
    with _temporal_lock:
        frames = _temporal_frames
    return Response(
        json.dumps({
            "temporal_frames": frames,
            "min": TEMPORAL_FRAMES_MIN,
            "max": TEMPORAL_FRAMES_MAX,
            "default": TEMPORAL_FRAMES_DEFAULT,
        }),
        mimetype="application/json",
    )


@app.route("/set_temporal_frames")
def set_temporal_frames():
    global _temporal_frames
    try:
        frames = int(request.args.get("frames", TEMPORAL_FRAMES_DEFAULT))
    except (TypeError, ValueError):
        return Response(json.dumps({"error": "invalid frames"}), status=400, mimetype="application/json")

    frames = max(TEMPORAL_FRAMES_MIN, min(TEMPORAL_FRAMES_MAX, frames))
    with _temporal_lock:
        _temporal_frames = frames
    return Response(json.dumps({"temporal_frames": frames}), mimetype="application/json")


if __name__ == "__main__":
    t = threading.Thread(target=camera_worker, daemon=True)
    t.start()

    print("等待相機初始化...")
    if not _camera_ready.wait(timeout=10):
        print("警告: 相機尚未就緒，伺服器仍會啟動，但畫面可能延遲出現。")

    print(f"深度圖:   http://<樹莓派IP>:{PORT}/")
    print(f"3D 點雲:  http://<樹莓派IP>:{PORT}/pointcloud")
    app.run(host=HOST, port=PORT, threaded=True)
