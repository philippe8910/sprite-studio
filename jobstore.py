"""任務狀態持久化：寫進 JSON 檔，伺服器重啟後還查得到，而且不會把中斷的任務誤報成還在跑。

只在狀態轉換（建立、開始、完成、失敗）時寫檔，進度百分比這類高頻更新不寫，避免 I/O 過多。
寫檔用「先寫暫存檔再 os.replace」，中途斷電也不會留下半份 JSON。
"""
import json
import os
import threading
import time

KEEP = 300          # 最多保留幾筆（依建立時間留最新的）
PERSIST_FIELDS = ("id", "kind", "pid", "label", "status", "progress", "message",
                  "created", "finished", "error", "request_id")


class JobStore(dict):
    def __init__(self, path):
        super().__init__()
        self.path = path
        self._lock = threading.Lock()
        self._load()

    def _load(self):
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, encoding="utf-8") as f:
                saved = json.load(f)
        except Exception:
            return
        for j in saved:
            if j.get("status") in ("queued", "running"):
                # 上次關閉時還沒跑完：執行緒已經不在了，標成中斷，讓前端可以重送
                j["status"] = "interrupted"
                j["message"] = "伺服器重啟時中斷，請重新送出"
                j.setdefault("finished", time.time())
            j.setdefault("items", [])
            j.setdefault("result", None)
            self[j["id"]] = j

    def save(self):
        with self._lock:
            jobs = sorted(self.values(), key=lambda j: j.get("created", 0))[-KEEP:]
            data = [{k: j.get(k) for k in PERSIST_FIELDS} for j in jobs]
            tmp = self.path + ".tmp"
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=0)
            os.replace(tmp, self.path)

    def transition(self, job, status, **fields):
        """改狀態並寫檔。"""
        job["status"] = status
        job.update(fields)
        if status in ("done", "error", "interrupted"):
            job["finished"] = time.time()
        self.save()
