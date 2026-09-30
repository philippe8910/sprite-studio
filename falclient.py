# -*- coding: utf-8 -*-
"""fal.ai queue client — submit / poll / fetch, shared by image and video jobs.

All fal models used here live behind https://queue.fal.run/<model>: POST returns
a request_id, then you poll .../status until COMPLETED and GET the response.
Images are passed as base64 data URIs (proven to work in the original game pipeline).
"""
import base64
import json
import mimetypes
import os
import threading
import time
import urllib.error
import urllib.request

QUEUE = "https://queue.fal.run"
# 選用：舊專案的 secrets.json 路徑（內含 falApiKey），用環境變數指定
SECRETS = os.environ.get("FAL_SECRETS_JSON", "")
SETTINGS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")

# 拿一個不存在的 request id 問狀態：金鑰對＝200、金鑰錯＝401，而且不會產生任何費用。
PROBE = QUEUE + "/fal-ai/nano-banana/requests/00000000-0000-0000-0000-000000000000/status"


class FalError(RuntimeError):
    pass


# 同時送出的生成任務上限（fal 帳號有併發限制；超過會吃 429）。可用環境變數調整。
MAX_CONCURRENT = int(os.environ.get("FAL_MAX_CONCURRENT", "4"))
_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT)
RETRY_STATUS = (408, 429, 500, 502, 503, 504)


def _retry(fn, what, attempts=4, base=2.0, sleep=time.sleep):
    """暫時性錯誤（429 / 5xx / 連線中斷）以指數退避重試；4xx 等確定性錯誤立刻拋出。"""
    for i in range(attempts):
        try:
            return fn()
        except urllib.error.HTTPError as e:
            if e.code not in RETRY_STATUS or i == attempts - 1:
                raise FalError(f"{what} {e.code}: {e.read().decode(errors='replace')[:400]}")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if i == attempts - 1:
                raise FalError(f"{what} 連線失敗：{e}")
        sleep(base * (2 ** i))


def api_key():
    """金鑰來源優先序：設定頁填的 → 環境變數 FAL_KEY → 舊的 secrets.json。"""
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            key = ((json.load(f).get("fal") or {}).get("api_key") or "").strip()
        if key:
            return key
    except Exception:
        pass
    key = os.environ.get("FAL_KEY")
    if key:
        return key
    try:
        with open(SECRETS, encoding="utf-8") as f:
            return json.load(f)["falApiKey"]
    except Exception:
        raise FalError("還沒設定 fal.ai API 金鑰——點右上角「API 金鑰」填入")


def key_source():
    """給 UI 顯示金鑰是哪來的（不回傳金鑰本身）。"""
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            if ((json.load(f).get("fal") or {}).get("api_key") or "").strip():
                return "settings"
    except Exception:
        pass
    if os.environ.get("FAL_KEY"):
        return "env"
    try:
        with open(SECRETS, encoding="utf-8") as f:
            if json.load(f).get("falApiKey"):
                return "secrets"
    except Exception:
        pass
    return ""


def verify(key=None):
    """驗證金鑰能不能用。回 {ok, status, error}。不花錢。"""
    k = (key or "").strip() or api_key()
    req = urllib.request.Request(PROBE, headers={"Authorization": f"Key {k}"})
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            return {"ok": True, "status": resp.status}
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:200]
        if e.code in (401, 403):
            return {"ok": False, "status": e.code, "error": "金鑰無效：" + body}
        # 其他狀態碼代表有通過驗證（只是這個 request id 不存在）
        return {"ok": True, "status": e.code}
    except Exception as e:
        return {"ok": False, "status": 0, "error": f"{type(e).__name__}: {e}"[:200]}


def data_uri(path):
    mime = mimetypes.guess_type(path)[0] or "image/png"
    with open(path, "rb") as f:
        return f"data:{mime};base64," + base64.b64encode(f.read()).decode()


def _req(url, data=None, headers=None, timeout=180):
    r = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return resp.status, resp.read()


def run(model, payload, on_status=None, poll=2.0, timeout=1200, on_submit=None):
    """Submit a job and block until it finishes. Returns the result dict.

    on_status(status, queue_position) is called on every poll so the web UI can
    show live progress.
    """
    with _SLOTS:
        return _run(model, payload, on_status, poll, timeout, on_submit=on_submit)


def resume(info, on_status=None, poll=2.0, timeout=1200):
    """接回先前送出的請求（on_submit 存下來的 info），只查狀態與取結果，不會重新計費。"""
    with _SLOTS:
        return _poll(info, {"Authorization": f"Key {api_key()}"}, on_status, poll, timeout)


def _run(model, payload, on_status, poll, timeout, on_submit=None):
    headers = {"Authorization": f"Key {api_key()}", "Content-Type": "application/json"}
    body = json.dumps(payload).encode()
    _, resp = _retry(lambda: _req(f"{QUEUE}/{model}", body, headers), "submit")
    start = json.loads(resp)
    rid = start.get("request_id")
    info = {"model": model, "request_id": rid,
            "status_url": start.get("status_url") or f"{QUEUE}/{model}/requests/{rid}/status",
            "response_url": start.get("response_url") or f"{QUEUE}/{model}/requests/{rid}"}
    if on_submit:
        on_submit(info)          # 讓呼叫端先存起來：之後下載失敗或重啟，可用 resume() 接回
    return _poll(info, headers, on_status, poll, timeout)


def _poll(info, headers, on_status, poll, timeout, max_poll_errors=20):
    rid, status_url, response_url = info["request_id"], info["status_url"], info["response_url"]
    if on_status:
        on_status("IN_QUEUE", None, rid)

    deadline = time.time() + timeout
    errors = 0
    while time.time() < deadline:
        time.sleep(poll)
        try:
            _, b = _req(status_url, headers=headers, timeout=60)
            errors = 0
        except urllib.error.HTTPError as e:
            if e.code == 202:
                b = e.read()
            elif e.code in RETRY_STATUS:
                errors += 1
                if errors >= max_poll_errors:
                    raise FalError(f"status 連續 {errors} 次 {e.code}，放棄（request {rid}）")
                continue
            else:
                raise FalError(f"status {e.code}: {e.read().decode(errors='replace')[:300]}")
        except Exception as e:
            errors += 1
            if errors >= max_poll_errors:
                raise FalError(f"status 連續 {errors} 次失敗：{e}（request {rid}）")
            continue
        st = json.loads(b)
        s = st.get("status")
        if on_status:
            on_status(s, st.get("queue_position"), rid)
        if s == "COMPLETED":
            _, result = _retry(lambda: _req(response_url, headers=headers, timeout=120), "result")
            return json.loads(result)
        if s in ("FAILED", "ERROR"):
            raise FalError("generation failed: " + b.decode(errors="replace")[:400])
    raise FalError("timeout waiting for fal.ai")


def download(url, dest, timeout=120):
    _, blob = _retry(lambda: _req(url, timeout=timeout), "download")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as f:
        f.write(blob)
    return len(blob)


def first_video_url(result):
    v = result.get("video") or (result.get("videos") or [None])[0]
    if not v:
        raise FalError("no video in result: " + json.dumps(result)[:300])
    return v["url"] if isinstance(v, dict) else v


def image_urls(result):
    imgs = result.get("images") or ([result["image"]] if result.get("image") else [])
    out = []
    for im in imgs:
        out.append(im["url"] if isinstance(im, dict) else im)
    if not out:
        raise FalError("no images in result: " + json.dumps(result)[:300])
    return out
