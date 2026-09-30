import io
import json
import urllib.error

import pytest

import falclient


def http_error(code):
    return urllib.error.HTTPError("https://x", code, "err", {}, io.BytesIO(b"boom"))


def test_retry_recovers_from_transient_errors():
    calls, sleeps = [], []

    def fn():
        calls.append(1)
        if len(calls) < 3:
            raise http_error(503)
        return "ok"

    assert falclient._retry(fn, "submit", sleep=sleeps.append) == "ok"
    assert len(calls) == 3
    assert sleeps == [2.0, 4.0]                        # 指數退避


def test_retry_does_not_retry_client_errors():
    calls = []

    def fn():
        calls.append(1)
        raise http_error(422)

    with pytest.raises(falclient.FalError):
        falclient._retry(fn, "submit", sleep=lambda s: None)
    assert len(calls) == 1                             # 422 是請求本身有問題，重送沒用


def test_retry_gives_up_after_attempts():
    with pytest.raises(falclient.FalError):
        falclient._retry(lambda: (_ for _ in ()).throw(http_error(429)), "submit",
                         attempts=3, sleep=lambda s: None)


def test_resume_polls_existing_request_without_submitting(monkeypatch):
    seen = []

    def fake_req(url, data=None, headers=None, timeout=180):
        seen.append(url)
        assert data is None                            # 只查狀態與拿結果，絕不重新送出
        if url.endswith("/status"):
            return 200, json.dumps({"status": "COMPLETED"}).encode()
        return 200, json.dumps({"video": {"url": "https://v/1.mp4"}}).encode()

    monkeypatch.setattr(falclient, "_req", fake_req)
    monkeypatch.setattr(falclient, "api_key", lambda: "k")
    info = {"request_id": "r1", "status_url": "https://q/r1/status", "response_url": "https://q/r1"}
    res = falclient.resume(info, poll=0)
    assert falclient.first_video_url(res) == "https://v/1.mp4"
    assert seen == ["https://q/r1/status", "https://q/r1"]


def test_run_reports_submission_for_later_resume(monkeypatch):
    saved = []

    def fake_req(url, data=None, headers=None, timeout=180):
        if data is not None:
            return 200, json.dumps({"request_id": "r9", "status_url": "https://q/r9/status",
                                    "response_url": "https://q/r9"}).encode()
        if url.endswith("/status"):
            return 200, json.dumps({"status": "COMPLETED"}).encode()
        return 200, json.dumps({"images": [{"url": "https://i/1.png"}]}).encode()

    monkeypatch.setattr(falclient, "_req", fake_req)
    monkeypatch.setattr(falclient, "api_key", lambda: "k")
    falclient.run("fal-ai/x", {"prompt": "p"}, poll=0, on_submit=saved.append)
    assert saved and saved[0]["request_id"] == "r9"
