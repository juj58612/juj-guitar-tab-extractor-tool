這個資料夾故意不把 zip 檔案放進 git repo (兩個安裝包各約 15MB，會讓 repo 越滾越大)。

實際做法：把 guitar_tab_extractor_win.zip / guitar_tab_extractor_mac.zip
上傳成這個 GitHub repo 的一個 Release 附件，然後把 site/index.html 裡兩個下載按鈕的
href 換成該 Release 附件的直接下載網址，格式是：

  https://github.com/<你的帳號>/<repo名稱>/releases/download/<tag>/guitar_tab_extractor_win.zip
  https://github.com/<你的帳號>/<repo名稱>/releases/download/<tag>/guitar_tab_extractor_mac.zip

這兩個 zip 檔案 Claude 已經另外幫你打包好、透過對話直接傳給你了，不在這個 repo 壓縮檔裡。
