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
