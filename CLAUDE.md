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

## 發佈流程（改了 `app/` 之後）—— 使用者要求的固定規則，每次都要做

**只要修改了 `app/` 裡的任何程式，就一定要同時更新下載包，不能只改程式。**
做不到時（例如沒有權限、使用者還不想發佈），要在回覆裡明確提醒使用者「下載包還沒更新」。

1. 執行 `python3 build_packages.py`（Windows 用 `python build_packages.py`）：
   自動重新打包 `site/downloads/guitar_tab_extractor_win.zip`、`guitar_tab_extractor_mac.zip`
   （Windows 版拿掉 `start_mac.command`，Mac 版拿掉 `start_windows.bat`，保留 Mac 啟動檔的執行權限）
2. 用 GitHub Desktop Commit、Push（程式和兩個 zip 一起）。Summary 欄位一定要填，否則 Commit 按鈕按不下去
3. Render 會自動重新部署，下載頁按鈕直接下載 `site/downloads/` 裡的檔案
4. 推送後要到 GitHub 核對檔案內容（例如 md5）跟本機一致，才算完成

（2026-10-25 起不再用 GitHub Release 放安裝包：GitHub Desktop 管不到 Release，使用者操作太麻煩。
舊的 Release v1.0 已沒有連結指向它。）

## 工作環境注意事項

- 這個 repo 在兩台電腦上輪流維護：Mac（放在外接硬碟 `/Volumes/1T  01/`，路徑中有兩個空格，指令中要加引號），以及學校網域 163.20.0.X 的 Windows
- 使用者平常用 GitHub Desktop 做 Commit／Push。開始改之前先 pull，改完要 push
- **在 Windows 上開 session 時**：先跑 `git fetch` 檢查吉他、鋼琴兩個 repo 是否落後 GitHub，落後就提醒使用者在 GitHub Desktop 按 Fetch origin → Pull 再開始（使用者要求的提醒）
- Chat／Cowork 時期的工作也是在這個資料夾裡做的

## 重要決策

- 吉他和鋼琴兩款工具的下載統一放在吉他站（本 repo `site/`）的合併下載頁，**不另外為鋼琴版開 Render 站**
- 2026-10-04：整合 will095614/sheetthief（別人從鋼琴版改的版本）的功能，但保留原本的自動擷取：
  - 上傳時擋掉非影片檔
  - 「自動擷取／手動擷取」兩種模式讓使用者選（手動＝自己拖時間軸一張張擷取）
  - 畫質處理三選一：不處理／智慧畫質增強（sheetthief 的對比拉伸＋銳化）／黑白清晰化（原本的）
  - 吉他、鋼琴兩個 repo 都要做同樣的修改

- 2026-10-25：自動擷取改成「上傳後自動掃描 → 預覽、移除抓錯的 → 選輸出方式再產生 PDF」
  - 後端：`pipeline.extract_sections()` 只抓譜不出 PDF；`/api/auto_extract` 把結果存成擷取片段，PDF 一律走 `/api/build_manual`
  - 使用者要求以自動擷取為主（手動沒效率），但**手動擷取功能要保留**（放在預覽區下方的「調整／手動補抓」）
  - 換頁判斷改用「墨跡差異比例」，可處理整個畫面都是譜的影片（Guitar Pro 播放畫面）
  - `start_mac.command` 固定用 Chrome 開啟
  - 擷取後再「切行」：一行 = 和弦圖＋五線譜＋TAB（`pipeline.split_pages_into_lines`），預覽與排版都以「行」為單位，緊密排滿 A4，可調縮放比例
  - 去重複用時間軸邏輯：只刪相鄰截圖之間的重疊行；隔一段時間再出現一樣的行 = 又演奏了一遍，**保留**（使用者明確要求）
  - 播放提示（黃色提示框、藍色游標）要清掉；固定位置的浮水印也去掉
  - 影片開頭的歌名／和弦表／調弦設定頁要抓出來，標成「開頭資訊頁」讓使用者決定留或刪
  - 兩種版面都要支援：「整頁都是譜」(行與行之間的空白下刀，沿用原本做法) 與「譜卡在畫面下方」(每畫面一行，上下邊界跟著內容走，含歌詞)
  - 譜卡範圍 = 畫面中最長的一段連續淺色列；譜線偵測要排除並排和弦圖的格子線、影片中的吉他弦
  - 相鄰截圖重疊的判斷：以譜線對齊比對 + 捲動位移一致才算重疊；位移對不上 = 重複演奏，保留

- 2026-10-25（晚）：鋼琴譜併入本工具
  - 網頁上傳前讓使用者選「吉他譜／鋼琴譜」（比自動判斷穩定，吉他規則不受影響），`score_type` 從上傳一路傳到切行
  - 鋼琴：框選範圍用譜表位置找（不會框到虛擬鍵盤）；一行 = 高音＋低音譜表；換頁比對時拿掉譜線只比音符
  - 鋼琴譜**不附影片字幕**（使用者決定）
  - **不做**「去除/繞過蓋在譜上的作者販售提示、從其他時間點拼回被遮住的音符」：那是譜作者保護付費作品的方式。鋼琴模式也不去除固定位置的文字
  - 網頁加警語：建議只留作自己練習用，不要再轉傳，也不要販售；需要購買請與原曲譜製作者聯絡
  - 舊的鋼琴版專案（juj-piano-tab-extractor-tool）使用者決定不再維護

## 目前進度與待辦

- 合併下載頁已放上吉他＋鋼琴四個安裝包（指向 Release `v1.0`）
- 鋼琴版本機有 1 個 commit（README 兩台電腦流程）還沒 push 到 GitHub（2026-10-04 確認）
- 整合 sheetthief 功能後，要重新打包四個 zip 並更新 Release，否則下載到的還是舊版
