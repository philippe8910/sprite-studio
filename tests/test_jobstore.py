import json

import jobstore


def test_jobs_survive_restart_and_running_ones_are_marked_interrupted(tmp_path):
    path = str(tmp_path / "jobs.json")
    a = jobstore.JobStore(path)
    a["j1"] = {"id": "j1", "kind": "pack", "status": "queued", "created": 1}
    a["j2"] = {"id": "j2", "kind": "video_gen", "status": "queued", "created": 2}
    a.transition(a["j1"], "done", progress=100)
    a.transition(a["j2"], "running")

    b = jobstore.JobStore(path)                       # 模擬伺服器重啟
    assert b["j1"]["status"] == "done"
    assert b["j2"]["status"] == "interrupted"          # 不能繼續謊報「還在跑」


def test_save_is_atomic_and_valid_json(tmp_path):
    path = str(tmp_path / "jobs.json")
    s = jobstore.JobStore(path)
    for i in range(5):
        s[f"j{i}"] = {"id": f"j{i}", "status": "queued", "created": i}
    s.save()
    data = json.loads(open(path, encoding="utf-8").read())
    assert len(data) == 5
    assert not (tmp_path / "jobs.json.tmp").exists()
