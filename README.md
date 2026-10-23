# 鬥地主 × Heuristic Learning（HL）

用 **Heuristic Learning**（以程式碼當策略、由 coding agent 迭代修改，不用梯度）研究鬥地主，並附一個可以直接玩的單檔遊戲。

這個專案有三塊：

| 部分 | 是什麼 | 需要什麼 |
|---|---|---|
| [`doudizhu_solo.html`](doudizhu_solo.html) | **單檔離線遊戲**：單人遊玩、NPC 補位、三種難度、花色大小規則 | 一個瀏覽器，雙擊開啟 |
| [`policy.py`](policy.py) 等 | **HL 策略研究**：啟發式策略、對 RLCard 規則 bot 的配對評估框架、失敗實驗 | Python 3.13、RLCard |
| [`server.py`](server.py) + [`static/index.html`](static/index.html) | **網頁牌桌**：人類、規則 bot、HL 策略、Claude（API）、Codex CLI、vLLM 模型同桌對打並複盤 | Python（FastAPI） |

> 本專案由作者與 [Claude Code](https://claude.com/claude-code) 協作開發。

---

## 1. 單檔遊戲（最快上手）

用瀏覽器開啟 [`doudizhu_solo.html`](doudizhu_solo.html) 即可，**不需要伺服器、不載入任何外部資源**。

- **流程**：選「單人遊玩」→ 選「地主」或「農民」（農民可選座位）→ 缺的角色由 NPC 補上。也有「觀戰」模式（三位 NPC 對打）。
- **花色大小**：♠ > ♥ > ♦ > ♣。同點數、同牌型時以較大花色壓過（對子、順子等看最大那張）；**點數永遠優先於花色**。傳統鬥地主花色不分大小，所以這是可以在設定關閉的規則，預設開啟。
- **NPC 難度**
  - **簡單**：只出最小的牌。
  - **標準**：本專案 HL 啟發式（v2）的 JavaScript 移植。
  - **困難**：蒙地卡羅搜尋（determinization）。只使用該座位看得到的資訊（自己的手牌、各家張數、出牌紀錄、地主公開底牌），隨機分配未知的牌、模擬整局，選勝率最高的一手；每手約 1～2 秒。
- 規則：54 張牌，地主 20 張（含公開的 3 張底牌）、農民各 17 張；沒有叫牌階段（由你選身分決定誰是地主）。
- 網址參數（測試用）：`?auto=1&seed=5&level=easy|hard|expert&spectate=1&fast=1&suit=0&role=farmer`。

### 引擎測試（Node；以 v22 測試）

```bash
node tests/solo_engine.test.js   # 牌型辨識、花色規則、合法出牌枚舉、整局 NPC 對打（合法性、牌數守恆）
node tests/solo_pimc.test.js     # 困難 NPC 的「不偷看」資訊隔離測試、抽樣不變量、合法性
node tests/solo_strength.js 1000 25 30   # 困難 vs 標準的強度比較（起始種子 局數 每手樣本數）
```

「不偷看」測試的做法：把其他人手上看不到的牌換成另一種合理分配（張數、公開底牌、已出的牌都不變），困難 NPC 在同樣亂數下必須做出完全相同的選擇。我們也故意讓它偷看，確認測試會失敗。

**強度**（300 個種子，同種子配對，對手是標準 NPC）：

| 角色 | 全標準 | 困難 | 配對差（95% 區間） |
|---|---|---|---|
| 地主 | 68.3% | 78.7% | +10.3 pp（+5.3 ～ +15.4） |
| 農民 | 31.7% | 72.0% | +40.3 pp（+34.3 ～ +46.3） |

> ⚠️ 這是**最有利的情境**：困難 NPC 模擬對手時假設對手用標準策略，而這次的對手剛好就是標準 NPC。面對真人，優勢一定比這小，沒有量化。

---

## 2. HL 策略研究（Python）

HL 的做法：策略是可讀的程式碼（[`policy.py`](policy.py)），用評估結果與失敗分析驅動修改，並以回歸測試固化已驗證的行為。**目前的迭代是人工讀結果後修改，並非自動化迴圈。**

### 評估框架

- 對手是 [RLCard](https://github.com/datamllab/rlcard) 的規則 bot（`DouDizhuRuleAgentV1`）；基線是它坐滿三個位置，**同一批發牌種子配對比較**。
- 種子分成迭代用（dev 0–1999）與只在里程碑跑一次的 held-out（100000–101999）。
- RLCard 固定 0 號為地主、沒有叫牌階段。

### 結果（對 RLCard 規則 bot，2000 局）

| 版本 | 角色 | 勝率 | 對基線的配對差 |
|---|---|---|---|
| policy v2（held-out） | 地主 | 71.4% | +21.5 pp（+19.0 ～ +24.0） |
| policy v2（held-out） | 農民 | 54.7% | +4.6 pp（+2.2 ～ +7.0） |

- **v2 的迭代**：v0 農民端與基線打平 → v1 加三條規則反而變差 → 消融實驗發現「保留 2／王」對農民有害 → 關掉後得到 v2。
- **失敗實驗 v3（殘局安全領出）**：對手剩 ≤2 張時，用未出現牌池算每個領出被壓過的機率並選最安全者。dev 上兩個角色都**變差約 1 pp**（區間不含 0），所以旗標預設關閉、沒有跑 held-out。診斷（逐局找第一個分歧點、分類統計）顯示傷害集中在「對手剩 2 張」；原先假設「提早耗掉大牌」經反事實實驗（禁領 2／王）**沒有被驗證**，真正原因未知。
- 基線很弱（RLCard 的 naive 規則 bot），**沒有和 DouZero 等強 AI 比較**；不含叫牌與對手手牌推論。

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt   # Linux/macOS 為 .venv/bin/pip
python -m unittest tests.test_policy                                     # 規則測試＋回歸門檻
python compare.py dev 5,2          # v2 vs v3 配對比較（容忍 5%、對手剩 ≤2 張啟動）
python diagnose.py dev 5 2         # v2 vs v3 第一分歧點診斷
```

評估的重現性：不同 Python 行程間 RLCard 合法動作順序可能因雜湊隨機化而不同，跨次評估約有 ±0.2 pp 抖動；比較請在同一行程內做（`compare.py` 已是），或設 `PYTHONHASHSEED=0`。

---

## 3. 網頁牌桌（多種玩家同桌）

```bash
python server.py        # → http://127.0.0.1:8765（只綁本機）
```

每個座位可以選：人類（瀏覽器）、`external`（由另一個程式／AI 透過 HTTP API 入座）、`policy`（本專案 HL 策略）、`rule`（RLCard 規則 bot）、`codex`（[Codex CLI](https://github.com/openai/codex)，唯讀、每手一次呼叫）、`vllm`（OpenAI 相容端點上的模型）。

- 視角隔離：`/api/state` 只回該座位看得到的資訊；對局結束後 `/api/replay` 才開放完整牌局，並存成 `replays/*.json`。
- 伺服器一次只跑一局；有未結束的局時開新局回 409，必須明確 `force`。
- 複盤頁會標出逐手耗時與「逾時由系統代出」的手。

### LLM 玩家的公平原則

- **只有 HL 策略（`policy.py`）能使用策略；LLM 玩家完全靠自己對規則的理解。**提示詞只有規則說明、該座位可見的牌局資訊與合法出牌清單，沒有任何策略提示（`tests/test_llm_players.py` 守門，含「這些函式不得引用 policy」）。
- **每手思考時間上限 40 秒**（`arena.THINK_LIMIT_SECS`）。逾時或回傳非法動作時，由系統代出「中性保守牌」（能 pass 就 pass，否則出最小單張），不使用 HL 策略代打，並在結果中誠實計數。
- 用官方 `openai` 客戶端時務必 `max_retries=0`：預設逾時會自動重試，一次呼叫可卡到約三倍時限而擊穿上限（我們在評估中踩到，`tests/test_llm_players.py::HardTimeLimitWithRealClientTests` 用真實客戶端打一個不回應的本機伺服器來守住）。

### 設定 vLLM 端點

```bash
cp .vllm.env.example .vllm.env    # 填入你自己的 VLLM_URL（含 /v1）與 VLLM_MODEL；此檔不會被提交
```

### 一個 vLLM 模型的量測結果（僅供參考）

在某個 35B 等級的開源模型、**直覺模式（關閉思考）**下，對 RLCard 規則 bot（300 個種子，同種子配對）：地主 11.7%、農民 8.3%，遠低於規則 bot 基線（47.3%／52.7%）與 policy v2（69.0%／53.0%）。觀察到的風格是過度被動（該壓不壓）。思考模式的樣本太小（4 個種子）、無法下結論。原始資料在 [`results/`](results/)。

---

## 專案結構

```
doudizhu_solo.html      單檔離線遊戲（引擎＋NPC＋UI）
policy.py               HL 啟發式策略（v2；v3 實驗旗標預設關閉）
runner.py compare.py    評估框架（配對比較）
diagnose.py ablate.py   第一分歧點診斷、消融實驗
arena.py                多方對戰（human／policy／rule／codex／vllm）
server.py static/       網頁牌桌（FastAPI）
eval_llm.py             LLM 玩家對規則 bot 的多行程評估
run_matches.py          透過伺服器 API 連續跑多局
ext_client.py           外部玩家的 API 客戶端範例
tests/                  Python 測試（unittest）與單檔遊戲的 Node 測試
results/                精選的評估原始資料
docs/NOTES.md           開發筆記（較詳細的實驗紀錄）
```

## 授權

[MIT](LICENSE)
