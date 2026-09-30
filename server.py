# -*- coding: utf-8 -*-
"""Sprite Studio — 像素角色動畫 AI 工作流後端。

流程：上傳立繪 → 參考姿勢生成 5 張候選 → 挑選 → 套用動作提示詞預設 → 生成影片
      → 抽幀挑幀（即時播放）→ 重生成 → 打包 sprite strips + anims.json。

啟動：python server.py  然後開 http://127.0.0.1:8765
"""
import base64
import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import uuid
import zipfile

from flask import Flask, jsonify, request, send_file, send_from_directory, abort

import falcatalog
import falclient
import gamespec
import localgen
import pipeline
import presets

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
PROJECTS = os.path.join(DATA, "projects")
POSELIB = os.path.join(ROOT, "poselib")
os.makedirs(PROJECTS, exist_ok=True)
os.makedirs(POSELIB, exist_ok=True)

app = Flask(__name__, static_folder=os.path.join(ROOT, "static"), static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 * 1024
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0   # 本機工具：改完 app.js 重整就要看到新的
app.json.sort_keys = False   # 模型/預設的排列順序＝推薦順序，別讓 Flask 重排


@app.after_request
def _no_cache_static(resp):
    if request.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp

LOCK = threading.Lock()
JOBS = {}
SETTINGS_PATH = os.path.join(ROOT, "settings.json")

DEFAULT_SETTINGS = {
    "local": {
        "comfy_url": "http://127.0.0.1:8188",
        "a1111_url": "http://127.0.0.1:7860",
        "image": {"kind": "comfyui", "workflow": "comfy_img2img.json", "model": "", "vae": "",
                  "steps": 28, "cfg": 6.5, "denoise": 0.62, "width": 1024, "height": 1024,
                  "sampler": "DPM++ 2M"},
        "video": {"kind": "comfyui", "workflow": "", "model": "",
                  "steps": 20, "cfg": 5.0, "frames": 81, "fps": 16,
                  "width": 720, "height": 720},
    },
    "extract": {"preview_w": 320, "hue_lo": 35, "hue_hi": 90, "dom": 25, "sat": 60, "val": 40},
    "defaults": {"image_model": "nano-banana", "video_model": "kling25",
                 "pose_n": 5, "frame_w": 240},
    "fal": {"api_key": "", "catalog": True},
    # 角色生成頁「姿勢」下拉自己加的項目（所有專案共用）：[{key,label,en,neg}]
    "char_poses": [],
}


def load_settings():
    s = json.loads(json.dumps(DEFAULT_SETTINGS))
    if os.path.exists(SETTINGS_PATH):
        try:
            with open(SETTINGS_PATH, encoding="utf-8") as f:
                disk = json.load(f)
            for k, v in disk.items():
                if isinstance(v, dict) and isinstance(s.get(k), dict):
                    for kk, vv in v.items():
                        if isinstance(vv, dict) and isinstance(s[k].get(kk), dict):
                            s[k][kk].update(vv)
                        else:
                            s[k][kk] = vv
                else:
                    s[k] = v
        except Exception:
            pass
    return s


def save_settings(s):
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=1)
    return s


def public_settings(s=None):
    """給前端的設定：金鑰只回報「有沒有、從哪來」，本體不外流。"""
    s = json.loads(json.dumps(s or load_settings()))
    fal = s.setdefault("fal", {})
    key = (fal.get("api_key") or "").strip()
    fal["api_key"] = ""
    fal["has_key"] = bool(falclient.key_source())
    fal["source"] = falclient.key_source()
    fal["masked"] = (key[:6] + "…" + key[-4:]) if len(key) > 12 else ("已設定" if key else "")
    return s


def resolve_model(kind, key):
    """把模型選單的值轉成可用的模型資訊。

    key 可能是內建短名（nano-banana / kling25）、fal 的 endpoint id（fal-ai/…），
    或 "local"。回傳 {key,id,label,price,extra,local}；認不得回 None。
    """
    if not key:
        return None
    if key.startswith("local"):
        return {"key": key, "id": "local", "label": "本機模型", "price": 0.0,
                "extra": {}, "local": True}
    table = presets.IMAGE_MODELS if kind == "image" else presets.VIDEO_MODELS
    m = table.get(key)
    if m:
        return {"key": key, "id": m["id"], "label": m["label"], "price": m.get("price", 0),
                "extra": dict(m.get("extra") or {}), "local": False}
    if "/" in key:      # fal endpoint id：價格從線上目錄的快取撈
        info = {}
        try:
            for kind_ in ("image", "video"):
                for it in falcatalog.models(kind_):
                    if it["id"] == key:
                        info = it
                        break
                if info:
                    break
        except Exception:
            pass
        return {"key": key, "id": key, "label": info.get("label") or key,
                "price": info.get("price") or 0.0, "extra": {}, "local": False}
    return None


def local_cfg(stage):
    """組出 localgen 要的 {kind,url,workflow}。"""
    st = load_settings()["local"]
    conf = dict(st.get(stage) or {})
    conf["url"] = st["comfy_url"] if conf.get("kind") == "comfyui" else st["a1111_url"]
    return conf


# ---------------------------------------------------------------- 小工具

def slug(text, fallback="item"):
    s = re.sub(r"[^\w一-鿿-]+", "_", (text or "").strip())
    s = s.strip("_")
    return s or fallback


def slug_ascii(text, fallback="proj"):
    """專案 id / 檔名用：只留 ASCII，中文名一律退回 fallback（路徑與網址才不會出事）。"""
    s = re.sub(r"[^A-Za-z0-9_-]+", "_", (text or "").strip()).strip("_")
    return s.lower() or fallback


def now():
    return time.time()


def pdir(pid):
    d = os.path.join(PROJECTS, pid)
    if not os.path.isdir(d):
        abort(404, "project not found")
    return d


def load_project(pid):
    with open(os.path.join(pdir(pid), "project.json"), encoding="utf-8") as f:
        return json.load(f)


def save_project(p):
    p["updated"] = now()
    d = os.path.join(PROJECTS, p["id"])
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, "project.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(p, f, ensure_ascii=False, indent=1)
    os.replace(tmp, os.path.join(d, "project.json"))
    return p


def add_spend(p, amount):
    p["spend"] = round(float(p.get("spend", 0)) + float(amount), 4)


def rel(p_id, abs_path):
    return os.path.relpath(abs_path, os.path.join(PROJECTS, p_id)).replace("\\", "/")


def furl(pid, relpath, bust=True):
    if not relpath:
        return None
    u = f"/files/{pid}/{relpath}"
    return u + (f"?v={int(now())}" if bust else "")


# ---------------------------------------------------------------- 任務系統

def new_job(kind, pid, label):
    jid = uuid.uuid4().hex[:12]
    JOBS[jid] = {"id": jid, "kind": kind, "pid": pid, "label": label,
                 "status": "queued", "progress": 0, "message": "排隊中…",
                 "created": now(), "result": None, "error": None, "items": []}
    return JOBS[jid]


def run_job(job, fn):
    def wrap():
        job["status"] = "running"
        try:
            job["result"] = fn(job)
            job["status"] = "done"
            job["progress"] = 100
            job["message"] = "完成"
        except Exception as e:
            job["status"] = "error"
            job["error"] = f"{type(e).__name__}: {e}"
            job["message"] = job["error"]
            traceback.print_exc()
    t = threading.Thread(target=wrap, daemon=True)
    t.start()
    return job


# ---------------------------------------------------------------- 靜態 / 檔案

@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/favicon.ico")
def favicon():
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">'
           '<rect width="16" height="16" rx="3" fill="#12151b"/>'
           '<path d="M2 12 L8 3 L14 12 Z" fill="#9dff3d"/></svg>')
    return app.response_class(svg, mimetype="image/svg+xml")


@app.route("/files/<pid>/<path:sub>")
def files(pid, sub):
    return send_from_directory(os.path.join(PROJECTS, pid), sub)


@app.route("/poselib/<path:sub>")
def poselib_file(sub):
    return send_from_directory(POSELIB, sub)


# ---------------------------------------------------------------- 設定

@app.get("/api/config")
def api_config():
    lib = []
    manifest = {}
    mpath = os.path.join(POSELIB, "poselib.json")
    if os.path.exists(mpath):
        with open(mpath, encoding="utf-8") as f:
            manifest = json.load(f)
    for name in sorted(os.listdir(POSELIB)):
        if not name.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
            continue
        info = manifest.get(name, {})
        lib.append({"name": name, "url": f"/poselib/{name}",
                    "label": info.get("label", os.path.splitext(name)[0]),
                    "pose": info.get("pose", "")})
    st = load_settings()
    img_models = dict(presets.IMAGE_MODELS)
    vid_models = dict(presets.VIDEO_MODELS)
    img_models["local"] = {
        "id": "local", "label": "本機 · " + ("ComfyUI" if st["local"]["image"]["kind"] == "comfyui" else "A1111"),
        "price": 0.0, "local": True,
        "note": st["local"]["image"].get("workflow") or st["local"]["image"].get("model") or "到「設定」指定 workflow / 模型",
    }
    vid_models["local"] = {
        "id": "local", "label": "本機 · ComfyUI workflow", "price": 0.0, "local": True,
        "negative": True, "durations": ["—"], "keeps_aspect": True,
        "note": st["local"]["video"].get("workflow") or "到「設定」指定影片 workflow",
        "best_for": [],
    }
    return jsonify({
        "image_models": img_models,
        "video_models": vid_models,
        "char_parts": presets.char_parts(st.get("char_poses")),
        "pose_presets": presets.POSE_PRESETS,
        "anim_presets": presets.ANIM_PRESETS,
        "poselib": lib,
        "settings": public_settings(st),
    })


@app.post("/api/char_parts/pose")
def api_char_pose_add():
    """角色生成頁自訂姿勢：存進 settings.json，所有專案共用。"""
    body = request.get_json(force=True, silent=True) or {}
    label = (body.get("label") or "").strip()
    en = (body.get("en") or "").strip()
    if not label:
        abort(400, "姿勢名稱是空的")
    if not en:
        abort(400, "英文提示詞片段是空的（模型只看這段）")
    s = load_settings()
    poses = s.setdefault("char_poses", [])
    key = "u_" + slug_ascii(label, "pose") + "_" + uuid.uuid4().hex[:4]
    poses.append({"key": key, "label": label[:40], "en": en,
                  "neg": (body.get("neg") or "").strip()})
    save_settings(s)
    return jsonify({"key": key, "parts": presets.char_parts(poses)})


@app.delete("/api/char_parts/pose")
def api_char_pose_del():
    """只能刪自訂的（內建的沒有 u_ 前綴，刪不掉）。"""
    key = (request.get_json(force=True, silent=True) or {}).get("key") or ""
    s = load_settings()
    poses = [p for p in s.get("char_poses", []) if p.get("key") != key]
    s["char_poses"] = poses
    save_settings(s)
    return jsonify({"parts": presets.char_parts(poses)})


# ---------------------------------------------------------------- fal.ai 線上目錄

@app.get("/api/fal/key")
def api_fal_key():
    return jsonify(public_settings()["fal"])


@app.post("/api/fal/key")
def api_fal_key_set():
    """存金鑰。key 傳空字串＝清掉（改回吃環境變數／舊 secrets.json）。"""
    body = request.get_json(force=True, silent=True) or {}
    key = (body.get("key") or "").strip()
    s = load_settings()
    s.setdefault("fal", {})["api_key"] = key
    save_settings(s)
    return jsonify(public_settings(s)["fal"])


@app.post("/api/fal/verify")
def api_fal_verify():
    """驗證金鑰（可帶 key 先試再存）。用不存在的 request id 問狀態，不花錢。"""
    body = request.get_json(force=True, silent=True) or {}
    try:
        return jsonify(falclient.verify(body.get("key")))
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"[:200]})


@app.get("/api/fal/models")
def api_fal_models():
    """向 fal.ai 要目前上架的模型清單（image / video）。"""
    kind = request.args.get("kind", "video")
    if kind not in ("image", "video"):
        abort(400, "kind 只能是 image 或 video")
    refresh = request.args.get("refresh") == "1"
    try:
        items = falcatalog.models(kind, refresh=refresh)
        return jsonify({"ok": True, "kind": kind, "items": items,
                        "at": falcatalog.list_age(kind)})
    except Exception as e:
        return jsonify({"ok": False, "kind": kind, "items": [],
                        "error": f"{type(e).__name__}: {e}"[:200]})


@app.get("/api/fal/schema")
def api_fal_schema():
    """某個模型吃哪些參數（前端用它決定顯示哪幾格、選單有哪些選項）。"""
    key = request.args.get("id") or ""
    m = resolve_model(request.args.get("kind", "video"), key)
    if not m or m.get("local"):
        return jsonify({"ok": False, "error": "本機模型沒有線上 schema"})
    try:
        sch = falcatalog.schema(m["id"], refresh=request.args.get("refresh") == "1")
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]})
    if not sch:
        return jsonify({"ok": False, "error": "拿不到這個模型的 schema"})
    return jsonify({"ok": True, "id": m["id"], "schema": sch})


# ---------------------------------------------------------------- 開啟檔案位置

def _reveal_roots():
    """允許開啟的根目錄——這是本機工具，但還是別讓網頁能開任意路徑。"""
    return [os.path.realpath(x) for x in
            (DATA, POSELIB, localgen.WORKFLOW_DIR, gamespec.GAME_DIR, ROOT)]


@app.post("/api/reveal")
def api_reveal():
    """在檔案總管開啟並選取檔案（或直接開資料夾）。"""
    body = request.get_json(force=True, silent=True) or {}
    pid = body.get("pid")
    rel_path = body.get("path") or ""
    if body.get("abs"):
        target = body["abs"]
    elif pid:
        target = os.path.join(PROJECTS, pid, rel_path)
    else:
        target = os.path.join(ROOT, rel_path)
    target = os.path.realpath(target)

    if not any(target == r or target.startswith(r + os.sep) for r in _reveal_roots()):
        abort(400, "不允許開啟這個路徑")
    if not os.path.exists(target):
        # 檔案被刪掉時退一層開資料夾
        target = os.path.dirname(target)
        if not os.path.exists(target):
            abort(404, "路徑不存在")

    try:
        if sys.platform == "win32":
            if os.path.isdir(target):
                subprocess.Popen(f'explorer "{target}"')
            else:
                subprocess.Popen(f'explorer /select,"{target}"')
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R" if os.path.isfile(target) else "-a", target]
                             if os.path.isfile(target) else ["open", target])
        else:
            subprocess.Popen(["xdg-open", target if os.path.isdir(target) else os.path.dirname(target)])
    except Exception as e:
        abort(500, f"開啟失敗：{e}")
    return jsonify({"opened": target, "is_dir": os.path.isdir(target)})


# ---------------------------------------------------------------- 本機模型

@app.get("/api/settings")
def api_get_settings():
    return jsonify(public_settings())


@app.patch("/api/settings")
def api_patch_settings():
    s = load_settings()
    body = request.get_json(force=True, silent=True) or {}
    # 前端拿到的設定裡金鑰是空的，別讓它回存時把金鑰洗掉（要清請用 /api/fal/key）
    if isinstance(body.get("fal"), dict) and not (body["fal"].get("api_key") or "").strip():
        body["fal"].pop("api_key", None)
    for k, v in body.items():
        if isinstance(v, dict) and isinstance(s.get(k), dict):
            for kk, vv in v.items():
                if isinstance(vv, dict) and isinstance(s[k].get(kk), dict):
                    s[k][kk].update(vv)
                else:
                    s[k][kk] = vv
        else:
            s[k] = v
    save_settings(s)
    return jsonify(public_settings(s))


@app.get("/api/local/ping")
def api_local_ping():
    kind = request.args.get("kind", "comfyui")
    st = load_settings()["local"]
    url = request.args.get("url") or (st["comfy_url"] if kind == "comfyui" else st["a1111_url"])
    try:
        return jsonify(localgen.ping(kind, url))
    except Exception as e:
        return jsonify({"ok": False, "kind": kind, "url": url,
                        "error": f"{type(e).__name__}: {e}"[:300]})


@app.get("/api/local/models")
def api_local_models():
    st = load_settings()["local"]
    node = request.args.get("node", "CheckpointLoaderSimple")
    try:
        if request.args.get("kind") == "a1111":
            return jsonify(localgen.A1111(st["a1111_url"]).ping().get("models", []))
        return jsonify(localgen.Comfy(st["comfy_url"]).models(node))
    except Exception as e:
        return jsonify({"error": str(e)[:200]})


@app.get("/api/local/workflows")
def api_local_workflows():
    return jsonify(localgen.list_workflows())


@app.post("/api/local/workflows")
def api_local_workflow_add():
    f = request.files.get("file")
    if f:
        name = localgen.save_workflow(f.filename, f.read().decode("utf-8"))
    else:
        body = request.get_json(force=True, silent=True) or {}
        if not body.get("content"):
            abort(400, "沒有內容")
        try:
            name = localgen.save_workflow(body.get("name") or "workflow.json", body["content"])
        except json.JSONDecodeError as e:
            abort(400, f"不是合法 JSON：{e}")
    return jsonify({"name": name, "workflows": localgen.list_workflows()})


@app.delete("/api/local/workflows/<name>")
def api_local_workflow_del(name):
    path = os.path.join(localgen.WORKFLOW_DIR, os.path.basename(name))
    if os.path.exists(path):
        os.remove(path)
    return jsonify({"ok": True, "workflows": localgen.list_workflows()})


# ---------------------------------------------------------------- 專案 CRUD

@app.get("/api/projects")
def api_projects():
    out = []
    for pid in os.listdir(PROJECTS):
        pj = os.path.join(PROJECTS, pid, "project.json")
        if not os.path.exists(pj):
            continue
        try:
            with open(pj, encoding="utf-8") as f:
                p = json.load(f)
        except Exception:
            continue
        thumb = None
        for key in sorted(p.get("poses", {})):
            thumb = furl(pid, p["poses"][key]["file"], False)
            break
        if not thumb and p.get("source", {}).get("green"):
            thumb = furl(pid, p["source"]["green"], False)
        out.append({"id": pid, "name": p.get("name", pid), "updated": p.get("updated", 0),
                    "clips": len([c for c in p.get("clips", {}).values() if c.get("frames")]),
                    "poses": len(p.get("poses", {})), "spend": p.get("spend", 0),
                    "thumb": thumb})
    out.sort(key=lambda x: -x["updated"])
    return jsonify(out)


@app.post("/api/projects")
def api_create_project():
    body = request.get_json(force=True, silent=True) or {}
    name = body.get("name") or "未命名角色"
    pid = f"{slug_ascii(name)}_{uuid.uuid4().hex[:6]}"
    p = {"id": pid, "name": name, "character": body.get("character", ""),
         "created": now(), "updated": now(), "source": {}, "poses": {},
         "clips": {}, "candidates": [], "spend": 0.0,
         "pack": {"frame_w": 240, "outline_dark": 0.32, "outline_px": 0, "pad": 6},
         "export_dir": ""}
    os.makedirs(os.path.join(PROJECTS, pid), exist_ok=True)
    save_project(p)
    return jsonify(p)


@app.get("/api/projects/<pid>")
def api_get_project(pid):
    return jsonify(load_project(pid))


@app.patch("/api/projects/<pid>")
def api_patch_project(pid):
    p = load_project(pid)
    body = request.get_json(force=True, silent=True) or {}
    for k in ("name", "character", "neg", "facing", "pack", "export_dir", "defaults",
              "char_combo"):
        if k in body:
            p[k] = body[k]
    return jsonify(save_project(p))


@app.delete("/api/projects/<pid>")
def api_delete_project(pid):
    shutil.rmtree(pdir(pid), ignore_errors=True)
    return jsonify({"ok": True})


@app.post("/api/projects/<pid>/duplicate")
def api_duplicate(pid):
    p = load_project(pid)
    new_id = f"{slug_ascii(p['name'])}_{uuid.uuid4().hex[:6]}"
    shutil.copytree(pdir(pid), os.path.join(PROJECTS, new_id))
    p["id"] = new_id
    p["name"] = p["name"] + " 複本"
    p["created"] = now()
    save_project(p)
    return jsonify(p)


# ---------------------------------------------------------------- 上傳

def _save_upload(dest_dir, file_storage, stem):
    os.makedirs(dest_dir, exist_ok=True)
    ext = os.path.splitext(file_storage.filename or "")[1].lower() or ".png"
    if ext not in (".png", ".jpg", ".jpeg", ".webp"):
        ext = ".png"
    path = os.path.join(dest_dir, stem + ext)
    file_storage.save(path)
    return path


def _make_green(pid, path):
    """把一張立繪整理成 {file, green, green_ratio}：本來就是綠幕就沿用，否則 flood-fill。"""
    green = os.path.join(pdir(pid), "source", "char_green.png")
    ratio = pipeline.green_ratio(path)
    if ratio > 0.25:                      # 本來就是綠幕，直接沿用
        img = pipeline.imread(path)
        if img is not None and img.ndim == 3 and img.shape[2] == 4:
            bgr = img[:, :, :3].copy()
            bgr[img[:, :, 3] < 128] = pipeline.GREEN_BGR
            pipeline.imwrite(green, bgr)
        else:
            shutil.copyfile(path, green)
    else:
        pipeline.greenify(path, green)
    return {"file": rel(pid, path), "green": rel(pid, green), "green_ratio": round(ratio, 3)}


# 立繪只能來自「角色生成」的候選圖（/char_pick）——外部圖片上傳的入口已經拿掉了。


@app.post("/api/projects/<pid>/source/regreen")
def api_regreen(pid):
    """用不同容差重跑 flood-fill 轉綠幕（頭髮被吃掉/背景沒清乾淨時調）。"""
    p = load_project(pid)
    body = request.get_json(force=True, silent=True) or {}
    src = p.get("source", {}).get("file")
    if not src:
        abort(400, "還沒上傳立繪")
    raw = os.path.join(pdir(pid), src)
    green = os.path.join(pdir(pid), "source", "char_green.png")
    pipeline.greenify(raw, green, tol=int(body.get("tol", 26)))
    p["source"]["green"] = rel(pid, green)
    p["source"]["green_ratio"] = round(pipeline.green_ratio(green), 3)
    p["source"]["tol"] = int(body.get("tol", 26))
    save_project(p)
    return jsonify(p["source"])


@app.post("/api/projects/<pid>/poseref")
def api_upload_poseref(pid):
    """使用者自備的參考姿勢圖（存在專案內，可重複使用）。"""
    p = load_project(pid)
    f = request.files.get("file")
    if not f:
        abort(400, "no file")
    stem = slug_ascii(os.path.splitext(f.filename or "ref")[0], "ref") + "_" + uuid.uuid4().hex[:4]
    path = _save_upload(os.path.join(pdir(pid), "refs"), f, stem)
    p.setdefault("refs", []).append({"file": rel(pid, path), "label": stem})
    save_project(p)
    return jsonify({"file": rel(pid, path), "url": furl(pid, rel(pid, path))})


@app.post("/api/projects/<pid>/pose_upload")
def api_pose_upload(pid):
    """直接上傳一張已經做好的姿勢立繪，跳過 AI 生成。"""
    p = load_project(pid)
    f = request.files.get("file")
    key = slug_ascii(request.form.get("key") or "pose")
    if not f:
        abort(400, "no file")
    raw = _save_upload(os.path.join(pdir(pid), "poses"), f, key + "_raw")
    green = os.path.join(pdir(pid), "poses", key + ".png")
    if pipeline.green_ratio(raw) > 0.25:
        shutil.copyfile(raw, green)
    else:
        pipeline.greenify(raw, green)
    p.setdefault("poses", {})[key] = {"file": rel(pid, green), "label": key,
                                      "created": now(), "source": "upload"}
    save_project(p)
    return jsonify(p["poses"][key])


# ---------------------------------------------------------------- 圖片生成共用核心
# 姿勢重繪與角色生成走同一套：n 張候選、雲端平行／本機序列、進度掛在 job["items"]。

def _abs_ref(pid, r):
    """把前端傳來的參考圖路徑轉成絕對路徑（可能是專案內的，也可能是共用姿勢庫）。"""
    if r.startswith("/poselib/"):
        return os.path.join(POSELIB, r.split("/poselib/", 1)[1])
    return os.path.join(pdir(pid), r)


def _image_payload(model, prompt, neg, uris, opts):
    """依模型的 input schema 組 payload；抓不到 schema 就退回舊的固定欄位。"""
    sch = None
    try:
        sch = falcatalog.schema(model["id"])
    except Exception:
        sch = None
    payload = falcatalog.build_payload(sch, prompt=prompt, negative=neg,
                                       images=uris, opts=opts)
    if payload is None:
        payload = {"prompt": prompt, "num_images": 1}
        if uris:
            payload["image_urls"] = uris
        if opts.get("seed") is not None:
            payload["seed"] = opts["seed"]
        payload.update(model.get("extra") or {})
        return payload
    # 內建模型的 extra 是實測過的偏好值，schema 有這格才套（不覆蓋使用者選的）
    fields = sch.get("fields") or {}
    for k, v in (model.get("extra") or {}).items():
        if k in fields and k not in payload:
            payload[k] = v
    return payload


def _image_batch(job, pid, model, n, prompt, neg, img_paths, out_dir, opts, local_over=None):
    """跑 n 張圖，回傳成功的 index 清單。job["items"] 會即時更新給前端輪詢。"""
    if model.get("local"):
        cfg = local_cfg("image")
        cfg.update({k: v for k, v in (local_over or {}).items() if v not in (None, "")})
        if not img_paths:
            raise RuntimeError("本機模型是圖生圖，至少要有一張輸入圖")
        made = []
        for i in range(n):
            job["items"][i]["status"] = "running"
            job["message"] = f"本地生成 {i + 1}/{n}"
            dest = os.path.join(out_dir, f"{i}.png")
            try:
                seed = opts.get("seed")
                localgen.gen_images(
                    cfg, img_paths, prompt, neg, [dest],
                    params={**cfg, "seed": (int(seed) + i) if seed else None},
                    on_status=lambda m: job.__setitem__("message", m))
                job["items"][i].update(status="done", url=furl(pid, rel(pid, dest)))
                made.append(i)
            except Exception as e:
                job["items"][i].update(status="error", error=str(e)[:300])
            job["progress"] = int((i + 1) / n * 100)
        return made, cfg

    uris = [falclient.data_uri(x) for x in img_paths]
    base_seed = int(opts.get("seed") or int(now())) % 10 ** 8
    done = [0]
    lock = threading.Lock()

    def one(i):
        try:
            job["items"][i]["status"] = "running"
            payload = _image_payload(model, prompt, neg, uris,
                                     {**opts, "seed": base_seed + i * 7919, "num_images": 1})
            result = falclient.run(model["id"], payload, poll=1.5, timeout=600)
            url = falclient.image_urls(result)[0]
            dest = os.path.join(out_dir, f"{i}.png")
            falclient.download(url, dest)
            job["items"][i].update(status="done", url=furl(pid, rel(pid, dest)))
        except Exception as e:
            job["items"][i].update(status="error", error=str(e)[:200])
        finally:
            with lock:
                done[0] += 1
                job["progress"] = int(done[0] / n * 100)
                job["message"] = f"已完成 {done[0]}/{n} 張"

    threads = [threading.Thread(target=one, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
        time.sleep(0.3)
    for t in threads:
        t.join()
    return [it["index"] for it in job["items"] if it["status"] == "done"], None


# ---------------------------------------------------------------- 姿勢圖生成（回傳 5 張候選）

@app.post("/api/projects/<pid>/pose_gen")
def api_pose_gen(pid):
    p = load_project(pid)
    body = request.get_json(force=True, silent=True) or {}
    model = resolve_model("image", body.get("model", "nano-banana"))
    if not model:
        abort(400, "unknown model")
    n = int(body.get("n", 5))
    prompt = presets.fill(body.get("prompt", ""), p.get("character"))
    neg = presets.fill(body.get("neg", ""), p.get("character"))
    if not prompt.strip():
        abort(400, "prompt is empty")

    # 第一張是角色原圖（模型照抄它的設計），後面接參考姿勢圖。
    # base 可以指定成別張（例如拿已完成的 crouch 當底再改成 crouch_attack）。
    src = body.get("base") or p.get("source", {}).get("green") or p.get("source", {}).get("file")
    if not src:
        abort(400, "請先在「角色生成」上傳或生成立繪")
    if not os.path.exists(os.path.join(pdir(pid), src)):
        abort(400, f"找不到角色原圖 {src}")
    img_paths = [os.path.join(pdir(pid), src)]
    img_paths += [_abs_ref(pid, r) for r in body.get("refs", [])]
    img_paths = [x for x in img_paths if os.path.exists(x)]

    cand_id = uuid.uuid4().hex[:8]
    out_dir = os.path.join(pdir(pid), "candidates", cand_id)
    os.makedirs(out_dir, exist_ok=True)
    job = new_job("pose_gen", pid, f"生成 {n} 張姿勢候選")
    job["items"] = [{"index": i, "status": "queued", "url": None} for i in range(n)]
    opts = {"seed": body.get("seed"), "aspect_ratio": body.get("aspect_ratio"),
            "output_format": "png"}

    def work(job):
        made, cfg = _image_batch(job, pid, model, n, prompt, neg, img_paths, out_dir, opts,
                                 body.get("local"))
        with LOCK:
            proj = load_project(pid)
            entry = {"id": cand_id, "created": now(), "prompt": prompt,
                     "model": model["key"], "refs": body.get("refs", []),
                     "base": src, "pose_key": body.get("pose_key", ""),
                     "images": [rel(pid, os.path.join(out_dir, f"{i}.png")) for i in made],
                     "cost": 0.0 if model["local"] else round(model["price"] * len(made), 4)}
            if cfg:
                entry["local"] = {"kind": cfg.get("kind"), "workflow": cfg.get("workflow"),
                                  "model": cfg.get("model")}
            proj.setdefault("candidates", []).insert(0, entry)
            add_spend(proj, entry["cost"])
            save_project(proj)
        if not made:
            raise RuntimeError("全部失敗：" + str(job["items"][0].get("error", ""))[:300])
        return entry

    run_job(job, work)
    return jsonify({"job": job["id"]})


# ---------------------------------------------------------------- 角色立繪生成（第一步）

@app.post("/api/projects/<pid>/styleref")
def api_upload_styleref(pid):
    """風格參考圖：生成角色時一起送給模型，叫它照這個畫風畫。"""
    p = load_project(pid)
    f = request.files.get("file")
    if not f:
        abort(400, "no file")
    stem = slug_ascii(os.path.splitext(f.filename or "style")[0], "style") + "_" + uuid.uuid4().hex[:4]
    path = _save_upload(os.path.join(pdir(pid), "styles"), f, stem)
    entry = {"file": rel(pid, path), "label": os.path.splitext(f.filename or stem)[0][:24]}
    p.setdefault("style_refs", []).append(entry)
    save_project(p)
    return jsonify({**entry, "url": furl(pid, entry["file"])})


@app.delete("/api/projects/<pid>/styleref")
def api_delete_styleref(pid):
    p = load_project(pid)
    target = (request.get_json(force=True, silent=True) or {}).get("file")
    p["style_refs"] = [r for r in p.get("style_refs", []) if r["file"] != target]
    save_project(p)
    return jsonify({"ok": True, "style_refs": p["style_refs"]})


@app.post("/api/projects/<pid>/char_gen")
def api_char_gen(pid):
    """從提示詞（＋可選的風格參考圖）生角色立繪候選。挑一張就成為專案立繪。"""
    p = load_project(pid)
    body = request.get_json(force=True, silent=True) or {}
    model = resolve_model("image", body.get("model", "nano-banana"))
    if not model:
        abort(400, "unknown model")
    n = int(body.get("n", 4))
    prompt = presets.fill(body.get("prompt", ""), p.get("character"))
    neg = presets.fill(body.get("neg", ""), p.get("character"))
    if not prompt.strip():
        abort(400, "prompt is empty")
    img_paths = [x for x in (_abs_ref(pid, r) for r in body.get("refs", [])) if os.path.exists(x)]

    if not model["local"] and not img_paths:
        # 純文生圖：模型若硬性要圖，先擋下來講清楚，不要送出去吃 422
        try:
            if falcatalog.needs_image(falcatalog.schema(model["id"])):
                abort(400, f"「{model['label']}」一定要有輸入圖——請加一張風格參考，或改用文生圖模型")
        except FileNotFoundError:
            pass

    cand_id = uuid.uuid4().hex[:8]
    out_dir = os.path.join(pdir(pid), "char_candidates", cand_id)
    os.makedirs(out_dir, exist_ok=True)
    job = new_job("char_gen", pid, f"生成 {n} 張角色立繪候選")
    job["items"] = [{"index": i, "status": "queued", "url": None} for i in range(n)]
    opts = {"seed": body.get("seed"), "aspect_ratio": body.get("aspect_ratio"),
            "output_format": "png"}

    def work(job):
        made, cfg = _image_batch(job, pid, model, n, prompt, neg, img_paths, out_dir, opts,
                                 body.get("local"))
        with LOCK:
            proj = load_project(pid)
            entry = {"id": cand_id, "created": now(), "prompt": prompt, "neg": neg,
                     "model": model["key"], "refs": body.get("refs", []),
                     "preset": body.get("preset", ""),
                     "images": [rel(pid, os.path.join(out_dir, f"{i}.png")) for i in made],
                     "cost": 0.0 if model["local"] else round(model["price"] * len(made), 4)}
            proj.setdefault("char_cands", []).insert(0, entry)
            add_spend(proj, entry["cost"])
            save_project(proj)
        if not made:
            raise RuntimeError("全部失敗：" + str(job["items"][0].get("error", ""))[:300])
        return entry

    run_job(job, work)
    return jsonify({"job": job["id"]})


@app.post("/api/projects/<pid>/char_pick")
def api_char_pick(pid):
    """把生成的候選設成專案的角色立繪（等同上傳一張，一樣自動轉綠幕）。"""
    p = load_project(pid)
    body = request.get_json(force=True, silent=True) or {}
    src_rel = body.get("image")
    src = os.path.join(pdir(pid), src_rel or "")
    if not src_rel or not os.path.exists(src):
        abort(400, "image not found")
    dest = os.path.join(pdir(pid), "source", "char.png")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.copyfile(src, dest)
    p["source"] = _make_green(pid, dest)
    p["source"]["from"] = src_rel
    save_project(p)
    return jsonify(p["source"])


@app.post("/api/projects/<pid>/inpaint")
def api_inpaint(pid):
    """局部替換：只重畫遮罩塗到的區域，其餘像素原封不動。"""
    p = load_project(pid)
    body = request.get_json(force=True, silent=True) or {}
    base_rel = body.get("base")
    base = os.path.join(pdir(pid), base_rel or "")
    if not base_rel or not os.path.exists(base):
        abort(400, "沒有底圖")
    mask_data = body.get("mask") or ""
    if "," not in mask_data:
        abort(400, "沒有遮罩")
    prompt = presets.fill(body.get("prompt", ""), p.get("character"))
    neg = presets.fill(body.get("neg", ""), p.get("character"))
    n = int(body.get("n", 2))

    cand_id = uuid.uuid4().hex[:8]
    out_dir = os.path.join(pdir(pid), "candidates", cand_id)
    os.makedirs(out_dir, exist_ok=True)
    mask_path = os.path.join(out_dir, "mask.png")
    with open(mask_path, "wb") as f:
        f.write(base64.b64decode(mask_data.split(",", 1)[1]))

    job = new_job("inpaint", pid, f"局部替換 {n} 張")
    job["items"] = [{"index": i, "status": "queued", "url": None} for i in range(n)]

    def work(job):
        cfg = local_cfg("image")
        cfg.update({k: v for k, v in (body.get("local") or {}).items() if v not in (None, "")})
        cfg.setdefault("workflow", "comfy_inpaint.json")
        made = []
        for i in range(n):
            job["items"][i]["status"] = "running"
            dest = os.path.join(out_dir, f"{i}.png")
            try:
                localgen.gen_images(
                    cfg, [base], prompt, neg, [dest], mask_path=mask_path,
                    params={**cfg, "seed": (int(body["seed"]) + i) if body.get("seed") else None},
                    on_status=lambda m: job.__setitem__("message", m))
                job["items"][i].update(status="done", url=furl(pid, rel(pid, dest)))
                made.append(i)
            except Exception as e:
                job["items"][i].update(status="error", error=str(e)[:300])
            job["progress"] = int((i + 1) / n * 100)
        with LOCK:
            proj = load_project(pid)
            entry = {"id": cand_id, "created": now(), "prompt": prompt, "model": "local-inpaint",
                     "refs": [base_rel], "pose_key": body.get("pose_key", ""),
                     "images": [rel(pid, os.path.join(out_dir, f"{i}.png")) for i in made],
                     "cost": 0.0, "mask": rel(pid, mask_path), "base": base_rel}
            proj.setdefault("candidates", []).insert(0, entry)
            save_project(proj)
        if not made:
            raise RuntimeError(str(job["items"][0].get("error", "局部替換失敗"))[:300])
        return entry

    run_job(job, work)
    return jsonify({"job": job["id"]})


@app.post("/api/projects/<pid>/pose_pick")
def api_pose_pick(pid):
    """從候選裡挑一張，存成專案的姿勢圖（自動轉綠幕）。"""
    p = load_project(pid)
    body = request.get_json(force=True, silent=True) or {}
    src_rel = body.get("image")
    key = slug_ascii(body.get("key") or "pose")
    src = os.path.join(pdir(pid), src_rel or "")
    if not src_rel or not os.path.exists(src):
        abort(400, "image not found")
    green = os.path.join(pdir(pid), "poses", key + ".png")
    os.makedirs(os.path.dirname(green), exist_ok=True)   # 沒生過姿勢的新專案還沒有這個資料夾
    if pipeline.green_ratio(src) > 0.25:
        shutil.copyfile(src, green)
    else:
        pipeline.greenify(src, green)
    p.setdefault("poses", {})[key] = {"file": rel(pid, green), "label": body.get("label") or key,
                                      "created": now(), "source": src_rel}
    save_project(p)
    return jsonify(p["poses"][key])


@app.delete("/api/projects/<pid>/poses/<key>")
def api_pose_delete(pid, key):
    p = load_project(pid)
    p.get("poses", {}).pop(key, None)
    save_project(p)
    return jsonify({"ok": True})


# ---------------------------------------------------------------- 動作 clip

@app.put("/api/projects/<pid>/clips/<clip>")
def api_put_clip(pid, clip):
    p = load_project(pid)
    body = request.get_json(force=True, silent=True) or {}
    c = p.setdefault("clips", {}).setdefault(clip, {"name": clip, "takes": [], "frames": []})
    for k in ("label", "pose", "model", "prompt", "neg", "duration", "resolution",
              "fps", "loop", "align", "scale", "xalign", "despeckle", "frames",
              "selected_take", "preset", "clear_warm"):
        if k in body:
            c[k] = body[k]
    save_project(p)
    return jsonify(c)


@app.delete("/api/projects/<pid>/clips/<clip>")
def api_delete_clip(pid, clip):
    p = load_project(pid)
    p.get("clips", {}).pop(clip, None)
    save_project(p)
    return jsonify({"ok": True})


@app.post("/api/projects/<pid>/clips/<clip>/generate")
def api_clip_generate(pid, clip):
    p = load_project(pid)
    body = request.get_json(force=True, silent=True) or {}
    c = p.setdefault("clips", {}).setdefault(clip, {"name": clip, "takes": [], "frames": []})
    for k in ("pose", "model", "prompt", "neg", "duration", "resolution", "fps",
              "loop", "align", "preset", "label"):
        if k in body:
            c[k] = body[k]
    model_key = c.get("model", "kling25")
    model = resolve_model("video", model_key)
    if not model:
        abort(400, "unknown video model")
    is_local = model["local"]
    pose_key = c.get("pose")
    pose = p.get("poses", {}).get(pose_key)
    if not pose:
        abort(400, f"姿勢圖 {pose_key} 還沒建立")
    img_path = os.path.join(pdir(pid), pose["file"])
    prompt = presets.fill(c.get("prompt", ""), p.get("character"))
    neg = presets.fill(c.get("neg", ""), p.get("character"))
    take_id = f"{int(now())}_{uuid.uuid4().hex[:4]}"
    save_project(p)

    job = new_job("video_gen", pid, f"{clip} 影片生成（{model['label']}）")

    def finish_take(vpath, meta_extra):
        """下載/產出影片後：抽幀 → 存成 take。雲端與本地共用。"""
        job["message"] = "抽幀去背中…"
        job["progress"] = 80
        fdir = os.path.join(pdir(pid), "frames", f"{clip}_{take_id}")

        def prog(i, total):
            job["progress"] = 80 + int(18 * i / max(1, total))
            job["message"] = f"抽幀 {i}/{total}"

        st = load_settings()["extract"]
        meta = pipeline.extract_take(
            vpath, fdir, preview_w=int(st.get("preview_w", 320)),
            key_opts={k: st[k] for k in ("hue_lo", "hue_hi", "dom", "sat", "val") if k in st},
            progress=prog)
        take = {"id": take_id, "video": rel(pid, vpath), "frames_dir": rel(pid, fdir),
                "created": now(), "pose": pose_key, "prompt": prompt, "neg": neg,
                "count": meta["count"], "preview_w": meta["preview_w"],
                "preview_h": meta["preview_h"], "src_w": meta["src_w"],
                "src_h": meta["src_h"], "src_fps": meta["src_fps"],
                "stats": meta["stats"],
                "segments": pipeline.suggest_segments(meta["stats"])}
        take.update(meta_extra)
        with LOCK:
            proj = load_project(pid)
            cc = proj.setdefault("clips", {}).setdefault(clip, {"name": clip, "takes": [], "frames": []})
            cc.setdefault("takes", []).insert(0, take)
            cc["selected_take"] = take_id
            if not cc.get("frames"):
                step = max(1, meta["count"] // 12)
                cc["frames"] = list(range(0, meta["count"], step))[:12]
            add_spend(proj, take.get("cost", 0))
            save_project(proj)
        return {"take": take_id, "count": meta["count"], "clip": clip}

    def work_local(job):
        cfg = local_cfg("video")
        cfg.update({k: v for k, v in (body.get("local") or {}).items() if v not in (None, "")})
        if not cfg.get("workflow"):
            raise RuntimeError("還沒指定影片 workflow——到「設定」選一份 ComfyUI workflow")
        vpath = os.path.join(pdir(pid), "videos", f"{clip}_{take_id}.mp4")
        os.makedirs(os.path.dirname(vpath), exist_ok=True)
        job["message"] = "本地生成中…"
        job["progress"] = 20
        out = localgen.gen_video(cfg, img_path, prompt, neg, vpath, params=cfg,
                                 on_status=lambda m: job.__setitem__("message", m))
        # ComfyUI 可能吐 webp/gif，副檔名跟著改
        if out != vpath:
            vpath = out
        return finish_take(vpath, {"model": model_key, "cost": 0,
                                   "local": {"workflow": cfg.get("workflow"),
                                             "model": cfg.get("model")}})

    def work(job):
        uri = falclient.data_uri(img_path)
        opts = {"duration": str(c.get("duration") or "5"),
                "resolution": c.get("resolution") or "720p",
                "aspect_ratio": body.get("aspect_ratio") or c.get("aspect_ratio") or "1:1",
                "cfg_scale": body.get("cfg_scale", c.get("cfg_scale", 0.5)),
                "camera_fixed": body.get("camera_fixed", c.get("camera_fixed", True)),
                "seed": body.get("seed") or c.get("seed")}
        try:
            sch = falcatalog.schema(model["id"])
        except Exception:
            sch = None
        payload = falcatalog.build_payload(
            sch, prompt=prompt, negative=neg or presets.NEG_COMMON, images=[uri], opts=opts)
        if payload is None:      # 線上 schema 拿不到 → 退回原本寫死的兩種組法
            payload = {"prompt": prompt, "image_url": uri, "duration": opts["duration"]}
            if model_key.startswith("kling"):
                payload["negative_prompt"] = neg or presets.NEG_COMMON
                payload["cfg_scale"] = float(opts["cfg_scale"])
            else:
                payload["resolution"] = opts["resolution"]
                payload["camera_fixed"] = True
                payload["aspect_ratio"] = opts["aspect_ratio"]

        def status(s, qpos, rid):
            job["message"] = {"IN_QUEUE": f"排隊中… {('#' + str(qpos)) if qpos is not None else ''}",
                              "IN_PROGRESS": "生成中…",
                              "COMPLETED": "下載中…"}.get(s, s or "")
            job["progress"] = {"IN_QUEUE": 10, "IN_PROGRESS": 45, "COMPLETED": 70}.get(s, 20)
            job["request_id"] = rid

        result = falclient.run(model["id"], payload, on_status=status, poll=2.5, timeout=1800)
        url = falclient.first_video_url(result)
        vpath = os.path.join(pdir(pid), "videos", f"{clip}_{take_id}.mp4")
        os.makedirs(os.path.dirname(vpath), exist_ok=True)
        falclient.download(url, vpath)
        return finish_take(vpath, {"model": model_key, "cost": model["price"]})

    run_job(job, work_local if is_local else work)
    return jsonify({"job": job["id"]})


@app.post("/api/projects/<pid>/clips/<clip>/import")
def api_clip_import(pid, clip):
    """匯入已有的 mp4（例如以前用 tools/gen_*.py 生成的），直接進挑幀流程。"""
    p = load_project(pid)
    f = request.files.get("file")
    if not f:
        abort(400, "no file")
    take_id = f"{int(now())}_{uuid.uuid4().hex[:4]}"
    vpath = os.path.join(pdir(pid), "videos", f"{clip}_{take_id}.mp4")
    os.makedirs(os.path.dirname(vpath), exist_ok=True)
    f.save(vpath)
    job = new_job("import", pid, f"{clip} 匯入抽幀")

    def work(job):
        fdir = os.path.join(pdir(pid), "frames", f"{clip}_{take_id}")

        def prog(i, total):
            job["progress"] = int(100 * i / max(1, total))
            job["message"] = f"抽幀 {i}/{total}"

        st = load_settings()["extract"]
        meta = pipeline.extract_take(
            vpath, fdir, preview_w=int(st.get("preview_w", 320)),
            key_opts={k: st[k] for k in ("hue_lo", "hue_hi", "dom", "sat", "val") if k in st},
            progress=prog)
        take = {"id": take_id, "video": rel(pid, vpath), "frames_dir": rel(pid, fdir),
                "created": now(), "model": "import", "prompt": "(匯入)", "neg": "",
                "pose": "", "cost": 0, "count": meta["count"],
                "preview_w": meta["preview_w"], "preview_h": meta["preview_h"],
                "src_w": meta["src_w"], "src_h": meta["src_h"], "src_fps": meta["src_fps"],
                "stats": meta["stats"], "segments": pipeline.suggest_segments(meta["stats"])}
        with LOCK:
            proj = load_project(pid)
            cc = proj.setdefault("clips", {}).setdefault(clip, {"name": clip, "takes": [], "frames": []})
            cc.setdefault("takes", []).insert(0, take)
            cc["selected_take"] = take_id
            save_project(proj)
        return {"take": take_id, "count": meta["count"]}

    run_job(job, work)
    return jsonify({"job": job["id"]})


@app.post("/api/projects/<pid>/clips/<clip>/split")
def api_clip_split(pid, clip, ):
    """把一支影片切成多個動作（跳躍 → rise / fall / land），共用同一個 take。"""
    p = load_project(pid)
    body = request.get_json(force=True, silent=True) or {}
    c = p.get("clips", {}).get(clip) or abort(404)
    take = next((t for t in c.get("takes", []) if t["id"] == body.get("take")),
                (c.get("takes") or [None])[0])
    if not take:
        abort(400, "沒有可用的 take")
    made = []
    for seg in body.get("segments", []):
        name = slug_ascii(seg.get("name"), "seg")
        shared = dict(take)
        shared["shared"] = True
        nc = p.setdefault("clips", {}).setdefault(name, {"name": name, "takes": [], "frames": []})
        nc.update({
            "label": seg.get("label") or f"{clip} 分段", "pose": c.get("pose"),
            "model": c.get("model"),
            "prompt": c.get("prompt"), "neg": c.get("neg"), "preset": c.get("preset"),
            "duration": c.get("duration"), "resolution": c.get("resolution"),
            "fps": seg.get("fps", c.get("fps", 12)), "loop": bool(seg.get("loop", False)),
            # 分段預設逐幀腳底對齊（跳躍那三段一定要），但呼叫端可以自己指定
            "align": bool(seg.get("align", True)),
            "xalign": seg.get("xalign") or ("first" if seg.get("align", True) else
                                            c.get("xalign", "median")),
            "scale": c.get("scale", 1),
            "frames": seg.get("frames", []), "selected_take": take["id"],
        })
        if not any(t["id"] == take["id"] for t in nc.get("takes", [])):
            nc.setdefault("takes", []).insert(0, shared)
        made.append(name)
    # 原 clip 的 take 也標記共用，避免刪除時把檔案砍掉害其他分段失效
    if made:
        take["shared"] = True
    save_project(p)
    return jsonify({"created": made})


@app.delete("/api/projects/<pid>/clips/<clip>/takes/<take_id>")
def api_delete_take(pid, clip, take_id):
    p = load_project(pid)
    c = p.get("clips", {}).get(clip)
    if not c:
        abort(404)
    takes = c.get("takes", [])
    keep = [t for t in takes if t["id"] != take_id]
    gone = [t for t in takes if t["id"] == take_id]
    c["takes"] = keep
    if c.get("selected_take") == take_id:
        c["selected_take"] = keep[0]["id"] if keep else None
    for t in gone:
        still_used = any(tt["id"] == take_id
                         for name, cc in p.get("clips", {}).items() if name != clip
                         for tt in cc.get("takes", []))
        if t.get("shared") or still_used:
            continue          # 被跳躍分段共用，檔案留著
        shutil.rmtree(os.path.join(pdir(pid), t.get("frames_dir", "")), ignore_errors=True)
        vp = os.path.join(pdir(pid), t.get("video", ""))
        if os.path.exists(vp):
            os.remove(vp)
    save_project(p)
    return jsonify({"ok": True})


@app.post("/api/projects/<pid>/clips/<clip>/takes/<take_id>/reextract")
def api_reextract(pid, clip, take_id):
    """用新的色鍵參數重新抽幀（影片不用重生成）。"""
    p = load_project(pid)
    c = p.get("clips", {}).get(clip) or abort(404)
    take = next((t for t in c.get("takes", []) if t["id"] == take_id), None)
    if not take:
        abort(404, "take not found")
    body = request.get_json(force=True, silent=True) or {}
    key_opts = {k: int(body[k]) for k in ("hue_lo", "hue_hi", "dom", "sat", "val") if k in body}
    vpath = os.path.join(pdir(pid), take["video"])
    fdir = os.path.join(pdir(pid), take["frames_dir"])
    job = new_job("reextract", pid, f"{clip} 重新抽幀")

    def work(job):
        def prog(i, total):
            job["progress"] = int(100 * i / max(1, total))
            job["message"] = f"抽幀 {i}/{total}"

        st = load_settings()["extract"]
        meta = pipeline.extract_take(vpath, fdir,
                                     preview_w=int(body.get("preview_w") or st.get("preview_w", 320)),
                                     key_opts=key_opts, progress=prog)
        with LOCK:
            proj = load_project(pid)
            for t in proj["clips"][clip]["takes"]:
                if t["id"] == take_id:
                    t.update({"count": meta["count"], "preview_w": meta["preview_w"],
                              "preview_h": meta["preview_h"], "stats": meta["stats"],
                              "key_opts": key_opts,
                              "segments": pipeline.suggest_segments(meta["stats"])})
            save_project(proj)
        return {"count": meta["count"]}

    run_job(job, work)
    return jsonify({"job": job["id"]})


@app.get("/api/projects/<pid>/clips/<clip>/loop")
def api_loop_suggest(pid, clip):
    p = load_project(pid)
    c = p.get("clips", {}).get(clip) or abort(404)
    take = next((t for t in c.get("takes", []) if t["id"] == (request.args.get("take") or c.get("selected_take"))), None)
    if not take:
        abort(404, "take not found")
    start = int(request.args.get("start", 0))
    fdir = os.path.join(pdir(pid), take["frames_dir"])
    return jsonify(pipeline.suggest_loop(fdir, take["count"], start))


# ---------------------------------------------------------------- 打包輸出

@app.post("/api/projects/<pid>/pack")
def api_pack(pid):
    p = load_project(pid)
    body = request.get_json(force=True, silent=True) or {}
    names = body.get("clips") or [k for k, c in p.get("clips", {}).items() if c.get("frames")]
    packcfg = dict(p.get("pack", {}))
    packcfg.update({k: v for k, v in body.items() if k in ("frame_w", "outline_dark", "outline_px", "pad")})
    p["pack"] = packcfg
    save_project(p)

    specs = []
    for name in names:
        c = p["clips"].get(name)
        if not c or not c.get("frames"):
            continue
        take = next((t for t in c.get("takes", []) if t["id"] == c.get("selected_take")),
                    (c.get("takes") or [None])[0])
        if not take:
            continue
        specs.append({
            "name": name, "video": os.path.join(pdir(pid), take["video"]),
            "frames": c["frames"], "fps": c.get("fps", 12), "loop": bool(c.get("loop", True)),
            "align": bool(c.get("align")), "scale": float(c.get("scale") or 1.0),
            "xalign": c.get("xalign", "median"), "despeckle": bool(c.get("despeckle")),
            "clear_warm": c.get("clear_warm"),
        })
    if not specs:
        abort(400, "沒有可打包的動作（請先挑幀）")

    job = new_job("pack", pid, f"打包 {len(specs)} 個動作")

    def work(job):
        out_dir = os.path.join(pdir(pid), "out")
        shutil.rmtree(out_dir, ignore_errors=True)
        done = [0]

        def prog(name, n):
            done[0] += 1
            job["progress"] = int(done[0] / len(specs) * 80)
            job["message"] = f"處理 {name}（{n} 幀）"

        meta = pipeline.pack(specs, out_dir,
                             frame_w=int(packcfg.get("frame_w", 240)),
                             pad=int(packcfg.get("pad", 6)),
                             outline_dark=float(packcfg.get("outline_dark", 0.32)),
                             outline_px=int(packcfg.get("outline_px", 0)),
                             progress=prog)
        job["progress"] = 88
        with open(os.path.join(out_dir, "anims.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=1, ensure_ascii=False)

        zpath = os.path.join(out_dir, "sprites.zip")
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            for fn in sorted(os.listdir(out_dir)):
                if fn != "sprites.zip":
                    z.write(os.path.join(out_dir, fn), fn)
        job["message"] = "完成"
        with LOCK:
            proj = load_project(pid)
            proj["last_pack"] = {"at": now(), "meta": meta,
                                 "clips": [s["name"] for s in specs]}
            save_project(proj)
        return {"meta": meta, "zip": furl(pid, "out/sprites.zip")}

    run_job(job, work)
    return jsonify({"job": job["id"]})


@app.get("/api/projects/<pid>/pack/download")
def api_pack_download(pid):
    z = os.path.join(pdir(pid), "out", "sprites.zip")
    if not os.path.exists(z):
        abort(404, "還沒打包")
    p = load_project(pid)
    return send_file(z, as_attachment=True, download_name=f"{slug_ascii(p['name'])}_sprites.zip")


@app.post("/api/projects/<pid>/export")
def api_export(pid):
    """把打包結果複製到遊戲的 assets 資料夾。"""
    p = load_project(pid)
    body = request.get_json(force=True, silent=True) or {}
    dest = (body.get("dir") or p.get("export_dir") or "").strip()
    if not dest:
        abort(400, "請填目標資料夾")
    out_dir = os.path.join(pdir(pid), "out")
    if not os.path.isdir(out_dir):
        abort(400, "還沒打包")
    os.makedirs(dest, exist_ok=True)
    copied = []
    for fn in sorted(os.listdir(out_dir)):
        if fn.endswith(".zip"):
            continue
        shutil.copyfile(os.path.join(out_dir, fn), os.path.join(dest, fn))
        copied.append(fn)
    p["export_dir"] = dest
    save_project(p)
    return jsonify({"copied": copied, "dir": dest})


# ---------------------------------------------------------------- 參考素材（既有遊戲）

@app.get("/game/<path:sub>")
def game_file(sub):
    """唯讀提供上線素材，給前端做並排／疊圖比對。"""
    return send_from_directory(gamespec.GAME_DIR, sub)


@app.get("/api/spec")
def api_spec():
    return jsonify(gamespec.load_spec(force=request.args.get("reload") == "1"))




@app.post("/api/projects/<pid>/scaffold")
def api_scaffold(pid):
    """一鍵補齊上線必要動作：缺的用預設建立，已存在的不動。"""
    p = load_project(pid)
    spec = gamespec.load_spec()
    poses = p.get("poses", {})
    made = []
    for name in spec["required"]:
        if name in p.get("clips", {}):
            continue
        preset = presets.anim_preset(name) or presets.anim_preset("custom")
        norm = spec["norms"].get(name, {})
        # 保留預設建議的姿勢（即使還沒做），驗收才看得出「這個動作缺哪張姿勢圖」
        pose = preset["pose"]
        p.setdefault("clips", {})[name] = {
            "name": name, "label": preset["label"], "pose": pose,
            "model": preset["model"], "prompt": preset["prompt"], "neg": preset["neg"],
            "preset": preset["key"], "duration": "5" if preset["model"] == "kling25" else "4",
            "resolution": "720p", "scale": 1, "xalign": "first" if norm.get("align") else "median",
            "fps": norm.get("fps_common", preset["fps"]),
            "loop": norm.get("loop", preset["loop"]),
            "align": bool(norm.get("align", preset["align"])),
            "frames": [], "takes": [],
        }
        made.append(name)
    save_project(p)
    return jsonify({"created": made, "required": spec["required"]})



# ---------------------------------------------------------------- 任務查詢

@app.get("/api/jobs/<jid>")
def api_job(jid):
    j = JOBS.get(jid)
    if not j:
        abort(404)
    return jsonify(j)


@app.get("/api/jobs")
def api_jobs():
    pid = request.args.get("pid")
    out = [j for j in JOBS.values() if not pid or j["pid"] == pid]
    out.sort(key=lambda j: -j["created"])
    return jsonify(out[:30])


if __name__ == "__main__":
    print("Sprite Studio → http://127.0.0.1:8765")
    app.run(host="127.0.0.1", port=8765, threaded=True, debug=False)
