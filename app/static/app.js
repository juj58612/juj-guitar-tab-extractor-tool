(function () {
  "use strict";

  var state = {
    jobId: null,
    duration: 0,
    videoW: 0,
    videoH: 0,
    cropBox: null, // {x,y,w,h} in ORIGINAL video pixel coords
    pollTimer: null,
    mode: "auto", // "auto" 自動擷取 | "manual" 手動擷取
    captures: [], // 手動模式擷取的片段 [{t, filename, url}]，依時間排序
  };

  var el = {
    tabFile: document.getElementById("tabFile"),
    tabUrl: document.getElementById("tabUrl"),
    panelFile: document.getElementById("panelFile"),
    panelUrl: document.getElementById("panelUrl"),
    urlInput: document.getElementById("urlInput"),
    urlSubmitBtn: document.getElementById("urlSubmitBtn"),

    dropzone: document.getElementById("dropzone"),
    fileInput: document.getElementById("fileInput"),
    uploadStatus: document.getElementById("uploadStatus"),

    stepCrop: document.getElementById("step-crop"),
    stepReview: document.getElementById("step-review"),
    reviewSummary: document.getElementById("reviewSummary"),
    toggleAdjustBtn: document.getElementById("toggleAdjustBtn"),
    autoPanel: document.getElementById("autoPanel"),
    reExtractBtn: document.getElementById("reExtractBtn"),
    stepParams: document.getElementById("step-params"),
    stepProgress: document.getElementById("step-progress"),
    stepProgressTitle: document.getElementById("stepProgressTitle"),
    stepResult: document.getElementById("step-result"),

    cropStage: document.getElementById("cropStage"),
    previewImg: document.getElementById("previewImg"),
    cropBox: document.getElementById("cropBox"),
    previewSeek: document.getElementById("previewSeek"),
    previewSeekLabel: document.getElementById("previewSeekLabel"),

    titleInput: document.getElementById("titleInput"),
    subtitleInput: document.getElementById("subtitleInput"),
    pageSizeInput: document.getElementById("pageSizeInput"),
    scaleInput: document.getElementById("scaleInput"),
    layoutPreviewBtn: document.getElementById("layoutPreviewBtn"),
    layoutInfo: document.getElementById("layoutInfo"),
    layoutPages: document.getElementById("layoutPages"),
    strideInput: document.getElementById("strideInput"),
    enhanceInput: document.getElementById("enhanceInput"),
    enhancePreviewBtn: document.getElementById("enhancePreviewBtn"),
    enhancePreviewWrap: document.getElementById("enhancePreviewWrap"),
    enhancePreviewHint: document.getElementById("enhancePreviewHint"),
    enhancePreviewImg: document.getElementById("enhancePreviewImg"),
    startBtn: document.getElementById("startBtn"),

    wrap: document.querySelector(".wrap"),
    modeAuto: document.getElementById("modeAuto"),
    modeManual: document.getElementById("modeManual"),
    hintAuto: document.getElementById("hintAuto"),
    hintManual: document.getElementById("hintManual"),
    manualPanel: document.getElementById("manualPanel"),
    captureBtn: document.getElementById("captureBtn"),
    gallery: document.getElementById("gallery"),
    imageModal: document.getElementById("imageModal"),
    imageModalClose: document.getElementById("imageModalClose"),
    modalImg: document.getElementById("modalImg"),

    progressBar: document.getElementById("progressBar"),
    progressMsg: document.getElementById("progressMsg"),

    resultSummary: document.getElementById("resultSummary"),
    downloadBtn: document.getElementById("downloadBtn"),
    restartBtn: document.getElementById("restartBtn"),

    errorBox: document.getElementById("errorBox"),
  };

  // ---------------- 記住上次輸入的標題/副標題 ----------------
  // 避免每次重新整理頁面/開新分析時，使用者忘記重新輸入自訂標題，
  // 導致 PDF 又跑回預設標題（之前使用者實際回報過這個狀況）。
  var TITLE_STORAGE_KEY = "guitar_tab_extractor_title";
  var SUBTITLE_STORAGE_KEY = "guitar_tab_extractor_subtitle";

  (function restoreTitleFields() {
    try {
      var savedTitle = window.localStorage.getItem(TITLE_STORAGE_KEY);
      if (savedTitle) el.titleInput.value = savedTitle;
      var savedSubtitle = window.localStorage.getItem(SUBTITLE_STORAGE_KEY);
      if (savedSubtitle) el.subtitleInput.value = savedSubtitle;
    } catch (e) {
      // localStorage 不可用時（例如某些隱私模式）就單純略過，不影響其他功能
    }
  })();

  function persistTitleFields() {
    try {
      window.localStorage.setItem(TITLE_STORAGE_KEY, el.titleInput.value || "");
      window.localStorage.setItem(SUBTITLE_STORAGE_KEY, el.subtitleInput.value || "");
    } catch (e) {
      // 略過
    }
  }

  el.titleInput.addEventListener("input", persistTitleFields);
  el.subtitleInput.addEventListener("input", persistTitleFields);

  function showError(msg) {
    el.errorBox.textContent = "發生錯誤：" + msg;
    el.errorBox.classList.remove("hidden");
  }
  function clearError() {
    el.errorBox.classList.add("hidden");
    el.errorBox.textContent = "";
  }

  // ---------------- 上傳檔案 / 貼上網址 頁籤切換 ----------------

  el.tabFile.addEventListener("click", function () {
    el.tabFile.classList.add("active");
    el.tabUrl.classList.remove("active");
    el.panelFile.classList.remove("hidden");
    el.panelUrl.classList.add("hidden");
    // 很多人會以為「上傳檔案」這顆按鈕就是選檔按鈕，所以按下去時直接跳出選檔視窗
    el.fileInput.click();
  });
  el.tabUrl.addEventListener("click", function () {
    el.tabUrl.classList.add("active");
    el.tabFile.classList.remove("active");
    el.panelUrl.classList.remove("hidden");
    el.panelFile.classList.add("hidden");
  });

  el.urlSubmitBtn.addEventListener("click", function () {
    var url = (el.urlInput.value || "").trim();
    if (!url) { showError("請輸入影片網址"); return; }
    submitUrl(url);
  });

  function submitUrl(url) {
    clearError();
    el.uploadStatus.textContent = "";
    el.stepProgress.classList.remove("hidden");
    el.stepProgressTitle.textContent = "下載影片中...";
    el.progressBar.style.width = "0%";
    el.progressMsg.textContent = "準備下載...";

    fetch("/api/from_url", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: url }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.error) {
          showError(data.error);
          el.stepProgress.classList.add("hidden");
          return;
        }
        state.jobId = data.job_id;
        pollUntilReady();
      })
      .catch(function (e) {
        showError(String(e));
        el.stepProgress.classList.add("hidden");
      });
  }

  function pollUntilReady() {
    if (state.pollTimer) clearInterval(state.pollTimer);
    state.pollTimer = setInterval(checkReady, 1000);
    checkReady();
  }

  function checkReady() {
    fetch("/api/status/" + state.jobId)
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.error && !data.status) { showError(data.error); clearInterval(state.pollTimer); return; }

        el.progressBar.style.width = (data.progress || 0) + "%";
        el.progressMsg.textContent = data.message || "";
        if (data.status === "preparing") {
          el.stepProgressTitle.textContent = "分析影片中...";
        }

        if (data.status === "ready") {
          clearInterval(state.pollTimer);
          el.stepProgress.classList.add("hidden");
          applyPreparedData(data);
        } else if (data.status === "error") {
          clearInterval(state.pollTimer);
          showError(data.error);
          el.stepProgress.classList.add("hidden");
        }
      })
      .catch(function (e) { showError(String(e)); });
  }

  function applyPreparedData(data) {
    state.duration = data.duration;
    state.videoW = data.width;
    state.videoH = data.height;
    state.cropBox = data.suggested_crop;

    el.uploadStatus.textContent = "已就緒！影片長度約 " + Math.round(data.duration) + " 秒。";
    el.previewSeek.max = Math.max(1, Math.floor(data.duration * 10) / 10);
    el.previewSeek.value = Math.round(data.duration * 4) / 10;
    updateSeekLabel();

    loadPreviewImage(data.preview_url + "?v=1");
    // 影片就緒後直接開始自動擷取，不用使用者再按任何按鈕
    startAutoExtract();
  }

  // ---------------- 自動擷取 (掃描 → 預覽) ----------------

  function startAutoExtract() {
    clearError();
    if (!state.jobId) return;
    el.stepReview.classList.add("hidden");
    el.stepParams.classList.add("hidden");
    el.stepResult.classList.add("hidden");
    el.stepProgressTitle.textContent = "🤖 自動掃描影片、擷取譜面中...";
    el.progressBar.style.width = "0%";
    el.progressMsg.textContent = "準備掃描影片...";
    el.stepProgress.classList.remove("hidden");
    el.reExtractBtn.disabled = true;

    fetch("/api/auto_extract", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        job_id: state.jobId,
        crop_box: state.cropBox ? roundedCropBox() : null,
        stride_sec: Number(el.strideInput.value) || 0.5,
      }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.error) { throw new Error(data.error); }
        if (state.pollTimer) clearInterval(state.pollTimer);
        state.pollTimer = setInterval(pollExtract, 1000);
        pollExtract();
      })
      .catch(function (e) {
        el.stepProgress.classList.add("hidden");
        el.reExtractBtn.disabled = false;
        showError(e.message || String(e));
      });
  }

  function pollExtract() {
    fetch("/api/status/" + state.jobId)
      .then(function (r) { return r.json(); })
      .then(function (data) {
        el.progressBar.style.width = (data.progress || 0) + "%";
        el.progressMsg.textContent = data.message || "";
        if (data.status === "extracted") {
          clearInterval(state.pollTimer);
          el.stepProgress.classList.add("hidden");
          el.reExtractBtn.disabled = false;
          state.captures = data.captures || [];
          showReview(true);
        } else if (data.status === "error" || (data.error && !data.status)) {
          clearInterval(state.pollTimer);
          el.stepProgress.classList.add("hidden");
          el.reExtractBtn.disabled = false;
          showError(data.error || "自動擷取失敗");
          // 自動擷取失敗時，打開調整區讓使用者改框選範圍或改用手動擷取
          state.captures = [];
          showReview(false);
          openAdjust();
        }
      })
      .catch(function (e) { showError(String(e)); });
  }

  function showReview(fromAuto) {
    el.stepReview.classList.remove("hidden");
    el.stepParams.classList.remove("hidden");
    renderGallery();
    if (fromAuto) {
      var nl = state.captures.filter(function (c) { return c.kind === "line"; }).length;
      var ni = state.captures.filter(function (c) { return c.kind === "intro"; }).length;
      var np = state.captures.filter(function (c) { return c.kind === "page"; }).length;
      el.reviewSummary.textContent = state.captures.length
        ? "自動擷取完成：依影片時間順序，共 " + nl + " 行譜" +
          (ni ? "、" + ni + " 張開頭資訊頁" : "") +
          (np ? "、" + np + " 張整頁截圖 (這幾張切不出行)" : "") + "。"
        : "自動擷取沒有找到譜面。請按「調整框選範圍／手動補抓畫面」，把綠色框框到譜面上再重新擷取。";
      if (!state.captures.length) openAdjust();
      el.stepReview.scrollIntoView({ behavior: "smooth", block: "start" });
      if (state.captures.length) previewLayout();
    }
  }

  function openAdjust() {
    el.stepCrop.classList.remove("hidden");
    el.toggleAdjustBtn.textContent = "收起調整區";
    drawCropBox();
  }

  el.toggleAdjustBtn.addEventListener("click", function () {
    if (el.stepCrop.classList.contains("hidden")) {
      openAdjust();
      el.stepCrop.scrollIntoView({ behavior: "smooth", block: "start" });
    } else {
      el.stepCrop.classList.add("hidden");
      el.toggleAdjustBtn.textContent = "🛠 調整框選範圍／手動補抓畫面";
    }
  });

  el.reExtractBtn.addEventListener("click", function () {
    startAutoExtract();
  });

  // ---------------- Upload ----------------

  ["dragenter", "dragover"].forEach(function (evt) {
    el.dropzone.addEventListener(evt, function (e) {
      e.preventDefault();
      el.dropzone.classList.add("dragover");
    });
  });
  ["dragleave", "drop"].forEach(function (evt) {
    el.dropzone.addEventListener(evt, function (e) {
      e.preventDefault();
      el.dropzone.classList.remove("dragover");
    });
  });
  el.dropzone.addEventListener("drop", function (e) {
    var files = e.dataTransfer.files;
    if (files && files.length) uploadFile(files[0]);
  });
  el.fileInput.addEventListener("change", function () {
    if (el.fileInput.files.length) uploadFile(el.fileInput.files[0]);
  });

  function uploadFile(file) {
    clearError();
    // 先在瀏覽器擋掉明顯不是影片的檔案，省得把整個檔案上傳完才被伺服器拒絕。
    // 有些影片格式 (例如 .mkv) 瀏覽器認不出類型、file.type 是空字串，這種交給伺服器判斷。
    if (file.type && file.type.indexOf("video/") !== 0) {
      showError("請上傳影片檔 (例如 mp4 / mov / mkv)，不支援這個檔案：" + file.name);
      return;
    }
    el.uploadStatus.textContent = "上傳並分析影片中，請稍候...(大檔案可能需要一點時間)";
    var fd = new FormData();
    fd.append("video", file);

    fetch("/api/upload", { method: "POST", body: fd })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.error) { showError(data.error); el.uploadStatus.textContent = ""; return; }
        state.jobId = data.job_id;
        applyPreparedData(data);
      })
      .catch(function (e) {
        showError(String(e));
        el.uploadStatus.textContent = "";
      });
  }

  function loadPreviewImage(url) {
    el.previewImg.onload = function () { drawCropBox(); };
    el.previewImg.src = url;
  }

  // ---------------- Crop box drag/resize ----------------
  // cropBox 的座標以「顯示影像的像素」為準，需要與原始影片像素互相換算

  function scaleFactor() {
    var displayedW = el.previewImg.clientWidth || 1;
    return displayedW / state.videoW;
  }

  function drawCropBox() {
    if (!state.cropBox) return;
    var s = scaleFactor();
    el.cropBox.style.left = (state.cropBox.x * s) + "px";
    el.cropBox.style.top = (state.cropBox.y * s) + "px";
    el.cropBox.style.width = (state.cropBox.w * s) + "px";
    el.cropBox.style.height = (state.cropBox.h * s) + "px";
  }

  window.addEventListener("resize", drawCropBox);

  (function setupCropDrag() {
    var dragging = null; // 'move' | 'nw' | 'ne' | 'sw' | 'se'
    var startMouse = { x: 0, y: 0 };
    var startBox = null;

    function toOrig(dx, dy) {
      var s = scaleFactor();
      return { dx: dx / s, dy: dy / s };
    }

    el.cropBox.addEventListener("mousedown", function (e) {
      dragging = "move";
      startMouse = { x: e.clientX, y: e.clientY };
      startBox = Object.assign({}, state.cropBox);
      e.preventDefault();
    });
    el.cropBox.querySelectorAll(".handle").forEach(function (h) {
      h.addEventListener("mousedown", function (e) {
        dragging = h.classList[1]; // nw/ne/sw/se
        startMouse = { x: e.clientX, y: e.clientY };
        startBox = Object.assign({}, state.cropBox);
        e.stopPropagation();
        e.preventDefault();
      });
    });

    document.addEventListener("mousemove", function (e) {
      if (!dragging) return;
      var d = toOrig(e.clientX - startMouse.x, e.clientY - startMouse.y);
      var box = Object.assign({}, startBox);

      if (dragging === "move") {
        box.x = clamp(startBox.x + d.dx, 0, state.videoW - startBox.w);
        box.y = clamp(startBox.y + d.dy, 0, state.videoH - startBox.h);
      } else {
        if (dragging.indexOf("n") === 0) {
          box.y = clamp(startBox.y + d.dy, 0, startBox.y + startBox.h - 20);
          box.h = startBox.y + startBox.h - box.y;
        }
        if (dragging.indexOf("s") === 0) {
          box.h = clamp(startBox.h + d.dy, 20, state.videoH - startBox.y);
        }
        if (dragging.indexOf("w") !== -1 && dragging.length === 2) {
          box.x = clamp(startBox.x + d.dx, 0, startBox.x + startBox.w - 20);
          box.w = startBox.x + startBox.w - box.x;
        }
        if (dragging.indexOf("e") !== -1 && dragging.length === 2) {
          box.w = clamp(startBox.w + d.dx, 20, state.videoW - startBox.x);
        }
      }
      state.cropBox = box;
      drawCropBox();
    });
    document.addEventListener("mouseup", function () { dragging = null; });
  })();

  function clamp(v, min, max) { return Math.max(min, Math.min(max, v)); }

  // ---------------- 時間軸 / 微調按鈕 ----------------
  // 拖曳時間軸或按 ±秒按鈕時，停下來 0.3 秒才去抓新畫面，避免連續送出一堆請求

  var seekDebounce = null;

  function currentT() { return Number(el.previewSeek.value); }

  function updateSeekLabel() {
    el.previewSeekLabel.textContent = currentT().toFixed(1) + " 秒";
  }

  function schedulePreview() {
    updateSeekLabel();
    if (seekDebounce) clearTimeout(seekDebounce);
    seekDebounce = setTimeout(fetchPreviewAt, 300);
  }

  function fetchPreviewAt() {
    if (!state.jobId) return;
    fetch("/api/preview_at", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: state.jobId, t: currentT() }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.error) { showError(data.error); return; }
        loadPreviewImage(data.preview_url);
      })
      .catch(function (e) { showError(String(e)); });
  }

  el.previewSeek.addEventListener("input", schedulePreview);

  document.querySelectorAll("[data-seek]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      if (!state.jobId) return;
      var next = clamp(currentT() + Number(btn.getAttribute("data-seek")), 0, Number(el.previewSeek.max));
      el.previewSeek.value = Math.round(next * 10) / 10;
      schedulePreview();
    });
  });

  function roundedCropBox() {
    return {
      x: Math.round(state.cropBox.x),
      y: Math.round(state.cropBox.y),
      w: Math.round(state.cropBox.w),
      h: Math.round(state.cropBox.h),
    };
  }

  // ---------------- 自動 / 手動 模式切換 ----------------

  function setMode(mode) {
    state.mode = mode;
    var manual = mode === "manual";
    el.modeAuto.classList.toggle("active", !manual);
    el.modeManual.classList.toggle("active", manual);
    el.hintAuto.classList.toggle("hidden", manual);
    el.hintManual.classList.toggle("hidden", !manual);
    el.manualPanel.classList.toggle("hidden", !manual);
    el.autoPanel.classList.toggle("hidden", manual);
  }

  el.modeAuto.addEventListener("click", function () { setMode("auto"); });
  el.modeManual.addEventListener("click", function () { setMode("manual"); });

  // ---------------- 手動擷取 ----------------

  el.captureBtn.addEventListener("click", function () {
    clearError();
    if (!state.jobId || !state.cropBox) { showError("請先上傳影片並確認框選區域"); return; }
    el.captureBtn.disabled = true;
    el.captureBtn.textContent = "擷取中...";

    fetch("/api/capture", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: state.jobId, t: currentT(), crop_box: roundedCropBox() }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.error) { showError(data.error); return; }
        (data.items || []).forEach(function (it, k) {
          // 同一個時間點切出多行時，用很小的偏移維持由上而下的順序
          state.captures.push({ t: it.t + k * 0.001, filename: it.filename, url: it.url, kind: it.kind });
        });
        state.captures.sort(function (a, b) { return a.t - b.t; });
        el.stepReview.classList.remove("hidden");
        el.stepParams.classList.remove("hidden");
        el.reviewSummary.textContent = "目前共 " + state.captures.length + " 項 (已加入手動擷取的畫面)。";
        renderGallery();
        scheduleLayoutPreview();
      })
      .catch(function (e) { showError(String(e)); })
      .then(function () {
        el.captureBtn.disabled = false;
        el.captureBtn.textContent = "📸 擷取當前畫面";
      });
  });

  function renderGallery() {
    el.gallery.innerHTML = "";
    if (!state.captures.length) {
      var empty = document.createElement("div");
      empty.className = "empty-note";
      empty.textContent = "目前沒有任何譜面。";
      el.gallery.appendChild(empty);
      return;
    }

    state.captures.forEach(function (item, idx) {
      var div = document.createElement("div");
      var kind = item.kind || "line";
      div.className = "gallery-item kind-" + kind;

      var img = document.createElement("img");
      img.src = item.url;
      img.alt = "第 " + (idx + 1) + " 張";
      img.loading = "lazy";
      img.addEventListener("click", function () {
        el.modalImg.src = item.url;
        el.imageModal.classList.remove("hidden");
      });

      var meta = document.createElement("div");
      meta.className = "meta";
      var info = document.createElement("div");
      info.className = "info";
      var label = kind === "intro" ? "📋 開頭資訊頁（歌名／和弦表／調弦）" : kind === "page" ? "整頁截圖" : "第 " + lineNo(idx) + " 行";
      info.textContent = label + "（影片 " + formatTime(item.t) + "）";

      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "remove-btn";
      btn.textContent = "移除";
      btn.addEventListener("click", function () {
        state.captures.splice(idx, 1);
        el.reviewSummary.textContent = "目前共 " + state.captures.length + " 項。";
        renderGallery();
        scheduleLayoutPreview();
      });

      meta.appendChild(info);
      meta.appendChild(btn);
      div.appendChild(img);
      div.appendChild(meta);
      el.gallery.appendChild(div);
    });
  }

  function lineNo(idx) {
    var n = 0;
    for (var i = 0; i <= idx; i++) if ((state.captures[i].kind || "line") === "line") n++;
    return n;
  }

  function formatTime(t) {
    var m = Math.floor(t / 60), sec = Math.floor(t % 60);
    return m + ":" + (sec < 10 ? "0" : "") + sec;
  }

  function closeModal() { el.imageModal.classList.add("hidden"); }
  el.imageModalClose.addEventListener("click", closeModal);
  el.imageModal.addEventListener("click", function (e) {
    if (e.target === el.imageModal) closeModal();
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") closeModal();
  });

  // ---------------- Params ----------------


  var ENHANCE_HINTS = {
    none: "第一張譜面的原始截圖 (不做畫質處理)：",
    smart: "第一張譜面套用「智慧畫質增強」後的樣子：",
    bw: "第一張譜面套用「黑白清晰化」後的樣子：",
  };
  var ENHANCE_PREVIEW_LABEL = "👀 預覽畫質處理效果";

  el.enhancePreviewBtn.addEventListener("click", function () {
    clearError();
    if (!state.jobId) { showError("請先上傳影片"); return; }
    if (!state.captures.length) { showError("預覽區沒有譜面可以示範"); return; }
    var mode = el.enhanceInput.value;
    el.enhancePreviewBtn.disabled = true;
    el.enhancePreviewBtn.textContent = "產生中...";
    fetch("/api/preview_enhance", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: state.jobId, filename: state.captures[0].filename, enhance: mode }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.error) { showError(data.error); return; }
        el.enhancePreviewHint.textContent = ENHANCE_HINTS[mode];
        el.enhancePreviewImg.src = data.preview_url;
        el.enhancePreviewWrap.classList.remove("hidden");
      })
      .catch(function (e) { showError(String(e)); })
      .then(function () {
        el.enhancePreviewBtn.disabled = false;
        el.enhancePreviewBtn.textContent = ENHANCE_PREVIEW_LABEL;
      });
  });

  // 換了處理方式，舊的預覽就不準了，先收起來
  el.enhanceInput.addEventListener("change", function () {
    el.enhancePreviewWrap.classList.add("hidden");
  });

  el.startBtn.addEventListener("click", function () {
    clearError();
    if (!state.jobId) { showError("請先上傳影片"); return; }
    buildManual();
  });

  // ---------------- 排版預覽 ----------------
  // 依目前的紙張大小 / 每頁段數 / 畫質處理，把預覽區的譜面排成一頁頁縮圖

  var layoutDebounce = null;
  var layoutSeq = 0;
  var LAYOUT_BTN_LABEL = "📐 預覽排版";

  function markLayoutStale() {
    if (el.layoutPages.children.length) el.layoutPages.classList.add("stale");
  }

  function scheduleLayoutPreview() {
    markLayoutStale();
    if (layoutDebounce) clearTimeout(layoutDebounce);
    layoutDebounce = setTimeout(previewLayout, 400);
  }

  function previewLayout() {
    clearError();
    if (!state.jobId || !state.captures.length) {
      el.layoutPages.innerHTML = "";
      el.layoutInfo.textContent = "預覽區沒有譜面可以排版。";
      return;
    }
    var seq = ++layoutSeq;
    el.layoutPreviewBtn.disabled = true;
    el.layoutPreviewBtn.textContent = "排版中...";
    el.layoutInfo.textContent = el.enhanceInput.value === "none" ? "排版中..." : "排版中 (含畫質處理，需要多幾秒)...";
    markLayoutStale();

    fetch("/api/preview_layout", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        job_id: state.jobId,
        captures: state.captures.map(function (c) { return { t: c.t, filename: c.filename, kind: c.kind || "line" }; }),
        title: el.titleInput.value || "吉他六線譜",
        subtitle: el.subtitleInput.value || "",
        page_size: el.pageSizeInput.value,
        scale: Number(el.scaleInput.value),
        enhance: el.enhanceInput.value,
      }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (seq !== layoutSeq) return; // 已經有更新的預覽請求，舊的結果丟掉
        if (data.error) { showError(data.error); el.layoutInfo.textContent = ""; return; }
        renderLayout(data);
      })
      .catch(function (e) { showError(String(e)); })
      .then(function () {
        if (seq !== layoutSeq) return;
        el.layoutPreviewBtn.disabled = false;
        el.layoutPreviewBtn.textContent = LAYOUT_BTN_LABEL;
      });
  }

  function renderLayout(data) {
    el.layoutPages.innerHTML = "";
    el.layoutPages.classList.remove("stale");
    var nLines = state.captures.filter(function (c) { return (c.kind || "line") === "line"; }).length;
    var nOther = state.captures.length - nLines;
    var pct = Math.round(Number(el.scaleInput.value) * 100);
    el.layoutInfo.textContent = "共 " + nLines + " 行譜" + (nOther ? "＋" + nOther + " 張資訊頁/整頁截圖" : "") +
      "，譜的大小 " + pct + "%，" + el.pageSizeInput.value + " 紙張共 " + data.page_count +
      " 頁 (平均每頁約 " + (nLines / Math.max(1, data.page_count)).toFixed(1) + " 行)。點縮圖可放大檢查字的大小。";
    data.pages.forEach(function (url, i) {
      var div = document.createElement("div");
      div.className = "layout-page";
      var img = document.createElement("img");
      img.src = url;
      img.alt = "第 " + (i + 1) + " 頁";
      img.loading = "lazy";
      img.addEventListener("click", function () {
        el.modalImg.src = url;
        el.imageModal.classList.remove("hidden");
      });
      div.appendChild(img);
      div.appendChild(document.createTextNode("第 " + (i + 1) + " 頁"));
      el.layoutPages.appendChild(div);
    });
  }

  el.layoutPreviewBtn.addEventListener("click", previewLayout);
  el.scaleInput.addEventListener("change", scheduleLayoutPreview);
  el.pageSizeInput.addEventListener("change", scheduleLayoutPreview);
  el.enhanceInput.addEventListener("change", markLayoutStale);

  function buildManual() {
    if (!state.captures.length) { showError("預覽區沒有任何譜面，無法產生 PDF"); return; }
    el.startBtn.disabled = true;
    el.stepResult.classList.add("hidden");
    el.stepProgressTitle.textContent = "產生樂譜 PDF 中...";
    el.progressBar.style.width = "50%";
    el.progressMsg.textContent = "共 " + state.captures.length + " 張片段";
    el.stepProgress.classList.remove("hidden");

    fetch("/api/build_manual", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        job_id: state.jobId,
        captures: state.captures.map(function (c) { return { t: c.t, filename: c.filename, kind: c.kind || "line" }; }),
        title: el.titleInput.value || "吉他六線譜",
        subtitle: el.subtitleInput.value || "",
        page_size: el.pageSizeInput.value,
        enhance: el.enhanceInput.value,
        scale: Number(el.scaleInput.value),
      }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        el.stepProgress.classList.add("hidden");
        if (data.error) { showError(data.error); return; }
        showResult("共 " + data.kept_count + " 張譜面，已依影片時間順序拼成 PDF。", data.pdf_url);
        el.stepResult.scrollIntoView({ behavior: "smooth", block: "start" });
      })
      .catch(function (e) {
        el.stepProgress.classList.add("hidden");
        showError(String(e));
      })
      .then(function () { el.startBtn.disabled = false; });
  }

  function showResult(summary, pdfUrl) {
    el.stepResult.classList.remove("hidden");
    el.resultSummary.textContent = summary;
    // 加上時間戳，避免重新產生後瀏覽器拿到快取的舊 PDF
    el.downloadBtn.href = pdfUrl + "?v=" + Date.now();
  }

  el.restartBtn.addEventListener("click", function () {
    location.reload();
  });
})();
