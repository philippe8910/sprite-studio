import math

import numpy as np

import eval_sprites
from conftest import sprite


def by_anim(results):
    return {r["anim"]: r for r in results}


def breathing(n=8):
    # 身體高度依正弦微幅起伏：首尾銜接自然的無縫循環
    return [sprite(body=(20, 50 + round(2 * math.sin(2 * math.pi * i / n)))) for i in range(n)]


def test_seamless_idle_passes(make_set):
    r = by_anim(eval_sprites.evaluate(make_set({"idle": (breathing(), True)})))
    assert r["idle"]["pass"], r["idle"]


def test_empty_frame_fails(make_set):
    frames = breathing()
    frames[3] = np.zeros_like(frames[3])
    r = by_anim(eval_sprites.evaluate(make_set({"idle": (frames, True)})))
    assert "empty" in r["idle"]["fails"]


def test_side_clip_fails(make_set):
    frames = [sprite(dx=-40) for _ in range(6)]          # 身體被推出左邊界
    r = by_anim(eval_sprites.evaluate(make_set({"attack": (frames, False)})))
    assert "clip" in r["attack"]["fails"]


def test_seam_jump_on_continuous_loop(make_set):
    # 身體越來越矮、最後一幀突然跳回原本高度：循環時會「閃一下」
    frames = [sprite(body=(20, 50 - 2 * i)) for i in range(8)]
    r = by_anim(eval_sprites.evaluate(make_set({"walk": (frames, True)})))
    assert "seam_jump" in r["walk"]["fails"]


def test_one_shot_action_not_checked_for_seam(make_set):
    frames = [sprite(body=(20, 50 - 2 * i)) for i in range(8)]
    r = by_anim(eval_sprites.evaluate(make_set({"attack": (frames, True)})))
    assert "seam_jump" not in r["attack"]["fails"]


def test_feet_drift_on_in_place_action(make_set):
    frames = [sprite(dy=-(i % 2) * 12) for i in range(8)]   # 待機時腳底上下跳 12px
    r = by_anim(eval_sprites.evaluate(make_set({"idle": (frames, True)})))
    assert "feet" in r["idle"]["fails"]


def test_duplicate_frames_are_warning_not_failure(make_set):
    frames = breathing()
    frames[4] = frames[3].copy()
    r = by_anim(eval_sprites.evaluate(make_set({"idle": (frames, True)})))
    assert "dup" in r["idle"]["warns"]
    assert "dup" not in r["idle"]["fails"]
