"""v2 vs v3（endgame_safe）配對比較：同一批種子、同樣對 rule-v1，看配對勝率差。
用法：python compare.py dev|heldout  [tol_pct,threat ...]  例：python compare.py dev 2,2 5,2 10,2
"""
import math
import sys

import numpy as np

import policy as P
from runner import DEV_SEEDS, HELDOUT_SEEDS, play_one, rule_policy


def outcomes(pol, seeds):
    rule = rule_policy()
    L = [play_one(s, [pol, rule, rule]) for s in seeds]            # 地主角色：地主勝
    Pz = [not play_one(s, [rule, pol, pol]) for s in seeds]        # 農民角色：農民勝
    return np.array(L, float), np.array(Pz, float)


def paired(a, b):
    d = a - b
    se = d.std(ddof=1) / math.sqrt(len(d))
    return d.mean(), d.mean() - 1.96 * se, d.mean() + 1.96 * se


def main():
    seeds = DEV_SEEDS if sys.argv[1] == 'dev' else HELDOUT_SEEDS
    cfgs = [tuple(map(int, c.split(','))) for c in sys.argv[2:]] or [(2, 2)]
    # 第三個數字 1 = 啟用 safe_no_high（反事實診斷）：例 python compare.py dev 5,2,1
    base = outcomes(lambda s: P.policy(s, {'endgame_safe': False}), seeds)
    print(f'n={len(seeds)}  v2 基線：地主 {base[0].mean():.3f}  農民 {base[1].mean():.3f}', flush=True)
    for cfg in cfgs:
        tol, thr = cfg[0], cfg[1]
        nohigh = len(cfg) > 2 and cfg[2] == 1
        P.PARAMS['safe_tol_pct'], P.PARAMS['safe_threat_cards'] = tol, thr
        new = outcomes(lambda s: P.policy(s, {'endgame_safe': True, 'safe_no_high': nohigh}), seeds)
        out = []
        for name, a, b in (('地主', new[0], base[0]), ('農民', new[1], base[1])):
            m, lo, hi = paired(a, b)
            out.append(f'{name} {a.mean():.3f} (v3-v2 {m:+.3f} CI {lo:+.3f}~{hi:+.3f})')
        print(f'tol={tol}% threat≤{thr} no_high={nohigh}： ' + '  |  '.join(out), flush=True)


main()
