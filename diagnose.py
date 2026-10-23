"""診斷 v3（endgame_safe）為什麼比 v2 差：逐局找「第一個決策分歧點」並分類統計。

原理：同一個種子、同樣對 rule-v1，v2 與 v3 在分歧點之前的盤面逐位元相同，
所以分歧點之後的勝負差只能歸因於那一手的選擇（之後的連鎖效應也算在那一手頭上）。
用法：python diagnose.py [dev|heldout] [tol_pct] [threat]
"""
import json
import sys
from collections import defaultdict

import numpy as np

import policy as P
from runner import DEV_SEEDS, HELDOUT_SEEDS, play_one, rule_policy

RANKS = P.RANKS


def logged(flags):
    log = []

    def pol(state):
        raw = state['raw_obs']
        a = P.policy(state, flags)
        log.append({'seat': raw['self'], 'hand': raw['current_hand'], 'act': a, 'counts': list(raw['num_cards_left']),
                    'pool': raw['others_hand'], 'lead': P._last_play(raw['trace']) is None,
                    'landlord': raw['landlord'], 'legal': list(raw['actions'])})
        return a
    return pol, log


def run(seed, role, flags):
    rule = rule_policy()
    pol, log = logged(flags)
    if role == 'landlord':
        won = play_one(seed, [pol, rule, rule])
    else:
        won = not play_one(seed, [rule, pol, pol])
    return bool(won), log


def kind(a):
    if a == 'pass':
        return 'pass'
    t, _ = P._info(a)
    if t in ('bomb', 'rocket'):
        return 'power'
    return {'solo': 'solo', 'pair': 'pair'}.get(t, 'multi')


def feat(rec, role, v2, v3, k_enemy):
    hand = rec['hand']
    a2, a3 = v2['act'], v3['act']
    cnt = {c: hand.count(c) for c in set(hand)}
    breaks = lambda a: kind(a) == 'solo' and cnt.get(a, 0) >= 2
    return {
        'role': role, 'k_enemy': k_enemy, 'hand_n': len(hand),
        'v2_kind': kind(a2), 'v3_kind': kind(a3),
        'v3_high': any(c in '2BR' for c in a3), 'v2_high': any(c in '2BR' for c in a2),
        'v3_breaks_pair': breaks(a3), 'v2_breaks_pair': breaks(a2),
        'v3_higher_than_v2': (sum(map(RANKS.index, a3)) / len(a3)) > (sum(map(RANKS.index, a2)) / len(a2)),
    }


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else 'dev'
    tol = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    thr = int(sys.argv[3]) if len(sys.argv) > 3 else 2
    P.PARAMS['safe_tol_pct'], P.PARAMS['safe_threat_cards'] = tol, thr
    seeds = DEV_SEEDS if which == 'dev' else HELDOUT_SEEDS
    f2, f3 = {'endgame_safe': False}, {'endgame_safe': True}

    rows, n_games, n_discord = [], 0, 0
    for s in seeds:
        for role in ('landlord', 'peasant'):
            w2, l2 = run(s, role, f2)
            w3, l3 = run(s, role, f3)
            n_games += 1
            n_discord += (w2 != w3)
            # 第一個分歧點
            div = None
            for i, (x, y) in enumerate(zip(l2, l3)):
                if x['act'] != y['act']:
                    assert x['hand'] == y['hand'] and x['seat'] == y['seat'], '分歧點之前盤面不同，歸因失效'
                    div = (i, x, y)
                    break
            if div is None:
                assert w2 == w3, '沒有分歧卻結果不同（隨機性漏洞）'
                continue
            i, x, y = div
            enemies = [e for e in range(3) if e != x['seat'] and (x['seat'] == x['landlord'] or e == x['landlord'])]
            k = min(x['counts'][e] for e in enemies)
            r = feat(x, role, x, y, k)
            r.update({'seed': s, 'idx': i, 'w2': w2, 'w3': w3, 'a2': x['act'], 'a3': y['act'], 'hand': x['hand'],
                      'counts': x['counts'], 'pool': x['pool'], 'n_agent_moves_before': i})
            rows.append(r)

    json.dump(rows, open(f'diag_{which}_tol{tol}_thr{thr}.json', 'w', encoding='utf-8'), ensure_ascii=False)
    d = np.array([int(r['w3']) - int(r['w2']) for r in rows])
    print(f'{which} tol={tol}% thr≤{thr}：{n_games} 場（種子×角色），有分歧的 {len(rows)} 場，結果不同的 {n_discord} 場')
    print(f'  分歧場內淨效果：v3 贏而 v2 輸 {int((d>0).sum())}、v2 贏而 v3 輸 {int((d<0).sum())}、同結果 {int((d==0).sum())}；'
          f'平均 {d.mean():+.3f}\n')

    def table(title, keyf):
        g = defaultdict(list)
        for r in rows:
            g[keyf(r)].append(int(r['w3']) - int(r['w2']))
        print(title)
        for key, v in sorted(g.items(), key=lambda kv: sum(kv[1])):
            v = np.array(v)
            print(f'  {str(key):38s} n={len(v):4d}  淨 {int(v.sum()):+4d} 場  平均 {v.mean():+.3f}  '
                  f'(v3勝v2敗 {int((v>0).sum())} / v2勝v3敗 {int((v<0).sum())})')
        print()

    table('依角色', lambda r: r['role'])
    table('依「對手最少剩幾張」', lambda r: r['k_enemy'])
    table('依 v2 → v3 領出牌型', lambda r: f"{r['v2_kind']} → {r['v3_kind']}")
    table('依 v3 是否領出 2／王', lambda r: ('v3 領高牌' if r['v3_high'] else 'v3 沒領高牌') + ('，v2 也是' if r['v2_high'] else ''))
    table('依 v3 是否拆對子出單張', lambda r: f"v3拆對={r['v3_breaks_pair']} v2拆對={r['v2_breaks_pair']}")
    table('依 v3 領的牌是否比 v2 大', lambda r: 'v3 領更大的牌' if r['v3_higher_than_v2'] else 'v3 領更小/相同')
    table('依分歧時手牌張數', lambda r: ('≤4' if r['hand_n'] <= 4 else '5-8' if r['hand_n'] <= 8 else '9+'))


main()
