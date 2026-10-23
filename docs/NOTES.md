# 鬥地主 × HL（Heuristic Learning）

HS 骨架：`policy.py`（策略）、`runner.py`（評估）、`tests/`（回歸）、`ablate.py`（消融）。
環境：RLCard 1.2.0（venv 內，需 `setuptools` 補 distutils）。`policy_v0.py` 為迭代起點備份。

## 評估設計
- 對手 = RLCard `DouDizhuRuleAgentV1`；基線 = 它坐滿三個位置，同一批發牌種子配對比較。
- dev 種子 0..1999 用來迭代；held-out 100000..101999 只在里程碑跑。
- RLCard 固定 player 0 為地主、**無叫牌階段**。

## 結果（policy v2，勝率；括號為配對差 vs 基線）
| 集合 | 地主 | 農民 |
|---|---|---|
| dev | 0.693 (+0.206) | 0.565 (+0.051) |
| held-out | 0.714 (+0.215) | 0.547 (+0.046) |

迭代紀錄：v0 農民 ≈ 0 → v1 加三條規則，農民變差（-0.034）→ 消融發現「保留高牌」有害 → 關掉後 v2。

## 限度
- rule-v1 是 RLCard 的 naive 規則，很弱；**未與 DouZero 比較**。
- 未做隱藏資訊推論（對手手牌信念）、未做叫牌。
- 規則仍是手寫 + 我（coding agent）人工迭代，尚未自動化 HL 迴圈。

## 網頁牌桌（server.py + static/index.html）
啟動：`.\.venv\Scripts\python.exe server.py` → http://127.0.0.1:8765（只綁本機）。
座位類型：人類（瀏覽器）／小c本人（HTTP API，token 見 `/api/admin/tokens`）／小c策略（policy.py）／rule-v1／小o（codex exec 唯讀）。
視角隔離：`/api/state` 只回該座位可見資訊，bot 座位不可窺視；`/api/replay` 對局結束後才開放。
策略設定：UI 可即時調 policy 旗標與數值參數、給小o 的自然語言策略偏好。複盤 JSON 存在 `replays/`。
測試：`python -m unittest tests.test_policy tests.test_server`

## v3 endgame_safe 實驗（2026-10-23，結論：無效，預設關閉）
規則：對手剩 ≤2 張時，用未出現牌池算每個領出「被壓過的機率」，挑最安全者（`policy.py` 的 `endgame_safe` 旗標，`compare.py` 做 v2 對 v3 配對比較）。
dev 2000 局（v3 − v2 配對勝率差）：容忍 2%／5%／10% 皆為地主 −0.7~−0.9pp、農民 −1.0~−1.3pp，95% CI 皆不含 0（變差）；啟動門檻 ≤3 張更差（−1.8／−1.7pp）；≤1 張與 v2 逐局相同。
依事先訂的完成條件（dev 無明確提升就不跑 held-out、不改預設），**未跑 held-out、旗標維持關閉**。原因為假設（未驗證）：優先領出「最難被壓」的牌會提早耗掉 2／王，損失控場。

## 玩家類型與公平原則（2026-10-23）
**只有小c 能用 HL 策略（policy.py）；小o（codex）與 Qwen3.6（vLLM）都靠它們自己原本的智能解讀規則來玩。**
它們的提示詞只有：規則說明、該座位可見的牌局資訊（手牌、出牌紀錄、各家張數、未出現牌池）、合法出牌清單；沒有任何策略提示或參數（`tests/test_llm_players.py` 驗證，含突變驗證）。
vLLM 端點：OpenAI 相容 API，設定在 `.vllm.env`（gitignored），用官方 `openai` 客戶端、固定關閉思考模式（預設思考模式回空 content）。
注意：Python 內建 urllib 會被該端點前的 Cloudflare 擋（error 1010），要用 openai 客戶端。
重現性：不同 Python 行程間 RLCard 合法動作順序可能不同（雜湊隨機化），同分取牌略有差異，跨次評估約有 ±0.2pp 抖動；比較請在同一行程內做（compare.py 已是），或設 `PYTHONHASHSEED=0`。

## 每手思考上限 40 秒（2026-10-23 Roy 由 30 秒改為 40 秒；30 秒時小o 約 20–25% 的手被逾時代出）
小o 與 vLLM 共用 `arena.THINK_LIMIT_SECS`（預設 40 秒，環境變數 `DDZ_THINK_LIMIT` 可覆寫），為**每手總時間**；人類與小c 本人座位不受限。
**注意**：本節以下與 README 較早段落提到的 30 秒數字（實測耗時、代出比例、思考預算 25 秒／max_tokens 3500）都是 30 秒時代的紀錄；現在思考預算 = 上限 − 5 秒 = 35 秒、max_tokens = 預算 × 140 = 4900。
- 逾時或回傳非法動作 → 系統代出**中性保守牌**（`arena.neutral_move`：能 pass 就 pass，否則最小單張），**不使用 HL 策略**（舊版是退回 policy，已改；`tests/test_llm_players.py` 守門：這些函式不得引用 policy）。
- 小o 逾時會用 `taskkill /T /F` 殺整棵行程樹（codex.cmd 底下是 node，只殺外層會留孤兒行程）。
- vLLM 兩種模式（`.vllm.env` 的 `VLLM_THINK`）：0 直覺（預設）／1 思考（預算 25 秒、max_tokens 3500；此端點約 145 token/s，簡單殘局也常在 4000 token 內想不完；沒想完就用剩餘時間要一個關閉思考的直覺答案）。

## 單檔單人遊戲 `doudizhu_solo.html`（2026-10-23）
不需要伺服器，雙擊用瀏覽器開即可（完全離線）。花色大小 ♠ > ♥ > ♦ > ♣（同點數時決勝，可在設定關閉；點數優先於花色）。
流程：單人遊玩／觀戰 → 選地主或農民（農民可選座位）→ 缺的角色由 NPC 補上 → NPC 難度：簡單／標準（HL v2 啟發式移植）／困難（蒙地卡羅搜尋，每手約 1～2 秒）。
網址參數（測試用）：`?auto=1&seed=5&level=hard|easy|expert&spectate=1&fast=1&suit=0&role=farmer`。
測試：`node tests/solo_engine.test.js`（牌型／花色規則／合法出牌枚舉／整局 NPC 對打，含突變驗證）、`node tests/solo_pimc.test.js`（困難 NPC 的**不偷看**資訊隔離測試）、`node tests/solo_strength.js <起始種子> <局數>`（困難對標準的強度比較）。
困難 NPC 強度（300 種子，同種子配對，對手為標準 NPC）：困難地主 +10.3pp（CI +5.3～+15.4）、困難農民 +40.3pp（CI +34.3～+46.3）。**注意**：這是最有利情境——困難 NPC 模擬對手時假設對手用標準策略，而這次對手剛好就是標準 NPC；面對真人優勢必然較小，未量化。
UI 對比度檢查：`tests/contrast_audit.js`（在瀏覽器載入頁面後 `eval(await (await fetch('/tests/contrast_audit.js')).text())`，淺色／深色模式各跑一次；牌桌漸層底以最亮端當保守底色）。2026-10-23 發現並修正「淺色模式下牌桌按鈕白底白字、次要文字看不見」——先前只在深色模式測試所以沒發現；修正後牌桌／開始畫面／規則視窗在兩種模式都是 0 個低對比項目（對比度 < 3），並用「把 bug 注回去」驗證檢查抓得到。
