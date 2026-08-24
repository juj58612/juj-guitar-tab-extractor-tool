#!/bin/bash
# 吉他六線譜自動擷取工具 - Mac 一鍵啟動
# 第一次執行需要幾分鐘安裝套件；之後每次啟動都很快。

cd "$(dirname "$0")"

if ! command -v python3 &> /dev/null; then
  echo "找不到 Python3。"
  echo "請先到 https://www.python.org/downloads/ 安裝 Python 3.9 以上版本，再重新雙擊本檔案。"
  read -p "按 Enter 鍵結束..."
  exit 1
fi

if [ ! -d "venv" ]; then
  echo "首次執行，正在建立虛擬環境..."
  python3 -m venv venv
fi

source venv/bin/activate

if [ ! -f "venv/.deps_installed" ]; then
  echo "正在安裝所需套件 (第一次執行需要幾分鐘)..."
  pip install --upgrade pip --quiet --no-cache-dir
  pip install -r requirements.txt --quiet --no-cache-dir
  touch venv/.deps_installed
fi

echo ""
echo "正在啟動伺服器..."
( sleep 2 && open "http://127.0.0.1:5001" ) &

python app.py

read -p "伺服器已關閉，按 Enter 鍵結束..."
