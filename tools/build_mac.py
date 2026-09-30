# -*- coding: utf-8 -*-
"""把 Sprite Studio 打包成 macOS 的 .app，輸出 dist/SpriteStudio-mac-<日期>.zip。

在 Windows 上跑就好（純 Python 打包，不需要 Mac、不需要 PyInstaller）：

    python tools\\build_mac.py                  只帶工具本身（~2 MB）
    python tools\\build_mac.py --with-projects  連 data/projects 一起帶（~144 MB）

產出的 .app 是「啟動器 + 安裝器」：
  - 程式碼放在 bundle 內 Contents/Resources/payload
  - 第一次雙擊時同步到 ~/SpriteStudio（可寫），建 .venv、裝 flask/numpy/opencv，
    然後起 server 並開瀏覽器
  - 之後每次雙擊：比對 build id，程式碼有更新就同步（data/ 與 settings.json 不動）

為什麼不把資料放在 .app 裡：從網路下載的未簽名 app 會被 macOS 的 App Translocation
丟到唯讀的隨機路徑執行，寫在 bundle 內的東西會整包消失。所以資料一律在家目錄。
"""
import hashlib
import io
import os
import shutil
import struct
import sys
import zipfile
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "dist")
APP_NAME = "Sprite Studio.app"
EXEC_NAME = "SpriteStudio"

# 要打包進去的程式碼（相對於專案根目錄）。data/projects、experiments、tools 都不帶。
PAYLOAD_FILES = [
    "server.py", "pipeline.py", "presets.py", "falcatalog.py",
    "falclient.py", "gamespec.py", "localgen.py",
]
PAYLOAD_DIRS = ["static", "poselib", "workflows"]
SKIP_SUFFIX = (".pyc", ".pyo", ".log", ".err")
# 已經是壓縮格式的，zip 再壓一次只是白花 CPU（678 張 png 差很多）
STORE_SUFFIX = (".png", ".jpg", ".jpeg", ".mp4", ".webp", ".zip", ".gif")


# ------------------------------------------------------------------ 圖示

def _dechroma(im, tol=60):
    """去掉 poselib 立繪的綠幕背景（角落顏色當背景色），回傳去背後裁切好的 RGBA。"""
    im = im.convert("RGBA")
    px = im.load()
    bg = px[0, 0][:3]
    w, h = im.size
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            d = abs(r - bg[0]) + abs(g - bg[1]) + abs(b - bg[2])
            if d < tol:
                px[x, y] = (r, g, b, 0)
    return im.crop(im.getbbox() or (0, 0, w, h))


def make_icon():
    """用招牌立繪 + 深色圓角底做 AppIcon.icns；PIL 或素材缺一就回 None（跳過圖示）。"""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print("  [icon] 沒有 Pillow，跳過圖示（app 會用系統預設圖示）")
        return None
    src = os.path.join(ROOT, "poselib", "stand.png")
    if not os.path.exists(src):
        print("  [icon] 找不到 poselib/stand.png，跳過圖示")
        return None

    char = _dechroma(Image.open(src))
    S = 1024
    pad = int(S * 0.10)                     # macOS 圖示慣例：內容不要頂到邊
    inner = S - pad * 2

    base = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    plate = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(plate).rounded_rectangle(
        [pad // 2, pad // 2, S - pad // 2, S - pad // 2],
        radius=int(S * 0.22), fill=(26, 28, 36, 255))
    base.alpha_composite(plate)

    # 立繪等比縮到內框，貼底對齊（腳站在圓角底盤上比置中好看）
    cw, ch = char.size
    k = min(inner / cw, inner / ch) * 0.92
    char = char.resize((max(1, int(cw * k)), max(1, int(ch * k))), Image.NEAREST)
    base.alpha_composite(char, ((S - char.width) // 2, S - pad - int(pad * 0.35) - char.height))

    # 右下角三格底片：一眼看出是「做動畫幀」的工具
    d = ImageDraw.Draw(base)
    fw, fh = int(S * 0.085), int(S * 0.085)
    gap = int(fw * 0.32)
    x0 = S - pad - fw * 3 - gap * 2
    y0 = S - pad - int(fh * 1.25)
    for i, col in enumerate([(255, 140, 170, 255), (255, 190, 205, 255), (250, 235, 240, 255)]):
        x = x0 + i * (fw + gap)
        d.rounded_rectangle([x, y0, x + fw, y0 + fh], radius=int(fw * 0.22), fill=col)

    # icns：magic + 每個尺寸一塊 PNG
    sizes = [("icp4", 16), ("icp5", 32), ("ic07", 128),
             ("ic08", 256), ("ic09", 512), ("ic10", 1024)]
    chunks = b""
    for tag, px_size in sizes:
        buf = io.BytesIO()
        base.resize((px_size, px_size), Image.LANCZOS).save(buf, "PNG")
        data = buf.getvalue()
        chunks += tag.encode("ascii") + struct.pack(">I", 8 + len(data)) + data
    icns = b"icns" + struct.pack(">I", 8 + len(chunks)) + chunks
    print("  [icon] AppIcon.icns  %.0f KB" % (len(icns) / 1024))
    return icns


# ------------------------------------------------------------------ 內容

INFO_PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>Sprite Studio</string>
    <key>CFBundleDisplayName</key><string>Sprite Studio</string>
    <key>CFBundleIdentifier</key><string>studio.sprite.launcher</string>
    <key>CFBundleExecutable</key><string>__EXEC__</string>
    <key>CFBundleIconFile</key><string>AppIcon</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleSignature</key><string>????</string>
    <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
    <key>CFBundleShortVersionString</key><string>1.0</string>
    <key>CFBundleVersion</key><string>__BUILD__</string>
    <key>LSMinimumSystemVersion</key><string>10.13</string>
    <key>LSApplicationCategoryType</key><string>public.app-category.graphics-design</string>
    <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
"""

LAUNCHER = r'''#!/bin/bash
# Sprite Studio - macOS launcher (unsigned, pure python)
# 這支就是 .app 的執行檔。雙擊 app = 執行這支。
set -u

BUNDLE="$(cd "$(dirname "$0")/../.." >/dev/null 2>&1 && pwd)"
PAYLOAD="$BUNDLE/Contents/Resources/payload"
APPDIR="$HOME/SpriteStudio"
PORT=8765
URL="http://127.0.0.1:$PORT"

# 非阻塞小提示（幾秒後自己關掉），不用任何自動化權限
say() {
  ( osascript -e "display dialog \"$1\" with title \"Sprite Studio\" buttons {\"好\"} default button 1 giving up after ${2:-8}" >/dev/null 2>&1 ) &
}
die() {
  osascript -e "display dialog \"$1\" with title \"Sprite Studio\" buttons {\"好\"} default button 1 with icon stop" >/dev/null 2>&1
  exit 1
}

mkdir -p "$APPDIR" || die "無法建立資料夾 $APPDIR"
LOG="$APPDIR/launcher.log"
exec >>"$LOG" 2>&1
echo "=== $(date) 啟動 ==="

# --- 1. 把 bundle 內的程式碼同步到可寫的家目錄 ---------------------------
# 只覆蓋程式碼；data/ 與 settings.json 是使用者的東西，只在不存在時給一份。
SRC_ID="$PAYLOAD/.build_id"
DST_ID="$APPDIR/.build_id"
FIRST_RUN=0
[ -f "$DST_ID" ] || FIRST_RUN=1
if [ ! -f "$DST_ID" ] || ! cmp -s "$SRC_ID" "$DST_ID"; then
  echo "同步程式碼 -> $APPDIR"
  for f in "$PAYLOAD"/*.py "$PAYLOAD/requirements.txt" "$PAYLOAD/READ_ME_first.txt"; do
    [ -f "$f" ] && cp -f "$f" "$APPDIR/"
  done
  for d in static poselib workflows; do
    [ -d "$PAYLOAD/$d" ] || continue
    mkdir -p "$APPDIR/$d"
    cp -R "$PAYLOAD/$d/." "$APPDIR/$d/"
  done
  mkdir -p "$APPDIR/data/projects"
  [ -f "$APPDIR/settings.json" ]       || cp "$PAYLOAD/settings.json" "$APPDIR/" 2>/dev/null
  [ -f "$APPDIR/data/fal_cache.json" ] || cp "$PAYLOAD/fal_cache.json" "$APPDIR/data/" 2>/dev/null

  # 隨包附的專案：只補「這台還沒有的」，已經存在的一律不碰，
  # 免得在 Mac 上改過的東西被下一版 app 蓋掉。
  SEED="$PAYLOAD/data_seed/projects"
  if [ -d "$SEED" ]; then
    [ "$FIRST_RUN" = "1" ] && say "正在把隨包附的專案素材複製到 ~/SpriteStudio，約十幾秒，接著會安裝 Python 套件。" 8
    for p in "$SEED"/*/; do
      [ -d "$p" ] || continue
      name="$(basename "$p")"
      if [ ! -d "$APPDIR/data/projects/$name" ]; then
        echo "帶入專案 $name"
        cp -R "$p" "$APPDIR/data/projects/$name"
      else
        echo "略過 $name（這台已經有了）"
      fi
    done
  fi
  cp -f "$SRC_ID" "$DST_ID"
fi
cd "$APPDIR" || die "無法進入 $APPDIR"

# --- 2. 找 python3 (3.9 以上) --------------------------------------------
PY=""
for c in /opt/homebrew/bin/python3 /usr/local/bin/python3 "$(command -v python3 2>/dev/null)" /usr/bin/python3; do
  [ -n "$c" ] && [ -x "$c" ] || continue
  if "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  open "https://www.python.org/downloads/macos/" 2>/dev/null
  die "找不到 Python 3.9 以上。\n\n已幫你打開下載頁，裝好之後再點一次 Sprite Studio。\n（或在「終端機」執行 xcode-select --install 也可以）"
fi
echo "python: $PY ($("$PY" -c 'import sys;print(sys.version.split()[0])'))"

# --- 3. 虛擬環境 + 套件 ---------------------------------------------------
VPY="$APPDIR/.venv/bin/python"
if [ ! -x "$VPY" ]; then
  [ "$FIRST_RUN" = "1" ] && say "第一次啟動，要先下載套件 (flask / numpy / opencv)，大約 1-3 分鐘。\n\n裝好之後瀏覽器會自己打開，這段時間請保持網路連線。" 12
  rm -rf "$APPDIR/.venv"
  "$PY" -m venv "$APPDIR/.venv" || die "建立虛擬環境失敗。\n詳細訊息在 $LOG"
fi
STAMP="$APPDIR/.venv/.deps_ok"
if [ ! -f "$STAMP" ] || [ "$APPDIR/requirements.txt" -nt "$STAMP" ]; then
  say "正在安裝套件，約 1-3 分鐘，裝好會自動打開瀏覽器..." 10
  "$VPY" -m pip install --upgrade pip >/dev/null 2>&1
  "$VPY" -m pip install -r "$APPDIR/requirements.txt" || \
    die "套件安裝失敗（通常是沒網路，或公司網路擋 pip）。\n連上網後再點一次即可接續安裝。\n\n詳細訊息：$LOG"
  date > "$STAMP"
fi

# --- 3.5 SSL 憑證 ---------------------------------------------------------
# python.org 版的 macOS Python 不吃系統鑰匙圈，要另外跑 Install Certificates.command
# 才有 CA bundle；沒跑過的話 urllib 連 fal.run 會直接 CERTIFICATE_VERIFY_FAILED。
# 這裡把 SSL_CERT_FILE 指到 venv 裡的 certifi，不管使用者裝的是哪套 python 都能連。
CA="$("$VPY" -c 'import certifi;print(certifi.where())' 2>/dev/null)"
if [ -n "$CA" ] && [ -f "$CA" ]; then
  export SSL_CERT_FILE="$CA"
  export REQUESTS_CA_BUNDLE="$CA"
  echo "CA bundle: $CA"
else
  echo "警告：找不到 certifi，連線可能會出現憑證錯誤"
fi

# --- 4. 已經在跑就只開瀏覽器，不要再起一個去撞埠 --------------------------
if "$VPY" -c "import socket,sys; sys.exit(0 if socket.socket().connect_ex(('127.0.0.1', $PORT))==0 else 1)"; then
  echo "$PORT 已有服務，直接開瀏覽器"
  open "$URL"
  exit 0
fi

# --- 5. 起 server，活過來再開瀏覽器 ---------------------------------------
(
  for _ in $(seq 1 120); do
    if curl -s -o /dev/null "$URL" 2>/dev/null; then open "$URL"; exit 0; fi
    sleep 0.5
  done
  osascript -e 'display dialog "伺服器沒有在時間內起來，請看 ~/SpriteStudio/launcher.log" with title "Sprite Studio" buttons {"好"} default button 1 with icon stop' >/dev/null 2>&1
) &

echo "啟動 server..."
exec "$VPY" "$APPDIR/server.py"
'''

TERMINAL_FALLBACK = r'''#!/bin/bash
# 備援啟動方式：雙擊 .app 沒反應時用這個，終端機會顯示每一步在做什麼。
# 它跑的是 ~/SpriteStudio 底下的程式碼（.app 第一次啟動時會複製過去）。
cd "$HOME/SpriteStudio" 2>/dev/null || { echo "找不到 ~/SpriteStudio，請先點一次 Sprite Studio.app"; read -n 1 -s -r -p "按任意鍵關閉"; exit 1; }
PORT=8765
URL="http://127.0.0.1:$PORT"

PY=""
for c in /opt/homebrew/bin/python3 /usr/local/bin/python3 "$(command -v python3 2>/dev/null)" /usr/bin/python3; do
  [ -n "$c" ] && [ -x "$c" ] || continue
  if "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then PY="$c"; break; fi
done
[ -n "$PY" ] || { echo "找不到 Python 3.9 以上，請先安裝：https://www.python.org/downloads/macos/"; read -n 1 -s -r -p "按任意鍵關閉"; exit 1; }

VPY=".venv/bin/python"
if [ ! -x "$VPY" ]; then
  echo "建立虛擬環境..."
  "$PY" -m venv .venv || { echo "失敗"; read -n 1 -s -r -p "按任意鍵關閉"; exit 1; }
fi
echo "檢查套件..."
"$VPY" -m pip install -r requirements.txt || { echo "套件安裝失敗（多半是沒網路）"; read -n 1 -s -r -p "按任意鍵關閉"; exit 1; }

CA="$("$VPY" -c 'import certifi;print(certifi.where())' 2>/dev/null)"
if [ -n "$CA" ] && [ -f "$CA" ]; then
  export SSL_CERT_FILE="$CA"
  export REQUESTS_CA_BUNDLE="$CA"
  echo "CA bundle: $CA"
fi

( for _ in $(seq 1 120); do curl -s -o /dev/null "$URL" 2>/dev/null && { open "$URL"; exit 0; }; sleep 0.5; done ) &
echo ""
echo "啟動中，瀏覽器會自動打開 $URL"
echo "要關掉：在這個視窗按 Control + C"
echo ""
exec "$VPY" server.py
'''

README = """Sprite Studio for macOS
=======================

【怎麼開】

1. 把「Sprite Studio.app」拖到「應用程式」資料夾（或任何你喜歡的地方）。

2. 第一次打開：對 app 按【右鍵】->【打開】->再按一次【打開】。
   ★ 第一次一定要用右鍵，直接雙擊會被 macOS 擋下來（這個 app 沒有花錢做
     Apple 簽章，系統一律先當成「來路不明」）。之後就可以直接雙擊。

   如果是 macOS 15 (Sequoia) 以上，右鍵可能也不給過，改成：
     打開【系統設定】->【隱私權與安全性】-> 往下捲到「已阻擋 Sprite Studio」
     -> 按【仍要打開】。

   兩個都不行的話，開「終端機」貼這行（把 app 拖進視窗可以自動填路徑）：
     xattr -dr com.apple.quarantine "/Applications/Sprite Studio.app"

3. 第一次啟動會跳一個小視窗說「正在安裝套件」，那是在建立 Python 環境並下載
   flask / numpy / opencv，約 1~3 分鐘（要有網路）。裝好瀏覽器會自己打開。
   之後每次啟動只要幾秒。

4. 網址是 http://127.0.0.1:8765 ，只有這台電腦連得到。

【怎麼關】
   在 Dock 上對 Sprite Studio 的圖示按右鍵 ->「結束」。
   （如果結束不掉，開「活動監視器」找 Python 結束它，或終端機執行
     pkill -f "SpriteStudio/server.py"）

【檔案放哪】
   ~/SpriteStudio/          <- 開啟「家目錄」就看得到
       data/projects/       <- 每個角色專案的素材、影格、輸出
       settings.json        <- 你的設定與 API 金鑰
       .venv/               <- Python 環境（可以整個刪掉重建）
       launcher.log         <- 啟動過程的紀錄，出事看這個

   app 本身只是啟動器，把 app 刪掉不會動到 ~/SpriteStudio 的東西。

   這一包有把 Windows 上既有的專案一起帶過來，第一次啟動會自動複製進
   data/projects/（所以第一次會多花十幾秒）。之後換新版 app 時：
   這台已經有的專案一律不碰，只會補上還沒有的，不會覆蓋你在 Mac 上改的東西。
   要自己手動搬也可以，把 sprite_studio\\data\\projects\\ 底下的資料夾
   複製到 ~/SpriteStudio/data/projects/ 就好。

【API 金鑰】
   金鑰沒有打包進來。開起來後點網頁右上角「API 金鑰」填 fal.ai 的 key，
   會存在 ~/SpriteStudio/settings.json，只留在這台電腦上。

【出問題時】

. 雙擊 app 完全沒反應
    -> 打開「疑難排解」資料夾，雙擊「從終端機啟動.command」，
       終端機會把卡在哪一步印出來。
    -> 如果連 .command 也沒反應，終端機執行：
         chmod +x "/Applications/Sprite Studio.app/Contents/MacOS/SpriteStudio"

. 說找不到 Python
    -> 會自動幫你開下載頁，裝完再點一次 app 就好。
       或終端機執行 xcode-select --install（用系統內建那套）。

. 套件安裝失敗
    -> 多半是沒網路。連上網再點一次，會接續裝完。

. 想整個重來
    -> 刪掉 ~/SpriteStudio/.venv（Finder 按 Command+Shift+. 顯示隱藏檔），
       再點一次 app 就會重建。data/ 不會受影響。

. 埠被佔用
    -> 終端機執行 lsof -nP -iTCP:8765 -sTCP:LISTEN 看是誰在佔。

. 出現 CERTIFICATE_VERIFY_FAILED / unable to get local issuer certificate
    -> 從 python.org 裝的 Python 不會用系統鑰匙圈，要自己給 CA 憑證。
       這一版的啟動器已經會自動處理（把 SSL_CERT_FILE 指到 certifi）。
       如果還是遇到，終端機執行：
         ~/SpriteStudio/.venv/bin/python -m pip install -U certifi
       再雙擊一次 app。或是找到並執行一次
         /Applications/Python 3.x/Install Certificates.command
       （3.x 換成你裝的版本，雙擊即可）。

【技術細節】
   - 純 Python，沒有原生執行檔，Intel 與 Apple Silicon 同一包都能跑。
   - 相依：flask、numpy、opencv-python-headless。
   - 伺服器只綁 127.0.0.1，外面連不進來。
   - 程式碼放在 app 內部，第一次啟動同步到 ~/SpriteStudio；之後換新版 app
     只會覆蓋程式碼，data/ 與 settings.json 不會被動到。
"""


# ------------------------------------------------------------------ 打包

def _zip_add(zf, arcname, data, mode=0o644, src_path=None):
    """寫一筆進 zip，並且真的保住 unix 權限位（create_system=3 是關鍵，
    在 Windows 上預設是 0/FAT，macOS 解壓就會把執行位元丟掉 -> app 點不開）。

    src_path 給定時改成串流寫入，不把整個檔案讀進記憶體（projects 有 144 MB）。
    """
    zi = zipfile.ZipInfo(arcname, date_time=datetime.now().timetuple()[:6])
    zi.create_system = 3
    zi.external_attr = (0o100000 | mode) << 16
    zi.compress_type = (zipfile.ZIP_STORED if arcname.lower().endswith(STORE_SUFFIX)
                        else zipfile.ZIP_DEFLATED)
    if src_path:
        with open(src_path, "rb") as fh, zf.open(zi, "w") as out:
            shutil.copyfileobj(fh, out, 1 << 20)
        return
    if isinstance(data, str):
        data = data.replace("\r\n", "\n").encode("utf-8")
    zf.writestr(zi, data)


def main(with_projects=False):
    build_id = datetime.now().strftime("%Y%m%d-%H%M")
    print("Sprite Studio -> macOS app   build %s%s"
          % (build_id, "  (含 data/projects)" if with_projects else ""))

    # 收集 payload
    payload = {}   # arc 相對路徑 -> bytes
    missing = [f for f in PAYLOAD_FILES if not os.path.exists(os.path.join(ROOT, f))]
    if missing:
        sys.exit("找不到這些必要檔案：%s" % ", ".join(missing))
    for f in PAYLOAD_FILES:
        payload[f] = open(os.path.join(ROOT, f), "rb").read()
    for d in PAYLOAD_DIRS:
        base = os.path.join(ROOT, d)
        if not os.path.isdir(base):
            print("  [warn] 沒有 %s/，跳過" % d)
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [x for x in dirnames if x != "__pycache__"]
            for fn in filenames:
                if fn.endswith(SKIP_SUFFIX):
                    continue
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, ROOT).replace("\\", "/")
                payload[rel] = open(full, "rb").read()

    req = os.path.join(ROOT, "tools", "mac", "requirements.txt")
    payload["requirements.txt"] = open(req, "rb").read()
    payload["READ_ME_first.txt"] = README.replace("\r\n", "\n").encode("utf-8")

    # settings.json 當範本帶一份，但金鑰一定要洗掉
    import json
    st = json.load(open(os.path.join(ROOT, "settings.json"), encoding="utf-8"))
    st.setdefault("fal", {})["api_key"] = ""
    payload["settings.json"] = json.dumps(st, ensure_ascii=False, indent=1).encode("utf-8")

    # fal 模型目錄快取：帶著可以省掉第一次開啟時的線上抓取
    cache = os.path.join(ROOT, "data", "fal_cache.json")
    if os.path.exists(cache):
        payload["fal_cache.json"] = open(cache, "rb").read()

    # 既有專案素材：大檔不進記憶體，payload 值放來源路徑，打包時再串流
    if with_projects:
        src = os.path.join(ROOT, "data", "projects")
        if not os.path.isdir(src):
            sys.exit("找不到 data/projects")
        n0 = len(payload)
        for dirpath, dirnames, filenames in os.walk(src):
            dirnames[:] = [x for x in dirnames if x != "__pycache__"]
            for fn in filenames:
                if fn.endswith(SKIP_SUFFIX):
                    continue
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, src).replace("\\", "/")
                payload["data_seed/projects/" + rel] = full
        projs = sorted(x for x in os.listdir(src) if os.path.isdir(os.path.join(src, x)))
        print("  帶入專案 %d 個 (%d 個檔案): %s" % (len(projs), len(payload) - n0, ", ".join(projs)))

    h = hashlib.sha256()
    for k in sorted(payload):
        h.update(k.encode())
        v = payload[k]
        if isinstance(v, bytes):
            h.update(v)
        else:                                  # 路徑：分塊讀，別把 144 MB 吃進記憶體
            with open(v, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
    payload[".build_id"] = ("%s %s\n" % (build_id, h.hexdigest()[:16])).encode()

    total = sum(len(v) if isinstance(v, bytes) else os.path.getsize(v)
                for v in payload.values())
    print("  payload: %d 個檔案, %.1f MB" % (len(payload), total / 1048576))

    icns = make_icon()

    os.makedirs(DIST, exist_ok=True)
    out = os.path.join(DIST, "SpriteStudio-mac-%s%s.zip"
                       % (build_id, "-with-projects" if with_projects else ""))
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        C = APP_NAME + "/Contents"
        _zip_add(zf, C + "/Info.plist",
                 INFO_PLIST.replace("__EXEC__", EXEC_NAME).replace("__BUILD__", build_id))
        _zip_add(zf, C + "/PkgInfo", "APPL????")
        _zip_add(zf, C + "/MacOS/" + EXEC_NAME, LAUNCHER, mode=0o755)
        if icns:
            _zip_add(zf, C + "/Resources/AppIcon.icns", icns)
        keys = sorted(payload)
        for i, rel in enumerate(keys, 1):
            v = payload[rel]
            arc = C + "/Resources/payload/" + rel
            if isinstance(v, bytes):
                _zip_add(zf, arc, v)
            else:
                _zip_add(zf, arc, None, src_path=v)
            if i % 25 == 0 or i == len(keys):
                done = os.path.getsize(out) if os.path.exists(out) else 0
                sys.stdout.write("\r  打包中 %d/%d (%.0f MB)   " % (i, len(keys), done / 1048576))
                sys.stdout.flush()
        print()
        _zip_add(zf, "READ_ME_first.txt", README)
        _zip_add(zf, "疑難排解/從終端機啟動.command", TERMINAL_FALLBACK, mode=0o755)
        _zip_add(zf, "疑難排解/看說明.txt",
                 "詳細說明在上一層的 READ_ME_first.txt。\n"
                 "這個資料夾裡的 .command 只有在 app 點不開時才需要用。\n")

    print("\n完成： %s  (%.1f MB)" % (out, os.path.getsize(out) / 1048576))
    print("把這個 zip 丟到 Mac 解壓縮，照 READ_ME_first.txt 走即可。")


if __name__ == "__main__":
    flags = set(sys.argv[1:])
    unknown = flags - {"--with-projects"}
    if unknown:
        sys.exit("不認得的參數：%s\n用法：python tools\\build_mac.py [--with-projects]"
                 % ", ".join(sorted(unknown)))
    main(with_projects="--with-projects" in flags)
