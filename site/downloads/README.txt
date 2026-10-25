這個資料夾放的是下載頁 (site/index.html) 兩個下載按鈕提供的安裝包：

  guitar_tab_extractor_win.zip   Windows 版（吉他譜／鋼琴譜都適用）
  guitar_tab_extractor_mac.zip   Mac 版（吉他譜／鋼琴譜都適用）

更新安裝包的方法（2026-10-25 起改用這個方式，不再用 GitHub Release）：
  1. 改完 app/ 的程式後，重新打包這兩個 zip，覆蓋這個資料夾裡的同名檔案
     （Windows 版拿掉 start_mac.command，Mac 版拿掉 start_windows.bat）
  2. 用 GitHub Desktop 按 Commit、Push
  3. Render 會自動重新部署，下載頁的按鈕就會下載到新版

每次更新會讓 repo 多約 30MB，在 GitHub 的限制內。
