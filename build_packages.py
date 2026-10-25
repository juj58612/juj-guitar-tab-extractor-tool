# -*- coding: utf-8 -*-
"""
一鍵重新打包下載頁的兩個安裝包 (Windows / Mac)。

只要改了 app/ 裡的程式，就要執行這個檔案，再用 GitHub Desktop Commit、Push，
下載頁 (Render) 提供的安裝包才會跟著更新：

    python3 build_packages.py        (Mac)
    python  build_packages.py        (Windows)

產生的檔案：
    site/downloads/guitar_tab_extractor_win.zip   ← 下載頁的 Windows 按鈕
    site/downloads/guitar_tab_extractor_mac.zip   ← 下載頁的 Mac 按鈕
(同時複製一份到 dist/，方便自己保存；dist/ 不會上傳到 GitHub 也沒關係)

只用 Python 內建模組，不用另外安裝任何東西。
"""
import os
import shutil
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, "app")
ROOT_IN_ZIP = "guitar_tab_extractor"

FILES = [
    "start_mac.command", "start_windows.bat", "requirements.txt", "downloader.py",
    "README.md", "pipeline.py", "app.py",
    "static/style.css", "static/app.js",
    "templates/index.html", "templates/login.html",
    "assets/fonts/NotoSansCJK-Regular.ttc",
]
SKIP = {"win": "start_mac.command", "mac": "start_windows.bat"}


def build(platform, out_path):
    stamp = time.localtime()[:6]
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        dirs = set()
        for rel in FILES:
            if rel == SKIP[platform]:
                continue
            src = os.path.join(APP, *rel.split("/"))
            if not os.path.exists(src):
                raise SystemExit("找不到檔案：app/" + rel)
            parts = rel.split("/")
            for i in range(1, len(parts)):
                d = ROOT_IN_ZIP + "/" + "/".join(parts[:i]) + "/"
                if d not in dirs:
                    zi = zipfile.ZipInfo(d, stamp)
                    zi.create_system = 3
                    zi.external_attr = (0o40755 << 16) | 0x10
                    z.writestr(zi, b"")
                    dirs.add(d)
            zi = zipfile.ZipInfo(ROOT_IN_ZIP + "/" + rel, stamp)
            zi.create_system = 3
            # Mac 的啟動檔要保留「可執行」權限，使用者解壓縮後才能直接雙擊
            zi.external_attr = ((0o100755 if rel.endswith(".command") else 0o100644) << 16)
            zi.compress_type = zipfile.ZIP_DEFLATED
            with open(src, "rb") as f:
                data = f.read()
            if rel.endswith(".bat"):
                # Windows 啟動檔一律用 CRLF 換行 (cmd.exe 讀 LF 換行有時會解析錯誤)
                data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
            z.writestr(zi, data)


def main():
    out_dir = os.path.join(HERE, "site", "downloads")
    dist_dir = os.path.join(HERE, "dist")
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(dist_dir, exist_ok=True)
    for platform in ("win", "mac"):
        name = "guitar_tab_extractor_{}.zip".format(platform)
        out = os.path.join(out_dir, name)
        build(platform, out)
        shutil.copyfile(out, os.path.join(dist_dir, name))
        print("已產生 site/downloads/{}  ({:.1f} MB)".format(name, os.path.getsize(out) / 1e6))
    print("完成！接著用 GitHub Desktop 按 Commit、Push，下載頁就會提供新版安裝包。")


if __name__ == "__main__":
    main()
