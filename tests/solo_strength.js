// 困難（蒙地卡羅）NPC 對標準 NPC 的強度比較。同種子配對，不設通過門檻，只回報。
// 用法：node tests/solo_strength.js <起始種子> <局數> [樣本數=30]   （可多開行程平行，結果印成 JSON 行）
const fs = require('fs'), path = require('path'), vm = require('vm');
const html = fs.readFileSync(path.join(__dirname, '..', 'doudizhu_solo.html'), 'utf-8');
const T = {};
vm.runInNewContext(html.match(/<script>([\s\S]*?)<\/script>/)[1], { __TEST__: T, console, Date });
const { RULES, makeGame, makeRng, npcMove, expertMove, farmersWin } = T;
const start = +process.argv[2], n = +process.argv[3], samples = +(process.argv[4] || 30);
RULES.suit = true;
function play(seed, expertSeats) {
  const G = makeGame({ seed, me: null, level: 'hard' });
  const rand = makeRng(seed * 7919 + 13);
  let guard = 0;
  while (!G.over) {
    if (++guard > 500) throw new Error('未終止');
    const s = G.turn;
    if (expertSeats.includes(s)) expertMove(G, s, { rand, samples, budgetMs: 1e9, minSamples: samples });
    else npcMove(G, s);
  }
  return !farmersWin(G);            // true = 地主勝
}
for (let seed = start; seed < start + n; seed++) {
  const base = play(seed, []);                 // 全標準
  const A = play(seed, [0]);                   // 困難地主 vs 標準農民
  const B = play(seed, [1, 2]);                // 標準地主 vs 困難農民
  console.log(JSON.stringify({ seed, base, A, B }));
}
