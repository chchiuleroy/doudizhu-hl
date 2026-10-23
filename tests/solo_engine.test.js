// 測試 doudizhu_solo.html 內嵌的遊戲引擎（不含 UI）。用法：node tests/solo_engine.test.js
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const html = fs.readFileSync(process.env.DDZ_HTML || path.join(__dirname, '..', 'doudizhu_solo.html'), 'utf-8');   // DDZ_HTML 供突變驗證
const m = html.match(/<script>([\s\S]*?)<\/script>/);
assert(m, '找不到 <script>');
const T = {};
vm.runInNewContext(m[1], { __TEST__: T, console });   // 沒有 document → 不會啟動 UI
const { RULES, makeDeck, classify, beats, generateAll, legalPlays, makeGame, applyPlay, applyPass, npcMove, farmersWin } = T;

let pass = 0;
function t(name, fn) { try { fn(); pass++; console.log('  ok  ' + name); } catch (e) { console.log('FAIL  ' + name + '\n      ' + e.message); process.exitCode = 1; } }
const C = (r, s) => ({ id: r * 4 + s, r, s });           // r: 0=3 … 11=A 12=2；s: 0♣ 1♦ 2♥ 3♠
const JK = r => ({ id: r === 13 ? 52 : 53, r, s: -1 });
const cls = cards => { const p = classify(cards); return p && p.t; };

console.log('牌型辨識');
t('單張/對子/三張/炸彈/王炸', () => {
  assert.strictEqual(cls([C(0, 0)]), 'single');
  assert.strictEqual(cls([C(5, 0), C(5, 1)]), 'pair');
  assert.strictEqual(cls([C(5, 0), C(5, 1), C(5, 2)]), 'trio');
  assert.strictEqual(cls([C(5, 0), C(5, 1), C(5, 2), C(5, 3)]), 'bomb');
  assert.strictEqual(cls([JK(13), JK(14)]), 'rocket');
  assert.notStrictEqual(cls([JK(13), C(0, 0)]), 'rocket');          // 小王＋一張普通牌不是王炸
  assert.strictEqual(cls([JK(13), C(0, 0)]), null);
});
t('三帶一／三帶二', () => {
  assert.strictEqual(cls([C(1, 0), C(1, 1), C(1, 2), C(4, 0)]), 'trio1');
  assert.strictEqual(cls([C(1, 0), C(1, 1), C(1, 2), C(4, 0), C(4, 1)]), 'trio2');
  assert.strictEqual(cls([C(1, 0), C(1, 1), C(1, 2), C(4, 0), C(5, 1)]), null);
});
t('順子：5 張以上、3 到 A、不含 2 與王', () => {
  assert.strictEqual(cls([0, 1, 2, 3, 4].map(r => C(r, 0))), 'straight');
  assert.strictEqual(cls([7, 8, 9, 10, 11].map(r => C(r, 0))), 'straight');     // 10 J Q K A
  assert.strictEqual(cls([8, 9, 10, 11, 12].map(r => C(r, 0))), null);           // J Q K A 2
  assert.strictEqual(cls([0, 1, 2, 3].map(r => C(r, 0))), null);
  assert.strictEqual(cls([0, 1, 2, 4, 5].map(r => C(r, 0))), null);              // 不連續
  assert.strictEqual(cls([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11].map(r => C(r, 0))), 'straight');   // 12 張 3..A
});
t('連對：3 對以上', () => {
  assert.strictEqual(cls([0, 0, 1, 1, 2, 2].map((r, i) => C(r, i % 2))), 'pairchain');
  assert.strictEqual(cls([0, 0, 1, 1].map((r, i) => C(r, i % 2))), null);
  assert.strictEqual(cls([10, 10, 11, 11, 12, 12].map((r, i) => C(r, i % 2))), null);   // K A 2 不連續（2 不能入連對）
});
t('飛機：純／帶單／帶對', () => {
  const p = [3, 3, 3, 4, 4, 4].map((r, i) => C(r, i % 3));
  assert.strictEqual(cls(p), 'plane');
  assert.strictEqual(cls([...p, C(0, 0), C(7, 1)]), 'plane1');
  assert.strictEqual(cls([...p, C(0, 0), C(0, 1), C(7, 0), C(7, 1)]), 'plane2');
  assert.strictEqual(cls([...p, C(0, 0)]), null);
  assert.strictEqual(cls([3, 3, 3, 5, 5, 5].map((r, i) => C(r, i % 3))), null);   // 不連續的兩組三張
});
t('四帶二', () => {
  const f = [6, 6, 6, 6].map((r, i) => C(r, i));
  assert.strictEqual(cls([...f, C(0, 0), C(1, 0)]), 'four2');
  assert.strictEqual(cls([...f, C(0, 0), C(0, 1), C(1, 0), C(1, 1)]), 'four22');
  assert.strictEqual(cls([...f, C(0, 0)]), null);
});

console.log('大小比較與花色規則');
const P = cards => classify(cards);
t('點數大小', () => {
  RULES.suit = true;
  assert(beats(P([C(5, 0)]), P([C(4, 3)])));          // 點數優先於花色：♣6 壓 ♠5
  assert(!beats(P([C(4, 3)]), P([C(5, 0)])));
  assert(beats(P([C(12, 0)]), P([C(11, 3)])));         // 2 > A
  assert(beats(P([JK(13)]), P([C(12, 3)])));           // 小王 > 2
  assert(beats(P([JK(14)]), P([JK(13)])));             // 大王 > 小王
});
t('花色規則開啟：同點數 ♠ > ♥ > ♦ > ♣', () => {
  RULES.suit = true;
  for (let hi = 1; hi < 4; hi++) for (let lo = 0; lo < hi; lo++) {
    assert(beats(P([C(6, hi)]), P([C(6, lo)])), `${hi} 應壓過 ${lo}`);
    assert(!beats(P([C(6, lo)]), P([C(6, hi)])), `${lo} 不該壓過 ${hi}`);
  }
  assert(!beats(P([C(6, 2)]), P([C(6, 2)])));          // 完全相同不算
});
t('花色規則關閉：同點數不能互壓', () => {
  RULES.suit = false;
  assert(!beats(P([C(6, 3)]), P([C(6, 0)])));
  RULES.suit = true;
});
t('對子／順子的花色決勝（看最大那張）', () => {
  RULES.suit = true;
  const lowPair = P([C(6, 0), C(6, 1)]), highPair = P([C(6, 2), C(6, 3)]), midPair = P([C(6, 0), C(6, 2)]);
  assert(beats(highPair, lowPair)); assert(!beats(lowPair, highPair));
  assert(beats(highPair, midPair));                    // ♠ 比 ♥ 大
  const s1 = [0, 1, 2, 3, 4].map(r => C(r, r === 4 ? 0 : 3)), s2 = [0, 1, 2, 3, 4].map(r => C(r, r === 4 ? 3 : 0));
  assert(beats(P(s2), P(s1)));                         // 最高張 ♠ 壓 ♣
});
t('炸彈／王炸／不同牌型', () => {
  RULES.suit = true;
  const bomb = P([5, 5, 5, 5].map((r, i) => C(r, i))), bomb2 = P([6, 6, 6, 6].map((r, i) => C(r, i)));
  assert(beats(bomb, P([C(12, 3)])));                  // 炸彈壓單張
  assert(beats(bomb2, bomb)); assert(!beats(bomb, bomb2));
  assert(beats(P([JK(13), JK(14)]), bomb2)); assert(!beats(bomb2, P([JK(13), JK(14)])));
  assert(!beats(P([C(5, 0), C(5, 1)]), P([C(4, 0)])));  // 對子不能壓單張
  assert(!beats(P([0, 1, 2, 3, 4, 5].map(r => C(r + 1, 0))), P([0, 1, 2, 3, 4].map(r => C(r, 0)))));   // 張數不同的順子
  assert(beats(P([1, 2, 3, 4, 5].map(r => C(r, 0))), P([0, 1, 2, 3, 4].map(r => C(r, 0)))));
});

console.log('合法出牌枚舉');
t('每個枚舉出的出牌都合法、無重複牌、屬於手牌', () => {
  for (let seed = 1; seed <= 60; seed++) {
    const G = makeGame({ seed, me: 0, level: 'hard' });
    [0, 1].forEach(s => {
      const hand = G.hands[s], ids = new Set(hand.map(c => c.id));
      generateAll(hand).forEach(({ cards, pat }) => {
        assert(cards.every(c => ids.has(c.id)), '含手牌以外的牌');
        assert.strictEqual(new Set(cards.map(c => c.id)).size, cards.length, '重複牌');
        const p2 = classify(cards);
        assert(p2 && p2.t === pat.t && p2.r === pat.r && p2.n === pat.n, '牌型不一致');
      });
    });
  }
});
t('已知手牌的枚舉內容', () => {
  const hand = [C(0, 0), C(0, 1), C(1, 0), C(1, 1), C(2, 0), C(2, 1), C(3, 2), C(4, 3)];
  const types = new Set(generateAll(hand).map(p => p.pat.t));
  ['single', 'pair', 'pairchain', 'straight'].forEach(x => assert(types.has(x), '缺 ' + x));
  assert(!types.has('bomb') && !types.has('rocket'));
});
t('花色規則決定同點數是否可出', () => {
  const hand = [C(4, 3), C(4, 0), C(2, 3), C(8, 0)];          // ♠7 ♣7 ♠5 ♣J
  const table = { seat: 1, cards: [C(4, 2)], pat: P([C(4, 2)]) };   // 桌上 ♥7
  RULES.suit = true;
  let ids = legalPlays(hand, table).map(p => p.cards.map(c => c.id).join());
  assert(ids.includes(C(4, 3).id + ''), '♠7 應可壓 ♥7');
  assert(!ids.includes(C(4, 0).id + ''), '♣7 不應壓 ♥7');
  assert(ids.includes(C(8, 0).id + ''), '♣J 可壓');
  RULES.suit = false;
  ids = legalPlays(hand, table).map(p => p.cards.map(c => c.id).join());
  assert(!ids.includes(C(4, 3).id + ''), '關閉規則後 ♠7 不能壓 ♥7');
  RULES.suit = true;
});

console.log('整局 NPC 對打：合法性、守恆、終止');
function playOut(seed, level, suit) {
  RULES.suit = suit;
  const G = makeGame({ seed, me: null, level });
  const total = () => G.hands.reduce((a, h) => a + h.length, 0) + G.history.reduce((a, h) => a + (h.cards ? h.cards.length : 0), 0);
  assert.strictEqual(total(), 54);
  let guard = 0;
  while (!G.over) {
    assert(++guard < 500, '對局未終止');
    const seat = G.turn, hand = G.hands[seat].slice(), table = G.table;
    const mv = npcMove(G, seat);
    if (mv) {
      const ids = new Set(hand.map(c => c.id));
      assert(mv.cards.every(c => ids.has(c.id)), '出了手牌外的牌');
      const pat = classify(mv.cards);
      assert(pat, '非法牌型');
      assert(!table || beats(pat, table.pat), '壓不過上一手');
    } else assert(table, '領出時不可不出');
    assert.strictEqual(total(), 54, '牌數不守恆');
  }
  return G;
}
t('300 局（標準 NPC、花色規則開）都能合法結束', () => { for (let s = 1; s <= 300; s++) playOut(s, 'hard', true); });
t('100 局（簡單 NPC、花色規則關）都能合法結束', () => { for (let s = 1; s <= 100; s++) playOut(s, 'easy', false); });
t('結果可重現（同種子同結果）', () => {
  const a = playOut(7, 'hard', true), b = playOut(7, 'hard', true);
  assert.deepStrictEqual(a.history, b.history);
});
t('標準 NPC 對簡單 NPC：統計勝率（僅回報，不設門檻）', () => {
  // 以「標準」方對「簡單」方打：地主標準＋農民簡單，與地主簡單＋農民標準，各 300 局
  const run = (landlordLevel, farmerLevel) => {
    let hardWins = 0, n = 300;
    for (let s = 1; s <= n; s++) {
      RULES.suit = true;
      const G = makeGame({ seed: s, me: null, level: 'hard' });
      const lv = seat => seat === 0 ? landlordLevel : farmerLevel;
      let guard = 0;
      while (!G.over) {
        assert(++guard < 500);
        const seat = G.turn;
        G.level = lv(seat);
        npcMove(G, seat);
      }
      const landlordWon = !farmersWin(G);
      if ((landlordLevel === 'hard' && landlordWon) || (farmerLevel === 'hard' && !landlordWon)) hardWins++;
    }
    return hardWins / n;
  };
  const a = run('hard', 'easy'), b = run('easy', 'hard');
  console.log(`      標準地主 vs 簡單農民：標準方勝率 ${(a * 100).toFixed(1)}%；簡單地主 vs 標準農民：標準方勝率 ${(b * 100).toFixed(1)}%`);
});

console.log(`\n${pass} 個測試通過` + (process.exitCode ? '，有失敗' : ''));
