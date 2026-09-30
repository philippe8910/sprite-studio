# -*- coding: utf-8 -*-
"""讀既有遊戲的素材當作動作慣例來源：掃 game/ 的 anims.json 與 miyako.html 的 SKINS，
得出動作清單、每個動作的幀數/fps 慣例，以及可拿來當比例參考的角色。

沒有 game/ 也能跑（退回內建慣例表）。
"""
import json
import os
import re

import numpy as np

import pipeline

GAME_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "game"))
GAME_HTML = "miyako.html"
SKIN_PREFIX = "assets_miyako"

# 射擊專屬的動作（彈殼／槍口）已經不是這個工具的預設了——這裡只保留一份名單，
# 用來把上線遊戲掃到的舊動作濾掉，不讓它們出現在「未建立」清單裡。
LEGACY_GUN_KEYS = ("shoot", "crouch_shoot", "run_shoot")

# 通用遊戲動作組：沒有 game/ 可讀時的建議清單，也決定清單排序
COMMON_ORDER = ["idle", "walk", "run", "attack", "jump", "jump_full", "hurt",
                "crouch_idle", "crouch_attack", "rise", "fall", "land", "roll", "death"]

_CACHE = {}


# ---------------------------------------------------------------- 讀規格

def _parse_skins_html(path):
    """從 miyako.html 抓每個 skin 的 fw/fh/feet/anchor/drawH/slim。

    先切出「每個 skin 的區塊」再抓欄位——直接用一條跨行正則會把不相干的
    label:（道具名之類）和後面某個 skin 的 fw: 配在一起。
    """
    if not os.path.exists(path):
        return {}
    html = open(path, encoding="utf-8").read()
    blocks = []
    head = re.search(r'const\s+SKINS\s*=\s*\{(.*?)\n\};', html, re.S)
    if head:
        for m in re.finditer(r'^\s{2}(\w+):\s*\{(.*?)^\s{2}\},', head.group(1) + "\n  },", re.S | re.M):
            blocks.append((m.group(1), m.group(2)))
    for m in re.finditer(r'SKINS\.(\w+)\s*=\s*\{(.*?)\n\};', html, re.S):
        blocks.append((m.group(1), m.group(2)))

    out = {}
    for sid, body in blocks:
        g = lambda k, cast=float: (lambda mm: cast(mm.group(1)) if mm else None)(
            re.search(rf'\b{k}:\s*([\d.]+)', body))
        if g("fw", int) is None:
            continue
        lm = re.search(r'label:\s*"([^"]+)"', body)
        dm = re.search(r'file:\s*"(assets_miyako[\w]*)/', body)
        out[sid] = {
            "id": sid, "label": lm.group(1) if lm else sid,
            # 預設造型的 anims 在 SKINS 外面（開機時的 ANIMS），區塊裡抓不到 file:
            "dir": dm.group(1) if dm else SKIN_PREFIX,
            "fw": g("fw", int), "fh": g("fh", int), "feet": g("feet"), "anchor": g("anchor"),
            "drawH": g("drawH", int) or 154, "slim": g("slim") or 1.0,
        }
    return out


def _body_px(strip_path, fw):
    """首幀角色本體的像素高度（不含裁切框留白）。"""
    im = pipeline.imread(strip_path)
    if im is None or im.ndim != 3 or im.shape[2] != 4:
        return None
    ys = np.where(im[:, :fw, 3].max(axis=1) > 0)[0]
    return int(ys.max() - ys.min() + 1) if len(ys) else None


def load_spec(force=False):
    """掃描上線素材，回傳規格 + 每個上線角色的量測值。"""
    if not force and "spec" in _CACHE:
        return _CACHE["spec"]

    html_skins = _parse_skins_html(os.path.join(GAME_DIR, GAME_HTML))
    by_dir = {v["dir"]: v for v in html_skins.values() if v.get("dir")}

    skins, anim_stats = [], {}
    if os.path.isdir(GAME_DIR):
        for name in sorted(os.listdir(GAME_DIR)):
            if not name.startswith(SKIN_PREFIX):
                continue
            ajson = os.path.join(GAME_DIR, name, "anims.json")
            if not os.path.exists(ajson):
                continue
            try:
                meta = json.load(open(ajson, encoding="utf-8"))
            except Exception:
                continue
            info = dict(by_dir.get(name) or {})
            sid = info.get("id") or (name.replace(SKIN_PREFIX, "").lstrip("_") or "default")
            anims = {k: v for k, v in meta.items() if not k.startswith("_")}
            for k, v in anims.items():
                s = anim_stats.setdefault(k, {"fps": [], "count": [], "loop": [], "n": 0})
                s["fps"].append(v.get("fps"))
                s["count"].append(v.get("count"))
                s["loop"].append(bool(v.get("loop")))
                s["n"] += 1
            fw = int(info.get("fw") or (list(anims.values())[0]["frameW"] if anims else 240))
            fh = int(info.get("fh") or (list(anims.values())[0]["frameH"] if anims else 0))
            body = _body_px(os.path.join(GAME_DIR, name, "idle.png"), fw) if "idle" in anims else None
            draw_h = int(info.get("drawH") or 154)
            skins.append({
                "id": sid, "label": info.get("label", name), "dir": name,
                "fw": fw, "fh": fh, "drawH": draw_h, "slim": info.get("slim", 1.0),
                "feetRatio": meta.get("_feetRatio"), "anchorX": meta.get("_anchorX"),
                "anims": {k: {kk: v[kk] for kk in ("count", "fps", "loop", "frameH")}
                          for k, v in anims.items()},
                "muzzle": {k[8:]: v for k, v in meta.items() if k.startswith("_muzzle_")},
                "bodyPx": body,
                "visualH": round(body * draw_h / fh, 1) if body and fh else None,
                "in_game": name in by_dir,
            })

    # 必要動作＝所有上線角色都有的動作（射擊類是舊遊戲專屬的，這裡濾掉）
    required = [k for k, s in anim_stats.items()
                if s["n"] == len([x for x in skins if x["anims"]]) and k not in LEGACY_GUN_KEYS]
    order = COMMON_ORDER
    required.sort(key=lambda k: order.index(k) if k in order else 99)

    norms = {}
    for k, s in anim_stats.items():
        if k in LEGACY_GUN_KEYS:
            continue
        fps = sorted(set(x for x in s["fps"] if x))
        cnt = [x for x in s["count"] if x]
        norms[k] = {
            "fps": fps, "fps_common": max(set(s["fps"]), key=s["fps"].count),
            "count": [min(cnt), max(cnt)] if cnt else [0, 0],
            "loop": max(set(s["loop"]), key=s["loop"].count),
            "align": k in ("rise", "fall", "land", "death", "roll"),
        }

    if not norms:      # 沒有 game/ 可讀（獨立使用）→ 退回通用慣例，工具照樣能驗
        norms = {k: {"fps": [v[0]], "fps_common": v[0], "count": v[1],
                     "loop": v[2], "align": k in ("rise", "fall", "land", "roll", "death")}
                 for k, v in {
                     "idle": (8, [8, 15], True), "walk": (10, [8, 12], True),
                     "run": (12, [8, 10], True), "attack": (20, [5, 10], False),
                     "jump": (20, [6, 12], False), "hurt": (18, [4, 10], False),
                     "crouch_idle": (5, [8, 15], True), "crouch_attack": (20, [5, 10], False),
                     "rise": (20, [4, 5], False), "fall": (12, [3, 3], False),
                     "land": (20, [4, 4], False),
                 }.items()}

    feet = [s["feetRatio"] for s in skins if s.get("feetRatio")]
    anch = [s["anchorX"] for s in skins if s.get("anchorX")]
    # 視覺身高基準取「最近三套（已做過歸一化）」的中位數，不被早期未校正的角色拉走
    vis = sorted([s["visualH"] for s in skins if s.get("visualH")])
    vis_target = float(np.median(vis)) if vis else 138.5

    spec = {
        "game_dir": GAME_DIR,
        "available": bool(skins),
        "required": required or order,
        "norms": norms,
        "frame_w": max(set(s["fw"] for s in skins), key=[s["fw"] for s in skins].count) if skins else 240,
        "feet_range": [round(min(feet), 4), round(max(feet), 4)] if feet else [0.97, 0.99],
        "anchor_range": [round(min(anch), 4), round(max(anch), 4)] if anch else [0.33, 0.44],
        "visual_h": round(vis_target, 1),
        "visual_h_all": {s["id"]: s["visualH"] for s in skins if s.get("visualH")},
        # 打包時要量槍口座標的動作。通用工具預設沒有；只有專案裡真的還留著舊的射擊
        # 動作時才會有東西，避免舊角色重打包後遊戲端的槍口資料掉了。
        "muzzle_keys": [k for k in LEGACY_GUN_KEYS if k in anim_stats],
        "skins": skins,
    }
    _CACHE["spec"] = spec
    return spec


