"""打包後 sprite strips 的自動品質評估。

用法：
    python tools/eval_sprites.py <素材資料夾或其上層資料夾> [--json 報告.json]

每個含 anims.json 的資料夾視為一組角色素材，逐個動作檢查下列項目；任何一項不過即判為失敗，
並附上失敗類型，方便只重做壞掉的那一支動作（而不是整套重生成）。

  empty      空白幀：不透明像素佔比 < 0.5%
  dup        （警告，不算失敗）連續重複幀：相鄰幀平均差 < 0.6。攻擊類動作常刻意停格，需人工確認
  seam_jump  循環接縫跳動：尾幀→首幀差 > 中位步距 × 2.5（循環會「閃一下」）。
             只檢查連續循環的動作（待機、走、跑、蹲姿待機等）；攻擊類一次一段重播，接縫本來就是出招瞬間
  seam_stall 循環接縫停頓：尾幀→首幀差 < 中位步距 × 0.25（尾幀≈首幀，每圈會卡一格）
  feet       腳底漂移：原地動作（待機、蹲姿、防禦）的最低不透明列逐幀極差 > 8 px；走跑跳類腳本來就會離地，不檢查
  clip       被畫框切到：不透明像素貼齊左右邊緣 ≥ 6 px（素材被裁掉；下緣是地面基準線，不檢查）
"""
import argparse, json, os, statistics, sys
import numpy as np
from PIL import Image

SEAMLESS = ('idle', 'walk', 'run', 'crouch_idle', 'guard', 'fly', 'swim')
GROUND = ('idle', 'crouch', 'crouch_idle', 'guard')

def frames_of(path, fw, fh, count):
    im = np.asarray(Image.open(path).convert('RGBA'), dtype=np.float32)
    out = []
    for i in range(count):
        f = im[:fh, i * fw:(i + 1) * fw]
        if f.shape[1] < fw:
            break
        out.append(f)
    return out

def diff(a, b):
    return float(np.abs(a - b).mean())

def check(name, meta, folder):
    fw, fh, n = meta['frameW'], meta['frameH'], meta['count']
    fr = frames_of(os.path.join(folder, meta['file']), fw, fh, n)
    fails, warns, info = [], [], {'frames': len(fr)}
    if len(fr) < n:
        fails.append('short_strip')
    alpha = [f[..., 3] > 16 for f in fr]
    if any(a.mean() < 0.005 for a in alpha):
        fails.append('empty')
    steps = [diff(fr[i], fr[i + 1]) for i in range(len(fr) - 1)]
    if steps and sum(s < 0.6 for s in steps):
        warns.append('dup'); info['dup_pairs'] = sum(s < 0.6 for s in steps)
    if meta.get('loop') and name in SEAMLESS and len(fr) >= 4 and steps:
        moving = [s for s in steps if s >= 0.6] or steps   # 停格不算進正常步距
        med = statistics.median(moving) or 1e-6
        ratio = diff(fr[-1], fr[0]) / med
        info['seam_ratio'] = round(ratio, 2)
        if ratio > 2.5: fails.append('seam_jump')
        elif ratio < 0.25: fails.append('seam_stall')
    if name in GROUND:
        feet = [int(np.nonzero(a.any(axis=1))[0].max()) for a in alpha if a.any()]
        if feet:
            info['feet_range'] = max(feet) - min(feet)
            if info['feet_range'] > 8: fails.append('feet')
    edge = max(max(a[:, 0].sum(), a[:, -1].sum()) for a in alpha) if alpha else 0
    if edge >= 6:
        fails.append('clip'); info['edge_px'] = int(edge)
    info['warns'] = warns
    return fails, info

def evaluate(root):
    sets = []
    for dp, _, fs in os.walk(root):
        if 'anims.json' in fs:
            sets.append(dp)
    results = []
    for folder in sorted(sets):
        anims = json.load(open(os.path.join(folder, 'anims.json'), encoding='utf-8'))
        for name, meta in anims.items():
            if not isinstance(meta, dict) or 'file' not in meta or not os.path.exists(os.path.join(folder, meta['file'])):
                continue
            try:
                fails, info = check(name, meta, folder)
            except Exception as e:
                fails, info = ['error'], {'error': str(e)}
            results.append({'set': os.path.relpath(folder, root), 'anim': name, 'pass': not fails, 'fails': fails, **info})
    return results

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('root')
    ap.add_argument('--json')
    a = ap.parse_args()
    res = evaluate(a.root)
    if not res:
        sys.exit('找不到任何 anims.json')
    passed = sum(r['pass'] for r in res)
    kinds, warns = {}, 0
    for r in res:
        for f in r['fails']:
            kinds[f] = kinds.get(f, 0) + 1
        warns += bool(r.get('warns'))
    print(f'動作數 {len(res)}｜通過 {passed}｜通過率 {passed / len(res):.1%}｜素材組 {len({r["set"] for r in res})}｜需人工確認（停格）{warns}')
    for k, v in sorted(kinds.items(), key=lambda x: -x[1]):
        print(f'  失敗類型 {k:<11} {v}')
    for r in res:
        if not r['pass']:
            print(f'  ✗ {r["set"]}/{r["anim"]}: {",".join(r["fails"])}')
    if a.json:
        json.dump({'total': len(res), 'passed': passed, 'fail_kinds': kinds, 'results': res},
                  open(a.json, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

if __name__ == '__main__':
    main()
