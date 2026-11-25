// 積分制與賽局測試。用法：node tests/solo_scoring.test.js   （DDZ_HTML 供突變驗證）
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const html = fs.readFileSync(process.env.DDZ_HTML || path.join(__dirname, '..', 'index.html'), 'utf-8');
const T = {};
vm.runInNewContext(html.match(/<script>([\s\S]*?)<\/script>/)[1], { __TEST__: T, console, Date });
const { START_CHIPS, SCORE_BASE, cardValue, handValue, settle, settleGame, PLAYER_NAME, assignSeats, newMatch, applySettlement, rankMatch,
        RULES, makeDeck, makeGame, npcMove } = T;
RULES.suit = true;

let pass = 0;
function t(name, fn) { try { fn(); pass++; console.log('  ok  ' + name); } catch (e) { console.log('FAIL  ' + name + '\n      ' + e.message); process.exitCode = 1; } }
const sum = a => Array.from(a).reduce((x, y) => x + y, 0);
const arr = a => Array.from(a);                 // vm 內建立的陣列 prototype 不同，deepStrictEqual 前先轉成本 realm 的陣列
const LEVELS = ['easy', 'hard', 'expert'];

console.log('牌面價值');
t('3–10 照牌面、J11 Q12 K13 A14、2＝15、小王16、大王17；整副牌合計 501', () => {
  const byRank = {};
  makeDeck().forEach(c => { byRank[c.r] = cardValue(c); });
  assert.deepStrictEqual(Array.from({ length: 15 }, (_, r) => byRank[r]), [3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17]);
  assert.strictEqual(handValue(makeDeck()), 4 * (3 + 4 + 5 + 6 + 7 + 8 + 9 + 10 + 11 + 12 + 13 + 14 + 15) + 16 + 17);
  assert.strictEqual(handValue(makeDeck()), 501);
  assert.strictEqual(handValue([]), 0);
});

console.log('結算公式（手算對照）');
t('地主贏：每位農民付 W＋自己剩牌價值，地主全收', () => {
  const { W } = SCORE_BASE.hard;
  const r = settle('hard', [0, 90, 40], true);
  assert.deepStrictEqual(arr(r.deltas), [(W + 90) + (W + 40), -(W + 90), -(W + 40)]);
});
t('農民贏：地主付兩位農民各 L＋地主剩牌價值；夥伴剩什麼、誰先出完都不影響', () => {
  const { L } = SCORE_BASE.hard;
  const a = settle('hard', [30, 0, 120], false), b = settle('hard', [30, 0, 5], false), c = settle('hard', [30, 200, 0], false);
  assert.deepStrictEqual(arr(a.deltas), [-2 * (L + 30), L + 30, L + 30]);
  assert.deepStrictEqual(arr(a.deltas), arr(b.deltas));
  assert.deepStrictEqual(arr(a.deltas), arr(c.deltas));
});
t('剩牌價值越高輸越多（每 1 價值 1 籌碼；只看自己的牌）', () => {
  for (const lv of LEVELS) {
    assert.strictEqual(settle(lv, [51, 0, 0], false).deltas[0] - settle(lv, [50, 0, 0], false).deltas[0], -2);
    assert.strictEqual(settle(lv, [0, 81, 30], true).deltas[1] - settle(lv, [0, 80, 30], true).deltas[1], -1);
    assert.strictEqual(settle(lv, [0, 81, 30], true).deltas[2], settle(lv, [0, 80, 30], true).deltas[2]);
  }
});
t('同樣張數，剩大牌比剩小牌輸得多（這正是改成牌面價值的目的）', () => {
  const five3 = [0, 1, 2, 3, 4].map(() => ({ r: 0 }));          // 剩五張 3 ＝ 15
  const bigFive = [{ r: 14 }, { r: 13 }, { r: 12 }, { r: 12 }, { r: 11 }]; // 大王小王 2 2 A ＝ 17+16+15+15+14
  assert(handValue(bigFive) > handValue(five3));
  const small = settle('easy', [0, handValue(five3), 0], true).deltas[1], big = settle('easy', [0, handValue(bigFive), 0], true).deltas[1];
  assert(big < small);
});
t('春天（輸家一張都沒出）的剩牌價值最高，不另加倍', () => {
  const dealt = makeDeck().slice(0, 17);
  const spring = settle('hard', [0, handValue(dealt), handValue(dealt)], true).deltas[0];
  assert.strictEqual(spring, 2 * (SCORE_BASE.hard.W + handValue(dealt)));
});

console.log('常數釘死（docs/SCORING.md 的規格值；要改請先重跑 tests/calibrate_scoring.js 並更新文件，再同步改這裡）');
t('起始籌碼與三級底分的精確值', () => {
  assert.strictEqual(START_CHIPS, 1000);
  assert.deepStrictEqual(JSON.parse(JSON.stringify(SCORE_BASE)), { easy: { W: 100, L: 70 }, hard: { W: 10, L: 165 }, expert: { W: 100, L: 60 } });
});
t('未知難度後備為標準級；零剩牌與滿手牌邊界', () => {
  assert.deepStrictEqual(arr(settle('nope', [0, 30, 50], true).deltas), arr(settle('hard', [0, 30, 50], true).deltas));
  assert.deepStrictEqual(arr(settle('expert', [0, 0, 0], true).deltas), [200, -100, -100]);          // 農民剩 0 仍付底分
  const r = settle('hard', [0, 120, 130], true);
  assert.deepStrictEqual(arr(r.pays), [0, 130, 140]);
  assert.strictEqual(r.base, SCORE_BASE.hard.W);
});

console.log('賽局：玩家與座位、籌碼、終局、名次');
t('assignSeats：你永遠坐 me 座位；電腦甲乙填其餘座位，每局輪替先後', () => {
  for (let me = 0; me < 3; me++) for (let g = 0; g < 4; g++) {
    const a = arr(assignSeats(me, g));
    assert.strictEqual(a[me], 0);
    assert.deepStrictEqual(a.slice().sort(), [0, 1, 2]);
  }
  assert.deepStrictEqual(arr(assignSeats(0, 0)), [0, 1, 2]);
  assert.deepStrictEqual(arr(assignSeats(0, 1)), [0, 2, 1]);
  assert.deepStrictEqual(arr(assignSeats(1, 0)), [1, 0, 2]);   // 你坐座位 1：座位 0（地主）是電腦甲、座位 2 是電腦乙
  assert.deepStrictEqual(arr(assignSeats(1, 1)), [2, 0, 1]);   // 下一局甲乙對調
});
t('applySettlement：籌碼依座位對應到玩家、總和守恆、不改原物件、局數＋1', () => {
  const m0 = newMatch('hard');
  assert.deepStrictEqual(arr(m0.chips), [1000, 1000, 1000]);
  const m1 = applySettlement(m0, [2, 0, 1], [300, -100, -200]);   // 座位 0 是玩家 2
  assert.deepStrictEqual(arr(m1.chips), [900, 800, 1300]);
  assert.deepStrictEqual(arr(m0.chips), [1000, 1000, 1000]);
  assert.strictEqual(sum(m1.chips), 3000);
  assert.strictEqual(m1.games, 1); assert.strictEqual(m1.level, 'hard'); assert.strictEqual(m1.over, false);
});
t('終局：任何人 ≤ 0 才結束（剛好 0 也結束，1 還沒）；同局兩人破產都算', () => {
  const mk = chips => ({ level: 'easy', chips, games: 5, over: false });
  assert.strictEqual(applySettlement(mk([1000, 1000, 1000]), [0, 1, 2], [-999, 500, 499]).over, false);
  assert.strictEqual(applySettlement(mk([1000, 1000, 1000]), [0, 1, 2], [-1000, 500, 500]).over, true);
  const r = applySettlement(mk([300, 250, 2450]), [0, 1, 2], [-300, -400, 700]);
  assert.deepStrictEqual(arr(r.chips), [0, -150, 3150]);
  assert.strictEqual(r.over, true);
  assert.strictEqual(r.chips.filter(c => c <= 0).length, 2);   // 兩人同局破產
});
t('rankMatch：由高到低，同分並列同名次且後面跳號，負數排最後', () => {
  assert.deepStrictEqual(arr(rankMatch([1200, 1500, 300])), [2, 1, 3]);
  assert.deepStrictEqual(arr(rankMatch([1500, 1500, -100])), [1, 1, 3]);
  assert.deepStrictEqual(arr(rankMatch([1000, 1000, 1000])), [1, 1, 1]);
  assert.deepStrictEqual(arr(rankMatch([3150, 0, -150])), [1, 2, 3]);
});

console.log('零和與整數（整局 NPC 對打）');
t('每局三人得分和為 0、皆為整數、贏家得正輸家得負；賽局總籌碼恆為 3000', () => {
  for (const lv of ['easy', 'hard']) {
    let match = newMatch(lv);
    for (let seed = 1; seed <= 300; seed++) {
      const G = makeGame({ seed, me: null, level: lv });
      while (!G.over) npcMove(G, G.turn);
      const r = settleGame(G);
      assert.strictEqual(sum(r.deltas), 0, `seed ${seed}`);
      assert(arr(r.deltas).every(Number.isInteger));
      assert.strictEqual(r.landlordWon, G.winner === G.landlord);
      if (r.landlordWon) assert(r.deltas[0] > 0 && r.deltas[1] < 0 && r.deltas[2] < 0);
      else assert(r.deltas[0] < 0 && r.deltas[1] > 0 && r.deltas[2] > 0);
      match = applySettlement(match, assignSeats(seed % 3, seed), r.deltas);
      assert.strictEqual(sum(match.chips), 3 * START_CHIPS);
    }
  }
});

console.log('校準守門：同級 NPC 自我對弈，地主每局期望得分≈0（底分表若與 NPC 行為脫節，這裡會紅）');
function selfplayGames(level, n) {
  const out = [];
  for (let seed = 1; seed <= n; seed++) {
    const G = makeGame({ seed, me: null, level });
    while (!G.over) npcMove(G, G.turn);
    out.push(settleGame(G));
  }
  return out;
}
// 固定種子 → 結果可重現。單局標準差約 260–280（逐局資料），n=2000 時標準誤約 6；容許值取約 2.5 倍標準誤。
// 困難（蒙地卡羅）級太慢，不在這裡測，見 tests/calibrate_scoring.js 與 docs/SCORING.md。
for (const [lv, tol] of [['easy', 16], ['hard', 16]]) {
  t(`${lv === 'easy' ? '簡單' : '標準'}級 |E_L| < ${tol}（約為單局標準差的 6%）`, () => {
    const games = selfplayGames(lv, 2000), e = sum(games.map(g => g.deltas[0])) / games.length;
    console.log('      E_L =', e.toFixed(2));
    assert(Math.abs(e) < tol, `E_L=${e}`);
  });
}
t('賽局長度守門：用同批資料 bootstrap，整場中位數落在 10–30 局（起始籌碼與底分的尺度沒跑掉）', () => {
  for (const lv of ['easy', 'hard']) {
    const games = selfplayGames(lv, 1500);
    let s = 12345; const rnd = () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296; };   // 簡單的可重現亂數
    const lens = [];
    for (let k = 0; k < 600; k++) {
      let m = newMatch(lv);
      while (!m.over && m.games < 5000) {
        const g = games[Math.floor(rnd() * games.length)], land = Math.floor(rnd() * 3);
        m = applySettlement(m, [0, 1, 2].map(seat => (land + seat) % 3), g.deltas);
      }
      lens.push(m.games);
    }
    lens.sort((a, b) => a - b);
    const med = lens[300];
    console.log(`      ${lv} 整場中位數 ${med} 局`);
    assert(med >= 10 && med <= 30, `${lv} median=${med}`);
  }
});

console.log(`\n${pass} 項通過${process.exitCode ? '，有失敗' : ''}`);
