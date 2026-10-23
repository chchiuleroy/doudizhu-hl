"""人機對戰：你坐一個位置，另外兩家由 policy（或 rule-v1）控制。

牌面：3-9、T(10)、J、Q、K、A、2、B(小王)、R(大王)。
輸入：例如 `34567` 出順子、`pass` 或 `p` 或空白 = 不出、`h` 看合法出牌、`quit` 離開（`q` 是皇后牌，不是離開）。
"""
import argparse
import json
import os
import random
import sys
import time

import numpy as np
import rlcard

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from policy import policy  # noqa: E402
from runner import rule_policy  # noqa: E402

RANKS = '3456789TJQKA2BR'
NAMES = {0: '地主', 1: '地主下家', 2: '地主上家'}
LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'games.jsonl')


def canon(text):
    t = text.strip().upper().replace('10', 'T')
    if t in ('', 'P', 'PASS'):
        return 'pass'
    return ''.join(sorted(t, key=lambda c: RANKS.index(c) if c in RANKS else 99))


def show(raw, me):
    print('-' * 50)
    for pid, act in raw['trace'][-6:]:
        who = '你' if pid == me else NAMES[pid]
        print(f'  {who}: {act}')
    left = raw['num_cards_left']
    print('  剩餘張數 ' + ' / '.join(f'{NAMES[i]}{"(你)" if i == me else ""}:{left[i]}' for i in range(3)))
    print(f'  你的手牌: {raw["current_hand"]}')


def human(state, me):
    raw = state['raw_obs']
    legal = set(raw['actions'])
    show(raw, me)
    while True:
        s = input('出牌> ')
        if s.strip().lower() in ('quit', 'exit'):
            raise KeyboardInterrupt
        if s.strip().lower() == 'h':
            print('  合法:', ' '.join(sorted(legal, key=lambda a: (len(a), a))[:60]))
            continue
        a = canon(s)
        if a in legal:
            return a
        print('  不合法（h 可看合法出牌；領出時不能 pass）')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--seat', type=int, default=0, choices=[0, 1, 2], help='0 地主 / 1 地主下家 / 2 地主上家')
    ap.add_argument('--opp', default='policy', choices=['policy', 'rule'])
    ap.add_argument('--seed', type=int, default=None)
    ap.add_argument('--auto', action='store_true', help='測試用：你的位置也由 policy 代打')
    args = ap.parse_args()

    seed = args.seed if args.seed is not None else random.SystemRandom().randrange(10**6)
    env = rlcard.make('doudizhu', config={'seed': seed})
    np.random.seed(seed)
    bot = policy if args.opp == 'policy' else rule_policy()
    me = args.seat
    print(f'你是「{NAMES[me]}」，對手/隊友 AI = {args.opp}，種子 {seed}')

    state, pid = env.reset()
    while not env.is_over():
        if pid == me and not args.auto:
            try:
                a = human(state, me)
            except (KeyboardInterrupt, EOFError):
                print('\n離開，不記錄。')
                return
        else:
            a = bot(state)
        state, pid = env.step(a, raw_action=True)

    landlord_won = env.get_payoffs()[0] > 0
    i_won = landlord_won if me == 0 else not landlord_won
    print('=' * 50)
    for pid_, act in state['raw_obs']['trace'][-4:]:
        print(f'  {NAMES[pid_]}: {act}')
    print('你贏了！' if i_won else '你輸了。')
    with open(LOG, 'a', encoding='utf-8') as f:
        f.write(json.dumps({'t': time.strftime('%F %T'), 'seed': seed, 'seat': me, 'opp': args.opp,
                            'auto': args.auto, 'won': bool(i_won)}, ensure_ascii=False) + '\n')


if __name__ == '__main__':
    main()
