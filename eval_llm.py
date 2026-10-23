"""量化 vLLM 玩家對 rule-v1 的勝率，並與 policy v2、rule-v1 基線配對比較。

同一個種子、同一個對手 rule-v1：
  地主角色 = 待測者坐地主；農民角色 = 待測者坐兩個農民座位。基線 = rule-v1 坐滿三位。
多行程平行（np.random 是全域狀態，不能用執行緒）；結果逐局寫入 JSONL，中斷可續跑。
用法：PYTHONHASHSEED=0 python eval_llm.py run <起始種子> <局數> [workers]
      python eval_llm.py report <jsonl>
"""
import json
import math
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.environ.get('EVAL_OUT', os.path.join(HERE, 'eval_llm_results.jsonl'))


def work(seed):
    import arena
    import policy as P
    from runner import play_one, rule_policy
    rule = rule_policy()
    out = {'seed': seed}
    try:
        out['base_L'] = bool(play_one(seed, [rule, rule, rule]))
        out['v2_L'] = bool(play_one(seed, [P.policy, rule, rule]))
        out['v2_P'] = not play_one(seed, [rule, P.policy, P.policy])
        for role in ('L', 'P'):
            stats = arena.new_stats()

            def llm(state, stats=stats):
                s = state['raw_obs']['self']
                return arena.vllm_move(state, s, arena.NAMES[s], stats)
            if role == 'L':
                out['llm_L'] = bool(play_one(seed, [llm, rule, rule]))
            else:
                out['llm_P'] = not play_one(seed, [rule, llm, llm])
            out[f'calls_{role}'] = stats['vllm_calls']
            out[f'fb_{role}'] = stats['vllm_fallbacks']          # 系統代出中性保守牌的次數
            out[f'secs_{role}'] = round(stats['vllm_secs'], 1)
            out[f'think_ok_{role}'] = stats['vllm_think_ok']
            out[f'instinct_{role}'] = stats['vllm_instinct']
            out[f'timeouts_{role}'] = stats['vllm_timeouts']
    except Exception as e:  # 不讓單局錯誤中斷整批；報告時會列出
        out['error'] = f'{type(e).__name__}: {e}'[:200]
    return out


def run(start, n, workers):
    done = set()
    if os.path.exists(OUT):
        with open(OUT, encoding='utf-8') as f:
            done = {json.loads(l)['seed'] for l in f if l.strip()}
    todo = [s for s in range(start, start + n) if s not in done]
    print(f'待跑 {len(todo)} 局（已完成 {len(done)}），workers={workers}', flush=True)
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=workers) as ex, open(OUT, 'a', encoding='utf-8') as f:
        futs = [ex.submit(work, s) for s in todo]
        for i, fu in enumerate(as_completed(futs), 1):
            f.write(json.dumps(fu.result(), ensure_ascii=False) + '\n')
            f.flush()
            if i % 10 == 0 or i == len(futs):
                print(f'  {i}/{len(futs)}  {time.time() - t0:.0f}s', flush=True)


def wilson(k, n, z=1.96):
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (c - h) / d, (c + h) / d


def paired(a, b):
    d = np.array(a, float) - np.array(b, float)
    se = d.std(ddof=1) / math.sqrt(len(d))
    return d.mean(), d.mean() - 1.96 * se, d.mean() + 1.96 * se


def report(path):
    rows = [json.loads(l) for l in open(path, encoding='utf-8') if l.strip()]
    bad = [r for r in rows if 'error' in r]
    rows = [r for r in rows if 'error' not in r]
    print(f'完成 {len(rows)} 局（種子×兩角色），錯誤 {len(bad)} 局' + (f'：{bad[0]["error"]}' if bad else ''))
    for role, name in (('L', '地主'), ('P', '農民')):
        base = [(not r['base_L']) if role == 'P' else r['base_L'] for r in rows]
        v2 = [r[f'v2_{role}'] for r in rows]
        llm = [r[f'llm_{role}'] for r in rows]
        n = len(rows)
        k = sum(llm)
        lo, hi = wilson(k, n)
        print(f'\n【{name}角色】n={n}')
        print(f'  rule-v1 基線  {np.mean(base):.3f}')
        print(f'  policy v2    {np.mean(v2):.3f}')
        print(f'  vLLM         {k / n:.3f}  Wilson95% ({lo:.3f}, {hi:.3f})')
        for lab, ref in (('vs rule-v1 基線', base), ('vs policy v2', v2)):
            m, a, b = paired(llm, ref)
            print(f'  配對差 vLLM {lab}: {m:+.3f}  CI ({a:+.3f}, {b:+.3f})')
        calls = sum(r[f'calls_{role}'] for r in rows)
        fb = sum(r[f'fb_{role}'] for r in rows)
        secs = sum(r[f'secs_{role}'] for r in rows)
        games_fb = sum(r[f'fb_{role}'] > 0 for r in rows)
        tk = sum(r.get(f'think_ok_{role}', 0) for r in rows)
        ins = sum(r.get(f'instinct_{role}', 0) for r in rows)
        to = sum(r.get(f'timeouts_{role}', 0) for r in rows)
        print(f'  vLLM 有效呼叫 {calls}（思考完成 {tk}、直覺答案 {ins}）、逾時/錯誤 {to}、'
              f'系統代出保守牌 {fb}（{fb / max(1, calls + fb):.1%} 的手）、含代出的局 {games_fb}/{n}、呼叫累計 {secs:.0f}s')
        clean = [r for r in rows if r[f'fb_{role}'] == 0]
        if clean and len(clean) < n:
            print(f'  （僅看零代出的 {len(clean)} 局：vLLM {np.mean([r[f"llm_{role}"] for r in clean]):.3f}，'
                  f'同局 v2 {np.mean([r[f"v2_{role}"] for r in clean]):.3f}）')


if __name__ == '__main__':
    if sys.argv[1] == 'run':
        run(int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]) if len(sys.argv) > 4 else 8)
    else:
        report(sys.argv[2] if len(sys.argv) > 2 else OUT)
