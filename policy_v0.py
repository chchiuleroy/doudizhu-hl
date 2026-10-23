"""HS policy：鬥地主啟發式策略（v0，HL 迴圈的起點）。

介面：policy(state) -> raw action 字串（state 為 RLCard 的完整 state dict）。
只使用玩家合法可得的資訊：自己手牌、已出牌 trace、各家剩餘張數。
"""
import random

from rlcard.games.doudizhu.utils import CARD_TYPE

RANKS = '3456789TJQKA2BR'


def _info(action):
    """回傳 (type, rank)；多種詮釋時取 rank 最小者（保守估計牌力）。"""
    cands = CARD_TYPE[0][action]
    t, r = min(cands, key=lambda x: int(x[1]))
    return t, int(r)


def _is_power(action):
    return _info(action)[0] in ('bomb', 'rocket')


def _lead_cost(action):
    """出牌優先序：越小越先出。小牌多張組合優先，保留炸彈／2／王。"""
    t, r = _info(action)
    if t in ('bomb', 'rocket'):
        return 10_000 + r
    # 每張牌的平均點數越小越好；牌數越多越好（一次清掉更多）
    n = len(action)
    ranks = [RANKS.index(c) for c in action]
    return sum(ranks) / n - 1.5 * n


def _last_play(trace):
    """回傳 (出牌者, 牌)；跳過 pass。若為新一輪的領出則回傳 None。"""
    if not trace:
        return None
    if len(trace) >= 2 and trace[-1][1] == 'pass' and trace[-2][1] == 'pass':
        return None
    for pid, act in reversed(trace):
        if act != 'pass':
            return pid, act
    return None


def policy(state):
    raw = state['raw_obs']
    actions = raw['actions']
    me = raw['self']
    landlord = raw['landlord']
    last = _last_play(raw['trace'])

    if last is None:  # 領出
        leads = [a for a in actions if a != 'pass']
        return min(leads, key=_lead_cost)

    last_pid, last_act = last
    teammate = (me != landlord) and (last_pid != landlord)
    non_pass = [a for a in actions if a != 'pass']
    if teammate:
        return 'pass'
    if not non_pass:
        return 'pass'
    # 對手出牌：用最小的能壓過；炸彈只在對手快出完時用
    normal = [a for a in non_pass if not _is_power(a)]
    opp_left = min(n for i, n in enumerate(raw['num_cards_left']) if i != me and (me == landlord or i == landlord))
    if normal:
        return min(normal, key=lambda a: _info(a)[1])
    if opp_left <= 4:
        return min(non_pass, key=lambda a: _info(a)[1])
    return 'pass'
