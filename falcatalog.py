# -*- coding: utf-8 -*-
"""fal.ai 線上模型目錄 — 真的去 fal.ai 拿「現在有哪些模型」與每個模型吃什麼參數。

兩支公開端點（不需金鑰）：
  GET https://fal.ai/api/models?page=N&categories=image-to-video[&keywords=…]
      → {items:[{id,title,category,shortDescription,thumbnailUrl,pricingInfoOverride,…}], pages, total}
      每頁固定 40 筆。
  GET https://fal.ai/api/openapi/queue/openapi.json?endpoint_id=<id>
      → 該模型的 OpenAPI，components.schemas.*Input 就是它接受的欄位（含 enum 與預設值）。

有了 input schema，就不必為每個模型寫死 payload：想送的欄位只有在 schema 裡有才送，
duration / resolution / aspect_ratio 這種 enum 還能鉗到合法值。抓不到就回 None，
呼叫端退回原本寫死的組法。

結果快取在 data/fal_cache.json（清單 12 小時、schema 7 天），避免每次開頁都打網路。
"""
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(ROOT, "data", "fal_cache.json")

LIST_URL = "https://fal.ai/api/models"
SCHEMA_URL = "https://fal.ai/api/openapi/queue/openapi.json"
UA = {"User-Agent": "Mozilla/5.0 (SpriteStudio)"}

LIST_TTL = 12 * 3600
SCHEMA_TTL = 7 * 86400
PAGE_SIZE = 40

# 我們只關心「能拿圖生東西」的類別。image 分頁兩種都收（有的角色生成是純文生圖）。
KIND_CATEGORIES = {
    "image": ["image-to-image", "text-to-image"],
    "video": ["image-to-video"],
}

_lock = threading.Lock()
_cache = None


# ---------------------------------------------------------------- 快取

def _load():
    global _cache
    if _cache is not None:
        return _cache
    _cache = {"lists": {}, "schemas": {}}
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH, encoding="utf-8") as f:
                disk = json.load(f)
            if isinstance(disk, dict):
                _cache["lists"] = disk.get("lists") or {}
                _cache["schemas"] = disk.get("schemas") or {}
        except Exception:
            pass
    return _cache


def _flush():
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    tmp = CACHE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_cache, f, ensure_ascii=False)
    os.replace(tmp, CACHE_PATH)


def _get_json(url, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


# ---------------------------------------------------------------- 價格

_PRICE = re.compile(r"\$\s*([0-9]+(?:\.[0-9]+)?)")


def parse_price(text):
    """從 pricingInfoOverride 那段 markdown 撈第一個金額。撈不到回 None。

    注意這只是估價用（UI 顯示「約 $x」），fal 的計價常常帶條件
    （每秒／每百萬 token／不同解析度），所以原文一起留著給使用者看。
    """
    if not text:
        return None
    m = _PRICE.search(text)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


# ---------------------------------------------------------------- 模型清單

def _fetch_category(category, max_pages=6):
    out = []
    page = 1
    while page <= max_pages:
        url = f"{LIST_URL}?{urllib.parse.urlencode({'page': page, 'categories': category})}"
        data = _get_json(url)
        items = data.get("items") or []
        out.extend(items)
        pages = int(data.get("pages") or 1)
        if page >= pages or not items:
            break
        page += 1
    return out


def _slim(item):
    # 不是每個模型都有 pricingInfoOverride——有些只給 billingMessage（「按百萬像素計費」
    # 這種算不出單張價），那 price 就是 None，UI 要顯示「價格未知」而不是 $0.00。
    text = (item.get("pricingInfoOverride") or item.get("billingMessage") or "").strip()
    return {
        "id": item.get("id"),
        "label": item.get("title") or item.get("id"),
        "category": item.get("category"),
        "note": (item.get("shortDescription") or "").strip().replace("\n", " ")[:160],
        "price": parse_price(item.get("pricingInfoOverride")),
        "price_text": text[:300],
        "deprecated": bool(item.get("deprecated")),
        "thumb": item.get("thumbnailUrl"),
        "date": item.get("date"),
        "group": (item.get("group") or {}).get("label", ""),
        "fal": True,
    }


def models(kind, refresh=False, max_pages=6):
    """回傳 [{id,label,category,note,price,…}]，依 fal 站上的排序（＝熱門度）。

    kind: "image" | "video"。網路失敗時回傳上次的快取（有多舊就多舊），
    完全沒有快取才 raise——UI 端會顯示成「線上清單抓不到，仍可用內建模型」。
    """
    cats = KIND_CATEGORIES.get(kind)
    if not cats:
        raise ValueError("kind must be image or video")
    c = _load()
    key = kind
    entry = c["lists"].get(key)
    fresh = entry and (time.time() - entry.get("at", 0) < LIST_TTL)
    if entry and fresh and not refresh:
        return entry["items"]

    try:
        seen, items = set(), []
        for cat in cats:
            for it in _fetch_category(cat, max_pages=max_pages):
                mid = it.get("id")
                if not mid or mid in seen or it.get("removed"):
                    continue
                seen.add(mid)
                items.append(_slim(it))
        with _lock:
            c["lists"][key] = {"at": time.time(), "items": items}
            _flush()
        return items
    except Exception:
        if entry:
            return entry["items"]
        raise


def list_age(kind):
    e = _load()["lists"].get(kind)
    return e.get("at") if e else None


# ---------------------------------------------------------------- input schema

def _input_schema(doc):
    comps = (doc.get("components") or {}).get("schemas") or {}
    # 慣例：<ModelName>Input。有些模型會同時有 Output/其他子結構。
    names = [k for k in comps if k.lower().endswith("input")]
    if not names:
        return None
    # 挑欄位最多的那個（少數模型有 XxxInput 與 XxxBaseInput）
    name = max(names, key=lambda n: len(comps[n].get("properties") or {}))
    return comps[name]


def _field(name, spec):
    out = {"name": name, "type": spec.get("type"), "default": spec.get("default")}
    if "enum" in spec:
        out["enum"] = spec["enum"]
    else:
        # anyOf 形態（可為 null 的欄位）裡面也可能藏 enum
        for sub in spec.get("anyOf") or []:
            if isinstance(sub, dict) and "enum" in sub:
                out["enum"] = sub["enum"]
                break
            if isinstance(sub, dict) and sub.get("type") and sub.get("type") != "null":
                out.setdefault("type", sub["type"])
    return out


def schema(endpoint_id, refresh=False):
    """回傳 {"fields": {名稱: {type,enum,default}}, "required": [...]}；抓不到回 None。"""
    if not endpoint_id:
        return None
    c = _load()
    entry = c["schemas"].get(endpoint_id)
    if entry and not refresh and (time.time() - entry.get("at", 0) < SCHEMA_TTL):
        return entry.get("schema")
    try:
        doc = _get_json(f"{SCHEMA_URL}?{urllib.parse.urlencode({'endpoint_id': endpoint_id})}")
    except Exception:
        return entry.get("schema") if entry else None
    inp = _input_schema(doc)
    if inp is None:
        return None
    meta = ((doc.get("info") or {}).get("x-fal-metadata") or {})
    out = {
        "fields": {k: _field(k, v) for k, v in (inp.get("properties") or {}).items()},
        "required": list(inp.get("required") or []),
        "category": meta.get("category", ""),
        "playground": meta.get("playgroundUrl", ""),
    }
    with _lock:
        c["schemas"][endpoint_id] = {"at": time.time(), "schema": out}
        _flush()
    return out


# ---------------------------------------------------------------- payload 組裝

def _clamp_enum(field, value, fallback=None):
    """把想送的值鉗到 schema 允許的 enum 裡；不合法就用預設值／第一個選項。"""
    enum = field.get("enum")
    if not enum:
        return value
    if value is not None and str(value) in [str(e) for e in enum]:
        # 保持 enum 原本的型別（有些是字串 "5"，有些是數字 5）
        return next(e for e in enum if str(e) == str(value))
    for cand in (fallback, field.get("default")):
        if cand is not None and str(cand) in [str(e) for e in enum]:
            return next(e for e in enum if str(e) == str(cand))
    return enum[0]


def build_payload(sch, *, prompt, negative=None, images=None, opts=None):
    """依 input schema 組 payload。sch 為 None 時回 None（呼叫端自己退回寫死版）。

    images 是 data URI 陣列；模型收 image_urls 就整批送，只收 image_url 就送第一張。
    """
    if not sch:
        return None
    fields = sch.get("fields") or {}
    opts = opts or {}
    images = images or []
    p = {}

    if "prompt" in fields:
        p["prompt"] = prompt
    if images:
        if "image_urls" in fields:
            p["image_urls"] = images
        elif "image_url" in fields:
            p["image_url"] = images[0]
    if negative and "negative_prompt" in fields:
        p["negative_prompt"] = negative

    for key in ("duration", "resolution", "aspect_ratio", "image_size", "output_format",
                "num_images", "seed", "cfg_scale", "camera_fixed", "num_frames"):
        if key not in fields or key not in opts or opts[key] is None:
            continue
        f = fields[key]
        val = opts[key]
        if f.get("enum"):
            val = _clamp_enum(f, val)
        elif f.get("type") == "integer":
            try:
                val = int(val)
            except (TypeError, ValueError):
                continue
        elif f.get("type") == "number":
            try:
                val = float(val)
            except (TypeError, ValueError):
                continue
        elif f.get("type") == "boolean":
            val = bool(val)
        p[key] = val

    # 有些模型的 negative_prompt 是必填（Kling 給了預設字串，這裡補上避免 422）
    for name in sch.get("required") or []:
        if name in p or name not in fields:
            continue
        d = fields[name].get("default")
        if d is not None:
            p[name] = d
    return p


def needs_image(sch):
    """這個模型是不是一定要餵圖（image_url / image_urls 在 required 裡）。"""
    if not sch:
        return None
    req = set(sch.get("required") or [])
    return bool(req & {"image_url", "image_urls"})


def accepts_image(sch):
    if not sch:
        return None
    return bool(set((sch.get("fields") or {}).keys()) & {"image_url", "image_urls"})
