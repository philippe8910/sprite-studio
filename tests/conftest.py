import json
import os
import sys

import numpy as np
import pytest
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))


def sprite(w=64, h=80, dx=0, dy=0, body=(20, 50), color=(200, 60, 60, 255)):
    """一個簡單的「角色」：矩形身體，可左右／上下平移。"""
    a = np.zeros((h, w, 4), np.uint8)
    x0 = w // 2 - body[0] // 2 + dx
    y1 = h - 4 + dy
    a[max(0, y1 - body[1]):max(0, y1), max(0, x0):max(0, x0 + body[0])] = color
    return a


@pytest.fixture
def make_set(tmp_path):
    """寫出一組 sprite strips + anims.json，回傳資料夾路徑。"""
    def _make(anims):
        d = tmp_path / "set"
        d.mkdir(exist_ok=True)
        meta = {}
        for name, (frames, loop) in anims.items():
            h, w = frames[0].shape[:2]
            strip = np.concatenate(frames, axis=1)
            Image.fromarray(strip, "RGBA").save(d / f"{name}.png")
            meta[name] = {"file": f"{name}.png", "frameW": w, "frameH": h,
                          "count": len(frames), "fps": 10, "loop": loop}
        (d / "anims.json").write_text(json.dumps(meta), encoding="utf-8")
        return str(d)
    return _make
