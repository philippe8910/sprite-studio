# -*- coding: utf-8 -*-
"""影像管線：去背 / 轉綠幕 / 影片抽幀 / 對齊 / 打包 sprite strips。

全部沿用 tools/ 那套實戰驗證過的做法，並修掉兩個既知陷阱：
1. cv2.imread/imwrite 在中文路徑會靜默失敗 → 一律走 imdecode/imencode + fromfile/tofile。
2. 不同模型輸出解析度/長寬比不同（Kling 保比例、Seedance 裁成 1:1），
   硬 resize 成正方形會壓扁角色 → 改成「等比例依高度正規化後置中貼上共用畫布」。
"""
import json
import os

import cv2
import numpy as np

PREVIEW_W = 320          # 網頁預覽幀寬度
GREEN_BGR = (64, 177, 0)  # 標準綠幕色


# ---------------------------------------------------------------- I/O

def imread(path, flags=cv2.IMREAD_UNCHANGED):
    data = np.fromfile(path, dtype=np.uint8)
    if data.size == 0:
        return None
    return cv2.imdecode(data, flags)


def imwrite(path, img):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    ext = os.path.splitext(path)[1] or ".png"
    ok, buf = cv2.imencode(ext, img)
    if not ok:
        raise IOError("encode failed: " + path)
    buf.tofile(path)
    return path


# ---------------------------------------------------------------- 去背

def chroma_key(bgr, hue_lo=35, hue_hi=90, dom=25, sat=60, val=40):
    """綠幕色鍵 → BGRA。hue_lo 可上調（例如 57）保住角色身上的萊姆綠部件。"""
    b, g, r = (bgr[:, :, i].astype(np.int16) for i in range(3))
    dominance = g - np.maximum(b, r)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    h, s, v = (hsv[:, :, i].astype(np.int16) for i in range(3))
    green = (h > hue_lo) & (h < hue_hi) & (s > sat) & (v > val)
    bg = (dominance > dom) & green
    alpha = np.where(bg, 0, 255).astype(np.uint8)
    k = np.ones((3, 3), np.uint8)
    alpha = cv2.morphologyEx(alpha, cv2.MORPH_OPEN, k)
    alpha = cv2.morphologyEx(alpha, cv2.MORPH_CLOSE, k)
    # despill：只壓被綠幕染色的邊緣（int16 運算，避免 uint8 溢位）
    out = bgr.copy()
    spill = (dominance > 8) & (alpha > 0)
    limit = np.clip((b + r) // 2 + 12, 0, 255)
    out[:, :, 1] = np.where(spill, np.minimum(g, limit), g).astype(np.uint8)
    bgra = cv2.cvtColor(out, cv2.COLOR_BGR2BGRA)
    bgra[:, :, 3] = alpha
    return bgra


def key_flood(bgr, tol=26, hole_tol=14):
    """任意純色/灰底立繪去背：從四邊 flood fill，只吃「連到畫面邊界的背景色」。

    靜態圖用這招 100% 可靠，比色鍵安全（不會吃掉同色頭髮）。
    """
    h, w = bgr.shape[:2]
    corners = np.stack([bgr[2, 2], bgr[2, w - 3], bgr[h - 3, 2], bgr[h - 3, w - 3]]).astype(np.float32)
    ref = corners.mean(axis=0)
    dist = np.linalg.norm(bgr.astype(np.float32) - ref, axis=2)

    near = (dist < tol).astype(np.uint8)
    flood = ((1 - near) * 255).astype(np.uint8)
    ffmask = np.zeros((h + 2, w + 2), np.uint8)
    seeds = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]
    seeds += [(x, y) for x in range(0, w, 8) for y in (0, h - 1)]
    seeds += [(x, y) for y in range(0, h, 8) for x in (0, w - 1)]
    for sx, sy in seeds:
        if flood[sy, sx] == 0:
            cv2.floodFill(flood, ffmask, (sx, sy), 128)
    bgmask = flood == 128

    # 被肢體圍住的背景破口（光環中間等）：顏色等同背景就一起removal
    holes = ((dist < hole_tol) & ~bgmask).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(holes, 8)
    for c in range(1, n):
        if stats[c, cv2.CC_STAT_AREA] < 12:
            continue
        comp = labels == c
        if dist[comp].mean() < 8:
            bgmask |= comp

    alpha = np.where(bgmask, 0, 255).astype(np.uint8)
    k = np.ones((3, 3), np.uint8)
    alpha = cv2.morphologyEx(alpha, cv2.MORPH_OPEN, k)
    alpha = cv2.morphologyEx(alpha, cv2.MORPH_CLOSE, k)
    bgra = cv2.cvtColor(bgr, cv2.COLOR_BGR2BGRA)
    bgra[:, :, 3] = alpha
    return bgra


def greenify(src_path, dst_path, tol=26):
    """把任意背景的立繪轉成標準綠幕圖（影片模型只吃綠幕才好去背）。"""
    img = imread(src_path)
    if img is None:
        raise IOError("cannot read " + src_path)
    if img.ndim == 3 and img.shape[2] == 4:
        alpha = img[:, :, 3]
        bgr = img[:, :, :3].copy()
        if (alpha == 0).any():
            bgr[alpha < 128] = GREEN_BGR
            return imwrite(dst_path, bgr)
        img = bgr
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    bgra = key_flood(img[:, :, :3], tol=tol)
    out = img[:, :, :3].copy()
    out[bgra[:, :, 3] == 0] = GREEN_BGR
    return imwrite(dst_path, out)


def green_ratio(path):
    """回傳畫面中綠幕像素比例，用來判斷源圖是不是已經是綠幕。"""
    img = imread(path)
    if img is None:
        return 0.0
    if img.ndim == 3 and img.shape[2] == 4:
        img = img[:, :, :3]
    keyed = chroma_key(img)
    return float((keyed[:, :, 3] == 0).mean())


# ---------------------------------------------------------------- 幾何工具

def feet_of(bgra):
    ys = np.where(bgra[:, :, 3].max(axis=1) > 0)[0]
    return int(ys.max()) if len(ys) else bgra.shape[0] - 1


def feet_cx(bgra, band=30):
    """腳部帶（最底 band px）的水平中心＝身體站位，不被伸出的手臂或道具偏置。"""
    a = bgra[:, :, 3]
    ys = np.where(a.max(axis=1) > 0)[0]
    if not len(ys):
        return bgra.shape[1] / 2
    strip = a[max(0, ys.max() - band):ys.max() + 1]
    xs = np.where(strip.max(axis=0) > 0)[0]
    if not len(xs):
        return bgra.shape[1] / 2
    return (xs.min() + xs.max()) / 2


def shift_y(bgra, dy):
    if dy == 0:
        return bgra
    out = np.zeros_like(bgra)
    if dy > 0:
        out[dy:] = bgra[:-dy]
    else:
        out[:dy] = bgra[-dy:]
    return out


def shift_x(bgra, dx):
    if dx == 0:
        return bgra
    out = np.zeros_like(bgra)
    if dx > 0:
        out[:, dx:] = bgra[:, :-dx]
    else:
        out[:, :dx] = bgra[:, -dx:]
    return out


def resize_pm(bgra, size):
    """預乘 alpha 再縮圖：直接 resize BGRA 會把透明像素殘留的綠色平均進邊緣。"""
    a = bgra[:, :, 3].astype(np.float32) / 255
    pm = cv2.resize(bgra[:, :, :3].astype(np.float32) * a[:, :, None], size,
                    interpolation=cv2.INTER_AREA)
    a2 = cv2.resize(a, size, interpolation=cv2.INTER_AREA)
    rgb = np.zeros_like(pm)
    nz = a2 > 1e-4
    rgb[nz] = pm[nz] / a2[nz][:, None]
    out = np.zeros((size[1], size[0], 4), np.uint8)
    out[:, :, :3] = np.clip(rgb, 0, 255).astype(np.uint8)
    out[:, :, 3] = np.clip(a2 * 255, 0, 255).astype(np.uint8)
    return out


def scale_pm(bgra, s):
    """等比縮放，畫布尺寸不變（繞水平中心、底部不變）。"""
    if abs(s - 1.0) < 1e-4:
        return bgra
    h, w = bgra.shape[:2]
    nw, nh = max(1, int(round(w * s))), max(1, int(round(h * s)))
    small = resize_pm(bgra, (nw, nh))
    out = np.zeros_like(bgra)
    x0, y0 = (w - nw) // 2, (h - nh) // 2
    sx0, sy0 = max(0, -x0), max(0, -y0)
    dx0, dy0 = max(0, x0), max(0, y0)
    cw = min(nw - sx0, w - dx0)
    ch = min(nh - sy0, h - dy0)
    out[dy0:dy0 + ch, dx0:dx0 + cw] = small[sy0:sy0 + ch, sx0:sx0 + cw]
    return out


def place(bgra, cw, ch):
    """置中貼到共用畫布（不縮放，超出就裁）。"""
    h, w = bgra.shape[:2]
    out = np.zeros((ch, cw, 4), np.uint8)
    x0, y0 = (cw - w) // 2, (ch - h) // 2
    sx0, sy0 = max(0, -x0), max(0, -y0)
    dx0, dy0 = max(0, x0), max(0, y0)
    tw = min(w - sx0, cw - dx0)
    th = min(h - sy0, ch - dy0)
    out[dy0:dy0 + th, dx0:dx0 + tw] = bgra[sy0:sy0 + th, sx0:sx0 + tw]
    return out


def despeckle(bgra, min_area=1500):
    """砍掉小連通元件（模型偷加的飛鳥/雜點）。"""
    n, lab, stats, _ = cv2.connectedComponentsWithStats((bgra[:, :, 3] > 0).astype(np.uint8), 8)
    out = bgra.copy()
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] < min_area:
            out[:, :, 3][lab == i] = 0
    return out


def outline(bgra, dark=0.32, px=0):
    """最外圈像素壓暗＝半像素描邊，蓋掉去背邊緣髒色；px>0 再加一圈黑邊。"""
    a = (bgra[:, :, 3] > 0).astype(np.uint8)
    k = np.ones((3, 3), np.uint8)
    edge = (a - cv2.erode(a, k)) > 0
    out = bgra.copy()
    out[:, :, :3][edge] = (out[:, :, :3][edge] * dark).astype(np.uint8)
    if px > 0:
        ring = (cv2.dilate(a, k, iterations=px) - a) > 0
        out[ring] = (12, 12, 16, 255)
    return out


def clear_warm(bgra, y0f=0.25, y1f=0.60, thresh=(220, 180, 140)):
    """清掉指定行帶內的亮暖色像素（影片模型自己多加的火光、閃光等特效）。"""
    h = bgra.shape[0]
    y0, y1 = int(h * y0f), int(h * y1f)
    band = bgra[y0:y1]
    b, g, r = (band[:, :, i].astype(np.int16) for i in range(3))
    hot = (r > thresh[0]) & (g > thresh[1]) & (b < thresh[2])
    band[:, :, 3][hot] = 0
    return bgra


# ---------------------------------------------------------------- 影片

def video_info(path):
    cap = cv2.VideoCapture(path)
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 24
    cap.release()
    return {"count": n, "width": w, "height": h, "fps": round(float(fps), 2)}


def read_frames(path, indices=None):
    """讀出（指定的）原始 BGR 幀。indices=None 表示全部。"""
    want = None if indices is None else set(int(i) for i in indices)
    cap = cv2.VideoCapture(path)
    out, i = {}, 0
    while True:
        ok, f = cap.read()
        if not ok:
            break
        if want is None or i in want:
            out[i] = f
        i += 1
    cap.release()
    if indices is None:
        return [out[k] for k in sorted(out)]
    return [out[i] for i in indices if i in out]


def extract_take(video_path, out_dir, key_opts=None, progress=None, preview_w=None):
    """抽出全部幀 → 去背 → 存成預覽 PNG（RGBA），同時算出挑幀用的統計。

    回傳 meta dict（也會寫成 out_dir/meta.json）。
    """
    os.makedirs(out_dir, exist_ok=True)
    key_opts = key_opts or {}
    info = video_info(video_path)
    cap = cv2.VideoCapture(video_path)
    stats, i = [], 0
    pw = int(preview_w or PREVIEW_W)
    ph = None
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        h, w = frame.shape[:2]
        if ph is None:
            ph = int(round(h * pw / w))
        small = cv2.resize(frame, (pw, ph), interpolation=cv2.INTER_AREA)
        keyed = chroma_key(small, **key_opts)
        imwrite(os.path.join(out_dir, f"f{i:03d}.png"), keyed)

        a = keyed[:, :, 3]
        ys = np.where(a.max(axis=1) > 0)[0]
        xs = np.where(a.max(axis=0) > 0)[0]
        b, g, r = (keyed[:, :, c].astype(np.int16) for c in range(3))
        warm = int((((r > 215) & (g > 170) & (b < 150)) & (a > 0)).sum())
        stats.append({
            "feet": float(ys.max() / ph) if len(ys) else 1.0,
            "top": float(ys.min() / ph) if len(ys) else 0.0,
            "cx": float(feet_cx(keyed) / pw),
            "area": int((a > 0).sum()),
            "warm": warm,
            "x0": float(xs.min() / pw) if len(xs) else 0.0,
            "x1": float(xs.max() / pw) if len(xs) else 1.0,
        })
        if progress and i % 10 == 0:
            progress(i, info["count"])
        i += 1
    cap.release()
    meta = {
        "count": i, "preview_w": pw, "preview_h": ph,
        "src_w": info["width"], "src_h": info["height"], "src_fps": info["fps"],
        "stats": stats,
    }
    with open(os.path.join(out_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f)
    return meta


def frame_signatures(frames_dir, count, size=24):
    """讀預覽幀 → 24x24 灰階特徵，用來找無縫循環點。"""
    sigs = []
    for i in range(count):
        img = imread(os.path.join(frames_dir, f"f{i:03d}.png"))
        if img is None:
            sigs.append(np.zeros((size, size), np.float32))
            continue
        a = img[:, :, 3:4].astype(np.float32) / 255.0
        rgb = img[:, :, :3].astype(np.float32) * a  # 預乘，背景不干擾
        small = cv2.resize(rgb, (size, size), interpolation=cv2.INTER_AREA)
        sigs.append(small.mean(axis=2))
    return np.stack(sigs) if sigs else np.zeros((0, size, size), np.float32)


def suggest_loop(frames_dir, count, start, min_period=6, max_period=None):
    """給定起始幀，回傳最接近無縫循環的幾個週期長度（差異越小越無縫）。"""
    sigs = frame_signatures(frames_dir, count)
    if len(sigs) == 0 or start >= len(sigs):
        return []
    max_period = max_period or (len(sigs) - start - 1)
    ref = sigs[start]
    out = []
    for p in range(min_period, min(max_period, len(sigs) - start - 1) + 1):
        d = float(np.abs(ref - sigs[start + p]).mean())
        out.append({"period": p, "diff": round(d, 4)})
    out.sort(key=lambda x: x["diff"])
    return out[:8]


def suggest_segments(stats):
    """依腳底 y 曲線切出 rise / fall / land 建議區間（跳躍系用）。"""
    if not stats:
        return {}
    feet = np.array([s["feet"] for s in stats])
    ground = float(np.percentile(feet, 90))
    air = feet < ground - 0.01
    if not air.any():
        return {}
    idx = np.where(air)[0]
    # 取最長的一段連續滯空
    runs, cur = [], [idx[0]]
    for a, b in zip(idx, idx[1:]):
        if b - a == 1:
            cur.append(b)
        else:
            runs.append(cur)
            cur = [b]
    runs.append(cur)
    seg = max(runs, key=len)
    apex = int(seg[int(np.argmin(feet[seg]))])
    return {
        "rise": [int(seg[0]), apex],
        "fall": [apex, int(seg[-1])],
        "land": [int(seg[-1]), int(min(len(feet) - 1, seg[-1] + 10))],
        "ground": round(ground, 4),
        "apex": apex,
    }


# ---------------------------------------------------------------- 打包

def pack(clips, out_dir, frame_w=240, pad=6, outline_dark=0.32, outline_px=0,
         progress=None):
    """把多個 clip 打包成 sprite strips + anims.json。

    clips: [{name, video, frames:[i...], fps, loop, align, scale, xalign,
             despeckle, key_opts, clear_warm}]
    順序：色鍵 → 火光清除 → 高度正規化 → 縮放校正 → 腳底對齊 → 站位對齊
          → 共用裁切 → 描邊 → strips
    """
    os.makedirs(out_dir, exist_ok=True)
    live = [c for c in clips if c.get("frames")]
    if not live:
        raise ValueError("沒有任何已挑選幀的動作")

    infos = {}
    for c in live:
        infos[c["name"]] = video_info(c["video"])
    base_h = min(i["height"] for i in infos.values())
    canvas_h = base_h
    canvas_w = int(max(i["width"] * base_h / i["height"] for i in infos.values()))
    canvas_w = int(canvas_w * 1.25)          # 左右留位移空間
    feet_line = int(canvas_h * 0.92)
    ref_cx = canvas_w / 2

    processed = {}
    for c in live:
        info = infos[c["name"]]
        s_norm = base_h / info["height"]
        raw = read_frames(c["video"], c["frames"])
        keyed = []
        for f in raw:
            k = chroma_key(f, **(c.get("key_opts") or {}))
            if c.get("clear_warm"):
                k = clear_warm(k, *c["clear_warm"]) if isinstance(c["clear_warm"], (list, tuple)) else clear_warm(k)
            if c.get("despeckle"):
                k = despeckle(k)
            if abs(s_norm - 1.0) > 1e-3:
                k = resize_pm(k, (int(round(info["width"] * s_norm)), canvas_h))
            k = place(k, canvas_w, canvas_h)
            sc = float(c.get("scale") or 1.0)
            if abs(sc - 1.0) > 1e-4:
                k = scale_pm(k, sc)
            keyed.append(k)
        if not keyed:
            continue
        if c.get("align"):                    # 跳躍系：逐幀歸零，否則和物理疊加
            keyed = [shift_y(f, feet_line - feet_of(f)) for f in keyed]
        else:
            med = int(np.median([feet_of(f) for f in keyed]))
            keyed = [shift_y(f, feet_line - med) for f in keyed]
        processed[c["name"]] = keyed
        if progress:
            progress(c["name"], len(keyed))

    # 站位 x 對齊（跑步基準；倒地/跳躍用首幀，重心會亂跑）
    for c in live:
        frames = processed.get(c["name"])
        if not frames:
            continue
        if c.get("xalign") == "first":
            cur = feet_cx(frames[0])
        else:
            cur = float(np.median([feet_cx(f) for f in frames]))
        dx = int(round(ref_cx - cur))
        if dx:
            processed[c["name"]] = [shift_x(f, dx) for f in frames]

    union = np.zeros((canvas_h, canvas_w), np.uint8)
    for frames in processed.values():
        for f in frames:
            union = np.maximum(union, f[:, :, 3])
    ys, xs = np.where(union > 0)
    if not len(ys):
        raise ValueError("去背後畫面全空——請檢查影片是不是綠幕背景")
    y0, y1 = max(0, ys.min() - pad), min(canvas_h, ys.max() + 1 + pad)
    x0, x1 = max(0, xs.min() - pad), min(canvas_w, xs.max() + 1 + pad)

    meta = {}
    for c in live:
        frames = processed.get(c["name"])
        if not frames:
            continue
        crops = []
        for f in frames:
            crop = f[y0:y1, x0:x1]
            fw = frame_w
            fh = max(1, int(round(crop.shape[0] * fw / crop.shape[1])))
            crops.append(outline(resize_pm(crop, (fw, fh)), outline_dark, outline_px))
        fh, fw = crops[0].shape[:2]
        strip = np.zeros((fh, fw * len(crops), 4), np.uint8)
        for i, cimg in enumerate(crops):
            strip[:, i * fw:(i + 1) * fw] = cimg
        imwrite(os.path.join(out_dir, f"{c['name']}.png"), strip)
        meta[c["name"]] = {
            "file": f"{c['name']}.png", "frameW": fw, "frameH": fh,
            "count": len(crops), "fps": c.get("fps", 12), "loop": bool(c.get("loop", True)),
        }

    ref_name = "idle" if "idle" in processed else next(iter(processed))
    cxs = [np.where(f[:, :, 3] > 0)[1].mean() for f in processed[ref_name]]
    meta["_feetRatio"] = round((feet_line - y0) / (y1 - y0), 4)
    meta["_anchorX"] = round(float((np.mean(cxs) - x0) / (x1 - x0)), 4)
    meta["_canvas"] = {"w": canvas_w, "h": canvas_h, "feetLine": feet_line,
                       "crop": [int(x0), int(y0), int(x1), int(y1)]}
    with open(os.path.join(out_dir, "anims.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=1, ensure_ascii=False)
    return meta
