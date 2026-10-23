import os
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient

import arena
import policy as P
import server

_tmp = tempfile.mkdtemp()
server.REPLAY_DIR = os.path.join(_tmp, 'replays')
os.makedirs(server.REPLAY_DIR, exist_ok=True)
arena.LOG = os.path.join(_tmp, 'games.jsonl')
c = TestClient(server.app)


def new(kinds, seed=11):
    r = c.post('/api/new', json={'kinds': kinds, 'seed': seed, 'force': True})
    assert r.status_code == 200, r.text
    return r.json()


def wait_my_turn(seat, tok, timeout=20):
    t = time.time()
    while time.time() - t < timeout:
        v = c.get('/api/state', params={'seat': seat, 'token': tok}).json()
        if v['over'] or v['my_turn']:
            return v
        time.sleep(0.05)
    raise AssertionError('等不到輪到自己')


class ServerTests(unittest.TestCase):
    def test_full_game_with_external_seat_follows_policy_hint(self):
        g = new(['external', 'policy', 'policy'])
        tok = g['tokens']['0']
        for _ in range(200):
            v = wait_my_turn(0, tok)
            if v['over']:
                break
            h = c.get('/api/hint', params={'seat': 0, 'token': tok}).json()['suggest']
            self.assertIn(h, v['legal'])
            c.post('/api/move', json={'seat': 0, 'token': tok, 'action': h})
        else:
            self.fail('局數過長')
        r = c.get('/api/replay').json()
        self.assertEqual(len(r['initial']), 3)
        # 外部座位全程照 policy 建議 → 與建議 100% 相同
        self.assertEqual(r['per_seat']['0']['agree_policy'], r['per_seat']['0']['moves'])

    def test_view_hides_other_hands_and_token_checks(self):
        g = new(['human', 'policy', 'policy'])
        tok = g['tokens']['0']
        v = c.get('/api/state', params={'seat': 0, 'token': tok}).json()
        self.assertEqual(len(v['hand']), 20)
        self.assertNotIn('initial', v)
        self.assertNotIn('reveal', v)
        self.assertEqual(c.get('/api/state', params={'seat': 0, 'token': 'bad'}).status_code, 403)
        self.assertEqual(c.get('/api/state', params={'seat': 1, 'token': tok}).status_code, 403)  # bot 座位不可窺視
        self.assertEqual(c.get('/api/replay').status_code, 404)  # 未結束不給複盤
        pub = c.get('/api/public').json()
        self.assertNotIn('hand', pub)
        self.assertEqual(c.get('/api/admin/tokens').json()['external'], {})  # human token 不外流

    def test_illegal_and_out_of_turn_moves(self):
        g = new(['policy', 'human', 'policy'], seed=5)
        tok = g['tokens']['1']
        v = wait_my_turn(1, tok)
        self.assertEqual(c.post('/api/move', json={'seat': 1, 'token': tok, 'action': 'zzz'}).status_code, 422)
        self.assertEqual(c.post('/api/move', json={'seat': 1, 'token': tok, 'action': v['hand'][0] * 5}).status_code, 422)

    def test_new_game_refuses_to_replace_unfinished_game_unless_forced(self):
        new(['human', 'policy', 'policy'])                    # 有人類座位，不會自己結束
        first = c.get('/api/public').json()['game_id']
        r = c.post('/api/new', json={'kinds': ['policy'] * 3, 'seed': 3})
        self.assertEqual(r.status_code, 409)
        self.assertIn('進行中', r.json()['detail'])
        self.assertEqual(c.get('/api/public').json()['game_id'], first)   # 原本的局沒被取代
        r = c.post('/api/new', json={'kinds': ['policy'] * 3, 'seed': 3, 'force': True})
        self.assertEqual(r.status_code, 200)
        self.assertNotEqual(c.get('/api/public').json()['game_id'], first)

    def test_new_game_allowed_after_previous_finished(self):
        new(['policy'] * 3, seed=4)
        for _ in range(200):
            if c.get('/api/public').json()['over']:
                break
            time.sleep(0.1)
        self.assertEqual(c.post('/api/new', json={'kinds': ['policy'] * 3, 'seed': 5}).status_code, 200)

    def test_timeout_fallback_moves_are_flagged_forced(self):
        class Dead:
            chat = SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: (_ for _ in ()).throw(TimeoutError())))

            def with_options(self, **kw):
                return self
        old = (arena._VLLM_CLIENT, arena._vllm_cfg)
        arena._VLLM_CLIENT = Dead()
        # 不依賴本機 .vllm.env：自備假設定（clone 下來沒有該檔案時 vllm_available() 會是 False）
        arena._vllm_cfg = lambda: {'VLLM_URL': 'http://127.0.0.1:9/v1', 'VLLM_MODEL': 'test-model', 'VLLM_THINK': '0'}
        try:
            new(['vllm', 'policy', 'policy'], seed=6)
            for _ in range(300):
                if c.get('/api/public').json()['over']:
                    break
                time.sleep(0.1)
            r = c.get('/api/replay').json()
        finally:
            arena._VLLM_CLIENT, arena._vllm_cfg = old
        llm = [m for m in r['moves'] if m['kind'] == 'vllm']
        self.assertTrue(llm and all(m['forced'] for m in llm))                       # 每手都逾時 → 全是代出
        self.assertFalse(any(m['forced'] for m in r['moves'] if m['kind'] == 'policy'))
        self.assertEqual(r['stats']['vllm_fallbacks'], len(llm))
        for m in llm:   # 代出的是中性保守牌：能 pass 就 pass，否則最小單張；絕不是 HL 的選擇邏輯
            self.assertTrue(m['action'] == 'pass' or len(m['action']) == 1)

    def test_strategy_roundtrip_and_validation(self):
        old = dict(P.PARAMS)
        try:
            r = c.post('/api/strategy', json={'params': {'bomb_opp_left': 9}}).json()
            self.assertEqual(r['params']['bomb_opp_left'], 9)
            self.assertEqual(P.PARAMS['bomb_opp_left'], 9)
            self.assertEqual(c.post('/api/strategy', json={'flags': {'nope': True}}).status_code, 400)
        finally:
            P.PARAMS.update(old)


if __name__ == '__main__':
    unittest.main()
