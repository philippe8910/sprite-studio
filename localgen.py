# -*- coding: utf-8 -*-
"""本地生成串接：ComfyUI 與 A1111/Forge。

ComfyUI 走 API 格式的 workflow JSON（在 ComfyUI 裡「匯出 (API)」那種），
我們只做佔位符替換，所以你用什麼模型、什麼節點都不影響——換模型＝換一份 workflow。

佔位符（字串完全等於它時會換成對應型別，夾在句子裡則做字串取代）：
  %prompt% %negative% %image% %image2% %seed% %steps% %cfg% %denoise%
  %width% %height% %frames% %fps% %model%

A1111 走 /sdapi/v1/img2img，只支援圖片。
"""
import base64
import json
import mimetypes
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

ROOT = os.path.dirname(os.path.abspath(__file__))
WORKFLOW_DIR = os.path.join(ROOT, "workflows")
os.makedirs(WORKFLOW_DIR, exist_ok=True)


class LocalError(RuntimeError):
    pass


def _req(url, data=None, headers=None, timeout=120, method=None):
    r = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return resp.read()


def _json(url, payload=None, timeout=120, method=None):
    data = json.dumps(payload).encode() if payload is not None else None
    head = {"Content-Type": "application/json"} if data else {}
    raw = _req(url, data, head, timeout, method)
    return json.loads(raw) if raw else None


# ---------------------------------------------------------------- workflow 檔案

def list_workflows():
    out = []
    for fn in sorted(os.listdir(WORKFLOW_DIR)):
        if not fn.endswith(".json"):
            continue
        path = os.path.join(WORKFLOW_DIR, fn)
        info = {"file": fn, "size": os.path.getsize(path), "kind": "image", "note": ""}
        try:
            with open(path, encoding="utf-8") as f:
                wf = json.load(f)
            meta = wf.get("_meta") if isinstance(wf, dict) else None
            if isinstance(meta, dict):
                info["kind"] = meta.get("kind", info["kind"])
                info["note"] = meta.get("note", "")
                info["label"] = meta.get("label")
            info["placeholders"] = sorted(set(find_placeholders(wf)))
            info["nodes"] = len([k for k in wf if not k.startswith("_")])
        except Exception as e:
            info["error"] = str(e)[:120]
        info.setdefault("label", os.path.splitext(fn)[0])
        out.append(info)
    return out


def load_workflow(name):
    path = os.path.join(WORKFLOW_DIR, os.path.basename(name))
    if not os.path.exists(path):
        raise LocalError(f"找不到 workflow：{name}")
    with open(path, encoding="utf-8") as f:
        wf = json.load(f)
    wf.pop("_meta", None)
    return wf


def save_workflow(name, content):
    name = os.path.basename(name)
    if not name.endswith(".json"):
        name += ".json"
    json.loads(content) if isinstance(content, str) else content   # 驗證
    path = os.path.join(WORKFLOW_DIR, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content if isinstance(content, str) else json.dumps(content, indent=1))
    return name


def find_placeholders(node):
    if isinstance(node, str):
        return [t for t in ("%prompt%", "%negative%", "%image%", "%image2%", "%mask%",
                            "%seed%", "%steps%", "%cfg%", "%denoise%", "%blend%",
                            "%width%", "%height%", "%frames%", "%fps%",
                            "%model%", "%vae%") if t in node]
    if isinstance(node, dict):
        return [t for v in node.values() for t in find_placeholders(v)]
    if isinstance(node, list):
        return [t for v in node for t in find_placeholders(v)]
    return []


def fill(node, values):
    """替換佔位符；整格就是佔位符時保留原本型別（seed 要是數字）。"""
    if isinstance(node, str):
        key = node.strip()
        if key.startswith("%") and key.endswith("%") and key.count("%") == 2:
            name = key[1:-1]
            if name in values:
                return values[name]
            return node
        out = node
        for k, v in values.items():
            tok = f"%{k}%"
            if tok in out:
                out = out.replace(tok, str(v))
        return out
    if isinstance(node, dict):
        return {k: fill(v, values) for k, v in node.items()}
    if isinstance(node, list):
        return [fill(v, values) for v in node]
    return node


# ---------------------------------------------------------------- ComfyUI

class Comfy:
    def __init__(self, url):
        self.url = (url or "http://127.0.0.1:8188").rstrip("/")
        self.client_id = uuid.uuid4().hex

    def ping(self, timeout=6):
        st = _json(f"{self.url}/system_stats", timeout=timeout)
        dev = (st.get("devices") or [{}])[0]
        return {"ok": True, "kind": "comfyui", "url": self.url,
                "device": dev.get("name", "?"),
                "vram_gb": round((dev.get("vram_total") or 0) / 1e9, 1),
                "version": (st.get("system") or {}).get("comfyui_version", "?")}

    def models(self, node="CheckpointLoaderSimple", timeout=15):
        """列出某個載入節點可選的模型檔名，給 UI 下拉用。"""
        try:
            info = _json(f"{self.url}/object_info/{node}", timeout=timeout)
        except Exception:
            return []
        try:
            req = info[node]["input"]["required"]
            first = list(req.values())[0]
            return first[0] if isinstance(first[0], list) else []
        except Exception:
            return []

    def upload_image(self, path, timeout=120):
        boundary = "----ss" + uuid.uuid4().hex[:10]
        fn = os.path.basename(path)
        mime = mimetypes.guess_type(path)[0] or "image/png"
        with open(path, "rb") as f:
            blob = f.read()
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; "
                f"filename=\"{fn}\"\r\nContent-Type: {mime}\r\n\r\n").encode() + blob + \
               f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\ntrue\r\n".encode() + \
               f"--{boundary}--\r\n".encode()
        raw = _req(f"{self.url}/upload/image", body,
                   {"Content-Type": f"multipart/form-data; boundary={boundary}"}, timeout)
        r = json.loads(raw)
        sub = r.get("subfolder") or ""
        return f"{sub}/{r['name']}" if sub else r["name"]

    def run(self, workflow, on_status=None, timeout=1800, poll=1.5):
        try:
            r = _json(f"{self.url}/prompt", {"prompt": workflow, "client_id": self.client_id})
        except urllib.error.HTTPError as e:
            raise LocalError("ComfyUI 拒絕 workflow：" + e.read().decode(errors="replace")[:400])
        except Exception as e:
            raise LocalError(f"連不上 ComfyUI（{self.url}）：{e}")
        if r.get("node_errors"):
            raise LocalError("workflow 有錯：" + json.dumps(r["node_errors"], ensure_ascii=False)[:400])
        pid = r["prompt_id"]
        deadline = time.time() + timeout
        lost = 0
        while time.time() < deadline:
            time.sleep(poll)
            try:
                hist = _json(f"{self.url}/history/{pid}", timeout=30)
                lost = 0
            except urllib.error.URLError as e:
                # ComfyUI 顯存不足時是整個 process abort，不是回錯誤碼。
                # 連續連不上就直接失敗，別讓使用者對著「生成中」等三十分鐘。
                lost += 1
                if lost >= 5:
                    raise LocalError(
                        f"ComfyUI 連線中斷（{self.url}）——多半是顯存不足導致它整個當掉，"
                        f"log 會看到 Fatal Python error: Aborted。降低尺寸或改用 VAEDecodeTiled 的"
                        f" workflow，再重開 ComfyUI。原始錯誤：{e}")
                continue
            except Exception:
                continue
            if pid in (hist or {}):
                entry = hist[pid]
                status = (entry.get("status") or {})
                if status.get("status_str") == "error":
                    msgs = [m for m in status.get("messages", []) if m and m[0] == "execution_error"]
                    raise LocalError("執行失敗：" + json.dumps(msgs, ensure_ascii=False)[:400])
                if status.get("completed") or entry.get("outputs"):
                    return entry.get("outputs", {})
            else:
                try:
                    q = _json(f"{self.url}/queue", timeout=20) or {}
                    pending = len(q.get("queue_pending", []))
                    running = len(q.get("queue_running", []))
                    if on_status:
                        on_status("running" if running else "queued", pending)
                except Exception:
                    pass
        raise LocalError("ComfyUI 逾時")

    def fetch(self, item, dest):
        q = urllib.parse.urlencode({"filename": item["filename"],
                                    "subfolder": item.get("subfolder", ""),
                                    "type": item.get("type", "output")})
        blob = _req(f"{self.url}/view?{q}", timeout=300)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(blob)
        return len(blob)


def collect_outputs(outputs, want="images"):
    """從 ComfyUI 的 outputs 撈出圖片或影片項目。"""
    keys = ("images",) if want == "images" else ("gifs", "videos", "images")
    got = []
    for node in outputs.values():
        for k in keys:
            for item in node.get(k, []) or []:
                if want == "video" and not str(item.get("filename", "")).lower().endswith(
                        (".mp4", ".webm", ".webp", ".gif", ".mkv", ".avi")):
                    continue
                got.append(item)
            if got and want == "images" and k == "images":
                break
    return got


# ---------------------------------------------------------------- A1111 / Forge

class A1111:
    def __init__(self, url):
        self.url = (url or "http://127.0.0.1:7860").rstrip("/")

    def ping(self, timeout=8):
        models = _json(f"{self.url}/sdapi/v1/sd-models", timeout=timeout) or []
        opt = _json(f"{self.url}/sdapi/v1/options", timeout=timeout) or {}
        return {"ok": True, "kind": "a1111", "url": self.url,
                "models": [m.get("model_name") or m.get("title") for m in models][:60],
                "current": opt.get("sd_model_checkpoint", "?")}

    def img2img(self, image_path, prompt, negative="", steps=28, cfg=6.5,
                denoise=0.62, width=1024, height=1024, sampler="DPM++ 2M",
                seed=-1, n=1, model=None, timeout=900, mask_path=None):
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        payload = {
            "init_images": [b64], "prompt": prompt, "negative_prompt": negative,
            "steps": int(steps), "cfg_scale": float(cfg),
            "denoising_strength": float(denoise),
            "width": int(width), "height": int(height),
            "sampler_name": sampler, "seed": int(seed),
            "batch_size": 1, "n_iter": int(n),
        }
        if mask_path:
            with open(mask_path, "rb") as f:
                payload["mask"] = base64.b64encode(f.read()).decode()
            payload["inpainting_fill"] = 1        # original
            payload["inpaint_full_res"] = False
            payload["mask_blur"] = 6
        if model:
            payload["override_settings"] = {"sd_model_checkpoint": model}
            payload["override_settings_restore_afterwards"] = True
        try:
            r = _json(f"{self.url}/sdapi/v1/img2img", payload, timeout=timeout)
        except urllib.error.HTTPError as e:
            raise LocalError("A1111 錯誤：" + e.read().decode(errors="replace")[:300])
        except Exception as e:
            raise LocalError(f"連不上 A1111（{self.url}）：{e}")
        return [base64.b64decode(x) for x in (r.get("images") or [])]


# ---------------------------------------------------------------- 對外介面

def ping(kind, url):
    if kind == "comfyui":
        return Comfy(url).ping()
    if kind == "a1111":
        return A1111(url).ping()
    raise LocalError("未知的本地服務：" + str(kind))


def gen_images(cfg, image_paths, prompt, negative, out_paths, params=None,
               on_status=None, mask_path=None):
    """本地產圖。out_paths 決定要幾張（每張跑一次，seed 遞增）。

    mask_path 給局部替換用：白色＝要重畫的區域。
    """
    params = dict(params or {})
    seed = params.get("seed")
    made = []
    if cfg["kind"] == "a1111":
        api = A1111(cfg["url"])
        for i, dest in enumerate(out_paths):
            if on_status:
                on_status(f"A1111 產圖 {i + 1}/{len(out_paths)}")
            blobs = api.img2img(
                image_paths[0], prompt, negative,
                steps=params.get("steps", 28), cfg=params.get("cfg", 6.5),
                denoise=params.get("denoise", 0.62),
                width=params.get("width", 1024), height=params.get("height", 1024),
                sampler=params.get("sampler", "DPM++ 2M"),
                seed=(seed + i) if seed else -1, model=params.get("model"),
                mask_path=mask_path)
            if not blobs:
                raise LocalError("A1111 沒有回傳圖片")
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                f.write(blobs[0])
            made.append(dest)
        return made

    comfy = Comfy(cfg["url"])
    wf_raw = load_workflow(cfg["workflow"])
    names = [comfy.upload_image(p) for p in image_paths[:2]]
    mask_name = comfy.upload_image(mask_path) if mask_path else ""
    for i, dest in enumerate(out_paths):
        if on_status:
            on_status(f"ComfyUI 產圖 {i + 1}/{len(out_paths)}")
        vals = {
            "prompt": prompt, "negative": negative,
            "image": names[0], "image2": names[1] if len(names) > 1 else names[0],
            "mask": mask_name, "blend": float(params.get("blend", 0.5)),
            "seed": int(seed + i) if seed else random.randint(1, 2 ** 31 - 1),
            "steps": int(params.get("steps", 28)), "cfg": float(params.get("cfg", 6.5)),
            "denoise": float(params.get("denoise", 0.62)),
            "width": int(params.get("width", 1024)), "height": int(params.get("height", 1024)),
            "model": params.get("model") or "", "vae": params.get("vae") or "",
        }
        outputs = comfy.run(fill(wf_raw, vals), on_status=lambda s, q: on_status and on_status(
            f"ComfyUI {i + 1}/{len(out_paths)}：{s}" + (f"（佇列 {q}）" if q else "")))
        items = collect_outputs(outputs, "images")
        if not items:
            raise LocalError("workflow 沒有輸出圖片（要有 SaveImage 或 PreviewImage 節點）")
        comfy.fetch(items[0], dest)
        made.append(dest)
    return made


def gen_video(cfg, image_path, prompt, negative, dest, params=None, on_status=None):
    """本地產影片（只支援 ComfyUI）。"""
    if cfg["kind"] != "comfyui":
        raise LocalError("影片只支援 ComfyUI workflow")
    params = dict(params or {})
    comfy = Comfy(cfg["url"])
    wf_raw = load_workflow(cfg["workflow"])
    name = comfy.upload_image(image_path)
    vals = {
        "prompt": prompt, "negative": negative, "image": name, "image2": name,
        "seed": int(params["seed"]) if params.get("seed") else random.randint(1, 2 ** 31 - 1),
        "steps": int(params.get("steps", 20)), "cfg": float(params.get("cfg", 5.0)),
        "denoise": float(params.get("denoise", 1.0)),
        "width": int(params.get("width", 720)), "height": int(params.get("height", 720)),
        "frames": int(params.get("frames", 81)), "fps": int(params.get("fps", 16)),
        "model": params.get("model") or "", "vae": params.get("vae") or "",
    }
    outputs = comfy.run(fill(wf_raw, vals),
                        on_status=lambda s, q: on_status and on_status(
                            f"ComfyUI：{s}" + (f"（佇列 {q}）" if q else "")))
    items = collect_outputs(outputs, "video")
    if not items:
        raise LocalError("workflow 沒有輸出影片（需要 VHS_VideoCombine 之類的節點，"
                         "或輸出 webp/gif 動畫）")
    comfy.fetch(items[0], dest)
    return dest
