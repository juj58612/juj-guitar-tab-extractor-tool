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
6. 依時間序把剩下的獨立譜面圖片，黑白清晰化後直向拼接成 A4/Letter 頁面，輸出成 PDF
"""

import os
import io
import json
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

def get_video_info(path):
    """回傳 (duration_sec, width, height, fps)"""
    cap = cv2.VideoCapture(path)
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
    cap = cv2.VideoCapture(path)
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

def suggest_crop_box(path, duration, samples=10):
    """
    抽樣數張畫面，找出「細節最豐富、最穩定」的水平帶狀區域，
    當作預設的譜面框選建議 (使用者仍可在介面上手動調整)。
    回傳 dict: {x, y, w, h} (原始影片像素座標)
    """
    duration, width, height, fps = get_video_info(path)
    ts = [duration * (i + 1) / (samples + 1) for i in range(samples)]
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

    # 由上而下找「從暗轉亮」且持續穩定的那一列，當作譜面卡片的頂端
    y0 = None
    for i in range(len(med) - 5):
        if med[i] < 0.3 and np.mean(med[i + 1:i + 6]) > 0.7:
            y0 = i + 1
            break

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


SIG_SIZE = (320, 72)  # 縮小後拿來比對內容是否相同的統一尺寸


def _content_signature(pil_img):
    """
    把畫面轉成「純黑白內容遮罩」，用來判斷兩張截圖是不是「同一份譜面」。

    這裡刻意不用一般的感知雜湊(perceptual hash)，因為這類教學影片常常會在
    譜面上疊一層會移動的「目前彈到哪裡」淺色提示框(高亮目前小節)——同一份
    譜面、只是提示框位置不同，畫面在感知雜湊上卻會被誤判成「差很多」。
    直接比對二值化後的黑白內容遮罩，只看「線條與數字」本身有沒有變，
    對這種半透明提示框的干擾就穩定得多。
    """
    gray = np.array(pil_img.convert("L").resize(SIG_SIZE, Image.LANCZOS))
    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return bw


def _content_diff(sig_a, sig_b):
    """回傳兩個內容遮罩之間「不一樣的像素」比例 (0~1)"""
    diff = sig_a != sig_b
    return float(np.mean(diff))


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


def process_video(
    path,
    crop_box,
    out_dir,
    stride_sec=0.5,
    density_threshold=None,
    similarity_threshold=6,
    title="吉他六線譜",
    page_size="A4",
    bw_enhance=False,
    progress_cb=None,
):
    """
    執行完整流程。progress_cb(percent:int, message:str) 用來回報進度。
    回傳 dict: {pdf_path, crops_dir, kept_count, sections: [{t, path}]}
    """

    def report(pct, msg):
        if progress_cb:
            progress_cb(pct, msg)

    duration, width, height, fps = get_video_info(path)
    if duration <= 0:
        raise RuntimeError("讀取不到影片長度，檔案可能已損毀")

    os.makedirs(out_dir, exist_ok=True)
    crops_dir = os.path.join(out_dir, "crops")
    os.makedirs(crops_dir, exist_ok=True)

    # --- 1. 取樣並計算每張的邊緣密度 -------------------------------------------------
    report(2, "正在讀取影片...")
    cap = cv2.VideoCapture(path)
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

    # --- 3. 依「畫面內容變化」切分出每一份「獨立顯示過」的譜面 -----------------------
    # 教學影片常會在譜面上疊一層會移動的「目前彈到哪」提示框，同一份譜面因此會
    # 連續出現好幾張「內容其實一樣、提示框位置不同」的畫面，這裡用內容遮罩比對
    # 把它們合併成同一段，只取其中最清晰的一張。
    report(68, "偵測譜面切換中...")
    CONTENT_SAME_FRACTION = 0.04  # 內容差異在 4% 以內，視為同一張譜面(只是提示框移動)

    runs = []
    cur_run = []
    cur_sig = None
    for t, crop, score in candidates:
        sig = _content_signature(crop)
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
        best = max(run, key=lambda r: r[2])  # 取該段落中最清晰(邊緣密度最高)的一張
        representatives.append((best[0], best[1]))

    # --- 4. 去除「跨時間重複」的譜面 -----------------------------------------------
    # (例如同一段落被重複講解/回顧，後面出現的重複畫面就濾掉，只保留第一次出現的)
    report(78, "比對去除重複譜面中...")
    dup_fraction = max(0.0, min(1.0, similarity_threshold / 100.0))
    kept = []
    kept_sigs = []
    for t, crop in representatives:
        sig = _content_signature(crop)
        is_dup = any(_content_diff(sig, ks) <= dup_fraction for ks in kept_sigs)
        if not is_dup:
            kept.append((t, crop))
            kept_sigs.append(sig)

    # --- 5. 存檔 + 拼成 PDF -------------------------------------------------------
    report(88, "產生樂譜檔案中..." if not bw_enhance else "黑白清晰化處理中...")
    sections = []
    for i, (t, crop) in enumerate(kept):
        if bw_enhance:
            crop = enhance_for_print(crop)
        fname = "section_{:03d}_t{:.0f}s.png".format(i + 1, t)
        fpath = os.path.join(crops_dir, fname)
        crop.save(fpath)
        sections.append({"t": t, "path": fpath, "filename": fname})

    pdf_path = os.path.join(out_dir, "guitar_tab_score.pdf")
    build_pdf([s["path"] for s in sections], pdf_path, title=title, page_size=page_size)

    report(100, "完成！")
    return {
        "pdf_path": pdf_path,
        "crops_dir": crops_dir,
        "kept_count": len(kept),
        "sections": sections,
    }


# ---------------------------------------------------------------------------
# PDF 拼接
# ---------------------------------------------------------------------------

PAGE_SIZES_PX = {
    # 以 150 dpi 估算的頁面像素尺寸
    "A4": (1240, 1754),
    "Letter": (1275, 1650),
}


def build_pdf(image_paths, out_path, title="吉他六線譜", page_size="A4"):
    if not image_paths:
        raise RuntimeError("沒有可用的譜面圖片可以組成 PDF")

    page_w, page_h = PAGE_SIZES_PX.get(page_size, PAGE_SIZES_PX["A4"])
    margin = 40
    header_h = 70
    gap = 18
    content_w = page_w - margin * 2

    pages = []
    page = Image.new("RGB", (page_w, page_h), "white")
    cursor_y = margin

    def new_page():
        nonlocal page, cursor_y
        pages.append(page)
        page = Image.new("RGB", (page_w, page_h), "white")
        cursor_y = margin

    def draw_header(pg, page_no):
        try:
            from PIL import ImageDraw, ImageFont
            draw = ImageDraw.Draw(pg)
            try:
                font = ImageFont.truetype(BUNDLED_FONT, 28)
            except Exception:
                font = ImageFont.load_default()
            draw.text((margin, 20), title, fill="black", font=font)
            draw.text(
                (page_w - margin - 60, page_h - 30),
                "- {} -".format(page_no),
                fill="gray",
                font=font,
            )
        except Exception:
            pass

    draw_header(page, len(pages) + 1)
    cursor_y = header_h

    for p in image_paths:
        im = Image.open(p).convert("RGB")
        scale = content_w / im.width
        new_w = content_w
        new_h = int(im.height * scale)
        im_resized = im.resize((new_w, new_h), Image.LANCZOS)

        if cursor_y + new_h + margin > page_h:
            new_page()
            draw_header(page, len(pages) + 1)
            cursor_y = header_h

        page.paste(im_resized, (margin, cursor_y))
        cursor_y += new_h + gap

    pages.append(page)

    first, rest = pages[0], pages[1:]
    first.save(out_path, save_all=True, append_images=rest)
    return out_path
