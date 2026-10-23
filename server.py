"""鬥地主牌桌伺服器（只綁 127.0.0.1）。

座位類型：
  human    你在瀏覽器入座（持 token）
  external 小c 本人（Claude）透過 HTTP API 入座（持 token，見 /api/admin/tokens）
  policy   小c 的 HL 啟發式策略（policy.py，可在 UI 調參）
  rule     RLCard rule-v1
  codex    小o（codex exec 唯讀，每手一次呼叫）
  vllm     Qwen3.6-35B-A3B（vLLM OpenAI 相容端點，設定在 .vllm.env）
視角隔離：/api/state 只回該座位看得到的資訊；對局結束後才開放 /api/replay 看完整牌局。
"""
import json
import os
import random
import secrets
import sys
import threading
import time

import numpy as np
import rlcard
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import arena as A  # noqa: E402
import policy as P  # noqa: E402
from play import NAMES, canon  # noqa: E402
from runner import rule_policy  # noqa: E402

KINDS = ('human', 'external', 'policy', 'rule', 'codex', 'vllm')
REPLAY_DIR = os.path.join(HERE, 'replays')
os.makedirs(REPLAY_DIR, exist_ok=True)
_RULE = rule_policy()

app = FastAPI()
GAME = None  # 目前唯一一局


class Game:
    def __init__(self, kinds, seed):
        self.id = secrets.token_hex(4)
        self.seed = seed
        self.kinds = kinds
        self.tokens = {i: secrets.token_urlsafe(9) for i, k in enumerate(kinds) if k in ('human', 'external')}
        self.env = rlcard.make('doudizhu', config={'seed': seed})
        np.random.seed(seed)
        self.env.reset()
        self.initial = [self.env.game.get_state(i)['current_hand'] for i in range(3)]
        self.moves = []
        self.lock = threading.RLock()
        self.worker = None
        self.thinking = None
        self.dead = False
        self.stats = A.new_stats()
        self.replay = None
        self.strategy = strategy_snapshot()

    # ---- 查詢 ----
    def pid(self):
        return self.env.get_player_id()

    def raw(self, seat):
        return self.env.game.get_state(seat)

    def view(self, seat):
        raw = self.raw(seat)
        over = self.env.is_over()
        my_turn = (not over) and self.pid() == seat
        v = {'game_id': self.id, 'seed': self.seed, 'seat': seat, 'kinds': self.kinds, 'names': NAMES,
             'landlord': raw['landlord'], 'hand': raw['current_hand'], 'trace': raw['trace'],
             'counts': raw['num_cards_left'], 'unseen': raw['others_hand'], 'turn': None if over else self.pid(),
             'my_turn': my_turn, 'legal': raw['actions'] if my_turn else [], 'thinking': self.thinking,
             'over': over, 'n_moves': len(self.moves), 'stats': self.stats}
        if over and self.replay:
            v['landlord_won'] = self.replay['landlord_won']
        return v

    # ---- 推進 ----
    def apply(self, seat, action, kind, ms=0, forced=False):
        """呼叫端需持有 lock。forced = 這手是逾時／非法而由系統代出的中性保守牌（複盤上標示）。"""
        raw = self.raw(seat)
        try:
            suggest = P.policy({'raw_obs': raw})
        except Exception:  # 建議只用於複盤，不能因它中斷牌局
            suggest = None
        self.moves.append({'n': len(self.moves) + 1, 'seat': seat, 'kind': kind, 'action': action,
                           'suggest': suggest, 'hand': raw['current_hand'], 'legal_n': len(raw['actions']),
                           'ms': int(ms), 'forced': bool(forced)})
        self.env.step(action, raw_action=True)
        if self.env.is_over():
            self.finish()

    def finish(self):
        won = bool(self.env.get_payoffs()[0] > 0)
        per = {}
        for s in range(3):
            ms = [m for m in self.moves if m['seat'] == s]
            per[s] = {'kind': self.kinds[s], 'moves': len(ms),
                      'passes': sum(m['action'] == 'pass' for m in ms),
                      'power': sum(len(m['action']) == 4 and len(set(m['action'])) == 1 or m['action'] == 'BR'
                                   for m in ms),
                      'agree_policy': sum(m['action'] == m['suggest'] for m in ms)}
        self.replay = {'game_id': self.id, 'seed': self.seed, 'kinds': self.kinds, 'initial': self.initial,
                       'landlord_won': won, 'moves': self.moves, 'per_seat': per, 'stats': self.stats,
                       'strategy': self.strategy, 'finished': time.strftime('%F %T')}
        with open(os.path.join(REPLAY_DIR, f'{time.strftime("%Y%m%d_%H%M%S")}_{self.id}.json'), 'w',
                  encoding='utf-8') as f:
            json.dump(self.replay, f, ensure_ascii=False, indent=1)
        with open(A.LOG, 'a', encoding='utf-8') as f:
            f.write(json.dumps({'t': self.replay['finished'], 'seed': self.seed, 'seats': self.kinds,
                                'landlord_won': won, 'source': 'server',
                                **{k: (round(v, 1) if isinstance(v, float) else v) for k, v in self.stats.items()}},
                               ensure_ascii=False) + '\n')

    def kick(self):
        """呼叫端需持有 lock。"""
        if self.worker is None:
            self.worker = threading.Thread(target=self._run_bots, daemon=True)
            self.worker.start()

    def _run_bots(self):
        while True:
            with self.lock:
                if self.dead or self.env.is_over():
                    self.worker = None
                    return
                pid = self.pid()
                kind = self.kinds[pid]
                if kind in ('human', 'external'):
                    self.worker = None
                    return
                self.thinking = pid
                state = {'raw_obs': self.raw(pid)}
            t0 = time.time()
            fb0 = self.stats['fallbacks'] + self.stats['vllm_fallbacks']
            try:
                a = self._bot_action(kind, pid, state)
            except Exception:
                # 意外錯誤的保底：LLM 座位用中性保守牌（不借用 HL 策略）；policy／rule 座位本來就是程式
                a = P.policy(state) if kind in ('policy', 'rule') else A.neutral_move(state['raw_obs']['actions'])
                self.stats['fallbacks'] += 1
            with self.lock:
                self.thinking = None
                if self.dead:
                    self.worker = None
                    return
                forced = self.stats['fallbacks'] + self.stats['vllm_fallbacks'] > fb0
                self.apply(pid, a, kind, (time.time() - t0) * 1000, forced)

    def _bot_action(self, kind, pid, state):
        if kind == 'policy':
            return P.policy(state)
        if kind == 'rule':
            return _RULE(state)
        if kind == 'vllm':
            return A.vllm_move(state, pid, NAMES[pid], self.stats)
        return A.codex_move(state, pid, NAMES[pid], self.stats)


def strategy_snapshot():
    return {'flags': dict(P.FLAGS), 'params': dict(P.PARAMS)}


def seat_game(seat, token):
    g = GAME
    if g is None:
        raise HTTPException(404, '還沒有牌局')
    if not 0 <= seat <= 2:
        raise HTTPException(400, 'seat 必須是 0/1/2')
    if g.kinds[seat] not in ('human', 'external') or g.tokens.get(seat) != token:
        raise HTTPException(403, 'token 不符或該座位不是玩家座位')
    return g


class NewGame(BaseModel):
    kinds: list[str]
    seed: int | None = None
    force: bool = False   # 目前有未結束的局時，必須明確 force 才會中止它


class Move(BaseModel):
    seat: int
    token: str
    action: str


class Strategy(BaseModel):
    flags: dict[str, bool] | None = None
    params: dict[str, int] | None = None


@app.get('/')
def index():
    return FileResponse(os.path.join(HERE, 'static', 'index.html'))


@app.post('/api/new')
def new_game(req: NewGame):
    global GAME
    if len(req.kinds) != 3 or any(k not in KINDS for k in req.kinds):
        raise HTTPException(400, f'kinds 需為 3 個，且屬於 {KINDS}')
    if 'codex' in req.kinds and not A.CODEX:
        raise HTTPException(400, '找不到 codex CLI')
    if 'vllm' in req.kinds and not A.vllm_available():
        raise HTTPException(400, '找不到 vLLM 設定（.vllm.env）')
    if GAME is not None and not GAME.env.is_over() and not req.force:
        # 伺服器一次只跑一局：悄悄取代會讓別人的局消失（2026-10-23 驗證賽被不明來源的開局插隊）
        raise HTTPException(409, f'目前有一局進行中（種子 {GAME.seed}，座位 {GAME.kinds}，已出 {len(GAME.moves)} 手）')
    if GAME is not None:
        GAME.dead = True
    seed = req.seed if req.seed is not None else random.SystemRandom().randrange(10**6)
    GAME = Game(req.kinds, seed)
    with GAME.lock:
        GAME.kick()
    return {'game_id': GAME.id, 'seed': seed, 'tokens': {str(k): v for k, v in GAME.tokens.items()}}


@app.get('/api/state')
def state(seat: int, token: str):
    g = seat_game(seat, token)
    with g.lock:
        return g.view(seat)


@app.post('/api/move')
def move(req: Move):
    g = seat_game(req.seat, req.token)
    with g.lock:
        if g.env.is_over():
            raise HTTPException(409, '牌局已結束')
        if g.pid() != req.seat:
            raise HTTPException(409, '還沒輪到你')
        a = canon(req.action)
        if a not in g.raw(req.seat)['actions']:
            raise HTTPException(422, f'不合法的出牌：{req.action}')
        g.apply(req.seat, a, g.kinds[req.seat])
        g.kick()
        return g.view(req.seat)


@app.get('/api/public')
def public():
    """觀戰用：只有公開資訊（出牌紀錄、各家張數、輪到誰），沒有任何手牌。"""
    g = GAME
    if g is None:
        raise HTTPException(404, '還沒有牌局')
    with g.lock:
        raw = g.raw(0)
        over = g.env.is_over()
        return {'game_id': g.id, 'seed': g.seed, 'kinds': g.kinds, 'landlord': 0, 'trace': raw['trace'],
                'counts': raw['num_cards_left'], 'turn': None if over else g.pid(), 'thinking': g.thinking,
                'over': over, 'stats': g.stats}


@app.get('/api/hint')
def hint(seat: int, token: str):
    g = seat_game(seat, token)
    with g.lock:
        if g.env.is_over() or g.pid() != seat:
            raise HTTPException(409, '輪到你的時候才有提示')
        return {'suggest': P.policy({'raw_obs': g.raw(seat)}), 'note': '小c 策略（policy）目前參數下的建議'}


@app.get('/api/strategy')
def get_strategy():
    return strategy_snapshot()


@app.post('/api/strategy')
def set_strategy(req: Strategy):
    for k, v in (req.flags or {}).items():
        if k not in P.FLAGS:
            raise HTTPException(400, f'未知旗標 {k}')
        P.FLAGS[k] = v
    for k, v in (req.params or {}).items():
        if k not in P.PARAMS:
            raise HTTPException(400, f'未知參數 {k}')
        P.PARAMS[k] = int(v)
    return strategy_snapshot()


@app.get('/api/replay')
def replay():
    g = GAME
    if g is None or g.replay is None:
        raise HTTPException(404, '牌局尚未結束，沒有複盤資料')
    return g.replay


@app.get('/api/admin/tokens')
def admin_tokens():
    """只回 external（小c 本人）座位的 token；human 座位 token 不外流。"""
    g = GAME
    if g is None:
        raise HTTPException(404, '還沒有牌局')
    return {'game_id': g.id, 'external': {str(k): v for k, v in g.tokens.items() if g.kinds[k] == 'external'}}


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=8765, log_level='warning')
