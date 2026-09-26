# juj 吉他影片六線譜自動擷取工具

上傳吉他教學影片，自動去除重複畫面、擷取六線譜片段，依時間順序拼成完整樂譜 PDF。

## 這個 repo 的結構

```
juj-guitar-tab-extractor/
├── site/                          landing page (部署到 Render 的靜態網站)
│   ├── index.html                 下載頁面，兩個按鈕分別下載 Win / Mac 版
│   └── downloads/
│       ├── guitar_tab_extractor_win.zip
│       └── guitar_tab_extractor_mac.zip
├── app/                           工具本體原始碼 (Flask 網頁 app)
│   ├── app.py                     後端伺服器
│   ├── pipeline.py                核心影像處理邏輯
│   ├── downloader.py              「貼上網址」模式用的下載邏輯 (yt-dlp)
│   ├── requirements.txt
│   ├── templates/ static/         網頁介面
│   ├── assets/fonts/              PDF 標題用的中文字型 (內附)
│   ├── start_windows.bat          Windows 一鍵啟動
│   ├── start_mac.command          Mac 一鍵啟動
│   └── README.md                  工具本身的完整使用/部署說明
└── render.yaml                    Render 靜態網站部署設定
```

## 為什麼 landing page 是純靜態頁面，不能線上處理影片？

影片分析(讀取每一幀畫面、跑 OpenCV 運算、組 PDF)需要實際的運算資源與較長的執行時間，
不適合放在沒有後端運算權限的靜態網站服務(例如免費方案的 Render Static Site)上執行。
所以這個 landing page 只負責「介紹工具 + 提供下載」，實際的影片處理都在使用者自己下載後、
於**自己的電腦本機**執行——影片內容完全不會離開使用者的裝置。

如果之後想要一個「大家都能連上、由伺服器統一處理」的線上版本，需要換成有實際運算資源
(至少要能跑 Python + OpenCV) 的方案，例如 Render 的 Web Service(付費方案)、或其他有
持續運算資源的主機，而不是純靜態網站服務。`app/` 資料夾本身就是一個完整可以這樣部署的
Flask app，`app/README.md` 裡有「部署到伺服器」的完整說明(含環境變數、安全性注意事項)。

## 部署 landing page 到 Render

1. 把這個 repo 推上 GitHub。
2. 到 [Render](https://render.com) → New → Static Site，選擇這個 GitHub repo。
3. Render 應該會自動讀到 `render.yaml`：Publish directory 設為 `site`，Build command 留空即可。
4. 部署完成後，Render 會給一個 `https://xxx.onrender.com` 網址，兩個下載按鈕會直接從
   `site/downloads/` 提供 zip 檔下載。

## 在本機直接執行(開發者)

```bash
cd app
python3 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

然後開瀏覽器到 `http://127.0.0.1:5001`。一般使用者不需要做這些，直接用
`start_windows.bat` / `start_mac.command` 雙擊啟動即可。

## 更新下載用的 zip

`site/downloads/` 裡的兩個 zip 是從 `app/` 打包出來的(Windows 版拿掉 `start_mac.command`，
Mac 版拿掉 `start_windows.bat`，其餘完全相同)。之後如果修改了 `app/` 裡的原始碼，記得
重新打包這兩個 zip，不然 landing page 上下載到的版本會跟 repo 裡的原始碼不一致。

## 兩台電腦（Mac＋Windows）共同維護與部署流程

這個 repo 會在兩台電腦上輪流修改：一台是 Mac，另一台是在 163.20.0.X 網域裡的 Windows。
**Render 不用在每台電腦上分別設定**。Render 會直接從 GitHub 抓程式碼，所以只要把修改 push
到 GitHub，Render 就會自動重新部署。

```
Mac ─┐
     ├─ push → GitHub → Render 自動部署
Win ─┘
```

### 一、Render 設定（只要做一次，哪台電腦都可以）

1. 用瀏覽器登入 <https://dashboard.render.com>，打開這個專案的服務，到 **Settings**。
2. 確認 **Auto-Deploy** 設成 **On Commit**。設好之後，每次 push 到 GitHub，Render 就會自動重新部署。
3. 如果哪一次沒有自動部署，就到該服務頁面按 **Manual Deploy → Deploy latest commit**。

> 目前吉他和鋼琴兩款工具的下載，都統一放在 `juj-guitar-tab-extractor-tool` 這個 Render 站的合併下載頁。
> 四個安裝包都掛在 guitar repo 的 GitHub Release `v1.0`。

### 二、Mac

- repo 放在外接硬碟 `1T 01` 裡。
- 用 GitHub Desktop 打開這個資料夾，照平常的方式 Commit → Push。

### 三、Windows（163.20.0.X 網域）

1. 安裝 GitHub Desktop（<https://desktop.github.com>），登入 `juj58612` 帳號。
2. 選 **File → Clone repository**，把兩個 repo 都 clone 到本機，例如 `D:\juj\`：
   - `juj58612/juj-guitar-tab-extractor-tool`
   - `juj58612/juj-piano-tab-extractor-tool`
3. 之後改程式都在這兩個 clone 下來的資料夾裡改。
4. 如果外接硬碟會接到這台 Windows 上用，就直接開硬碟裡的資料夾，**不要再 clone 一份**，免得兩份內容越來越不一樣。

### 四、兩台輪流用的規則（最重要）

| 時機 | 要做的事 |
|---|---|
| 開始改之前 | GitHub Desktop 按 **Fetch origin → Pull**，拿到另一台最新的版本 |
| 改完之後 | **Commit to main → Push origin**，不要留著沒推 |
| 平常 | 不要兩台同時改同一個檔案，不然會出現衝突（conflict） |
| 改了 `app/` 的程式 | 重新打包 Win／Mac 兩個 zip，更新到 GitHub Release，否則下載到的會是舊版 |

### 五、163.20.0.X 網域可能遇到的問題

這個網段是學校網路，連外可能被擋。第一次在 Windows 那台使用前，先測試兩件事：

- 瀏覽器能不能打開 `github.com` 和 `dashboard.render.com`。
- GitHub Desktop 能不能 **Fetch origin** 成功。

如果 GitHub 被擋，就只能在 Mac 那台 push，Windows 那台只當作「看網站、下載工具」用。
要解決的話，請學校資訊組開放 `github.com`、`*.githubusercontent.com` 和 `*.onrender.com`。
