"""HS policy：鬥地主啟發式策略（v0 → v2 → v3 endgame_safe，HL 迴圈的產物）。

介面：policy(state, flags=None) -> raw action 字串（state 為 RLCard 的完整 state dict）。
只使用玩家合法可得的資訊：自己手牌、已出牌 trace、各家剩餘張數、尚未出現的牌池。
flags 可臨時覆蓋 FLAGS（評估時用來比較不同版本，不動全域設定）。
"""
from math import comb

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


FLAGS = {'go_out': True, 'lone_lead': True, 'save_high': False, 'teammate_pass': True, 'endgame_safe': False,
         'safe_no_high': False}
# 數值參數（UI 可調）；預設值 = 已驗證的 v2 行為
PARAMS = {'save_high_opp_left': 6, 'save_high_hand_left': 6, 'bomb_opp_left': 4,
          'safe_threat_cards': 2,   # 對手剩 ≤ N 張才啟動 endgame_safe
          'safe_tol_pct': 2}        # 被壓過機率與最小值差 ≤ N% 視為同樣安全


def _goes_out(action, hand):
    return sorted(action) == sorted(hand)


def _has_high(action):
    return any(c in '2BR' for c in action)


def _beat_prob(action, pool, k):
    """對手手上有 k 張牌、且這 k 張是從 pool（未出現牌池）均勻抽出時，能壓過 action 的機率。
    只看對手能否「同牌型更大」或王炸；k ≤ 2 時對手湊不出炸彈。"""
    n = len(pool)
    if k <= 0 or n < k:
        return 0.0
    t, _ = _info(action)
    if t in ('bomb', 'rocket'):
        return 0.0
    total = comb(n, k)
    cnt = {c: pool.count(c) for c in set(pool)}
    p_rocket = 1 / total if (k == 2 and cnt.get('B') and cnt.get('R')) else 0.0
    p = 0.0
    if t == 'solo':
        r = RANKS.index(action)
        higher = sum(c for ch, c in cnt.items() if RANKS.index(ch) > r)
        p = 1 - comb(n - higher, k) / total
    elif t == 'pair' and k == 2:
        r = RANKS.index(action[0])
        pairs = sum(comb(c, 2) for ch, c in cnt.items() if ch not in 'BR' and RANKS.index(ch) > r)
        p = pairs / total
    return min(1.0, p + p_rocket)


def _safe_lead(leads, raw, enemies, F=None):
    """對手快出完時，從 leads 選「最不容易被壓過」的領出；同樣安全者取原本的出牌優先序。
    F['safe_no_high']：診斷用反事實——有非 2／王的選項時，不考慮 2／王（驗證「提早耗掉大牌」假設）。"""
    pool = raw['others_hand']
    if F and F.get('safe_no_high'):
        leads = [a for a in leads if not _has_high(a)] or leads
    threats = [raw['num_cards_left'][e] for e in enemies if raw['num_cards_left'][e] <= PARAMS['safe_threat_cards']]
    if not threats:
        return None

    def risk(a):
        ok = 1.0
        for k in threats:
            ok *= 1 - _beat_prob(a, pool, k)
        return 1 - ok

    risks = {a: risk(a) for a in leads}
    best = min(risks.values())
    tol = PARAMS['safe_tol_pct'] / 100
    return min((a for a in leads if risks[a] <= best + tol), key=_lead_cost)


def policy(state, flags=None):
    F = {**FLAGS, **(flags or {})}
    raw = state['raw_obs']
    actions = raw['actions']
    me = raw['self']
    landlord = raw['landlord']
    hand = raw['current_hand']
    last = _last_play(raw['trace'])
    enemies = [i for i in range(3) if i != me and (me == landlord or i == landlord)]
    opp_left = min(raw['num_cards_left'][i] for i in enemies)

    # 能一手出完就直接贏
    for a in (actions if F['go_out'] else []):
        if a != 'pass' and _goes_out(a, hand):
            return a

    if last is None:  # 領出
        leads = [a for a in actions if a != 'pass']
        if F['endgame_safe']:
            a = _safe_lead(leads, raw, enemies, F)
            if a is not None:
                return a
        if F['lone_lead'] and opp_left == 1:  # 對手只剩一張：別領單張，除非只剩單張可出
            multi = [a for a in leads if len(a) > 1 and not _is_power(a)]
            if multi:
                return min(multi, key=_lead_cost)
            return max(leads, key=lambda a: _info(a)[1] if not _is_power(a) else -1)
        return min(leads, key=_lead_cost)

    last_pid, last_act = last
    teammate = (me != landlord) and (last_pid != landlord)
    non_pass = [a for a in actions if a != 'pass']
    if (teammate and F['teammate_pass']) or not non_pass:
        return 'pass'
    normal = [a for a in non_pass if not _is_power(a)]
    # 高牌（2／王）不輕易浪費在對手快不出完的牌上
    cheap = [a for a in normal if not F['save_high'] or not _has_high(a) or opp_left <= PARAMS['save_high_opp_left']
             or len(hand) <= PARAMS['save_high_hand_left']]
    if cheap:
        return min(cheap, key=lambda a: _info(a)[1])
    if opp_left <= PARAMS['bomb_opp_left'] and non_pass:
        return min(non_pass, key=lambda a: _info(a)[1])
    return 'pass'
