"""評估失敗 → 分類 → 修復計畫。

原則：先用免費的本地修復（重挑幀、對齊、縮放後重新打包），修不好才花錢重新生成，
而且重新生成前先查快取：同一組輸入（模型、提示詞、起始圖、參數）生成過就直接沿用。

這個模組只依賴 numpy / hashlib，方便單元測試；讀圖與打包由呼叫端（server.py）處理。
"""
import hashlib
import json

import numpy as np

# 失敗類型 → 修復動作。free=True 不花錢（只改挑幀或打包參數後重新打包）。
ACTIONS = {
    "seam_jump":   {"action": "reloop", "free": True, "why": "循環接縫跳動 → 重新搜尋最順的循環段"},
    "seam_stall":  {"action": "reloop", "free": True, "why": "尾幀≈首幀會每圈卡一格 → 重新搜尋循環段"},
    "feet":        {"action": "align", "free": True, "why": "腳底上下漂移 → 開啟腳底對齊"},
    "clip":        {"action": "shrink", "free": True, "why": "被畫框切到 → 縮小 10% 重新置中"},
    "empty":       {"action": "regenerate", "free": False, "why": "有空白幀 → 素材本身壞掉，需重新生成"},
    "short_strip": {"action": "regenerate", "free": False, "why": "幀數不足 → 需重新生成"},
    "error":       {"action": "regenerate", "free": False, "why": "無法讀取 → 需重新生成"},
}


def best_loop(sigs, n_frames, min_period=None, max_period=None, skip_head=0.1):
    """在整段影片裡找最接近無縫循環的 (起點, 週期)。

    sigs: (N, h, w) 每幀的縮小灰階特徵。回傳 {start, period, seam, step, frames}：
    seam 是循環接縫差、step 是挑出來的相鄰幀平均差，seam / step 越小越無縫。
    刻意跳過開頭 skip_head 比例的幀（影片開頭常有從靜止圖起動的過渡）。
    """
    sigs = np.asarray(sigs, dtype=np.float32)
    n = len(sigs)
    if n < 4:
        return None
    # 週期太短會把「幾乎靜止」誤認成循環（待機動作特別容易），至少涵蓋影片的 1/6
    min_period = max(int(min_period or max(n_frames, n // 6)), 4)
    max_period = int(max_period or n // 2)
    head = int(n * skip_head)
    best = None
    for p in range(min_period, max_period + 1):
        for s in range(head, n - p):
            d = float(np.abs(sigs[s] - sigs[s + p]).mean())
            if best is None or d < best[0]:
                best = (d, s, p)
    if best is None:
        return None
    seam, start, period = best
    frames = pick_frames(start, period, n_frames)
    steps = [float(np.abs(sigs[a] - sigs[b]).mean()) for a, b in zip(frames, frames[1:])]
    return {"start": start, "period": period, "seam": round(seam, 4),
            "step": round(float(np.median(steps)) if steps else 0.0, 4), "frames": frames}


def pick_frames(start, period, n):
    """在 [start, start+period) 等距取 n 幀；不含 start+period（那幀≈首幀，含進來每圈會卡一格）。"""
    n = max(1, int(n))
    return [int(round(start + i * period / n)) for i in range(n)]


def plan(results):
    """eval_sprites.evaluate() 的結果 → 每個失敗動作一筆修復計畫。

    同一個動作若同時有免費與付費的問題，只列付費那筆（重新生成後其他問題一起重來）。
    """
    by_anim = {}
    for r in results:
        if r.get("pass"):
            continue
        acts = [dict(ACTIONS[f], fail=f) for f in r.get("fails", []) if f in ACTIONS]
        if not acts:
            continue
        paid = [a for a in acts if not a["free"]]
        by_anim[r["anim"]] = paid[:1] if paid else acts
    return by_anim


def apply_free(clip_cfg, actions, sigs=None):
    """把免費修復套到 clip 設定上（原地修改並回傳做了什麼）。不含重新打包。"""
    done = []
    for a in actions:
        if a["action"] == "reloop" and sigs is not None:
            n = max(4, len(clip_cfg.get("frames") or []) or 8)
            lp = best_loop(sigs, n)
            if lp:
                clip_cfg["frames"] = lp["frames"]
                done.append({"action": "reloop", "start": lp["start"], "period": lp["period"],
                             "seam_ratio": round(lp["seam"] / lp["step"], 2) if lp["step"] else None})
        elif a["action"] == "align":
            clip_cfg["align"] = True
            done.append({"action": "align"})
        elif a["action"] == "shrink":
            clip_cfg["scale"] = round(float(clip_cfg.get("scale") or 1.0) * 0.9, 3)
            done.append({"action": "shrink", "scale": clip_cfg["scale"]})
    return done


def cache_key(model_id, prompt, neg, image_bytes, opts):
    """生成快取鍵：相同模型、提示詞、起始圖內容與參數 → 相同鍵。

    seed 為空時代表「每次都不同」，不應該命中快取，所以把它換成隨機以外的明確標記交給呼叫端決定；
    這裡只負責把輸入穩定地雜湊起來。
    """
    h = hashlib.sha256()
    h.update(model_id.encode())
    h.update(b"\0" + (prompt or "").encode())
    h.update(b"\0" + (neg or "").encode())
    h.update(b"\0" + hashlib.sha256(image_bytes or b"").digest())
    h.update(b"\0" + json.dumps(opts or {}, sort_keys=True, ensure_ascii=False).encode())
    return h.hexdigest()[:24]
