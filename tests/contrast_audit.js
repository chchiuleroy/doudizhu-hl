(() => {
  const parse = s => { const m = s.match(/rgba?\(([^)]+)\)/); if (!m) return null; const p = m[1].split(',').map(x => parseFloat(x)); return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 }; };
  const lum = c => { const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b); };
  const blend = (top, bot) => ({ r: top.r * top.a + bot.r * (1 - top.a), g: top.g * top.a + bot.g * (1 - top.a), b: top.b * top.a + bot.b * (1 - top.a), a: 1 });
  const bgOf = el => { // 由內往外疊色，直到不透明
    const layers = []; let e = el;
    const hex = h => { h = h.trim().replace('#', ''); return { r: parseInt(h.slice(0, 2), 16), g: parseInt(h.slice(2, 4), 16), b: parseInt(h.slice(4, 6), 16), a: 1 }; };
    while (e) {
      const st = getComputedStyle(e);
      if (st.backgroundImage.includes('gradient')) {      // 漸層底（牌桌）：用最亮的那端當保守底色，而不是當成白色
        layers.push(hex(getComputedStyle(document.documentElement).getPropertyValue('--felt1'))); break;
      }
      const c = parse(st.backgroundColor); if (c && c.a > 0) { layers.push(c); if (c.a >= 1) break; }
      e = e.parentElement;
    }
    let base = { r: 255, g: 255, b: 255, a: 1 };
    for (let i = layers.length - 1; i >= 0; i--) base = blend(layers[i], base);
    return base;
  };
  const bad = [];
  document.querySelectorAll('button, .seat, .slot, .badge, .legend, .mute, h1, h2, b, span, div').forEach(el => {
    const r = el.getBoundingClientRect(); if (r.width < 4 || r.height < 4) return;
    const st = getComputedStyle(el); if (st.visibility === 'hidden' || st.display === 'none') return;
    const own = [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim().length > 0);
    if (!own) return;
    if (el.closest('.card')) return;                     // 撲克牌本身固定白底
    const fg = parse(st.color), bg = bgOf(el); if (!fg) return;
    const f = blend({ ...fg, a: fg.a * parseFloat(st.opacity || 1) }, bg);
    const L1 = lum(f), L2 = lum(bg), ratio = (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05);
    const disabled = el.disabled || (el.closest('button') && el.closest('button').disabled);
    if (ratio < 3 && !disabled) bad.push({ text: el.innerText.trim().slice(0, 12), tag: el.tagName + '.' + el.className, ratio: +ratio.toFixed(2) });
  });
  return JSON.stringify({ scheme: matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light', page: document.getElementById('start').hidden ? 'game' : 'start', lowContrast: bad.slice(0, 12), n: bad.length });
})()
