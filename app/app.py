# -*- coding: utf-8 -*-
"""
吉他六線譜自動擷取工具 - 網頁伺服器 (本地端 / 也可部署到伺服器)

本地用法：
    python app.py
    然後瀏覽器開啟 http://127.0.0.1:5001
(一般不需要手動執行，用 start_mac.command / start_windows.bat 雙擊即可)

部署到伺服器：見 README.md「部署到伺服器」章節。重點環境變數：
    HOST         監聽位址，本地預設 127.0.0.1，伺服器上通常設成 0.0.0.0
    PORT         監聽埠號，預設 5001
    ACCESS_CODE  設定後，網頁與所有 API 都需要輸入這組存取碼才能使用
                 (伺服器對外開放時強烈建議設定，避免被不特定人濫用)
"""

import os
import uuid
import shutil
import threading
import traceback

from flask import (
    Flask, request, jsonify, send_file, render_template, url_for,
    redirect, make_response,
)

import pipeline
import downloader

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WORK_DIR = os.path.join(BASE_DIR, "_work")
os.makedirs(WORK_DIR, exist_ok=True)

ACCESS_CODE = os.environ.get("ACCESS_CODE", "").strip()
COOKIE_NAME = "gte_access_code"

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024 * 1024  # 4GB 上限
app.config["TEMPLATES_AUTO_RELOAD"] = True


@app.context_processor
def _static_version():
    """
    網頁引用 app.js / style.css 時加上檔案修改時間 (?v=...)，程式一更新瀏覽器就會
    抓新的，不會發生「新版網頁 + 舊版快取程式」混在一起而出錯的情況。
    """
    def static_v(filename):
        try:
            v = int(os.path.getmtime(os.path.join(app.static_folder, filename)))
        except OSError:
            v = 0
        return url_for("static", filename=filename, v=v)
    return {"static_v": static_v}

VIDEO_EXTS = {
    ".mp4", ".m4v", ".mov", ".avi", ".mkv", ".webm", ".flv", ".wmv",
    ".ts", ".mts", ".m2ts", ".mpg", ".mpeg", ".3gp",
}

SCORE_TYPES = ("guitar", "piano")
DEFAULT_TITLES = {"guitar": "吉他六線譜", "piano": "鋼琴譜"}


def _score_type(value):
    return value if value in SCORE_TYPES else "guitar"


JOBS = {}  # job_id -> dict(status, progress, message, result, error, video_path, ...)
JOBS_LOCK = threading.Lock()


def job_dir(job_id):
    d = os.path.join(WORK_DIR, job_id)
    os.makedirs(d, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# 簡易存取碼保護 (只有設定 ACCESS_CODE 環境變數時才會啟用)
# ---------------------------------------------------------------------------

def _authorized(req):
    if not ACCESS_CODE:
        return True
    return req.cookies.get(COOKIE_NAME) == ACCESS_CODE


@app.before_request
def _check_access():
    if not ACCESS_CODE:
        return None
    if request.path in ("/api/login", "/login") or request.path.startswith("/static/"):
        return None
    if not _authorized(request):
        if request.path.startswith("/api/"):
            return jsonify({"error": "需要存取碼", "need_login": True}), 401
        return redirect("/login")
    return None


@app.route("/login", methods=["GET", "POST"])
def login_page():
    error = None
    if request.method == "POST":
        code = request.form.get("code", "")
        if code == ACCESS_CODE:
            resp = make_response(redirect("/"))
            resp.set_cookie(COOKIE_NAME, code, max_age=60 * 60 * 24 * 30, httponly=True, samesite="Lax")
            return resp
        error = "存取碼錯誤"
    return render_template("login.html", error=error)


# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


def _finish_preparing(job_id, video_path, score_type="guitar"):
    """影片就緒後 (無論是上傳還是網址下載來的)，計算長度/預設框選/預覽圖"""
    d = job_dir(job_id)
    duration, width, height, fps = pipeline.get_video_info(video_path)

    try:
        crop_box = pipeline.suggest_crop_box(video_path, duration, score_type=score_type)
    except Exception:
        crop_box = {"x": 0, "y": int(height * 0.55), "w": width, "h": int(height * 0.42)}

    preview_t = duration * 0.4
    preview_img = pipeline.grab_frame_at(video_path, preview_t)
    preview_path = os.path.join(d, "preview.jpg")
    if preview_img is not None:
        preview_img.save(preview_path, quality=88)

    with JOBS_LOCK:
        JOBS[job_id].update({
            "status": "ready",
            "progress": 100,
            "message": "",
            "video_path": video_path,
            "duration": duration,
            "width": width,
            "height": height,
            "fps": fps,
            "suggested_crop": crop_box,
        })


@app.route("/api/upload", methods=["POST"])
def api_upload():
    f = request.files.get("video")
    if not f or not f.filename:
        return jsonify({"error": "沒有收到影片檔案"}), 400

    # 先擋掉明顯不是影片的檔案 (例如誤選了 PDF/圖片)，不然要到 OpenCV 讀檔時才會失敗
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in VIDEO_EXTS:
        return jsonify({"error": "不支援的檔案格式「{}」，請上傳影片檔 (例如 mp4 / mov / mkv)".format(ext or f.filename)}), 400

    job_id = uuid.uuid4().hex[:12]
    d = job_dir(job_id)
    video_path = os.path.join(d, "source" + ext)
    f.save(video_path)

    score_type = _score_type(request.form.get("score_type"))
    with JOBS_LOCK:
        JOBS[job_id] = {"status": "preparing", "progress": 0, "message": "", "score_type": score_type}

    try:
        _finish_preparing(job_id, video_path, score_type)
    except Exception as e:
        shutil.rmtree(d, ignore_errors=True)
        with JOBS_LOCK:
            JOBS.pop(job_id, None)
        return jsonify({"error": "無法讀取影片：{}".format(e)}), 400

    with JOBS_LOCK:
        job = JOBS[job_id]

    return jsonify({
        "job_id": job_id,
        "duration": job["duration"],
        "width": job["width"],
        "height": job["height"],
        "suggested_crop": job["suggested_crop"],
        "preview_url": url_for("api_preview_image", job_id=job_id),
    })


def _run_url_download(job_id, url, score_type="guitar"):
    d = job_dir(job_id)

    def progress_cb(pct, msg):
        with JOBS_LOCK:
            JOBS[job_id]["progress"] = pct
            JOBS[job_id]["message"] = msg

    try:
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "downloading"

        video_path = downloader.download_video_from_url(url, d, progress_cb=progress_cb)

        with JOBS_LOCK:
            JOBS[job_id]["status"] = "preparing"
            JOBS[job_id]["message"] = "分析影片中..."

        _finish_preparing(job_id, video_path, score_type)

    except downloader.UnsafeURLError as e:
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "error"
            JOBS[job_id]["error"] = str(e)
    except Exception as e:
        traceback.print_exc()
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "error"
            JOBS[job_id]["error"] = "下載或分析失敗：{}".format(e)


@app.route("/api/from_url", methods=["POST"])
def api_from_url():
    data = request.get_json(force=True)
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "請輸入影片網址"}), 400

    try:
        downloader.assert_public_http_url(url)
    except downloader.UnsafeURLError as e:
        return jsonify({"error": str(e)}), 400

    job_id = uuid.uuid4().hex[:12]
    job_dir(job_id)
    score_type = _score_type(data.get("score_type"))
    with JOBS_LOCK:
        JOBS[job_id] = {"status": "downloading", "progress": 0, "message": "準備下載...", "score_type": score_type}

    th = threading.Thread(target=_run_url_download, args=(job_id, url, score_type), daemon=True)
    th.start()

    return jsonify({"job_id": job_id})


@app.route("/api/preview/<job_id>")
def api_preview_image(job_id):
    d = job_dir(job_id)
    p = os.path.join(d, "preview.jpg")
    if not os.path.exists(p):
        return jsonify({"error": "找不到預覽畫面"}), 404
    return send_file(p, mimetype="image/jpeg")


@app.route("/api/preview_at", methods=["POST"])
def api_preview_at():
    """依指定秒數重新抓一張預覽畫面，方便使用者挑一張含有譜面的畫面來框選"""
    data = request.get_json(force=True)
    job_id = data.get("job_id")
    t = float(data.get("t", 0))
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job or "video_path" not in job:
        return jsonify({"error": "找不到工作"}), 404

    img = pipeline.grab_frame_at(job["video_path"], t)
    if img is None:
        return jsonify({"error": "抓取畫面失敗"}), 400
    d = job_dir(job_id)
    p = os.path.join(d, "preview.jpg")
    img.save(p, quality=88)
    return jsonify({"preview_url": url_for("api_preview_image", job_id=job_id) + "?t=" + str(t)})


@app.route("/api/preview_enhance", methods=["POST"])
def api_preview_enhance():
    """
    畫質處理的「先看再決定」預覽：只針對目前時間點、目前框選的區域，套用使用者
    選的處理方式 (智慧畫質增強 / 黑白清晰化) 產生一張預覽，不會影響正式輸出。
    """
    data = request.get_json(force=True)
    job_id = data.get("job_id")
    crop_box = data.get("crop_box")
    mode = data.get("enhance", "smart")
    if mode not in pipeline.ENHANCE_MODES:
        return jsonify({"error": "不支援的畫質處理方式"}), 400
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job or "video_path" not in job:
        return jsonify({"error": "找不到工作"}), 404
    if not crop_box and not data.get("filename"):
        return jsonify({"error": "缺少框選區域"}), 400

    t = float(data.get("t", job.get("duration", 0) * 0.4))
    filename = data.get("filename")
    try:
        if filename:
            # 直接拿已擷取的譜面片段來預覽 (預覽結果區用)
            p = os.path.join(job_dir(job_id), "captures", os.path.basename(str(filename)))
            if not os.path.exists(p):
                return jsonify({"error": "找不到這張譜面"}), 404
            crop = pipeline.Image.open(p).convert("RGB")
        else:
            img = pipeline.grab_frame_at(job["video_path"], t)
            if img is None:
                return jsonify({"error": "抓取畫面失敗"}), 400
            crop = pipeline._crop(img, crop_box)
        enhanced = pipeline.apply_enhance(crop, mode)
    except Exception as e:
        return jsonify({"error": "產生預覽失敗：{}".format(e)}), 400

    d = job_dir(job_id)
    p = os.path.join(d, "preview_enhance.jpg")
    enhanced.save(p, quality=92)
    return jsonify({
        "preview_url": url_for("api_preview_enhance_image", job_id=job_id) + "?t=" + str(t) + "&m=" + mode + "&f=" + str(filename or "")
    })


@app.route("/api/preview_enhance_image/<job_id>")
def api_preview_enhance_image(job_id):
    d = job_dir(job_id)
    p = os.path.join(d, "preview_enhance.jpg")
    if not os.path.exists(p):
        return jsonify({"error": "尚未產生預覽"}), 404
    return send_file(p, mimetype="image/jpeg")


# ---------------------------------------------------------------------------
# 手動模式：使用者自己拖時間軸、一張張擷取想要的畫面
# (構想來自 will095614/sheetthief)
# ---------------------------------------------------------------------------

@app.route("/api/capture", methods=["POST"])
def api_capture():
    data = request.get_json(force=True)
    job_id = data.get("job_id")
    crop_box = data.get("crop_box")
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job or "video_path" not in job:
        return jsonify({"error": "找不到工作"}), 404
    if not crop_box:
        return jsonify({"error": "缺少框選區域"}), 400

    t = float(data.get("t", 0))
    img = pipeline.grab_frame_at(job["video_path"], t)
    if img is None:
        return jsonify({"error": "抓取畫面失敗"}), 400

    try:
        crop = pipeline._crop(img, crop_box)
    except Exception as e:
        return jsonify({"error": "裁切畫面失敗：{}".format(e)}), 400

    cap_dir = os.path.join(job_dir(job_id), "captures")
    os.makedirs(cap_dir, exist_ok=True)

    # 跟自動擷取一樣切成一行一行；切不出來 (例如和弦表、調弦設定畫面) 就整張保留
    with JOBS_LOCK:
        ext = job.get("line_ext")
        score_type = job.get("score_type", "guitar")
    lines, _ = pipeline.split_pages_into_lines([(t, crop)], ext=ext, score_type=score_type)
    items = []
    if lines:
        for ln in lines:
            fname = "capture_t{:.1f}_{}.png".format(t, uuid.uuid4().hex[:6])
            ln["img"].save(os.path.join(cap_dir, fname))
            items.append({"t": t, "filename": fname, "kind": "line"})
    else:
        img = pipeline.Image.fromarray(pipeline.clean_highlight(crop)).convert("RGB")
        fname = "capture_t{:.1f}_{}.png".format(t, uuid.uuid4().hex[:6])
        img.save(os.path.join(cap_dir, fname))
        items.append({"t": t, "filename": fname, "kind": "page"})
    for it in items:
        it["url"] = url_for("api_capture_image", job_id=job_id, filename=it["filename"])
    return jsonify({"items": items})


@app.route("/api/capture_image/<job_id>/<filename>")
def api_capture_image(job_id, filename):
    p = os.path.join(job_dir(job_id), "captures", os.path.basename(filename))
    if not os.path.exists(p):
        return jsonify({"error": "找不到檔案"}), 404
    return send_file(p, mimetype="image/png")


@app.route("/api/build_manual", methods=["POST"])
def api_build_manual():
    """把手動擷取的畫面 (依前端傳來的順序) 套用畫質處理後拼成 PDF"""
    data = request.get_json(force=True)
    job_id = data.get("job_id")
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job or "video_path" not in job:
        return jsonify({"error": "找不到工作，請重新上傳影片或重新輸入網址"}), 404

    enhance = data.get("enhance", "none")
    if enhance not in pipeline.ENHANCE_MODES:
        return jsonify({"error": "不支援的畫質處理方式"}), 400

    cap_dir = os.path.join(job_dir(job_id), "captures")
    captures = []
    for item in data.get("captures") or []:
        p = os.path.join(cap_dir, os.path.basename(str(item.get("filename", ""))))
        if os.path.exists(p):
            captures.append({"t": float(item.get("t", 0)), "path": p,
                             "kind": item.get("kind") if item.get("kind") in ("line", "intro", "page") else "line"})
    if not captures:
        return jsonify({"error": "還沒有擷取任何畫面，請先按「📸 擷取當前畫面」"}), 400

    try:
        result = pipeline.build_from_captures(
            captures,
            out_dir=job_dir(job_id),
            title=data.get("title") or DEFAULT_TITLES[job.get("score_type", "guitar")],
            subtitle=(data.get("subtitle") or "").strip(),
            page_size=data.get("page_size") or "A4",
            enhance=enhance,
            scale=data.get("scale") or 1.0,
        )
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": "產生 PDF 失敗：{}".format(e)}), 500

    with JOBS_LOCK:
        job["status"] = "done"
        job["progress"] = 100
        job["result"] = result

    return jsonify({
        "kept_count": result["kept_count"],
        "pdf_url": url_for("api_download_pdf", job_id=job_id),
    })


def _run_auto_extract(job_id, params):
    """
    自動擷取：掃過整部影片抓出每一份譜面，存成「擷取片段」(跟手動擷取同一種格式)，
    讓前端先列出來給使用者預覽、移除抓錯的，再用 /api/build_manual 產生 PDF。
    """
    def progress_cb(pct, msg):
        with JOBS_LOCK:
            JOBS[job_id]["progress"] = pct
            JOBS[job_id]["message"] = msg

    try:
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "extracting"
            video_path = JOBS[job_id]["video_path"]
            score_type = JOBS[job_id].get("score_type", "guitar")

        kept, intro_pages = pipeline.extract_sections(
            video_path,
            crop_box=params["crop_box"],
            stride_sec=params["stride_sec"],
            similarity_threshold=params["similarity_threshold"],
            progress_cb=progress_cb,
            return_intro=True,
            score_type=score_type,
        )

        progress_cb(96, "把每張截圖切成一行一行的譜...")
        lines, ext = pipeline.split_pages_into_lines(kept, score_type=score_type)

        cap_dir = os.path.join(job_dir(job_id), "captures")
        os.makedirs(cap_dir, exist_ok=True)
        captures = []
        # 開頭資訊頁 (歌名/和弦表/調弦)：清掉提示色後整頁保留，讓使用者決定留不留
        for t, crop in intro_pages:
            img = pipeline.Image.fromarray(pipeline.clean_highlight(crop)).convert("RGB")
            fname = "intro_t{:.1f}_{}.png".format(t, uuid.uuid4().hex[:6])
            img.save(os.path.join(cap_dir, fname))
            captures.append({"t": t, "filename": fname, "kind": "intro"})
        if lines:
            for ln in lines:
                fname = "line_t{:.1f}_{}.png".format(ln["t"], uuid.uuid4().hex[:6])
                ln["img"].save(os.path.join(cap_dir, fname))
                captures.append({"t": ln["t"], "filename": fname, "kind": "line"})
        else:
            # 切不出「行」(例如不是標準的五線譜/TAB 版面)：退回整張截圖
            for t, crop in kept:
                img = pipeline.Image.fromarray(pipeline.clean_highlight(crop)).convert("RGB")
                fname = "page_t{:.1f}_{}.png".format(t, uuid.uuid4().hex[:6])
                img.save(os.path.join(cap_dir, fname))
                captures.append({"t": t, "filename": fname, "kind": "page"})

        with JOBS_LOCK:
            JOBS[job_id]["status"] = "extracted"
            JOBS[job_id]["progress"] = 100
            JOBS[job_id]["message"] = ""
            JOBS[job_id]["auto_captures"] = captures
            JOBS[job_id]["line_ext"] = ext

    except Exception as e:
        traceback.print_exc()
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "error"
            JOBS[job_id]["error"] = str(e)


@app.route("/api/auto_extract", methods=["POST"])
def api_auto_extract():
    data = request.get_json(force=True)
    job_id = data.get("job_id")
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job or "video_path" not in job:
        return jsonify({"error": "找不到工作，請重新上傳影片或重新輸入網址"}), 404
    if job.get("status") == "extracting":
        return jsonify({"error": "正在擷取中，請稍候"}), 409

    crop_box = data.get("crop_box") or job.get("suggested_crop")
    params = {
        "crop_box": {k: int(round(float(crop_box[k]))) for k in ("x", "y", "w", "h")},
        "stride_sec": float(data.get("stride_sec", 0.5)),
        "similarity_threshold": int(data.get("similarity_threshold", 6)),
    }

    with JOBS_LOCK:
        JOBS[job_id]["status"] = "extracting"
        JOBS[job_id]["progress"] = 0
        JOBS[job_id]["message"] = "準備掃描影片..."
        JOBS[job_id].pop("auto_captures", None)

    th = threading.Thread(target=_run_auto_extract, args=(job_id, params), daemon=True)
    th.start()
    return jsonify({"ok": True})


@app.route("/api/preview_layout", methods=["POST"])
def api_preview_layout():
    """
    排版預覽：依使用者選的紙張大小、每頁幾段、畫質處理，把預覽區目前的譜面排成
    一頁頁的縮圖給使用者看，確認後再產生 PDF。
    """
    data = request.get_json(force=True)
    job_id = data.get("job_id")
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job or "video_path" not in job:
        return jsonify({"error": "找不到工作，請重新上傳影片或重新輸入網址"}), 404
    enhance = data.get("enhance", "none")
    if enhance not in pipeline.ENHANCE_MODES:
        return jsonify({"error": "不支援的畫質處理方式"}), 400

    cap_dir = os.path.join(job_dir(job_id), "captures")
    paths = []
    for item in data.get("captures") or []:
        p = os.path.join(cap_dir, os.path.basename(str(item.get("filename", ""))))
        if os.path.exists(p):
            kind = item.get("kind") if item.get("kind") in ("line", "intro", "page") else "line"
            paths.append((p, kind))
    if not paths:
        return jsonify({"error": "預覽區沒有任何譜面"}), 400

    try:
        pages = pipeline.layout_pages(
            paths,
            title=data.get("title") or DEFAULT_TITLES[job.get("score_type", "guitar")],
            subtitle=(data.get("subtitle") or "").strip(),
            page_size=data.get("page_size") or "A4",
            scale=data.get("scale") or 1.0,
            enhance=enhance,
        )
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": "產生排版預覽失敗：{}".format(e)}), 500

    out_dir = os.path.join(job_dir(job_id), "layout_preview")
    shutil.rmtree(out_dir, ignore_errors=True)
    os.makedirs(out_dir)
    stamp = uuid.uuid4().hex[:6]
    urls = []
    for i, pg in enumerate(pages):
        fname = "page_{:03d}_{}.jpg".format(i + 1, stamp)
        thumb = pg.copy()
        thumb.thumbnail((620, 900))
        thumb.save(os.path.join(out_dir, fname), quality=85)
        urls.append(url_for("api_layout_page", job_id=job_id, filename=fname))
    return jsonify({"page_count": len(pages), "pages": urls})


@app.route("/api/layout_page/<job_id>/<filename>")
def api_layout_page(job_id, filename):
    p = os.path.join(job_dir(job_id), "layout_preview", os.path.basename(filename))
    if not os.path.exists(p):
        return jsonify({"error": "找不到檔案"}), 404
    return send_file(p, mimetype="image/jpeg")


def _run_job(job_id, params):
    d = job_dir(job_id)

    def progress_cb(pct, msg):
        with JOBS_LOCK:
            JOBS[job_id]["progress"] = pct
            JOBS[job_id]["message"] = msg

    try:
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "running"
            video_path = JOBS[job_id]["video_path"]

        result = pipeline.process_video(
            video_path,
            crop_box=params["crop_box"],
            out_dir=d,
            stride_sec=params["stride_sec"],
            density_threshold=params.get("density_threshold"),
            similarity_threshold=params["similarity_threshold"],
            title=params["title"],
            subtitle=params.get("subtitle", ""),
            page_size=params["page_size"],
            enhance=params.get("enhance", "none"),
            progress_cb=progress_cb,
        )

        with JOBS_LOCK:
            JOBS[job_id]["status"] = "done"
            JOBS[job_id]["progress"] = 100
            JOBS[job_id]["result"] = result

    except Exception as e:
        traceback.print_exc()
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "error"
            JOBS[job_id]["error"] = str(e)


@app.route("/api/process", methods=["POST"])
def api_process():
    data = request.get_json(force=True)
    job_id = data.get("job_id")
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job or "video_path" not in job:
        return jsonify({"error": "找不到工作，請重新上傳影片或重新輸入網址"}), 404

    crop_box = data.get("crop_box")
    if not crop_box:
        return jsonify({"error": "缺少框選區域"}), 400

    enhance = data.get("enhance", "none")
    if enhance not in pipeline.ENHANCE_MODES:
        return jsonify({"error": "不支援的畫質處理方式"}), 400

    params = {
        "crop_box": crop_box,
        "stride_sec": float(data.get("stride_sec", 0.5)),
        "density_threshold": data.get("density_threshold"),
        "similarity_threshold": int(data.get("similarity_threshold", 6)),
        "title": data.get("title") or DEFAULT_TITLES[job.get("score_type", "guitar")],
        "subtitle": (data.get("subtitle") or "").strip(),
        "page_size": data.get("page_size") or "A4",
        "enhance": enhance,
    }

    with JOBS_LOCK:
        JOBS[job_id]["status"] = "queued"
        JOBS[job_id]["progress"] = 0

    th = threading.Thread(target=_run_job, args=(job_id, params), daemon=True)
    th.start()

    return jsonify({"ok": True})


@app.route("/api/status/<job_id>")
def api_status(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "找不到工作"}), 404

    resp = {
        "status": job["status"],
        "progress": job.get("progress", 0),
        "message": job.get("message", ""),
    }

    if job["status"] in ("ready", "queued", "running", "extracting", "extracted", "done") and "duration" in job:
        resp["duration"] = job["duration"]
        resp["width"] = job["width"]
        resp["height"] = job["height"]
        resp["suggested_crop"] = job["suggested_crop"]
        resp["preview_url"] = url_for("api_preview_image", job_id=job_id)

    if job["status"] == "extracted":
        resp["captures"] = [
            {"t": c["t"], "filename": c["filename"], "kind": c.get("kind", "line"),
             "url": url_for("api_capture_image", job_id=job_id, filename=c["filename"])}
            for c in job.get("auto_captures", [])
        ]

    if job["status"] == "done":
        result = job["result"]
        resp["kept_count"] = result["kept_count"]
        resp["pdf_url"] = url_for("api_download_pdf", job_id=job_id)
        resp["sections"] = [
            {"t": s["t"], "url": url_for("api_download_section", job_id=job_id, filename=s["filename"])}
            for s in result["sections"]
        ]
    if job["status"] == "error":
        resp["error"] = job.get("error", "未知錯誤")
    return jsonify(resp)


@app.route("/api/download/<job_id>/pdf")
def api_download_pdf(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job or job.get("status") != "done":
        return jsonify({"error": "尚未完成"}), 404
    pdf_path = job["result"]["pdf_path"]
    return send_file(pdf_path, as_attachment=True, download_name="score.pdf")


@app.route("/api/download/<job_id>/section/<filename>")
def api_download_section(job_id, filename):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job or job.get("status") != "done":
        return jsonify({"error": "尚未完成"}), 404
    p = os.path.join(job["result"]["crops_dir"], os.path.basename(filename))
    if not os.path.exists(p):
        return jsonify({"error": "找不到檔案"}), 404
    return send_file(p, mimetype="image/png")


if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", 5001))
    print("=" * 50)
    print(" 吉他六線譜自動擷取工具已啟動")
    if host == "127.0.0.1":
        print(" 請在瀏覽器開啟： http://127.0.0.1:{}".format(port))
    else:
        print(" 監聽於 {}:{}".format(host, port))
    if ACCESS_CODE:
        print(" 已啟用存取碼保護")
    else:
        print(" 未設定 ACCESS_CODE，任何能連到這個網址的人都能直接使用")
    print("=" * 50)
    app.run(host=host, port=port, debug=False, threaded=True)
