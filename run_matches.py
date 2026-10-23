"""透過牌桌伺服器 API 連續跑多局（走網頁實際使用的同一條路徑），驗證完成率、耗時、30 秒上限與代出保守牌。

用法：python run_matches.py run [起始索引] [結束索引]   # 賽程見 PLAN
      python run_matches.py report
結果逐局寫入 matches.jsonl（append），中斷可由索引續跑。伺服器一次只跑一局，所以序列進行。
"""
import json
import os
import sys
import time
import urllib.request

BASE = 'http://127.0.0.1:8765'
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'matches.jsonl')
STUCK_SECS = 300   # 出牌紀錄這麼久沒有增加就判定卡住

V, C, P = 'vllm', 'codex', 'policy'
PLAN = (
    [([V, V, V], 200 + i) for i in range(10)] +                      # A：vLLM 三方（快）
    [([P, V, V], 210 + i) for i in range(6)] +                       # B：HL 地主 vs vLLM 農民
    [([V, P, P], 216 + i) for i in range(4)] +                       # C：vLLM 地主 vs HL 農民
    [([C, V, V], 220 + i) for i in range(3)] +                       # D：小o 地主 vs vLLM 農民
    [([V, C, V], 223 + i) for i in range(3)]                         # E：小o 當農民之一
)


# 量測 40 秒上限下小o 被逾時代出的比例（2026-10-23）：小o 當地主 4 局、當農民之一 4 局
PLAN_CODEX40 = [([C, V, V], 500 + i) for i in range(4)] + [([V, C, V], 504 + i) for i in range(4)]
if os.environ.get('MATCH_PLAN') == 'codex40':
    PLAN = PLAN_CODEX40
    OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'matches_codex40.jsonl')


def call(path, body=None, timeout=60):
    req = urllib.request.Request(BASE + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={'content-type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def play(idx, kinds, seed):
    t0 = time.time()
    rec = {'idx': idx, 'kinds': kinds, 'seed': seed}
    try:
        mine = call('/api/new', {'kinds': kinds, 'seed': seed})['game_id']
        last_n, stuck_at = -1, time.time()
        while True:
            p = call('/api/public')
            if p['game_id'] != mine:   # 伺服器一次只跑一局：別的客戶端開了新局就會把這局取代
                rec['anomaly'] = f'被插隊：目前牌局 {p["game_id"]}（種子 {p["seed"]}，座位 {p["kinds"]}）不是我開的 {mine}'
                break
            if p['over']:
                break
            n = len(p['trace'])
            if n != last_n:
                last_n, stuck_at = n, time.time()
            if time.time() - stuck_at > STUCK_SECS:
                rec['anomaly'] = f'卡住：出牌紀錄 {n} 手後 {STUCK_SECS}s 無進展'
                break
            time.sleep(1)
        if 'anomaly' not in rec:
            r = call('/api/replay')
            assert r['game_id'] == mine and r['seed'] == seed and r['kinds'] == kinds, '複盤與我開的局不一致'
            rec['landlord_won'] = r['landlord_won']
            rec['moves'] = len(r['moves'])
            rec['stats'] = r['stats']
            for s, kind in enumerate(kinds):
                ms = [m['ms'] for m in r['moves'] if m['seat'] == s]
                rec[f'seat{s}'] = {'kind': kind, 'moves': len(ms), 'max_ms': max(ms), 'mean_ms': round(sum(ms) / len(ms)),
                                   'over_31s': sum(x > 31000 for x in ms),
                                   'agree_policy': r['per_seat'][str(s)]['agree_policy']}
    except Exception as e:
        rec['anomaly'] = f'{type(e).__name__}: {e}'[:200]
    rec['secs'] = round(time.time() - t0, 1)
    with open(OUT, 'a', encoding='utf-8') as f:
        f.write(json.dumps(rec, ensure_ascii=False) + '\n')
    return rec


def report():
    rows = [json.loads(l) for l in open(OUT, encoding='utf-8') if l.strip()]
    ok = [r for r in rows if 'anomaly' not in r]
    print(f'共 {len(rows)} 局，正常結束 {len(ok)}，異常 {len(rows) - len(ok)}')
    for r in rows:
        if 'anomaly' in r:
            print('  異常：', r['idx'], r['kinds'], r['seed'], r['anomaly'])
    groups = {}
    for r in ok:
        groups.setdefault(tuple(r['kinds']), []).append(r)
    for kinds, g in groups.items():
        lw = sum(r['landlord_won'] for r in g)
        st = {k: sum(r['stats'][k] for r in g) for k in g[0]['stats'] if isinstance(g[0]['stats'][k], (int, float))}
        mx = max(max(r[f'seat{s}']['max_ms'] for s in range(3)) for r in g)
        over = sum(r[f'seat{s}']['over_31s'] for r in g for s in range(3))
        print(f'\n{kinds}  局數 {len(g)}  地主方勝 {lw}/{len(g)}  平均每局 {sum(r["secs"] for r in g) / len(g):.0f}s  單手最長 {mx / 1000:.1f}s  超過31s的手 {over}')
        print('  ', {k: (round(v, 1) if isinstance(v, float) else v) for k, v in st.items() if v})


if __name__ == '__main__':
    if sys.argv[1] == 'run':
        a = int(sys.argv[2]) if len(sys.argv) > 2 else 0
        b = int(sys.argv[3]) if len(sys.argv) > 3 else len(PLAN)
        for i in range(a, min(b, len(PLAN))):
            r = play(i, *PLAN[i])
            print(i, PLAN[i][0], 'seed', PLAN[i][1], '->', r.get('anomaly') or ('地主勝' if r['landlord_won'] else '農民勝'),
                  f'{r["secs"]}s', flush=True)
    else:
        report()
