// 積分底分校準：同級 NPC 三人對打，逐局套用計分，量地主每局期望得分；並解出讓期望=0 的 L（W 固定、k=SCORE_K）。
// 用法：node tests/calibrate_scoring.js <easy|hard|expert> <局數> [起始種子=1]
//   expert 每局約 2.5 秒（取樣 20，與重現 docs/SCORING.md 的設定相同），建議開多個行程各跑一段種子。
// 輸出一行 JSON：n、p（地主勝率）、E_L（以目前 SCORE_BASE 逐局算出的地主每局期望得分與 95% 區間）、L_star（讓期望=0 的 L，W 用目前值）。
const fs = require('fs'), path = require('path'), vm = require('vm');
const html = fs.readFileSync(process.env.DDZ_HTML || path.join(__dirname, '..', 'index.html'), 'utf-8');
const T = {};
vm.runInNewContext(html.match(/<script>([\s\S]*?)<\/script>/)[1], { __TEST__: T, console, Date });
const { RULES, SCORE_K, SCORE_BASE, settle, makeGame, makeRng, npcMove, expertMove, farmersWin } = T;

function selfPlay(level, seed) {
  const G = makeGame({ seed, me: null, level: level === 'expert' ? 'hard' : level });   // 搜尋型 NPC 的底層規則同「標準」；結算用 level
  const rand = makeRng(seed * 7919 + 13);
  for (let guard = 0; !G.over; guard++) {
    if (guard > 500) throw new Error('未終止');
    if (level === 'expert') expertMove(G, G.turn, { rand, samples: 20, budgetMs: 1e9, minSamples: 20 });
    else npcMove(G, G.turn);
  }
  return { landlordWon: !farmersWin(G), counts: G.hands.map(h => h.length) };
}
function calibrate(level, n, start) {
  RULES.suit = true;
  const games = [];
  for (let s = start; s < start + n; s++) games.push(selfPlay(level, s));
  return summarize(level, games);
}
function summarize(level, games) {
  const d = games.map(g => settle(level, g.counts, g.landlordWon).deltas[0]);
  const m = d.reduce((a, b) => a + b, 0) / d.length;
  const sd = Math.sqrt(d.reduce((a, b) => a + (b - m) ** 2, 0) / (d.length - 1));
  // E_L 對 L 是線性的：用 L=0 與 L=1 兩點內插解 L*
  const evAt = L => games.reduce((a, g) => a + settle(level, g.counts, g.landlordWon).deltas[0] + (g.landlordWon ? 0 : 2 * (SCORE_BASE[level].L - L)), 0) / games.length;
  const e0 = evAt(0), e1 = evAt(1);
  return { level, n: games.length, p: +(games.filter(g => g.landlordWon).length / games.length).toFixed(4),
           E_L: +m.toFixed(3), ci95: +(1.96 * sd / Math.sqrt(d.length)).toFixed(3), L_star: +(e0 / (e0 - e1)).toFixed(2),
           base: SCORE_BASE[level], k: SCORE_K };
}
module.exports = { calibrate, summarize };
if (require.main === module) {
  const [level, n, start] = [process.argv[2], +process.argv[3], +(process.argv[4] || 1)];
  if (!SCORE_BASE[level] || !n) { console.error('用法：node tests/calibrate_scoring.js <easy|hard|expert> <局數> [起始種子]'); process.exit(2); }
  console.log(JSON.stringify(calibrate(level, n, start)));
}
