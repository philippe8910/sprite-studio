#!/bin/bash
# Sprite Studio - macOS launcher
# 第一次執行會自動建立 .venv 並安裝 flask / numpy / opencv，之後啟動就很快。

cd "$(dirname "$0")" || exit 1
PORT=8765
URL="http://127.0.0.1:$PORT"

bye() {
  echo ""
  echo "$1"
  echo ""
  read -n 1 -s -r -p "按任意鍵關閉這個視窗..."
  echo ""
  exit 1
}

echo "=================================="
echo "  Sprite Studio"
echo "=================================="
echo ""

# --- 1. 找一個可用的 python3 (需要 3.9 以上) -------------------------------
PY=""
for c in /opt/homebrew/bin/python3 /usr/local/bin/python3 "$(command -v python3 2>/dev/null)" /usr/bin/python3; do
  [ -n "$c" ] || continue
  [ -x "$c" ] || continue
  if "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
    PY="$c"
    break
  fi
done

if [ -z "$PY" ]; then
  echo "找不到 Python 3.9 以上。"
  echo ""
  echo "兩種裝法擇一："
  echo "  1) 終端機執行  xcode-select --install   (系統內建, 免下載安裝檔)"
  echo "  2) 到 https://www.python.org/downloads/macos/ 下載安裝"
  echo ""
  bye "裝好之後再雙擊一次這個檔案。"
fi
echo "Python: $PY  ($("$PY" -c 'import sys;print(sys.version.split()[0])'))"

# --- 2. 建立 / 重用虛擬環境 ------------------------------------------------
VENV=".venv"
VPY="$VENV/bin/python"
if [ ! -x "$VPY" ]; then
  echo ""
  echo "第一次啟動：建立虛擬環境 (.venv)..."
  rm -rf "$VENV"
  "$PY" -m venv "$VENV" || bye "建立虛擬環境失敗。"
fi

STAMP="$VENV/.deps_ok"
if [ ! -f "$STAMP" ] || [ requirements.txt -nt "$STAMP" ]; then
  echo ""
  echo "安裝套件 (flask / numpy / opencv)，第一次約 1-3 分鐘，請稍候..."
  echo ""
  "$VPY" -m pip install --upgrade pip >/dev/null 2>&1
  "$VPY" -m pip install -r requirements.txt || bye "套件安裝失敗 (通常是沒網路或公司網路擋 pip)。"
  date > "$STAMP"
  echo ""
  echo "套件安裝完成。"
fi

# --- 3. 已經在跑就直接開瀏覽器（不要再起一個去撞埠） -----------------------
if "$VPY" -c "import socket,sys; sys.exit(0 if socket.socket().connect_ex(('127.0.0.1', $PORT))==0 else 1)"; then
  echo ""
  echo "$PORT 已經有 Sprite Studio 在跑了，直接開瀏覽器。"
  open "$URL"
  exit 0
fi

# --- 4. 起伺服器 + 等它活過來再開瀏覽器 ------------------------------------
(
  for _ in $(seq 1 60); do
    if curl -s -o /dev/null "$URL" 2>/dev/null; then
      open "$URL"
      exit 0
    fi
    sleep 0.5
  done
) &

echo ""
echo "啟動中... 瀏覽器會自動打開 $URL"
echo "要關掉 Sprite Studio：在這個視窗按 Control + C，或直接關掉視窗。"
echo ""
exec "$VPY" server.py
