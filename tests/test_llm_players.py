"""公平原則：只有小c 能用 HL 策略；小o 與 vLLM 只拿到規則＋該座位可見資訊＋合法出牌，沒有策略提示，
逾時或非法時由系統代出「中性保守牌」（不借用 HL 策略）。每手思考時間上限 40 秒（Roy 2026-10-23 由 30 改 40）。"""
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import arena


def make_state():
    raw = {'current_hand': '34558TJJQA2B',
           'actions': ['3', '4', '5', '8', 'T', 'J', 'Q', 'A', '2', 'B', '55', 'JJ'],
           'trace': [(0, 'pass'), (1, 'pass')], 'self': 2, 'landlord': 0,
           'num_cards_left': [2, 7, 12], 'others_hand': '4567TJQ22'}
    return {'raw_obs': raw}


# 策略性字眼：提示詞裡出現就代表有人在替 LLM 玩家下策略（規則說明與牌局資訊不應含這些）
FORBIDDEN = ['策略', '偏好', '建議', '優先', '保留', 'policy', 'HL', '啟發']


def reply(content, finish='stop'):
    msg = SimpleNamespace(content=content)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason=finish)])


class FakeClient:
    """script：依序回傳的 reply 或要丟出的例外；同時記錄每次請求的提示詞、是否開思考、timeout。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []
        self._timeout = None
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def with_options(self, timeout=None):
        self._timeout = timeout
        return self

    def _create(self, **kw):
        self.calls.append({'prompt': kw['messages'][0]['content'], 'thinking': kw['extra_body']['chat_template_kwargs']['enable_thinking'],
                           'timeout': self._timeout, 'max_tokens': kw['max_tokens']})
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


# 測試自備假設定：不依賴使用者本機的 .vllm.env（別人 clone 下來沒有這個檔案，測試也必須能過）
FAKE_CFG = {'VLLM_URL': 'http://127.0.0.1:9/v1', 'VLLM_MODEL': 'test-model'}


class WithClient:
    def __init__(self, client, think='0'):
        self.client, self.think = client, think

    def __enter__(self):
        self.old = (arena._VLLM_CLIENT, arena._vllm_cfg)
        arena._VLLM_CLIENT = self.client
        arena._vllm_cfg = lambda: {**FAKE_CFG, 'VLLM_THINK': self.think}
        return self

    def __exit__(self, *a):
        arena._VLLM_CLIENT, arena._vllm_cfg = self.old


class LlmPlayerPromptTests(unittest.TestCase):
    def test_vllm_prompt_has_no_strategy_hints_and_only_visible_info(self):
        fake = FakeClient([reply('{"action": "55"}')])
        with WithClient(fake):
            stats = arena.new_stats()
            a = arena.vllm_move(make_state(), 2, '地主上家', stats)
        self.assertEqual(a, '55')
        self.assertEqual(stats['vllm_calls'], 1)
        p = fake.calls[0]['prompt']
        for w in FORBIDDEN:
            self.assertNotIn(w, p, f'vLLM 提示詞不應含「{w}」')
        for must in ('34558TJJQA2B', '4567TJQ22', "['3', '4'"):  # 自己手牌、牌池、合法出牌
            self.assertIn(must, p)

    def test_codex_prompt_has_no_strategy_hints(self):
        captured = {}

        def fake_run(pf, of, workdir, legal, state, stats):
            captured['p'] = open(pf, encoding='utf-8').read()
            return '55'

        old = arena._run_codex
        arena._run_codex = fake_run
        try:
            arena.codex_move(make_state(), 2, '地主上家', arena.new_stats())
        finally:
            arena._run_codex = old
        for w in FORBIDDEN:
            self.assertNotIn(w, captured['p'], f'小o 提示詞不應含「{w}」')
        self.assertIn('34558TJJQA2B', captured['p'])


class NeutralFallbackTests(unittest.TestCase):
    def test_neutral_move_rules(self):
        self.assertEqual(arena.neutral_move(['pass', '55', '3']), 'pass')
        self.assertEqual(arena.neutral_move(['5', '55', 'T', '3', '33']), '3')      # 領出：最小單張
        self.assertEqual(arena.neutral_move(['55', 'JJ']), '55')                   # 沒有單張就取最小的

    def test_llm_players_never_reference_hl_policy(self):
        # 守門：LLM 玩家的程式路徑不得引用 policy（HL 策略）——否則逾時代打就等於偷用策略
        for f in (arena.vllm_move, arena.codex_move, arena._run_codex, arena.neutral_move):
            self.assertNotIn('policy', f.__code__.co_names, f.__name__)

    def test_illegal_answer_gives_neutral_not_policy(self):
        fake = FakeClient([reply('{"action": "99"}'), reply('{"action": "99"}')])
        with WithClient(fake):
            stats = arena.new_stats()
            st = make_state()
            a = arena.vllm_move(st, 2, '地主上家', stats)
        self.assertEqual(a, arena.neutral_move(st['raw_obs']['actions']))
        self.assertEqual(stats['vllm_fallbacks'], 1)
        self.assertEqual(stats['vllm_calls'], 0)

    def test_timeouts_are_counted_and_become_neutral(self):
        fake = FakeClient([TimeoutError('t'), TimeoutError('t')])
        with WithClient(fake):
            stats = arena.new_stats()
            st = make_state()
            a = arena.vllm_move(st, 2, '地主上家', stats)
        self.assertEqual(a, arena.neutral_move(st['raw_obs']['actions']))
        self.assertEqual(stats['vllm_timeouts'], 2)
        self.assertEqual(stats['vllm_fallbacks'], 1)


class HardTimeLimitWithRealClientTests(unittest.TestCase):
    """用真實 openai 客戶端打「永遠不回應的伺服器」：實際耗時必須守在每手上限內（不能被自動重試放大）。"""

    def test_hanging_server_cannot_exceed_the_per_move_limit(self):
        import socketserver
        import threading
        import time

        class Hang(socketserver.BaseRequestHandler):
            def handle(self):
                self.request.recv(65536)
                time.sleep(30)           # 收到請求後不回應

        srv = socketserver.ThreadingTCPServer(('127.0.0.1', 0), Hang)
        srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        old = (arena._VLLM_CLIENT, arena._vllm_cfg, arena.THINK_LIMIT_SECS)
        arena._VLLM_CLIENT = None                      # 強迫 vllm_move 自己建立真實客戶端
        arena.THINK_LIMIT_SECS = 8.0
        arena._vllm_cfg = lambda: {**FAKE_CFG, 'VLLM_URL': f'http://127.0.0.1:{srv.server_address[1]}/v1', 'VLLM_THINK': '1'}
        try:
            stats = arena.new_stats()
            st = make_state()
            t0 = time.time()
            a = arena.vllm_move(st, 2, '地主上家', stats)
            elapsed = time.time() - t0
        finally:
            arena._VLLM_CLIENT, arena._vllm_cfg, arena.THINK_LIMIT_SECS = old
            srv.shutdown()
        self.assertLess(elapsed, 8.0 + 1.5, f'耗時 {elapsed:.1f}s 超過上限（自動重試沒關？）')
        self.assertEqual(a, arena.neutral_move(st['raw_obs']['actions']))
        self.assertEqual(stats['vllm_fallbacks'], 1)


class ThinkLimitTests(unittest.TestCase):
    def test_think_mode_uses_budget_below_limit_and_valid_answer_counts(self):
        fake = FakeClient([reply('{"action": "JJ"}')])
        with WithClient(fake, think='1'):
            stats = arena.new_stats()
            a = arena.vllm_move(make_state(), 2, '地主上家', stats)
        self.assertEqual(a, 'JJ')
        c = fake.calls[0]
        self.assertTrue(c['thinking'])
        self.assertLessEqual(c['timeout'], arena.THINK_LIMIT_SECS - 5 + 1e-9)   # 思考預算 = 上限 − 5 秒
        self.assertEqual(c['max_tokens'], int((arena.THINK_LIMIT_SECS - 5) * 140))   # token 預算隨時限縮放
        self.assertEqual((stats['vllm_think_ok'], stats['vllm_instinct']), (1, 0))

    def test_unfinished_thinking_falls_to_instinct_within_remaining_time(self):
        # 第一次：思考被 max_tokens 截斷（finish=length、content 空）→ 第二次關思考要直覺答案
        fake = FakeClient([reply('', finish='length'), reply('{"action": "55"}')])
        with WithClient(fake, think='1'):
            stats = arena.new_stats()
            a = arena.vllm_move(make_state(), 2, '地主上家', stats)
        self.assertEqual(a, '55')
        self.assertEqual([c['thinking'] for c in fake.calls], [True, False])
        self.assertLess(fake.calls[1]['timeout'], arena.THINK_LIMIT_SECS)      # 剩餘時間，不會超過總額
        self.assertEqual((stats['vllm_think_ok'], stats['vllm_instinct']), (0, 1))

    def test_total_budget_is_40_seconds_by_default(self):
        self.assertEqual(arena.THINK_LIMIT_SECS, 40.0)


if __name__ == '__main__':
    unittest.main()
