// 積分制測試。用法：node tests/solo_scoring.test.js   （DDZ_HTML 供突變驗證）
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const html = fs.readFileSync(process.env.DDZ_HTML || path.join(__dirname, '..', 'index.html'), 'utf-8');
const T = {};
vm.runInNewContext(html.match(/<script>([\s\S]*?)<\/script>/)[1], { __TEST__: T, console, Date });
const { SCORE_K, SCORE_BASE, settle, settleGame, RULES, makeGame, npcMove, farmersWin } = T;
RULES.suit = true;

let pass = 0;
function t(name, fn) { try { fn(); pass++; console.log('  ok  ' + name); } catch (e) { console.log('FAIL  ' + name + '\n      ' + e.message); process.exitCode = 1; } }
const sum = a => a.reduce((x, y) => x + y, 0);
const arr = a => Array.from(a);                 // vm 內建立的陣列 prototype 不同，deepStrictEqual 前先轉成本 realm 的陣列
const LEVELS = ['easy', 'hard', 'expert'];

console.log('結算公式（手算對照）');
t('地主贏：每位農民付 W＋自己剩牌，地主全收', () => {
  const { W } = SCORE_BASE.hard;
  const r = settle('hard', [0, 9, 4], true);
  assert.deepStrictEqual(arr(r.deltas), [(W + 9) + (W + 4), -(W + 9), -(W + 4)]);
});
t('農民贏：地主付兩位農民各 L＋地主剩牌；夥伴剩幾張都不影響', () => {
  const { L } = SCORE_BASE.hard;
  const a = settle('hard', [3, 0, 12], false), b = settle('hard', [3, 0, 1], false), c = settle('hard', [3, 17, 0], false);
  assert.deepStrictEqual(arr(a.deltas), [-2 * (L + 3), L + 3, L + 3]);
  assert.deepStrictEqual(arr(a.deltas), arr(b.deltas));
  assert.deepStrictEqual(arr(a.deltas), arr(c.deltas));     // 誰先出完也不影響
});
t('地主剩牌越多輸越多；農民剩牌越多輸越多（每張 SCORE_K 分）', () => {
  for (const lv of LEVELS) {
    assert.strictEqual(settle(lv, [5, 0, 0], false).deltas[0] - settle(lv, [4, 0, 0], false).deltas[0], -2 * SCORE_K);
    assert.strictEqual(settle(lv, [0, 8, 3], true).deltas[1] - settle(lv, [0, 7, 3], true).deltas[1], -SCORE_K);
    assert.strictEqual(settle(lv, [0, 8, 3], true).deltas[2], settle(lv, [0, 7, 3], true).deltas[2]);   // 只看自己的牌
  }
});
t('春天（輸家一張都沒出）的剩牌分是最高的，不另加倍', () => {
  const spring = settle('hard', [0, 17, 17], true).deltas[0], normal = settle('hard', [0, 5, 5], true).deltas[0];
  assert(spring > normal);
  assert.strictEqual(spring, 2 * (SCORE_BASE.hard.W + 17 * SCORE_K));
});
t('農民贏與地主贏的底分不同（身分不對稱）：標準級 L 遠大於 W', () => {
  assert(SCORE_BASE.hard.L > SCORE_BASE.hard.W);
  for (const lv of LEVELS) assert(SCORE_BASE[lv].W > 0 && SCORE_BASE[lv].L > 0);
});

console.log('常數釘死（docs/SCORING.md 的規格值；要改請先重跑 tests/calibrate_scoring.js 並更新文件，再同步改這裡）');
t('SCORE_K 與三級底分的精確值', () => {
  assert.strictEqual(SCORE_K, 1);
  assert.deepStrictEqual(JSON.parse(JSON.stringify(SCORE_BASE)), { easy: { W: 2, L: 2 }, hard: { W: 2, L: 20 }, expert: { W: 2, L: 2 } });
});
t('未知難度後備為標準級；滿手牌與零剩牌的邊界', () => {
  assert.deepStrictEqual(arr(settle('nope', [0, 3, 5], true).deltas), arr(settle('hard', [0, 3, 5], true).deltas));
  assert.deepStrictEqual(arr(settle('expert', [20, 0, 17], false).deltas), [-2 * (2 + 20), 22, 22]);   // 地主一張沒出
  assert.deepStrictEqual(arr(settle('easy', [0, 0, 0], true).deltas), [4, -2, -2]);                    // 農民剩 0 張仍付底分
  const r = settle('hard', [0, 17, 17], true);
  assert.deepStrictEqual(arr(r.pays), [0, 19, 19]);
  assert.strictEqual(r.base, SCORE_BASE.hard.W);
});

console.log('零和與整數（整局 NPC 對打）');
t('每局三人得分和為 0、皆為整數、贏家得正輸家得負', () => {
  for (const lv of ['easy', 'hard']) {
    for (let seed = 1; seed <= 300; seed++) {
      const G = makeGame({ seed, me: null, level: lv });
      while (!G.over) npcMove(G, G.turn);
      const r = settleGame(G);
      assert.strictEqual(sum(r.deltas), 0, `seed ${seed}`);
      assert(r.deltas.every(Number.isInteger));
      assert.strictEqual(r.landlordWon, G.winner === G.landlord);
      if (r.landlordWon) assert(r.deltas[0] > 0 && r.deltas[1] < 0 && r.deltas[2] < 0);
      else assert(r.deltas[0] < 0 && r.deltas[1] > 0 && r.deltas[2] > 0);
    }
  }
});

console.log('校準守門：同級 NPC 自我對弈，地主每局期望得分≈0（底分表若與 NPC 行為脫節，這裡會紅）');
function expectedLandlordScore(level, n) {
  let s = 0;
  for (let seed = 1; seed <= n; seed++) {
    const G = makeGame({ seed, me: null, level });
    while (!G.over) npcMove(G, G.turn);
    s += settleGame(G).deltas[0];
  }
  return s / n;
}
// 固定種子 → 結果可重現；容許值約為標準誤的 2.5 倍（簡單級 sd≈16、標準級 sd≈32，n=2000 時標準誤 0.4／0.7）。
// 困難（蒙地卡羅）級太慢，不在這裡測，見 tests/calibrate_scoring.js 與 docs/SCORING.md。
t('簡單級 |E_L| < 1.0', () => { const e = expectedLandlordScore('easy', 2000); console.log('      E_L =', e.toFixed(3)); assert(Math.abs(e) < 1.0, `E_L=${e}`); });
t('標準級 |E_L| < 2.0', () => { const e = expectedLandlordScore('hard', 2000); console.log('      E_L =', e.toFixed(3)); assert(Math.abs(e) < 2.0, `E_L=${e}`); });

console.log(`\n${pass} 項通過${process.exitCode ? '，有失敗' : ''}`);
