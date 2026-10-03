# CLAUDE.md

這份檔案是給 Claude Code 讀的專案說明。使用者說繁體中文，回覆、註解、commit 訊息都用繁體中文。

## 專案是什麼

「從影片中抓琴譜」：上傳吉他教學影片（或貼網址），自動擷取畫面中的六線譜、去除重複畫面，依時間順序拼成 PDF。
工具在使用者**自己的電腦本機**執行（Flask 網頁 app，`127.0.0.1:5001`），影片不會離開使用者的電腦。

另有一個姊妹專案 `juj58612/juj-piano-tab-extractor-tool`（鋼琴版），兩款工具的下載都統一放在本 repo 的 landing page。

## 結構

- `app/`：工具本體
  - `app.py`：Flask 後端，背景 thread 跑 job，狀態存在記憶體 `JOBS`；可用 `ACCESS_CODE` 環境變數加存取碼
  - `pipeline.py`：核心影像處理（OpenCV）：建議框選區域 `suggest_crop_box`、內容簽章去重複、`enhance_for_print` 黑白清晰化、`build_pdf`
  - `downloader.py`：「貼上網址」模式（yt-dlp），`assert_public_http_url` 擋內網位址（SSRF 防護，不要拿掉）
  - `templates/`、`static/`：前端介面
  - `assets/fonts/NotoSansCJK-Regular.ttc`：PDF 標題用的中文字型
  - `start_mac.command` / `start_windows.bat`：一般使用者的一鍵啟動
  - `_work/`：執行時的暫存資料夾（已 gitignore）
- `site/`：Render 上的靜態下載頁（`index.html`），只介紹工具和提供下載，**不做線上影片處理**
- `render.yaml`：Render Static Site 設定，publish `./site`

## 開發與執行

```bash
cd app
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python app.py   # http://127.0.0.1:5001
```

需要 Python 3.9 以上。目前沒有自動化測試。

## 發佈流程（改了 `app/` 之後）

1. 重新打包兩個 zip：Windows 版拿掉 `start_mac.command`，Mac 版拿掉 `start_windows.bat`，其餘相同
2. 把 zip 更新到 GitHub Release `v1.0`（吉他＋鋼琴四個安裝包都掛在這裡）。下載頁連結指向 Release，不是 `site/downloads/`
3. push 到 GitHub `main`，Render 會自動重新部署下載頁

## 工作環境注意事項

- 這個 repo 在兩台電腦上輪流維護：Mac（放在外接硬碟 `/Volumes/1T  01/`，路徑中有兩個空格，指令中要加引號），以及學校網域 163.20.0.X 的 Windows
- 使用者平常用 GitHub Desktop 做 Commit／Push。開始改之前先 pull，改完要 push
- Chat／Cowork 時期的工作也是在這個資料夾裡做的

## 重要決策

- 吉他和鋼琴兩款工具的下載統一放在吉他站（本 repo `site/`）的合併下載頁，**不另外為鋼琴版開 Render 站**
- 2026-10-04：整合 will095614/sheetthief（別人從鋼琴版改的版本）的功能，但保留原本的自動擷取：
  - 上傳時擋掉非影片檔
  - 「自動擷取／手動擷取」兩種模式讓使用者選（手動＝自己拖時間軸一張張擷取）
  - 畫質處理三選一：不處理／智慧畫質增強（sheetthief 的對比拉伸＋銳化）／黑白清晰化（原本的）
  - 吉他、鋼琴兩個 repo 都要做同樣的修改

## 目前進度與待辦

- 合併下載頁已放上吉他＋鋼琴四個安裝包（指向 Release `v1.0`）
- 鋼琴版本機有 1 個 commit（README 兩台電腦流程）還沒 push 到 GitHub（2026-10-04 確認）
- 整合 sheetthief 功能後，要重新打包四個 zip 並更新 Release，否則下載到的還是舊版
