// 困難（蒙地卡羅）NPC 的測試：資訊隔離（不偷看）、抽樣不變量、合法性、整局可結束。用法：node tests/solo_pimc.test.js
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const html = fs.readFileSync(process.env.DDZ_HTML || path.join(__dirname, '..', 'doudizhu_solo.html'), 'utf-8');
const T = {};
vm.runInNewContext(html.match(/<script>([\s\S]*?)<\/script>/)[1], { __TEST__: T, console, Date });
const { RULES, makeGame, makeRng, npcMove, publicInfo, determinize, pimcCandidates, pimcChoose, expertMove, classify, beats, farmersWin } = T;

let pass = 0;
function t(name, fn) { try { fn(); pass++; console.log('  ok  ' + name); } catch (e) { console.log('FAIL  ' + name + '\n      ' + e.message); process.exitCode = 1; } }
const key = mv => mv ? mv.cards.map(c => c.id).sort((a, b) => a - b).join(',') : 'pass';
const OPTS = seed => ({ rand: makeRng(seed), samples: 6, minSamples: 6, budgetMs: 1e9 });

// 取一個中盤狀態：用標準 NPC 先打 k 手
function midState(seed, k) {
  const G = makeGame({ seed, me: null, level: 'hard' });
  for (let i = 0; i < k && !G.over; i++) npcMove(G, G.turn);
  return G;
}
function cloneWithHidden(G, seat, rand) {   // 保留該座位看得到的一切，只重新分配其他人手上「看不到」的牌
  const hands = determinize(publicInfo(G, seat), rand);
  const G2 = Object.assign({}, G, { hands: hands.map(h => T.sortHand(h)) });
  return G2;
}

console.log('抽樣（determinize）不變量');
t('張數吻合、牌守恆、地主公開底牌留在地主手上', () => {
  for (let seed = 1; seed <= 40; seed++) {
    const G = midState(seed, seed % 12);
    if (G.over) continue;
    for (const seat of [0, 1, 2]) {
      const info = publicInfo(G, seat);
      const hands = determinize(info, makeRng(seed * 31 + seat));
      assert.deepStrictEqual(hands.map(h => h.length), G.hands.map(h => h.length), '張數不吻合');
      assert.deepStrictEqual(hands[seat].map(c => c.id).sort((a, b) => a - b), G.hands[seat].map(c => c.id).sort((a, b) => a - b), '自己的牌被改動');
      const all = hands.flat().map(c => c.id);
      assert.strictEqual(new Set(all).size, all.length, '牌重複');
      const playedIds = new Set(G.history.flatMap(h => h.cards || []));
      assert(all.every(id => !playedIds.has(id)), '抽到已出過的牌');
      if (seat !== 0) {
        const bottomLeft = G.bottom.map(c => c.id).filter(id => !playedIds.has(id));
        const lh = new Set(hands[0].map(c => c.id));
        assert(bottomLeft.every(id => lh.has(id)), '地主公開底牌不在地主手上');
      }
    }
  }
});

console.log('資訊隔離：換掉看不到的牌，選擇必須完全相同');
t('300 個狀態：其他人手牌換成另一種合理分配，結果不變（沒有偷看）', () => {
  let checked = 0, differs = 0;
  for (let seed = 1; seed <= 100; seed++) {
    for (const k of [0, 3, 6, 9, 14, 20]) {
      const G = midState(seed, k);
      if (G.over) continue;
      const seat = G.turn;
      const a = key(pimcChoose(publicInfo(G, seat), OPTS(seed + k)));
      const G2 = cloneWithHidden(G, seat, makeRng(99991 + seed * 13 + k));
      // 確認真的換掉了看不到的牌（至少有一次與原本不同），否則這個測試沒意義
      if (JSON.stringify(G2.hands) !== JSON.stringify(G.hands)) differs++;
      const b = key(pimcChoose(publicInfo(G2, seat), OPTS(seed + k)));
      assert.strictEqual(b, a, `座位 ${seat} 的選擇隨隱藏手牌改變 → 偷看了（種子 ${seed}，第 ${k} 手後）`);
      checked++;
    }
  }
  assert(checked >= 200, '檢查的狀態太少 ' + checked);
  assert(differs >= checked * 0.5, `隱藏手牌被換掉的比例太低（${differs}/${checked}），測試沒有鑑別力`);
  console.log(`      檢查 ${checked} 個狀態，其中 ${differs} 個確實換過隱藏手牌`);
});
t('publicInfo 不含其他人手牌（只有張數）', () => {
  const G = midState(5, 7);
  const seat = G.turn;
  const info = publicInfo(G, seat);
  const others = [0, 1, 2].filter(s => s !== seat);
  const hiddenIds = new Set(others.flatMap(s => G.hands[s].map(c => c.id)));
  const text = JSON.stringify({ ...info, played: [...info.played] });
  // info.hand 以外不得出現「別人手上、且尚未出過」的牌的完整物件
  const playedIds = info.played;
  others.forEach(s => G.hands[s].forEach(c => {
    if (!playedIds.has(c.id) && !G.bottom.some(b => b.id === c.id)) assert(!text.includes(JSON.stringify(c)), '洩漏手牌 ' + c.id);
  }));
  assert(Array.isArray(info.counts) && info.counts.length === 3);
});

console.log('合法性與整局');
t('候選與最終選擇都合法（含不出只在可以不出時）', () => {
  let n = 0;
  for (let seed = 1; seed <= 80; seed++) {
    for (const k of [0, 2, 5, 8, 12]) {
      const G = midState(seed, k);
      if (G.over) continue;
      const seat = G.turn, info = publicInfo(G, seat);
      pimcCandidates(info).forEach(mv => {
        if (!mv) { assert(G.table, '領出時候選含不出'); return; }
        const pat = classify(mv.cards);
        assert(pat && (!G.table || beats(pat, G.table.pat)), '候選不合法');
      });
      const mv = pimcChoose(info, OPTS(seed));
      if (mv) { assert(classify(mv.cards)); assert(!G.table || beats(classify(mv.cards), G.table.pat)); } else assert(G.table);
      n++;
    }
  }
  assert(n > 200);
});
t('三家都用困難 NPC 的整局能合法結束、牌守恆（12 局）', () => {
  RULES.suit = true;
  for (let seed = 1; seed <= 12; seed++) {
    const G = makeGame({ seed, me: null, level: 'hard' });
    const rand = makeRng(seed);
    let guard = 0;
    while (!G.over) {
      assert(++guard < 400);
      expertMove(G, G.turn, { rand, samples: 4, minSamples: 4, budgetMs: 1e9 });
      const total = G.hands.reduce((a, h) => a + h.length, 0) + G.history.reduce((a, h) => a + (h.cards ? h.cards.length : 0), 0);
      assert.strictEqual(total, 54);
    }
  }
});
t('時間預算：budgetMs 很小時仍會跑 minSamples 並在預算內結束', () => {
  const G = midState(3, 6);
  const t0 = Date.now();
  pimcChoose(publicInfo(G, G.turn), { rand: makeRng(1), samples: 1000, minSamples: 4, budgetMs: 60 });
  assert(Date.now() - t0 < 1500, '預算失效');
});

console.log(`\n${pass} 個測試通過` + (process.exitCode ? '，有失敗' : ''));
