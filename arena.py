"""多方對戰：每個座位可選 human / policy（小c 的 HL 啟發式）/ rule（RLCard rule-v1）/ codex（小o）/ vllm（Qwen 端點）。

**只有小c 能用 HL 策略（policy.py）；小o 與 vLLM 玩家完全靠它們自己原本的智能解讀規則來玩，
不接收任何策略提示或參數**（提示詞只有規則說明、該座位可見的牌局資訊與合法出牌清單）。

小o 以 `codex exec --sandbox read-only` 每手一次呼叫，只給它「該座位合法可見」的資訊
（自己手牌、出牌紀錄、各家剩餘張數、尚未出現的牌池、合法出牌清單），不給對手手牌。
每手思考時間上限 40 秒（THINK_LIMIT_SECS，所有 LLM 玩家共用）。逾時或回傳非法動作時，由系統代出「中性保守牌」
（能 pass 就 pass，否則出最小單張；不使用 HL 策略代打），並計入 fallbacks，結果紀錄會誠實標出。
"""
import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time

import numpy as np
import rlcard

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from play import NAMES, RANKS, canon, human  # noqa: E402
from policy import policy  # noqa: E402
from runner import rule_policy  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, 'games.jsonl')
CODEX = shutil.which('codex.cmd') or shutil.which('codex')
SCRATCH = tempfile.mkdtemp(prefix='ddz_codex_')  # 非 git 目錄，用 --skip-git-repo-check

RULES = """鬥地主規則（RLCard 版，無叫牌，座位 0 固定為地主）：
- 54 張牌，地主 20 張、兩個農民各 17 張；先出完手牌的一方獲勝（農民兩人同隊）。
- 牌面字元：3 4 5 6 7 8 9 T(10) J Q K A 2 B(小王) R(大王)，大小順序 3<4<…<K<A<2<B<R。
- 牌型：單張、對子、三張（可帶一張或一對）、順子（≥5 張連續，不含 2 和王）、連對（≥3 對）、飛機、四帶二、炸彈（四張同點）、王炸（BR）。
- 領出時任意合法牌型；跟牌必須同牌型且更大，炸彈可壓非炸彈，王炸最大；也可 pass（領出時不可 pass）。
- 你的動作必須逐字等於「合法出牌」清單中的一項。"""


# 每手思考時間上限（秒，Roy 2026-10-23 指定）。小o 與 vLLM 玩家共用；逾時由系統代出中性保守的一手。
THINK_LIMIT_SECS = float(os.environ.get('DDZ_THINK_LIMIT', '40'))   # 2026-10-23 Roy 由 30 秒改為 40 秒


def new_stats():
    """fallbacks / vllm_fallbacks = 逾時或回傳非法而由系統代出「中性保守牌」的次數（不是 HL 策略代打）。"""
    return {'codex_calls': 0, 'fallbacks': 0, 'codex_secs': 0.0, 'codex_timeouts': 0,
            'vllm_calls': 0, 'vllm_fallbacks': 0, 'vllm_secs': 0.0, 'vllm_timeouts': 0,
            'vllm_think_ok': 0, 'vllm_instinct': 0}


def neutral_move(legal):
    """逾時／非法時的中性代出：能 pass 就 pass，否則出最小單張。刻意不使用任何 HL 策略（公平原則）。"""
    if 'pass' in legal:
        return 'pass'
    return min(legal, key=lambda a: (len(a), [RANKS.index(c) for c in a]))


def _vllm_cfg():
    """環境變數優先，其次 .vllm.env（KEY=VALUE 行）。"""
    cfg = {}
    path = os.path.join(HERE, '.vllm.env')
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    cfg[k.strip()] = v.strip()
    for k in ('VLLM_URL', 'VLLM_MODEL', 'VLLM_KEY', 'VLLM_THINK'):
        if os.environ.get(k):
            cfg[k] = os.environ[k]
    return cfg


_VLLM_CLIENT = None


def vllm_available():
    cfg = _vllm_cfg()
    return bool(cfg.get('VLLM_URL') and cfg.get('VLLM_MODEL'))


def vllm_move(state, me, seat_name, stats):
    """vLLM（OpenAI 相容）玩家：同樣只給該座位可見資訊；每手總時間上限 THINK_LIMIT_SECS。
    兩種模式（VLLM_THINK）：
      0（預設）直覺模式：關閉思考，一次呼叫，非法時在時限內重試一次。
      1 思考模式：先開思考，預算 = 上限 − 5 秒、max_tokens = 預算 × 140（此端點實測約 145 token/s，
        所以預算內剛好生成完；上限 30 秒時為 3500、40 秒時為 4900）；
        沒在預算內想完就用剩餘時間要一個關閉思考的「直覺答案」；再失敗由系統代出中性保守牌。
    用官方 openai 客戶端；必須明確指定 enable_thinking（此端點預設會回空 content）。"""
    global _VLLM_CLIENT
    from openai import OpenAI
    cfg = _vllm_cfg()
    if _VLLM_CLIENT is None:
        # max_retries=0 很重要：openai 客戶端預設逾時會自動重試 2 次，一次呼叫可卡到 ~3 倍時限，
        # 會直接擊穿「每手」時限（2026-10-23 思考模式評估，30 秒上限下平均每手 56 秒才發現）
        _VLLM_CLIENT = OpenAI(base_url=cfg['VLLM_URL'], api_key=cfg.get('VLLM_KEY') or 'EMPTY',
                              timeout=THINK_LIMIT_SECS, max_retries=0)
    think = cfg.get('VLLM_THINK', '0') == '1'
    raw = state['raw_obs']
    legal = raw['actions']
    prompt = f"""你在玩鬥地主，請為你的座位選出這一手。只根據下面資訊判斷，不需要解釋。

{RULES}

你的座位：{me}（{seat_name}），地主座位：{raw['landlord']}
你的手牌：{raw['current_hand']}
各座位剩餘張數（依座位 0,1,2）：{raw['num_cards_left']}
出牌紀錄（座位, 牌；最舊到最新）：{raw['trace']}
尚未出現的牌（其他兩家手牌的合併，不知道怎麼分）：{raw['others_hand']}
合法出牌（必須從中選一項）：{legal}

只輸出一行 JSON，不要有其他文字：{{"action": "<合法出牌中的一項>"}}"""
    deadline = time.time() + THINK_LIMIT_SECS

    def ask(thinking, budget, max_tokens, temperature):
        """回傳合法動作或 None；budget 是這次呼叫的秒數上限。"""
        t0 = time.time()
        try:
            r = _VLLM_CLIENT.with_options(timeout=max(1.0, budget)).chat.completions.create(
                model=cfg['VLLM_MODEL'], messages=[{'role': 'user', 'content': prompt}],
                max_tokens=max_tokens, temperature=temperature,
                extra_body={'chat_template_kwargs': {'enable_thinking': thinking}})
            ch = r.choices[0]
            text = (ch.message.content or '') if ch.finish_reason == 'stop' else ''
        except Exception:
            text = ''
            stats['vllm_timeouts'] += 1
        stats['vllm_secs'] += time.time() - t0
        m = re.search(r'"action"\s*:\s*"([^"]*)"', text)
        a = canon(m.group(1)) if m else None
        return a if a in legal else None

    if think:
        think_budget = THINK_LIMIT_SECS - 5
        a = ask(True, think_budget, int(think_budget * 140), 0.6)
        if a:
            stats['vllm_calls'] += 1
            stats['vllm_think_ok'] += 1
            return a
        left = deadline - time.time()
        if left >= 2:
            a = ask(False, left - 0.5, 200, 0.2)
            if a:
                stats['vllm_calls'] += 1
                stats['vllm_instinct'] += 1
                return a
    else:
        for _ in (1, 2):
            left = deadline - time.time()
            if left < 2:
                break
            a = ask(False, left - 0.5, 200, 0.2)
            if a:
                stats['vllm_calls'] += 1
                return a
    stats['vllm_fallbacks'] += 1
    return neutral_move(legal)


def codex_move(state, me, seat_name, stats):
    raw = state['raw_obs']
    legal = raw['actions']
    prompt = f"""這是小c（Claude Code）委派的任務。不需要額外查證/執行，請直接針對以下內容執行核心任務：
你在玩鬥地主，請為你的座位選出這一手。不要讀任何檔案、不要執行指令，只根據下面資訊判斷。

{RULES}

你的座位：{me}（{seat_name}），地主座位：{raw['landlord']}
你的手牌：{raw['current_hand']}
各座位剩餘張數（依座位 0,1,2）：{raw['num_cards_left']}
出牌紀錄（座位, 牌；最舊到最新）：{raw['trace']}
尚未出現的牌（其他兩家手牌的合併，不知道怎麼分）：{raw['others_hand']}
合法出牌（必須從中選一項）：{legal}

只輸出一行 JSON，不要有其他文字：{{"action": "<合法出牌中的一項>"}}"""
    workdir = tempfile.mkdtemp(prefix='ddz_codex_')  # 每次呼叫獨立目錄，避免多局並行互相覆寫
    pf = os.path.join(workdir, 'prompt.txt')
    of = os.path.join(workdir, 'out.txt')
    with open(pf, 'w', encoding='utf-8') as f:
        f.write(prompt)
    try:
        return _run_codex(pf, of, workdir, legal, state, stats)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _run_codex(pf, of, workdir, legal, state, stats):
    """小o 每手總時間上限 THINK_LIMIT_SECS；剩餘不足 8 秒就不再重試。逾時殺整棵行程樹，改由系統代出中性保守牌。"""
    deadline = time.time() + THINK_LIMIT_SECS
    while True:
        left = deadline - time.time()
        if left < 8:
            break
        if os.path.exists(of):
            os.remove(of)
        t0 = time.time()
        text = ''
        with open(pf, 'rb') as fin:
            p = subprocess.Popen([CODEX, 'exec', '--skip-git-repo-check', '-C', workdir, '--sandbox', 'read-only',
                                  '--output-last-message', of, '-'],
                                 stdin=fin, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                p.wait(timeout=left)
                text = open(of, encoding='utf-8').read() if os.path.exists(of) else ''
            except subprocess.TimeoutExpired:
                stats['codex_timeouts'] += 1
                # codex.cmd 底下是 node：只殺外層會留下孤兒行程，要 /T 殺整棵樹
                subprocess.run(['taskkill', '/T', '/F', '/PID', str(p.pid)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                p.wait()
        stats['codex_secs'] += time.time() - t0
        m = re.search(r'"action"\s*:\s*"([^"]*)"', text)
        if m:
            a = canon(m.group(1))
            if a in legal:
                stats['codex_calls'] += 1
                return a
    stats['fallbacks'] += 1
    return neutral_move(legal)


def make_agent(kind, me, stats):
    if kind == 'human':
        return lambda s: human(s, me)
    if kind == 'policy':
        return policy
    if kind == 'rule':
        return rule_policy()
    if kind == 'codex':
        return lambda s: codex_move(s, me, NAMES[me], stats)
    if kind == 'vllm':
        return lambda s: vllm_move(s, me, NAMES[me], stats)
    raise ValueError(kind)


def main():
    ap = argparse.ArgumentParser()
    for i in range(3):
        ap.add_argument(f'--p{i}', default='policy', choices=['human', 'policy', 'rule', 'codex', 'vllm'])
    ap.add_argument('--seed', type=int, default=None)
    ap.add_argument('--quiet', action='store_true')
    args = ap.parse_args()
    kinds = [args.p0, args.p1, args.p2]
    if not CODEX and 'codex' in kinds:
        sys.exit('找不到 codex CLI')
    if 'vllm' in kinds and not vllm_available():
        sys.exit('找不到 vLLM 設定（.vllm.env 或環境變數 VLLM_URL／VLLM_MODEL）')

    seed = args.seed if args.seed is not None else random.SystemRandom().randrange(10**6)
    env = rlcard.make('doudizhu', config={'seed': seed})
    np.random.seed(seed)
    stats = new_stats()
    agents = [make_agent(k, i, stats) for i, k in enumerate(kinds)]
    print(f'座位：' + ' / '.join(f'{NAMES[i]}={k}' for i, k in enumerate(kinds)) + f'，種子 {seed}')

    state, pid = env.reset()
    n = 0
    while not env.is_over():
        try:
            a = agents[pid](state)
        except (KeyboardInterrupt, EOFError):
            print('\n離開，不記錄。')
            return
        n += 1
        if kinds[pid] != 'human' and not args.quiet:
            print(f'  [{n:02d}] {NAMES[pid]}({kinds[pid]}): {a}   剩 {state["raw_obs"]["num_cards_left"][pid] - (0 if a == "pass" else len(a))}', flush=True)
        state, pid = env.step(a, raw_action=True)

    landlord_won = bool(env.get_payoffs()[0] > 0)
    print('=' * 50)
    print('地主方勝' if landlord_won else '農民方勝', f'| 小o 呼叫 {stats["codex_calls"]} 次、逾時 {stats["codex_timeouts"]} 次、代出保守牌 {stats["fallbacks"]} 次、'
          f'小o 總耗時 {stats["codex_secs"]:.0f}s | vLLM 呼叫 {stats["vllm_calls"]} 次、'
          f'（思考完成 {stats["vllm_think_ok"]}、直覺答案 {stats["vllm_instinct"]}）、逾時 {stats["vllm_timeouts"]} 次、'
          f'代出保守牌 {stats["vllm_fallbacks"]} 次、'
          f'耗時 {stats["vllm_secs"]:.0f}s')
    with open(LOG, 'a', encoding='utf-8') as f:
        f.write(json.dumps({'t': time.strftime('%F %T'), 'seed': seed, 'seats': kinds, 'landlord_won': landlord_won,
                            **{k: (round(v, 1) if isinstance(v, float) else v) for k, v in stats.items()}},
                           ensure_ascii=False) + '\n')


if __name__ == '__main__':
    main()
