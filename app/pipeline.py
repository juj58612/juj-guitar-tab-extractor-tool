# -*- coding: utf-8 -*-
"""
吉他六線譜自動擷取 - 核心處理邏輯

流程：
1. 從影片中依固定間隔取樣畫面
2. 只保留使用者框選的「譜面區域」
3. 用邊緣密度(細節豐富程度) + 有沒有六線譜格線，判斷該幀是否為「譜面畫面」
   (而非鏡頭畫面/標題卡/頻道浮水印/黑畫面)
4. 用二值化內容遮罩比對「畫面內容是否改變」，把同一份譜面(即使畫面上有會移動的
   高亮提示框)合併成一段，每段取一張代表圖(邊緣密度最高的那張，通常最清晰)
5. 再次用內容遮罩比對，濾掉「跨時間重複」出現的譜面(同一段落被重複播放/講解)
6. 依時間序把剩下的獨立譜面圖片，(依使用者選擇做畫質處理後) 直向拼接成 A4/Letter 頁面，輸出成 PDF

另有手動模式 (build_from_captures)：跳過 1~5，直接用使用者自己擷取的畫面組 PDF。
"""

import os
import io
import json
import shutil
import time
import traceback

import cv2
import numpy as np
from PIL import Image

# 有些 Pillow 版本要等到「第一次存檔成某個格式」時，才會真的載入該格式的存檔外掛。
# build_pdf() 在組 PDF 時，內部會把每張圖用 JPEG 壓縮後包進 PDF —— 如果流程中
# 剛好從來沒有存過任何 .jpg 檔 (例如預覽圖產生失敗被跳過)，PDF 存檔外掛要用的
# JPEG 存檔外掛就還沒被載入，會炸出令人一頭霧水的 KeyError('JPEG')。
# 這裡在模組載入時就強制完整初始化一次，避免這種「順序剛好」才會出現的問題。
Image.init()


# ---------------------------------------------------------------------------
# 基礎工具
# ---------------------------------------------------------------------------

_CV_PATH_CACHE = {}


def _cv_path(path):
    """
    回傳一個 OpenCV 讀得到的影片路徑。

    Windows 上的 OpenCV 不支援含中文 (非英數) 字元的路徑 —— 例如工具放在
    「C:\\Users\\王小明\\Desktop」底下，影片就會讀不到。這時候先試著用 Windows 的
    「8.3 短檔名」(純英數)；如果系統關掉了短檔名，就把影片複製一份到純英數的
    暫存資料夾 (C:\\gte_cache) 再讀。Mac / Linux 沒有這個問題，原路徑直接用。
    """
    if os.name != "nt" or str(path).isascii():
        return path
    if path in _CV_PATH_CACHE and os.path.exists(_CV_PATH_CACHE[path]):
        return _CV_PATH_CACHE[path]
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(1024)
        if ctypes.windll.kernel32.GetShortPathNameW(str(path), buf, 1024) and buf.value.isascii():
            _CV_PATH_CACHE[path] = buf.value
            return buf.value
    except Exception:
        pass
    import hashlib
    cache_dir = os.path.join(os.environ.get("SystemDrive", "C:") + os.sep, "gte_cache")
    os.makedirs(cache_dir, exist_ok=True)
    name = hashlib.md5(str(path).encode("utf-8")).hexdigest()[:16] + os.path.splitext(path)[1]
    dst = os.path.join(cache_dir, name)
    if not os.path.exists(dst) or os.path.getsize(dst) != os.path.getsize(path):
        shutil.copyfile(path, dst)
    _CV_PATH_CACHE[path] = dst
    return dst


def get_video_info(path):
    """回傳 (duration_sec, width, height, fps)"""
    cap = cv2.VideoCapture(_cv_path(path))
    if not cap.isOpened():
        raise RuntimeError("無法開啟影片，請確認檔案格式是否支援 (建議 mp4/mov/mkv)")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    duration = frame_count / fps if fps > 0 else 0
    cap.release()
    return duration, width, height, fps


def grab_frame_at(path, t_sec):
    """抓取影片在 t_sec 秒時的畫面，回傳 PIL.Image (RGB) 或 None"""
    cap = cv2.VideoCapture(_cv_path(path))
    if not cap.isOpened():
        return None
    cap.set(cv2.CAP_PROP_POS_MSEC, max(0, t_sec) * 1000.0)
    ok, frame = cap.read()
    cap.release()
    if not ok or frame is None:
        return None
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    return Image.fromarray(rgb)


# ---------------------------------------------------------------------------
# 自動建議譜面框選區域
# ---------------------------------------------------------------------------

def suggest_crop_box(path, duration, samples=10, score_type="guitar"):
    """
    抽樣數張畫面，找出「細節最豐富、最穩定」的水平帶狀區域，
    當作預設的譜面框選建議 (使用者仍可在介面上手動調整)。
    回傳 dict: {x, y, w, h} (原始影片像素座標)
    """
    duration, width, height, fps = get_video_info(path)
    ts = [duration * (i + 1) / (samples + 1) for i in range(samples)]

    if score_type == "piano":
        box = _suggest_crop_by_staves(path, ts, width, height)
        if box:
            return box

    light_profiles = []
    for t in ts:
        img = grab_frame_at(path, t)
        if img is None:
            continue
        arr = np.array(img).astype(np.float32)
        mx = arr.max(axis=2)
        mn = arr.min(axis=2)
        sat = mx - mn
        # 「亮 + 低飽和」通常對應譜面卡片的淺色背景，跟鏡頭畫面/黑邊明顯不同
        light = (mx > 190) & (sat < 40)
        light_profiles.append(light.mean(axis=1))

    if not light_profiles:
        return {"x": 0, "y": int(height * 0.55), "w": width, "h": int(height * 0.4)}

    med = np.median(np.array(light_profiles), axis=0)

    # 「整個畫面都是譜」的影片 (例如 Guitar Pro / MuseScore 播放畫面，譜面佔滿全螢幕，
    # 只有左右可能有黑邊)：大部分的列都是淺色背景，找不到「從暗轉亮」的分界。
    # 這種情況下直接框住整塊淺色的譜面頁 (左右裁掉黑邊)，而不是退回「下半部」——
    # 退回下半部只會框到半頁譜，而且換頁時內容變化太小、自動擷取會判斷不出換頁。
    if np.mean(med > 0.5) >= 0.8:
        col_profiles = []
        for t in ts:
            img = grab_frame_at(path, t)
            if img is None:
                continue
            arr = np.array(img).astype(np.float32)
            mx = arr.max(axis=2)
            sat = mx - arr.min(axis=2)
            col_profiles.append(((mx > 190) & (sat < 40)).mean(axis=0))
        cmed = np.median(np.array(col_profiles), axis=0)
        cols = np.where(cmed > 0.5)[0]
        rows = np.where(med > 0.5)[0]
        if len(cols) > width * 0.3 and len(rows) > 0:
            x0, x1 = int(cols[0]), int(cols[-1]) + 1
            y0, y1 = int(rows[0]), int(rows[-1]) + 1
            return {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}

    # 譜卡 = 畫面中「最長的一段連續淺色列」(允許中間有幾列被譜線、文字壓暗)。
    # 以前是找「從暗轉亮」的那一列，但譜卡上緣常有一小段漸層/陰影，找不到明確的
    # 分界時就退回「畫面下方 40%」，結果上面切進演奏影像 (吉他弦還會被誤認成 TAB)，
    # 下面又切掉譜卡最底部的歌詞。
    y0 = None
    light_rows = med > 0.7
    best, cur_start, dark_run = None, None, 0
    for i, lit in enumerate(light_rows):
        if lit:
            if cur_start is None:
                cur_start = i
            dark_run = 0
            end = i
            if best is None or end - cur_start > best[1] - best[0]:
                best = (cur_start, end)
        elif cur_start is not None:
            dark_run += 1
            if dark_run > max(6, height // 150):
                cur_start, dark_run = None, 0
    if best is not None and best[1] - best[0] >= height * 0.15:
        y0 = int(best[0])
        card_bottom = int(best[1]) + 1
        if card_bottom >= height - max(6, height // 150):
            card_bottom = height  # 譜卡一路到畫面底部
        return {"x": 0, "y": y0, "w": int(width), "h": int(card_bottom - y0)}

    if y0 is None:
        # 找不到明顯分界，退回保守預設(下半部)
        y0 = int(height * 0.55)

    default_h = int(height * 0.4)
    y1 = min(height, y0 + default_h)

    # 有些教學影片在譜面卡片最下方還疊了一條「段落導覽列」(例如
    # 「前言｜前奏1-4｜主歌5-13｜...」)，背景通常比譜面卡片本身暗一截。
    # 如果照上面的預設高度硬切，很容易把這條導覽列也框進去，造成每張截圖
    # 下面都多一橫條不必要的東西。這裡從畫面最下面往上找，找出「最靠近
    # 底部、連續一段偏暗」的區域(=導覽列)，把預設框選的下邊界收回到它
    # 上面，讓建議框只包住真正的譜面。
    i = height - 1
    while i > y0 and med[i] > 0.85:
        i -= 1
    if i > y0:
        dark_bottom_run = height - 1 - i  # 這段暗區跑到多靠近畫面底部
        dark_top = i
        while dark_top > y0 and med[dark_top] < 0.85:
            dark_top -= 1
        candidate_y1 = dark_top + 1
        # 這段暗區至少要有幾像素高、且切完剩下的譜面區域不能小得離譜，
        # 才當作是導覽列；避免把單一條譜線的陰影誤判成導覽列
        if dark_bottom_run >= 3 and candidate_y1 - y0 >= 40:
            y1 = min(y1, candidate_y1)

    return {"x": 0, "y": int(y0), "w": int(width), "h": int(y1 - y0)}


def _suggest_crop_by_staves(path, ts, width, height):
    """
    鋼琴譜：直接找「成組的五線譜」在畫面哪裡，用譜表的位置決定框選範圍。
    (鋼琴教學影片下方常有虛擬鍵盤，白鍵是一大塊淺色區域，用「找淺色區塊」的
    方法會框到鍵盤而不是譜。) 上方多留和弦名稱/小節號的空間，下方多留低音譜表
    下加線音符的空間。找不到譜表時回傳 None，退回原本的做法。
    """
    tops, bots, sps = [], [], []
    for t in ts:
        img = grab_frame_at(path, t)
        if img is None:
            continue
        systems, sp = _find_systems(clean_highlight(img), "piano")
        for sy in systems:
            if sy["nstaff"] >= 2:
                tops.append(sy["top"])
                bots.append(sy["bot"])
                sps.append(sp)
    if len(tops) < 2:
        return None
    sp = float(np.median(sps))
    y0 = int(max(0, min(tops) - sp * 9))
    y1 = int(min(height, max(bots) + sp * 7))
    return {"x": 0, "y": y0, "w": int(width), "h": int(y1 - y0)}


# ---------------------------------------------------------------------------
# 主要流程
# ---------------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BUNDLED_FONT = os.path.join(BASE_DIR, "assets", "fonts", "NotoSansCJK-Regular.ttc")


def _crop(img, box):
    x, y, w, h = box["x"], box["y"], box["w"], box["h"]
    return img.crop((x, y, x + w, y + h))


def _otsu_threshold(values):
    """對一維數值陣列用 Otsu 法找出雙峰分佈之間的最佳分界點"""
    values = np.asarray(values, dtype=np.float64)
    vmax = values.max()
    if vmax <= 0:
        return 0.0
    scaled = np.clip(values / vmax * 255, 0, 255).astype(np.uint8)
    th, _ = cv2.threshold(scaled, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return float(th) / 255.0 * vmax


SIG_WIDTH = 320  # 縮小後拿來比對內容是否相同的統一寬度 (高度依原比例)


def _content_signature(pil_img, drop_lines=False):
    """
    把畫面轉成「墨跡遮罩」(哪些像素是黑色的線條/音符/數字)，用來判斷兩張截圖
    是不是「同一份譜面」。

    這裡刻意不用一般的感知雜湊(perceptual hash)，因為這類教學影片常常會在
    譜面上疊一層會移動的「目前彈到哪裡」淺色提示框(高亮目前小節)——同一份
    譜面、只是提示框位置不同，畫面在感知雜湊上卻會被誤判成「差很多」。
    直接比對二值化後的墨跡，只看「線條與數字」本身有沒有變，
    對這種半透明提示框的干擾就穩定得多。

    縮圖保留原本的長寬比 (以前固定壓成 320x72，整頁的譜會被壓扁到看不出差異)；
    稍微膨脹墨跡，讓影片壓縮造成的 1 像素抖動不會被當成內容改變。
    """
    w, h = pil_img.size
    sig_h = max(24, min(SIG_WIDTH, int(round(SIG_WIDTH * h / max(1, w)))))
    gray = np.array(pil_img.convert("L").resize((SIG_WIDTH, sig_h), Image.LANCZOS))
    _, ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    if drop_lines:
        # 鋼琴譜：每一頁的五線譜位置幾乎一樣，譜線會佔掉大部分墨跡，讓「換頁」
        # 看起來差異很小。把長橫線 (譜線) 拿掉，只比對音符、符號本身。
        k = max(15, SIG_WIDTH // 8)
        lines_ = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (k, 1)))
        ink = cv2.subtract(ink, lines_)
    ink = cv2.dilate(ink, np.ones((3, 3), np.uint8))
    return ink > 0


def _content_diff(sig_a, sig_b):
    """
    回傳兩張譜面「墨跡不一樣的比例」(0~1)：不一樣的墨跡 ÷ 兩張合起來的墨跡。

    以前是除以「整張圖的像素數」——但譜面大部分是白底，就算整頁換掉，不同的
    像素也只佔整張圖的 2% 左右，低於「換頁」門檻，導致整支影片被當成同一頁、
    自動擷取只剩 1 張。改成只看墨跡之後：同一頁 (只有提示框移動) 約 0~6%，
    換到另一頁約 70~90%，兩者分得很清楚。
    """
    if sig_a.shape != sig_b.shape:
        sig_b = cv2.resize(sig_b.astype(np.uint8), (sig_a.shape[1], sig_a.shape[0]),
                           interpolation=cv2.INTER_NEAREST) > 0
    union = np.logical_or(sig_a, sig_b).sum()
    if union == 0:
        return 0.0
    return float(np.logical_xor(sig_a, sig_b).sum()) / float(union)


def _count_staff_lines(pil_img):
    """
    偵測畫面裡有沒有「六線譜的橫線」(長長的水平直線，貫穿大半個畫面寬度)。
    用來分辨「真正的譜面畫面」跟「標題卡/頻道浮水印」等一樣文字很密集、
    但其實沒有六線譜格線的畫面 —— 這兩種在單純的邊緣密度上很像，
    但只有真正的譜面才會有這種一整排的水平線。
    """
    gray = np.array(pil_img.convert("L"))
    h, w = gray.shape
    edges = cv2.Canny(gray, 50, 150)
    lines = cv2.HoughLinesP(
        edges, 1, np.pi / 180,
        threshold=int(w * 0.25),
        minLineLength=int(w * 0.3),
        maxLineGap=10,
    )
    if lines is None:
        return 0
    # cv2.HoughLinesP 一般回傳形狀 (N, 1, 4)，但依 OpenCV 版本/安裝方式不同，
    # 少數情況下會拿到 (N, 4)。這裡統一攤平成 (N, 4) 再逐行拆解，避免版本差異
    # 導致「拆到單一純量」而炸出 cannot unpack non-iterable numpy.int32 object。
    lines = np.asarray(lines).reshape(-1, 4)
    count = 0
    for x1, y1, x2, y2 in lines:
        if abs(y2 - y1) < 3 and abs(x2 - x1) > w * 0.3:
            count += 1
    return count


def enhance_for_print(pil_img, scale=2):
    """
    把譜面截圖轉成「真正的黑白」(純黑線條/文字 + 純白底)，印出來/看起來更清晰銳利，
    而不是只把顏色去掉的灰階圖。

    做法 (依序)：
    1. 先放大 (預設 2 倍)，讓後面的二值化不會把細線/小數字的邊緣切得坑坑洞洞
    2. 保邊緣去噪 (bilateral filter)，去掉截圖本身的壓縮雜訊，但不模糊線條
    3. 自適應二值化 (adaptive threshold，而非單一全域門檻)——這類教學影片的譜面
       卡片背景亮度通常大致均勻，但仍會有淡淡的漸層/陰影，用單一全域門檻(Otsu)
       容易把整塊文字二值化成黑色色塊(細節全部糊掉)；自適應二值化用「附近區域」
       各自的門檻，能同時保留細的譜線與數字筆畫
    4. 開運算清掉二值化後殘留的孤立小黑點雜訊
    5. 極保守的橫向補線 (只接 1~2px 的小縫)，讓六條橫線盡量連續，不做左右方向的
       補線，避免把相鄰的數字黏在一起

    這是這個工具第二次嘗試「黑白化」——第一次的版本效果不好(整段被撤回)，這一版
    刻意先放大再處理、用自適應而非全域二值化、並對線條做保守補強，實際比對過
    好幾組參數的效果後才定案。啟用與否由使用者在介面上自行勾選，預設不套用。
    """
    w, h = pil_img.size
    big = pil_img.convert("L").resize((w * scale, h * scale), Image.LANCZOS)
    gray = np.array(big)

    denoised = cv2.bilateralFilter(gray, d=7, sigmaColor=50, sigmaSpace=50)

    block = max(15, (min(gray.shape) // 20) | 1)
    bw = cv2.adaptiveThreshold(
        denoised, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY,
        blockSize=block, C=10,
    )

    kernel_small = np.ones((2, 2), np.uint8)
    bw = cv2.morphologyEx(bw, cv2.MORPH_OPEN, kernel_small)

    kernel_h = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 1))
    bw = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, kernel_h)

    return Image.fromarray(bw).convert("RGB")


def enhance_quality(pil_img, scale=2):
    """
    智慧畫質增強 (白得更白、黑得更黑但不失真)，跟 enhance_for_print 的純黑白不同，
    這個保留灰階過渡，比較不會有二值化的鋸齒感：
    1. 轉灰階後放大，保留細節
    2. 取亮度分佈的 3% / 95% 百分位數，做對比度拉伸，把背景拉到純白、
       線條拉到更深的黑，中間顏色線性過渡
    3. 輕微銳化 (unsharp mask)
    (改寫自 will095614/sheetthief 的版本)
    """
    w, h = pil_img.size
    gray = pil_img.convert("L")
    # 百分位數用「放大前」的圖算：LANCZOS 放大會在深色線條旁產生一圈比背景還亮的
    # 振鈴 (ringing)，用放大後的圖算會把 95% 亮度墊高，背景就拉不到純白
    p_low, p_high = np.percentile(np.array(gray), (3, 95))
    arr = np.array(gray.resize((w * scale, h * scale), Image.LANCZOS))

    if p_high > p_low:
        arr = np.clip((arr.astype(np.float32) - p_low) * 255.0 / (p_high - p_low), 0, 255).astype(np.uint8)

    blurred = cv2.GaussianBlur(arr, (0, 0), 1.0)
    arr = cv2.addWeighted(arr, 1.2, blurred, -0.2, 0)

    return Image.fromarray(arr).convert("RGB")


ENHANCE_MODES = ("none", "smart", "bw")


def apply_enhance(pil_img, mode):
    """依使用者選的畫質處理方式處理圖片：none=原圖、smart=智慧畫質增強、bw=黑白清晰化"""
    if mode == "smart":
        return enhance_quality(pil_img)
    if mode == "bw":
        return enhance_for_print(pil_img)
    return pil_img


def extract_sections(
    path,
    crop_box,
    stride_sec=0.5,
    density_threshold=None,
    similarity_threshold=6,
    progress_cb=None,
    return_intro=False,
    score_type="guitar",
):
    """
    自動擷取的「掃描抓譜」部分 (步驟 1~4)：掃過整部影片，找出每一份獨立的譜面、
    去掉重複，回傳 [(t秒, 裁切後的 PIL 圖), ...] (依時間排序)。
    不做畫質處理、不產生 PDF —— 讓使用者先預覽、刪掉抓錯的，再決定輸出方式。
    progress_cb(percent:int, message:str) 用來回報進度。
    """

    def report(pct, msg):
        if progress_cb:
            progress_cb(pct, msg)

    duration, width, height, fps = get_video_info(path)
    if duration <= 0:
        raise RuntimeError("讀取不到影片長度，檔案可能已損毀")

    # --- 1. 取樣並計算每張的邊緣密度 -------------------------------------------------
    report(2, "正在讀取影片...")
    cap = cv2.VideoCapture(_cv_path(path))
    if not cap.isOpened():
        raise RuntimeError("無法開啟影片")

    scores = []  # (t, cropped_pil, score)

    step_frames = max(1, int(round(fps * stride_sec)))
    idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if idx % step_frames == 0:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            pil = Image.fromarray(rgb)
            crop = _crop(pil, crop_box)
            gray = np.array(crop.convert("L"))
            lap = cv2.Laplacian(gray, cv2.CV_32F)
            score = float(np.mean(np.abs(lap)))
            scores.append((idx / fps, crop, score))
            if len(scores) % 20 == 0:
                pct = 5 + int(55 * (idx / fps) / duration)
                report(min(pct, 60), "分析畫面中... {:.0f}/{:.0f} 秒".format(idx / fps, duration))
        idx += 1
    cap.release()

    if not scores:
        raise RuntimeError("沒有取樣到任何畫面，請確認影片是否正常")

    # --- 2. 判斷是否為「譜面畫面」 ------------------------------------------------
    # 注意：譜面卡片在很多這類教學影片裡幾乎「全程」顯示在畫面下方，
    # 鏡頭在上面播放的同時，下面的譜面內容會不斷「翻頁/捲動」到下一段。
    # 也就是說「文字密度高」的畫面不會只對應到單一一份譜面——同一個高密度
    # 區間裡，其實可能已經連續換過好幾份不同的譜面截圖了。
    # 所以這裡不能只靠「密度是否夠高」來切段落，還要靠「畫面內容有沒有變化」。
    report(62, "判斷譜面畫面中...")
    all_scores = np.array([s[2] for s in scores])
    if density_threshold is None:
        # 自動門檻: 譜面畫面在很多教學影片裡佔了大部分時間，
        # 用固定百分位數切一刀常常切在「譜面」這群樣本內部，誤殺掉不少譜面畫面。
        # 改用 Otsu 法在分數的直方圖上找「兩群資料之間的山谷」，
        # 不管譜面畫面佔比高低都能抓到正確的分界點。
        density_threshold = float(_otsu_threshold(all_scores))
        # 如果整支影片幾乎全程都是譜面 (例如全螢幕的譜面播放影片)，分數根本不是
        # 「譜面 / 非譜面」兩群，Otsu 會硬把譜面切成兩半、丟掉一半的譜面畫面。
        # 這時候改用寬鬆的門檻 (中位數的一半)，只濾掉黑畫面/空白畫面。
        low = all_scores[all_scores <= density_threshold]
        high = all_scores[all_scores > density_threshold]
        if len(low) and len(high) and low.mean() > 0.6 * high.mean():
            density_threshold = float(np.median(all_scores) * 0.5)

    density_pass = [(t, crop, score) for (t, crop, score) in scores if score > density_threshold]

    if not density_pass:
        raise RuntimeError(
            "沒有偵測到任何譜面畫面，請調整框選區域或降低「文字密度門檻」"
        )

    # 額外用「有沒有六線譜格線」再篩一次，濾掉標題卡/頻道浮水印這類
    # 文字密度也很高、但其實不是譜面內容的畫面
    report(65, "濾除標題卡/浮水印畫面中...")
    STAFF_LINE_MIN = 5
    candidates = [
        (t, crop, score)
        for (t, crop, score) in density_pass
        if _count_staff_lines(crop) >= STAFF_LINE_MIN
    ]

    if not candidates:
        # 保險：如果格線偵測太嚴格把全部濾光，退回只用密度篩選的結果
        candidates = density_pass

    # --- 開頭資訊頁 (歌名、和弦表、調弦設定...) -------------------------------------
    # 這類畫面在第一份譜面出現「之前」，內容豐富 (文字/和弦圖很多) 但沒有
    # 貫穿整個寬度的五線譜/TAB 橫線。抓出來另外標記，讓使用者決定要不要放進 PDF。
    intro_pages = []  # [(t, 裁切後的 PIL 圖)]
    if candidates:
        first_t = candidates[0][0]
        pre = []
        for (t, crop, score) in scores:
            if t >= first_t:
                break
            gray = np.array(crop.convert("L"))
            if score < density_threshold * 0.5 or np.mean(gray < 160) < 0.004:
                continue  # 黑畫面 / 幾乎空白
            if _count_staff_lines(crop) >= STAFF_LINE_MIN:
                continue
            pre.append((t, crop, score))
        run, prev_sig = [], None
        intro_runs = []
        for t, crop, score in pre:
            sig = _content_signature(crop, drop_lines=(score_type == "piano"))
            if prev_sig is not None and _content_diff(sig, prev_sig) > 0.25:
                intro_runs.append(run)
                run = []
            run.append((t, crop, score))
            prev_sig = sig
        if run:
            intro_runs.append(run)
        for r in intro_runs:
            if len(r) >= 2:  # 至少停留兩次取樣 (約 1 秒)，排除轉場畫面
                best = max(r, key=lambda x: x[2])
                intro_pages.append((best[0], best[1]))

    # --- 3. 依「畫面內容變化」切分出每一份「獨立顯示過」的譜面 -----------------------
    # 教學影片常會在譜面上疊一層會移動的「目前彈到哪」提示框，同一份譜面因此會
    # 連續出現好幾張「內容其實一樣、提示框位置不同」的畫面，這裡用內容遮罩比對
    # 把它們合併成同一段，只取其中最清晰的一張。
    report(68, "偵測譜面切換中...")
    CONTENT_SAME_FRACTION = 0.25  # 墨跡差異在 25% 以內，視為同一張譜面(只是提示框移動)

    runs = []
    cur_run = []
    cur_sig = None
    for t, crop, score in candidates:
        sig = _content_signature(crop, drop_lines=(score_type == "piano"))
        if cur_sig is None or _content_diff(sig, cur_sig) <= CONTENT_SAME_FRACTION:
            cur_run.append((t, crop, score))
        else:
            runs.append(cur_run)
            cur_run = [(t, crop, score)]
        cur_sig = sig
    if cur_run:
        runs.append(cur_run)

    # 濾掉「只出現一次取樣就消失」的段落：真正的譜面卡片會連續停留展示好幾秒，
    # 但運鏡中的吉他弦特寫、手部動作等畫面偶爾也會被前面的格線篩選誤判成
    # 「像六線譜」，這種鏡頭因為畫面持續在動，內容差異幾乎不可能連續兩次取樣
    # 都低於 CONTENT_SAME_FRACTION，所以只會形成長度 1 的段落——藉此濾掉這類
    # 誤判，且解析度越高、線條偵測越準，這種誤判反而越容易通過前面的格線篩選，
    # 這裡的過濾能同時擋掉。
    MIN_RUN_LEN = 2
    stable_runs = [r for r in runs if len(r) >= MIN_RUN_LEN]
    if stable_runs:
        runs = stable_runs
    # 保險：如果篩完整段影片一個符合的都沒有(例如取樣間隔設太大)，退回未過濾的結果，
    # 避免把真正的譜面也全部濾光

    representatives = []
    for run in runs:
        # 取該段落中最清晰 (邊緣密度最高) 的一張，但只從段落「中間」挑：
        # 換頁/換譜卡的瞬間，新舊兩張常常疊在一起淡入淡出，線條最多、分數最高，
        # 卻是最不能用的畫面。段落頭尾各去掉 20% 就能避開這種轉場畫面。
        if len(run) >= 5:
            cut = max(1, int(len(run) * 0.2))
            pool = run[cut:len(run) - cut]
        else:
            pool = run
        best = max(pool, key=lambda r: r[2])
        representatives.append((best[0], best[1]))

    # --- 4. 不再做「跨時間去重複」 -------------------------------------------------
    # 譜面是跟著演奏沿時間軸往下走的：時間軸上後面又出現一樣的譜，代表真的又演奏
    # 了一遍，要保留。連續停在畫面上的同一份譜，已經在步驟 3 合併成一段了；捲動
    # 造成的「上下兩張截圖重疊的那一行」，則在切行時 (split_pages_into_lines) 處理。
    kept = representatives

    report(95, "擷取完成，共 {} 張譜面".format(len(kept)))
    if return_intro:
        return kept, intro_pages
    return kept


def process_video(
    path,
    crop_box,
    out_dir,
    stride_sec=0.5,
    density_threshold=None,
    similarity_threshold=6,
    title="吉他六線譜",
    subtitle="",
    page_size="A4",
    enhance="none",
    progress_cb=None,
):
    """
    一次跑完整流程 (掃描抓譜 + 產生 PDF)。網頁介面現在改成「先抓譜、預覽、再輸出」，
    分別用 extract_sections() 和 build_from_captures()；這個函式保留給直接呼叫用。
    回傳 dict: {pdf_path, crops_dir, kept_count, sections: [{t, path}]}
    """

    def report(pct, msg):
        if progress_cb:
            progress_cb(pct, msg)

    kept = extract_sections(
        path, crop_box, stride_sec=stride_sec, density_threshold=density_threshold,
        similarity_threshold=similarity_threshold,
        progress_cb=lambda pct, msg: report(min(pct, 85), msg),
    )

    os.makedirs(out_dir, exist_ok=True)
    crops_dir = os.path.join(out_dir, "crops")
    os.makedirs(crops_dir, exist_ok=True)

    # --- 5. 存檔 + 拼成 PDF -------------------------------------------------------
    report(88, "產生樂譜檔案中..." if enhance == "none" else "畫質處理中...")
    sections = []
    for i, (t, crop) in enumerate(kept):
        crop = apply_enhance(crop, enhance)
        fname = "section_{:03d}_t{:.0f}s.png".format(i + 1, t)
        fpath = os.path.join(crops_dir, fname)
        crop.save(fpath)
        sections.append({"t": t, "path": fpath, "filename": fname})

    pdf_path = os.path.join(out_dir, "guitar_tab_score.pdf")
    build_pdf([(s["path"], "page") for s in sections], pdf_path, title=title, subtitle=subtitle, page_size=page_size)

    report(100, "完成！")
    return {
        "pdf_path": pdf_path,
        "crops_dir": crops_dir,
        "kept_count": len(kept),
        "sections": sections,
    }


def build_from_captures(captures, out_dir, title="吉他六線譜", subtitle="", page_size="A4", enhance="none", scale=1.0):
    """
    用預覽區確認過的項目 (captures: [{t, path, kind}]，依想要的順序排好) 產生 PDF：
    套用畫質處理後排版。原始檔保留不動，處理後的圖另存到 manual_out/，方便使用者
    換一種畫質處理方式重新產生。回傳格式與 process_video 相同。
    """
    if not captures:
        raise RuntimeError("還沒有擷取任何畫面")

    sections_dir = os.path.join(out_dir, "manual_out")
    shutil.rmtree(sections_dir, ignore_errors=True)
    os.makedirs(sections_dir)

    sections = []
    for i, c in enumerate(captures):
        img = apply_enhance(Image.open(c["path"]).convert("RGB"), enhance)
        fname = "section_{:03d}_t{:.0f}s.png".format(i + 1, c["t"])
        fpath = os.path.join(sections_dir, fname)
        img.save(fpath)
        sections.append({"t": c["t"], "path": fpath, "filename": fname, "kind": c.get("kind", "line")})

    pdf_path = os.path.join(out_dir, "guitar_tab_score.pdf")
    build_pdf([(x["path"], x["kind"]) for x in sections], pdf_path, title=title, subtitle=subtitle,
              page_size=page_size, scale=scale)
    return {
        "pdf_path": pdf_path,
        "crops_dir": sections_dir,
        "kept_count": len(sections),
        "sections": sections,
    }


# ---------------------------------------------------------------------------
# 切行：把一張張「整頁截圖」拆成以「一行譜」為單位
# (一行 = 上方的和弦圖 + 五線譜 + 六線譜 TAB，三者一組)
# ---------------------------------------------------------------------------

def clean_highlight(pil_img):
    """
    清掉影片的播放提示 (黃色/彩色的「目前彈到這裡」提示框、藍色游標線)，
    回傳灰階 numpy 陣列 (0~255)。

    做法：每個像素除以「附近的背景顏色」(25x25 範圍內的最亮值)，這樣不管背景
    是白色還是淡黃色的提示框，都會被拉回純白；黑色的音符/線條除以任何背景都還是
    黑的，不受影響。最後取三個色版中最亮的值：淡藍色游標、淡色提示這類「有顏色
    但不深」的東西會變白，真正的墨跡 (黑、深紅) 保留。
    """
    a = np.array(pil_img.convert("RGB")).astype(np.float32)
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 25))
    bg = np.stack([cv2.dilate(a[:, :, c], k) for c in range(3)], axis=2)
    n = np.clip(a / np.maximum(bg, 1.0), 0, 1)
    return (n.max(axis=2) * 255).astype(np.uint8)


def _find_staff_lines(gray):
    """
    找出畫面中所有「水平譜線」的 y 座標。

    TAB 譜線常常被密集的品格數字切成很多小段，所以先用較短的橫向線段 (40px) 把
    線找出來；但一排並排的和弦圖，格子的橫線也會被找到 (長得很像一組六線譜)，
    所以再檢查：真正的譜線從左到右的範圍夠寬，而且範圍內大部分都是線
    (只被數字打斷一點點)；和弦圖的格子線則是一小段一小段、中間空很多。
    """
    h, w = gray.shape
    ink = (gray < 235).astype(np.uint8)  # 譜線常常是很淡的灰色，門檻要放寬
    k = max(15, min(40, int(w * 0.03)))
    horiz = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (k, 1)))
    cov = horiz.sum(axis=1) / float(w)
    rows = np.where(cov > 0.25)[0]
    runs = []
    for r in rows:
        if runs and r - runs[-1][1] <= 1:
            runs[-1][1] = r
        else:
            runs.append([r, r])
    ys = []
    for a, b in runs:
        if b - a + 1 > 4:
            continue  # 粗的橫條 (例如 Guitar Pro 頁與頁之間的深色分隔條、連桿) 不是譜線
        row = horiz[a:b + 1].max(axis=0)
        xs = np.where(row > 0)[0]
        if len(xs) == 0:
            continue
        extent = xs[-1] - xs[0] + 1
        if extent >= w * 0.3 and len(xs) / float(extent) >= 0.55:
            ys.append((a + b) / 2.0)
    return ys


def _find_systems(gray, score_type="guitar"):
    """
    把譜線分組成「譜表」(五線譜 5 條、TAB 6 條，偶爾被音符蓋住會少一兩條)，
    再把上下靠得很近的譜表 (五線譜 + 下面的 TAB) 組成一個 system (= 一行)。
    回傳 ([{top, bot, nstaff}], 譜線間距)
    """
    ys = _find_staff_lines(gray)
    if len(ys) < 4:
        return [], 0.0
    d = np.diff(ys)
    sp = float(np.median(d[d <= np.percentile(d, 50)]))
    if sp <= 0:
        return [], 0.0
    staffs = [[ys[0]]]
    for y, dd in zip(ys[1:], d):
        if dd <= sp * 1.6:
            staffs[-1].append(y)
        else:
            staffs.append([y])
    staffs = [st for st in staffs if len(st) >= 3]
    if not staffs:
        return [], sp
    # 同一行 = 五線譜 (5 條線，偶爾被音符蓋住少一兩條) + 下方的 TAB (6 條線)。
    # 優先用「幾條線」來配對：5 線譜下面接 6 線譜，距離稍遠 (音符很高/很低時) 也算同一行；
    # 6 線接 6 線 (純 TAB 的譜) 則各自是一行。另外距離非常近的譜表也視為同一行。
    systems = [[staffs[0]]]
    for st in staffs[1:]:
        prev = systems[-1][-1]
        gap_ = st[0] - prev[-1]
        if score_type == "piano":
            # 鋼琴大譜表：高音譜表 + 低音譜表 (兩組都是 5 條線) 配成一行。
            # 兩個譜表之間可能有很多加線音符，距離比吉他譜的五線譜+TAB 稍遠也算。
            pair = len(systems[-1]) == 1 and gap_ <= sp * 12
        else:
            pair_by_count = len(prev) <= 5 and len(st) >= 6 and len(systems[-1]) == 1 and gap_ <= sp * 16
            pair = pair_by_count or gap_ <= sp * 8
        if pair:
            systems[-1].append(st)
        else:
            systems.append([st])
    return [{"top": sy[0][0], "bot": sy[-1][-1], "nstaff": len(sy)} for sy in systems], sp


def _best_blank_cut(blank, a, b):
    """在 a~b 之間找最長的一段空白列，回傳它的中間 (= 兩行譜之間最適合下刀的位置)"""
    a, b = int(max(0, a)), int(min(len(blank), b))
    best, cur = None, None
    for y in range(a, b):
        if blank[y]:
            cur = y if cur is None else cur
            if best is None or y - cur > best[1] - best[0]:
                best = (cur, y)
        else:
            cur = None
    if best is None:
        return (a + b) / 2.0
    return (best[0] + best[1]) / 2.0


def _row_has_ink(gray, y):
    return (gray[y] < 200).sum() > max(1, gray.shape[1] * 0.002)


def _follow_content(gray, start, direction, limit, gap):
    """
    從譜線 (start) 往上 (direction=-1) 或往下 (+1) 一列列掃描，只要內容還連著
    (中間的空白不超過 gap 列) 就繼續；回傳內容的最外緣。limit 是最遠可以到哪。
    """
    h = gray.shape[0]
    y = int(round(start))
    last = y
    limit = int(max(0, min(h - 1, round(limit))))
    while True:
        y += direction
        if (direction < 0 and y < limit) or (direction > 0 and y > limit) or y < 0 or y >= h:
            break
        if _row_has_ink(gray, y):
            last = y
        elif abs(y - last) > gap:
            break
    return last - 4 if direction < 0 else last + 5


def _analyze_page(gray, score_type="guitar"):
    systems, sp = _find_systems(gray, score_type)
    blank = (gray < 200).sum(axis=1) <= 1
    cuts = [
        _best_blank_cut(blank, systems[i]["bot"] + sp, systems[i + 1]["top"] - sp)
        for i in range(len(systems) - 1)
    ]
    return {"gray": gray, "systems": systems, "sp": sp, "cuts": cuts}


def _remove_static_overlay(pages):
    """
    去掉「每一頁都出現在同一個位置」的浮水印 (例如錄影軟體的 BANDICAM 字樣、
    頻道名稱)：同尺寸的頁面中，有 85% 以上都是墨跡的像素，又不在譜表範圍內，
    就視為浮水印塗白。譜表 (五線譜/TAB) 本身的位置會略為變動、而且在保護範圍內，
    不會被誤刪。
    """
    by_size = {}
    for pg in pages:
        by_size.setdefault(pg["gray"].shape, []).append(pg)
    for shape, group in by_size.items():
        if len(group) < 5:
            continue
        stack = np.stack([(g["gray"] < 235) for g in group], axis=0)
        static = stack.mean(axis=0) >= 0.85
        protect = np.zeros(shape, bool)
        for g in group:
            for sy in g["systems"]:
                t = int(max(0, sy["top"] - g["sp"] * 2))
                b = int(min(shape[0], sy["bot"] + g["sp"] * 2))
                protect[t:b, :] = True
        mask = static & ~protect
        if mask.sum() == 0:
            continue
        mask = cv2.dilate(mask.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
        for g in group:
            g["gray"] = g["gray"].copy()
            g["gray"][mask] = 255


def _line_signature(gray_line, drop_lines=False):
    """
    一行譜的內容簽章，用來判斷相鄰截圖之間是不是同一行 (捲動造成的重疊)。
    以譜線為基準對齊、只取「譜表本體」(第一條譜線上方一點 ~ 最後一條譜線下方一點)：
    這樣提示框殘影、上下差幾個像素、和弦名稱有沒有被切到，都不會影響比對。
    """
    g = np.asarray(gray_line)
    ys = _find_staff_lines(g)
    if len(ys) >= 2:
        d = np.diff(ys)
        sp = float(np.median(d[d <= np.percentile(d, 50)])) or 8.0
        t = int(max(0, ys[0] - sp * 1.5))
        b = int(min(g.shape[0], ys[-1] + sp * 1.0))
        g = g[t:b]
    h, w = g.shape
    sig_h = max(16, min(240, int(round(400.0 * h / max(1, w)))))
    small = cv2.resize(g, (400, sig_h), interpolation=cv2.INTER_AREA)
    ink = (small < 200).astype(np.uint8)
    if drop_lines:
        lines_ = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (50, 1)))
        ink = cv2.subtract(ink, lines_)
    ink = cv2.dilate(ink, np.ones((3, 3), np.uint8))
    return ink > 0


def split_pages_into_lines(pages_in, ext=None, score_type="guitar"):
    """
    pages_in：[(t秒, PIL 整頁截圖), ...] 依時間排序。
    回傳 (lines, ext)：
      lines = [{"t": t, "img": PIL 灰階 RGB 圖, "page_index": i}, ...] 依時間/由上而下排序
      ext   = {"above": 譜表上方要多切多少 (和弦圖), "below": 譜表下方要多切多少}，
              給之後手動補抓時沿用
    規則：
      - 每一行 = 和弦圖 + 五線譜 + TAB。兩行之間在最寬的空白處下刀。
      - 不完整的行 (被畫面邊緣切到、缺少五線譜或 TAB) 丟掉，通常下一張截圖會有完整版本。
      - 重複判斷用時間軸：只丟掉「跟上一張截圖重疊」的行 (捲動造成)；
        隔了一段時間再出現一樣的行 = 真的又演奏了一遍，保留。
    """
    pages = []
    for t, pil in pages_in:
        pg = _analyze_page(clean_highlight(pil), score_type)
        pg["t"] = t
        pages.append(pg)

    # 一行通常有幾個譜表 (五線譜+TAB = 2；純 TAB = 1)，取多數決
    counts = [sy["nstaff"] for pg in pages for sy in pg["systems"]]
    if not counts:
        return [], ext
    nstaff = int(np.bincount(counts).argmax())

    # 譜表上下要多切多少 (和弦圖在上方、符尾/指法在下方)：從頁面內部的切點統計
    measured_ext = False
    if not ext:
        above, below = [], []
        for pg in pages:
            for i, c in enumerate(pg["cuts"]):
                below.append(c - pg["systems"][i]["bot"])
                above.append(pg["systems"][i + 1]["top"] - c)
        sps = [pg["sp"] for pg in pages if pg["sp"] > 0]
        sp = float(np.median(sps)) if sps else 8.0
        ext = {
            "above": float(np.median(above)) if above else sp * 16,
            "below": float(np.median(below)) if below else sp * 3.5,
            "measured": bool(above),
        }
    measured_ext = bool(ext.get("measured"))

    if score_type != "piano":
        # 吉他譜：去掉錄影軟體之類固定位置的浮水印。鋼琴譜不做：鋼琴教學影片上
        # 固定出現的文字常是譜的作者聲明 (例如請購買正版樂譜)，照影片原樣保留。
        _remove_static_overlay(pages)

    raw = []  # (page_index, t, top, bot)
    for pi, pg in enumerate(pages):
        g, sysl, sp, cuts = pg["gray"], pg["systems"], pg["sp"], pg["cuts"]
        h = g.shape[0]
        for i, sy in enumerate(sysl):
            complete = (
                sy["nstaff"] >= nstaff
                and sy["top"] > sp * 1.5
                and sy["bot"] < h - sp * 1.5
            )
            if not complete:
                continue
            # 上下邊界：兩行之間 → 在最寬的空白處下刀 (整頁譜的影片，原本就很準)；
            # 畫面最上/最下那一行 → 從譜線往外「跟著內容走」：和弦名稱、和弦圖、
            # 符尾、歌詞都包進來，遇到夠大的空白才停 (畫面只有一兩行的譜卡影片靠這個)
            if measured_ext:
                # 整頁譜的影片 (畫面裡有好幾行)：沿用原本的做法，效果已經很好
                top = cuts[i - 1] if i > 0 else sy["top"] - ext["above"] * 1.1
                bot = cuts[i] if i < len(cuts) else sy["bot"] + ext["below"] * 1.1
                top, bot = int(max(0, top)), int(min(h, bot))
            else:
                # 譜卡影片 (畫面只有一兩行，例如譜只在畫面下方)：沒有「兩行之間的空白」
                # 可以參考，改成從譜線往外「跟著內容走」——和弦名稱、和弦圖、符尾、
                # 歌詞都包進來，遇到夠大的空白或譜卡邊緣才停
                gap = max(6, sp * 2.5)
                top = cuts[i - 1] if i > 0 else _follow_content(g, sy["top"], -1, 0, gap)
                bot = cuts[i] if i < len(cuts) else _follow_content(g, sy["bot"], +1, h, gap)
                top, bot = int(max(0, top)), int(min(h, bot))
                # 內容一路碰到畫面邊緣 = 這一行被切到了 (和弦名稱或歌詞在畫面外)，不收
                if (top <= 1 and _row_has_ink(g, 0)) or (bot >= h - 1 and _row_has_ink(g, h - 1)):
                    continue
            rows = np.where((g[top:bot] < 200).sum(axis=1) > 1)[0]
            if len(rows):
                top, bot = max(0, top + rows[0] - 4), min(h, top + rows[-1] + 5)
            raw.append((pi, pg["t"], top, bot))

    if not raw:
        return [], ext

    # 左右留白統一裁掉 (所有行用同一個左右範圍，排版時才會對齊)
    lefts, rights = [], []
    for pi, t, top, bot in raw:
        cols = np.where((pages[pi]["gray"][top:bot] < 200).sum(axis=0) > 0)[0]
        if len(cols):
            lefts.append(cols[0])
            rights.append(cols[-1])
    x0 = max(0, int(min(lefts)) - 6) if lefts else 0
    x1 = int(max(rights)) + 7 if rights else None

    # 重疊判斷 (時間軸邏輯)：
    #   一行譜在畫面上捲動時，會連續出現在相鄰的幾張截圖裡 —— 這是「重疊」，只留一份。
    #   但歌曲也可能緊接著把同一段再彈一次 (另一行、不同小節編號，音符一樣)，這要保留。
    #   分辨方法：畫面捲動時，同一張截圖裡的所有行都往上移動「相同的距離」。所以跟前
    #   一兩張截圖比對相同、而且移動距離跟其他重疊的行一致 → 重疊；移動距離對不上 →
    #   是另一行剛好內容一樣 (重複演奏)，保留。隔更多張才又出現的一樣內容，一律保留。
    OVERLAP_WINDOW = 2
    # 同一行在不同截圖裡，因為提示框殘影、浮水印、畫面些微縮放，差異約 0~40%；
    # 不同的行差異都在 65% 以上 (實測)。門檻取中間。
    SAME_LINE = 0.5
    items = []
    for pi, t, top, bot in raw:
        arr = pages[pi]["gray"][top:bot, x0:x1]
        items.append({"pi": pi, "t": t, "top": top, "arr": arr,
                      "sig": _line_signature(arr, drop_lines=(score_type == "piano"))})

    def sig_diff(a, b):
        if a.shape != b.shape:
            b = cv2.resize(b.astype(np.uint8), (a.shape[1], a.shape[0]), interpolation=cv2.INTER_NEAREST) > 0
        return _content_diff(a, b)

    # 每一行跟前面 1~2 張截圖裡最像的那一行配對，記下位移
    for it in items:
        it["match"] = None
        best = None
        for jt in items:
            if jt["pi"] >= it["pi"] or jt["pi"] < it["pi"] - OVERLAP_WINDOW:
                continue
            dd = sig_diff(it["sig"], jt["sig"])
            if dd <= SAME_LINE and (best is None or dd < best[0]):
                best = (dd, jt)
        if best:
            it["match"] = best[1]
            it["shift"] = it["top"] - best[1]["top"]

    # 每一組 (這張截圖, 前面那張截圖) 的「主要位移」= 捲動的距離
    sps = [pg["sp"] for pg in pages if pg["sp"] > 0]
    tol = max(6.0, (float(np.median(sps)) if sps else 8.0) * 3)
    by_pair = {}
    for it in items:
        if it["match"] is not None:
            by_pair.setdefault((it["pi"], it["match"]["pi"]), []).append(it["shift"])

    def is_overlap(it):
        if it["match"] is None:
            return False
        shifts = by_pair[(it["pi"], it["match"]["pi"])]
        # 找出最多行一致的位移 (眾數)
        best_shift = max(shifts, key=lambda s0: sum(1 for s1 in shifts if abs(s1 - s0) <= tol))
        return abs(it["shift"] - best_shift) <= tol

    lines = []
    for it in items:
        if is_overlap(it):
            continue
        lines.append({"t": it["t"], "img": Image.fromarray(it["arr"]).convert("RGB"), "page_index": it["pi"]})
    return lines, ext


# ---------------------------------------------------------------------------
# PDF 拼接
# ---------------------------------------------------------------------------

PAGE_SIZES_PX = {
    # 以 150 dpi 估算的頁面像素尺寸
    "A4": (1240, 1754),
    "Letter": (1275, 1650),
}


SCALE_CHOICES = (1.0, 0.85, 0.7, 0.6)


def layout_pages(items, title="吉他六線譜", subtitle="", page_size="A4", scale=1.0, enhance="none"):
    """
    把譜面排進一頁頁的紙張，回傳 [PIL.Image 頁面, ...]。

    items：[(圖片路徑, 種類), ...] 或單純的路徑清單。種類：
      "line"  = 一行譜 (和弦圖+五線譜+TAB)：寬度 = 紙張可用寬度 × scale，從上往下緊密排，
                這頁塞不下才換頁 —— 這樣一頁可以放好幾行，不浪費紙張
      "intro" = 開頭資訊頁 (歌名/和弦表/調弦)、"page" = 沒辦法切行的整張截圖：
                撐滿寬度，比一頁還高時縮小到一頁放得下
    scale：一行譜的縮放比例 (1.0 / 0.85 / 0.7 ...)，越小每頁塞越多行、字也越小。
    """
    norm = []
    for it in items:
        if isinstance(it, (tuple, list)):
            norm.append((it[0], it[1] or "line"))
        else:
            norm.append((it, "line"))
    if not norm:
        raise RuntimeError("沒有可用的譜面圖片可以組成 PDF")

    subtitle = (subtitle or "").strip()
    try:
        scale = float(scale or 1.0)
    except (TypeError, ValueError):
        scale = 1.0
    scale = max(0.3, min(1.0, scale))

    page_w, page_h = PAGE_SIZES_PX.get(page_size, PAGE_SIZES_PX["A4"])
    margin = 40
    header_h = 95 if subtitle else 70
    footer = 40
    gap = 12
    content_w = page_w - margin * 2
    content_h = page_h - header_h - footer

    def draw_header(pg, page_no):
        try:
            from PIL import ImageDraw, ImageFont
            draw = ImageDraw.Draw(pg)
            try:
                font = ImageFont.truetype(BUNDLED_FONT, 28)
                small = ImageFont.truetype(BUNDLED_FONT, 18)
            except Exception:
                font = small = ImageFont.load_default()
            draw.text((margin, 20), title, fill="black", font=font)
            if subtitle:
                try:
                    sub_font = ImageFont.truetype(BUNDLED_FONT, 16)
                except Exception:
                    sub_font = font
                draw.text((margin, 56), subtitle, fill=(100, 100, 100), font=sub_font)
            draw.text((page_w - margin - 50, page_h - 30), "- {} -".format(page_no), fill="gray", font=small)
        except Exception:
            pass

    pages = []
    state = {"page": None, "y": 0}

    def new_page():
        pg = Image.new("RGB", (page_w, page_h), "white")
        draw_header(pg, len(pages) + 1)
        pages.append(pg)
        state["page"], state["y"] = pg, header_h

    new_page()
    for path, kind in norm:
        im = apply_enhance(Image.open(path).convert("RGB"), enhance)
        target_w = content_w if kind != "line" else int(content_w * scale)
        new_w = target_w
        new_h = int(round(im.height * new_w / float(im.width)))
        if new_h > content_h:  # 單張比一頁還高 → 縮到一頁放得下
            new_w, new_h = int(new_w * content_h / float(new_h)), content_h
        if state["y"] + new_h > header_h + content_h and state["y"] > header_h:
            new_page()
        im = im.resize((max(1, new_w), max(1, new_h)), Image.LANCZOS)
        state["page"].paste(im, (margin + (content_w - new_w) // 2, int(state["y"])))
        state["y"] += new_h + gap
    return pages


def build_pdf(items, out_path, title="吉他六線譜", subtitle="", page_size="A4", scale=1.0):
    """items 是已經做完畫質處理的圖片 [(路徑, 種類)]，排版後存成 PDF"""
    pages = layout_pages(items, title=title, subtitle=subtitle, page_size=page_size, scale=scale)
    first, rest = pages[0], pages[1:]
    first.save(out_path, save_all=True, append_images=rest, resolution=150)
    return out_path
