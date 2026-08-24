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
