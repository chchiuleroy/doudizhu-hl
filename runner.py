"""HS runner: 對 RLCard 規則 bot 跑固定種子的勝率評估。

角色說明（RLCard：player 0 恆為地主）：
  landlord 評估：待測 policy 坐地主，對手兩個農民是 rule-v1
  peasant  評估：待測 policy 坐兩個農民，對手地主是 rule-v1
基線 = rule-v1 坐滿三個位置，同一批發牌種子，可做配對比較。
"""
import math

import numpy as np
import rlcard
from rlcard.models.doudizhu_rule_models import DouDizhuRuleAgentV1

DEV_SEEDS = range(0, 2000)          # 迭代 policy 時看的種子
HELDOUT_SEEDS = range(100000, 102000)  # 只在里程碑時跑，不拿來調參


def rule_policy():
    agent = DouDizhuRuleAgentV1()
    return lambda raw_state: agent.step(raw_state)


def play_one(seed, policies):
    """policies: 長度 3 的 callable list，輸入完整 state（含 raw_obs），回傳 raw action 字串。
    回傳 True 代表地主方勝。"""
    env = rlcard.make('doudizhu', config={'seed': seed})
    np.random.seed(seed)  # rule agent 用到全域 np.random
    state, pid = env.reset()
    while not env.is_over():
        action = policies[pid](state)
        assert action in state['raw_legal_actions'], (action, state['raw_legal_actions'][:5])
        state, pid = env.step(action, raw_action=True)
    return env.get_payoffs()[0] > 0


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - h) / d, (c + h) / d)


def evaluate(policy, seeds):
    """回傳 dict：各角色勝率、基線勝率、配對差。"""
    rule = rule_policy()
    seeds = list(seeds)
    base_l, new_l, base_p, new_p = [], [], [], []
    for s in seeds:
        base_l.append(play_one(s, [rule, rule, rule]))
        new_l.append(play_one(s, [policy, rule, rule]))
        base_p.append(not play_one(s, [rule, rule, rule]))   # 農民勝 = 地主敗
        new_p.append(not play_one(s, [rule, policy, policy]))
    n = len(seeds)
    out = {'n': n}
    for name, b, w in (('landlord', base_l, new_l), ('peasant', base_p, new_p)):
        kb, kw = sum(b), sum(w)
        diff = np.array(w, float) - np.array(b, float)
        se = diff.std(ddof=1) / math.sqrt(n)
        out[name] = {
            'baseline': kb / n, 'agent': kw / n,
            'agent_ci95': wilson(kw, n),
            'paired_diff': diff.mean(), 'paired_diff_ci95': (diff.mean() - 1.96 * se, diff.mean() + 1.96 * se),
        }
    return out


def fmt(res):
    lines = [f"n={res['n']}"]
    for role in ('landlord', 'peasant'):
        r = res[role]
        lines.append(
            f"{role:9s} base={r['baseline']:.3f}  agent={r['agent']:.3f} "
            f"CI=({r['agent_ci95'][0]:.3f},{r['agent_ci95'][1]:.3f})  "
            f"paired_diff={r['paired_diff']:+.3f} CI=({r['paired_diff_ci95'][0]:+.3f},{r['paired_diff_ci95'][1]:+.3f})")
    return "\n".join(lines)
