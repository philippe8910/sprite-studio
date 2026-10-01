import numpy as np

import repair


def periodic_sigs(n=120, period=36, noise=0.0, seed=0):
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    base = np.stack([np.sin(2 * np.pi * t / period + k) for k in range(16)], axis=1)
    sigs = base.reshape(n, 4, 4).astype(np.float32)
    return sigs + rng.normal(0, noise, sigs.shape).astype(np.float32)


def test_best_loop_finds_true_period():
    lp = repair.best_loop(periodic_sigs(period=36, noise=0.01), n_frames=10, min_period=12)
    assert lp["period"] in (36, 72)
    assert lp["seam"] < lp["step"] * 0.5            # 接縫比正常步距小很多＝無縫


def test_pick_frames_excludes_endpoint():
    fr = repair.pick_frames(33, 36, 10)
    assert fr[0] == 33 and fr[-1] < 33 + 36          # 不含 start+period，否則每圈卡一格
    assert len(fr) == 10 and fr == sorted(fr)


def test_plan_prefers_paid_when_asset_is_broken():
    results = [
        {"anim": "idle", "pass": False, "fails": ["seam_jump"]},
        {"anim": "walk", "pass": False, "fails": ["feet", "empty"]},
        {"anim": "run", "pass": True, "fails": []},
    ]
    plans = repair.plan(results)
    assert [a["action"] for a in plans["idle"]] == ["reloop"]
    assert [a["action"] for a in plans["walk"]] == ["regenerate"]
    assert "run" not in plans


def test_apply_free_align_shrink_and_reloop():
    cfg = {"frames": list(range(0, 80, 10)), "scale": 1.0}
    acts = [dict(repair.ACTIONS[k], fail=k) for k in ("feet", "clip", "seam_jump")]
    done = repair.apply_free(cfg, acts, periodic_sigs(period=24, noise=0.005))
    assert cfg["align"] is True and cfg["scale"] == 0.9
    assert {d["action"] for d in done} == {"align", "shrink", "reloop"}
    assert len(cfg["frames"]) == 8


def test_cache_key_stable_and_sensitive():
    k = repair.cache_key("m", "p", "n", b"img", {"seed": 1, "duration": "5"})
    assert k == repair.cache_key("m", "p", "n", b"img", {"duration": "5", "seed": 1})
    assert k != repair.cache_key("m", "p", "n", b"img2", {"seed": 1, "duration": "5"})
    assert k != repair.cache_key("m", "p", "n", b"img", {"seed": 2, "duration": "5"})


def test_claim_submit_then_resume_after_request_id():
    pending = {}
    assert repair.claim(pending, "k", 100, "job1")[0] == "submit"
    pending["k"].update(request_id="r1", status_url="s", response_url="r")
    decision, entry = repair.claim(pending, "k", 200, "job2")
    assert decision == "resume" and entry["request_id"] == "r1"     # 不重新送出＝不重複付費


def test_claim_concurrent_same_input_waits():
    pending = {}
    assert repair.claim(pending, "k", 100, "job1")[0] == "submit"
    assert repair.claim(pending, "k", 101, "job2")[0] == "wait"      # 另一個任務正在送出


def test_stale_claim_is_taken_over():
    pending = {}
    repair.claim(pending, "k", 100, "job1")                           # 佔位後卡住沒送出
    assert repair.claim(pending, "k", 100 + repair.CLAIM_TTL + 1, "job2")[0] == "submit"


def test_release_after_failure_allows_fresh_submit():
    pending = {}
    repair.claim(pending, "k", 100, "job1")
    pending["k"].update(request_id="r1")
    repair.release(pending, "k", "job1")                              # 模型回 FAILED → 清掉
    assert repair.claim(pending, "k", 200, "job2")[0] == "submit"    # 不會一直接回失敗的請求


def test_release_does_not_remove_someone_elses_claim():
    pending = {}
    repair.claim(pending, "k", 100, "job2")
    repair.release(pending, "k", "job1")
    assert "k" in pending


def test_release_cannot_clear_another_jobs_live_request():
    pending = {}
    repair.claim(pending, "k", 100, "job1")
    pending["k"].update(request_id="r1")              # job1 的請求還在跑
    repair.release(pending, "k", "job2")               # 別的任務失敗時不能把它清掉
    assert pending["k"]["request_id"] == "r1"


def test_uncertain_submit_blocks_immediate_resubmit():
    pending = {}
    repair.claim(pending, "k", 100, "job1")
    repair.mark_unconfirmed(pending, "k", "job1")       # 送出時逾時：fal 可能已經收下
    assert repair.claim(pending, "k", 120, "job2")[0] == "blocked"
    assert repair.claim(pending, "k", 100 + repair.CLAIM_TTL + 1, "job2")[0] == "submit"


def test_download_error_after_submit_keeps_claim_for_resume():
    # 已拿到 request id（fal 已收下並計費），之後下載回 4xx：不能清掉，否則下次會重送再付一次
    pending = {}
    repair.claim(pending, "k", 100, "job1")
    pending["k"].update(request_id="r1", owner="job1")
    assert repair.on_error(pending, "k", "job1", "rejected") == "keep"
    assert repair.claim(pending, "k", 200, "job2")[0] == "resume"


def test_on_error_rules_before_request_id():
    for kind, expect in (("rejected", "release"), ("uncertain", "unconfirmed"), ("failed", "release")):
        pending = {}
        repair.claim(pending, "k", 100, "job1")
        assert repair.on_error(pending, "k", "job1", kind) == expect
