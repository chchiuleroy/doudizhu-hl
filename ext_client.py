"""小c 本人（Claude）的 API 客戶端：只讀 external 座位的視角。

用法：
  python ext_client.py wait [秒]     等到輪到我或牌局結束，印出我的視角（預設最多等 100 秒）
  python ext_client.py move <牌>     出牌（pass 表示不出）
token 來自 /api/admin/tokens（只含 external 座位），拿不到 human 座位的 token 也不會去讀對手視角。
"""
import json
import sys
import time
import urllib.error
import urllib.request

BASE = 'http://127.0.0.1:8765'


def call(path, body=None):
    req = urllib.request.Request(BASE + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={'content-type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return {'error': e.code, 'detail': json.loads(e.read().decode() or '{}').get('detail')}


def me():
    t = call('/api/admin/tokens')
    (seat, tok), = t['external'].items()
    return int(seat), tok


def show(v):
    names = v['names']
    print(f"我是座位 {v['seat']}（{names[str(v['seat'])]}），種子 {v['seed']}，已出 {v['n_moves']} 手")
    print('剩餘張數：', v['counts'], '| 輪到：', v['turn'], '| 思考中：', v['thinking'])
    print('我的手牌：', v['hand'])
    print('尚未出現的牌（其餘兩家合併）：', v['unseen'])
    print('出牌紀錄：', ' | '.join(f"{p}:{a}" for p, a in v['trace']))
    if v['my_turn']:
        print('合法出牌：', ' '.join(v['legal']))
    if v['over']:
        print('牌局結束')


def main():
    seat, tok = me()
    if sys.argv[1] == 'wait':
        limit = float(sys.argv[2]) if len(sys.argv) > 2 else 100
        t0 = time.time()
        while True:
            v = call(f'/api/state?seat={seat}&token={tok}')
            if v.get('over') or v.get('my_turn') or time.time() - t0 > limit:
                break
            time.sleep(1)
        show(v)
    elif sys.argv[1] == 'move':
        r = call('/api/move', {'seat': seat, 'token': tok, 'action': sys.argv[2]})
        print(r if 'error' in r else f"已出：{sys.argv[2]}")


main()
