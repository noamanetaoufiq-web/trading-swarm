'use strict';
/* Trading Swarm dashboard - vanilla JS, no dependencies, SVG charts. */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmt = (n, d = 2) => (n === null || n === undefined || isNaN(n)) ? '–' : Number(n).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
const sgn = (n, d = 2) => n == null || isNaN(n) ? '–' : (n > 0 ? '+' : '') + fmt(n, d);
const cls = n => n > 0 ? 'up' : n < 0 ? 'dn' : 'mut';
const sleep = ms => new Promise(r => setTimeout(r, ms));
const TOKEN = new URLSearchParams(location.search).get('token') || sessionStorage.getItem('swarm_tk') || '';
if (TOKEN) sessionStorage.setItem('swarm_tk', TOKEN);

const S = { state: null, run: null, sym: null, tf: '1h', labSym: null, labDemo: false, jrDemo: false, busy: false,
  opts: { demo: false, no_llm: false, send_live: false, symbols: null, query: '' },
  wiz: null };

/* ------------------------------------------------------------------ api / jobs / ui helpers */
async function api(path, { method = 'GET', body } = {}) {
  const r = await fetch(path, { method, headers: { 'Content-Type': 'application/json', 'X-Token': TOKEN }, body: body ? JSON.stringify(body) : undefined });
  let j = null; try { j = await r.json(); } catch (e) { /* no body */ }
  if (!r.ok) throw new Error((j && (j.detail || j.message)) || ('HTTP ' + r.status));
  return j;
}
function toast(msg, err) {
  const t = document.createElement('div'); t.className = 'toast' + (err ? ' err' : ''); t.textContent = msg;
  $('#toast').appendChild(t); setTimeout(() => t.remove(), err ? 7000 : 3500);
}
async function runJob(path, body, logEl) {
  const { job_id } = await api(path, { method: 'POST', body });
  let since = 0;
  for (;;) {
    await sleep(700);
    const j = await api(`/api/jobs/${job_id}?since=${since}`);
    since = j.next;
    if (logEl) { j.log.forEach(l => logEl.insertAdjacentHTML('beforeend', `<div>${esc(l)}</div>`)); logEl.scrollTop = 1e9; }
    if (j.status !== 'running') { if (j.status === 'error') throw new Error(j.error); return j.result; }
  }
}
const badge = (t, k = 'mut') => `<span class="badge b-${k}">${esc(t)}</span>`;
const decB = d => d === 'TRADE' ? badge(d, 'up') : d === 'WAIT' ? badge(d, 'warn') : badge(d || '–', 'mut');
const dirB = d => d === 'LONG' ? badge('LONG ▲', 'up') : d === 'SHORT' ? badge('SHORT ▼', 'dn') : badge('NO SIDE', 'mut');
const biasB = b => b === 'BULLISH' ? badge(b, 'up') : b === 'BEARISH' ? badge(b, 'dn') : badge(b || '–', 'mut');
const srcB = s => !s ? '' : s === 'llm' ? badge('Claude', 'vio') : s.startsWith('heuristic-fallback') ? badge('FALLBACK', 'dn') : s.startsWith('heuristic') ? badge('rules', 'mut') : badge(s, 'info');
const confBar = v => `<div class="bar"><i style="width:${Math.max(0, Math.min(100, v || 0))}%"></i></div>`;
const list = a => a && a.length ? `<ul class="l">${a.map(x => `<li>${esc(x)}</li>`).join('')}</ul>` : '<div class="hint">none</div>';
const kv = o => `<div class="kv">${Object.entries(o).map(([k, v]) => `<span>${esc(k)}</span><span>${v}</span>`).join('')}</div>`;
const copyBtn = txt => `<button class="btn ghost" data-copy="${esc(txt)}">copy</button>`;
function openDrawer(title, html) { $('#drawer-title').textContent = title; $('#drawer-body').innerHTML = html; $('#drawer').classList.add('open'); }
document.addEventListener('click', e => {
  const c = e.target.closest('[data-copy]'); if (c) { navigator.clipboard?.writeText(c.dataset.copy); toast('copied'); }
});

/* ------------------------------------------------------------------ SVG charts */
function candleChart(sr, levels, W = 900, H = 400) {
  if (!sr) return '<div class="hint">no chart data</div>';
  const cs = sr.candles, n = cs.length, pl = 8, pr = 72, pt = 10, pb = 22;
  let lo = Math.min(...cs.map(c => c[3])), hi = Math.max(...cs.map(c => c[2]));
  levels.forEach(l => { if (l.v != null) { lo = Math.min(lo, l.v); hi = Math.max(hi, l.v); } });
  const rg = (hi - lo) || 1; lo -= rg * .05; hi += rg * .05;
  const cw = (W - pl - pr) / n, X = i => pl + (i + .5) * cw, Y = v => pt + (hi - v) * (H - pt - pb) / (hi - lo), R = W - pr;
  let s = `<svg viewBox="0 0 ${W} ${H}" width="100%" style="display:block;background:#070b12;border-radius:12px;border:1px solid var(--line)">`;
  for (let k = 0; k <= 5; k++) { const v = lo + (hi - lo) * k / 5, y = Y(v); s += `<line x1="${pl}" x2="${R}" y1="${y}" y2="${y}" stroke="#141c2b"/><text x="${R + 6}" y="${y + 4}" fill="#5b667a" font-size="10">${fmt(v, v > 1000 ? 0 : 2)}</text>`; }
  const entry = levels.find(l => l.k === 'entry'), sl = levels.find(l => l.k === 'sl'), tp = levels.find(l => l.k === 'tp');
  if (entry && sl) s += `<rect x="${pl}" width="${R - pl}" y="${Math.min(Y(entry.v), Y(sl.v))}" height="${Math.abs(Y(entry.v) - Y(sl.v))}" fill="rgba(248,113,113,.08)"/>`;
  if (entry && tp) s += `<rect x="${pl}" width="${R - pl}" y="${Math.min(Y(entry.v), Y(tp.v))}" height="${Math.abs(Y(entry.v) - Y(tp.v))}" fill="rgba(52,211,153,.08)"/>`;
  (sr.fvg || []).forEach(z => { const i = Math.max(0, z.bar - sr.offset); if (z.bar - sr.offset > n) return;
    s += `<rect x="${X(i)}" y="${Y(z.high)}" width="${R - X(i)}" height="${Math.max(1, Y(z.low) - Y(z.high))}" fill="${z.side === 'bullish' ? 'rgba(52,211,153,.14)' : 'rgba(248,113,113,.14)'}"><title>FVG ${z.side} ${z.low}-${z.high} (${z.state})</title></rect>`; });
  (sr.order_blocks || []).forEach(z => { const i = Math.max(0, z.bar - sr.offset); if (z.bar - sr.offset > n) return; const col = z.side === 'bullish' ? '52,211,153' : '248,113,113';
    s += `<rect x="${X(i)}" y="${Y(z.high)}" width="${R - X(i)}" height="${Math.max(1.5, Y(z.low) - Y(z.high))}" fill="rgba(${col},.2)" stroke="rgba(${col},.8)" stroke-dasharray="3 3"><title>OB ${z.side} ${z.low}-${z.high} (${z.state})</title></rect><text x="${X(i) + 3}" y="${Y(z.high) - 3}" fill="rgb(${col})" font-size="9">OB</text>`; });
  (sr.liquidity_pools || []).forEach(p => { if (p.level < lo || p.level > hi) return; s += `<line x1="${pl}" x2="${R}" y1="${Y(p.level)}" y2="${Y(p.level)}" stroke="#a78bfa" stroke-dasharray="2 4" opacity=".7"/><text x="${pl + 4}" y="${Y(p.level) - 3}" fill="#a78bfa" font-size="9">${p.type}</text>`; });
  cs.forEach((c, i) => { const up = c[4] >= c[1], col = up ? '#34d399' : '#f87171', x = X(i);
    s += `<g><title>${c[0]}  O ${c[1]}  H ${c[2]}  L ${c[3]}  C ${c[4]}</title><line x1="${x}" x2="${x}" y1="${Y(c[2])}" y2="${Y(c[3])}" stroke="${col}"/><rect x="${x - cw * .31}" y="${Y(Math.max(c[1], c[4]))}" width="${cw * .62}" height="${Math.max(1, Math.abs(Y(c[1]) - Y(c[4])))}" fill="${col}"/></g>`; });
  const lc = { entry: '#38bdf8', sl: '#f87171', tp: '#34d399', px: '#e6ebf4' };
  levels.forEach(l => { if (l.v == null) return; const y = Y(l.v), c = lc[l.k] || '#fff';
    s += `<line x1="${pl}" x2="${R}" y1="${y}" y2="${y}" stroke="${c}" stroke-width="1.2" ${l.k === 'px' ? 'stroke-dasharray="4 3"' : ''}/><rect x="${R + 1}" y="${y - 8}" width="${pr - 4}" height="16" rx="4" fill="${c}"/><text x="${R + 5}" y="${y + 4}" fill="#04111a" font-size="10" font-weight="700">${l.t} ${fmt(l.v, l.v > 1000 ? 0 : 2)}</text>`; });
  const step = Math.ceil(n / 7); for (let i = 0; i < n; i += step) s += `<text x="${X(i)}" y="${H - 6}" fill="#5b667a" font-size="9" text-anchor="middle">${cs[i][0].slice(6)}</text>`;
  return s + '</svg>';
}
function lineChart(pts, { W = 520, H = 200, zero = true, color = '#4fd1c5' } = {}) {
  if (!pts || pts.length < 2) return '<div class="hint">not enough data</div>';
  const pl = 34, pr = 10, pt = 10, pb = 20, xs = pts.map(p => p[0]), ys = pts.map(p => p[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs); let lo = Math.min(...ys, zero ? 0 : Infinity), hi = Math.max(...ys, zero ? 0 : -Infinity); if (hi === lo) hi = lo + 1;
  const X = v => pl + (v - x0) / ((x1 - x0) || 1) * (W - pl - pr), Y = v => pt + (hi - v) * (H - pt - pb) / (hi - lo);
  const d = pts.map((p, i) => (i ? 'L' : 'M') + X(p[0]).toFixed(1) + ' ' + Y(p[1]).toFixed(1)).join(' ');
  let s = `<svg viewBox="0 0 ${W} ${H}" width="100%"><defs><linearGradient id="lg" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="${color}" stop-opacity=".35"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient></defs>`;
  for (let k = 0; k <= 4; k++) { const v = lo + (hi - lo) * k / 4; s += `<line x1="${pl}" x2="${W - pr}" y1="${Y(v)}" y2="${Y(v)}" stroke="#141c2b"/><text x="${pl - 4}" y="${Y(v) + 3}" fill="#5b667a" font-size="9" text-anchor="end">${fmt(v, 1)}</text>`; }
  if (zero) s += `<line x1="${pl}" x2="${W - pr}" y1="${Y(0)}" y2="${Y(0)}" stroke="#2a3850" stroke-dasharray="3 3"/>`;
  return s + `<path d="${d} L${X(x1)} ${Y(Math.max(lo, 0))} L${X(x0)} ${Y(Math.max(lo, 0))}Z" fill="url(#lg)"/><path d="${d}" fill="none" stroke="${color}" stroke-width="2"/></svg>`;
}
function fanChart(mc, W = 520, H = 230) {
  if (!mc || !mc.fan) return '<div class="hint">no Monte Carlo data</div>';
  const st = mc.fan_steps, f = mc.fan, pl = 40, pr = 10, pt = 10, pb = 22;
  let lo = Math.min(0, ...f.p5), hi = Math.max(0, ...f.p95); if (hi === lo) hi = lo + 1;
  const X = i => pl + st[i] / st[st.length - 1] * (W - pl - pr), Y = v => pt + (hi - v) * (H - pt - pb) / (hi - lo);
  const band = (a, b, col) => `<polygon points="${a.map((v, i) => X(i) + ',' + Y(v)).join(' ')} ${b.map((v, i) => X(b.length - 1 - i) + ',' + Y(b[b.length - 1 - i])).join(' ')}" fill="${col}"/>`;
  let s = `<svg viewBox="0 0 ${W} ${H}" width="100%">`;
  for (let k = 0; k <= 4; k++) { const v = lo + (hi - lo) * k / 4; s += `<line x1="${pl}" x2="${W - pr}" y1="${Y(v)}" y2="${Y(v)}" stroke="#141c2b"/><text x="${pl - 4}" y="${Y(v) + 3}" fill="#5b667a" font-size="9" text-anchor="end">${fmt(v, 0)}%</text>`; }
  s += band(f.p5, f.p95, 'rgba(56,189,248,.10)') + band(f.p25, f.p75, 'rgba(56,189,248,.22)') + `<line x1="${pl}" x2="${W - pr}" y1="${Y(0)}" y2="${Y(0)}" stroke="#2a3850" stroke-dasharray="3 3"/>`;
  s += `<polyline fill="none" stroke="#4fd1c5" stroke-width="2" points="${f.p50.map((v, i) => X(i) + ',' + Y(v)).join(' ')}"/>`;
  s += `<polyline fill="none" stroke="#f87171" stroke-width="1" stroke-dasharray="3 3" points="${f.p5.map((v, i) => X(i) + ',' + Y(v)).join(' ')}"/>`;
  s += `<text x="${W - pr}" y="${H - 5}" fill="#5b667a" font-size="9" text-anchor="end">trades →  (band: 5–95 %, 25–75 %, median)</text>`;
  return s + '</svg>';
}
function histChart(h, ruin, W = 520, H = 160) {
  if (!h) return '';
  const c = h.counts, e = h.edges, mx = Math.max(...c, 1), pl = 8, pb = 22, bw = (W - pl - 8) / c.length, x0 = e[0], x1 = e[e.length - 1];
  let s = `<svg viewBox="0 0 ${W} ${H}" width="100%">`;
  c.forEach((v, i) => { const hh = v / mx * (H - pb - 14), over = e[i] >= ruin; s += `<rect x="${pl + i * bw + 1}" y="${H - pb - hh}" width="${bw - 2}" height="${hh}" rx="2" fill="${over ? 'rgba(248,113,113,.7)' : 'rgba(79,209,197,.55)'}"><title>${e[i]}–${e[i + 1]}% : ${v}</title></rect>`; if (i % 2 === 0) s += `<text x="${pl + i * bw}" y="${H - 6}" fill="#5b667a" font-size="9">${e[i]}%</text>`; });
  if (ruin >= x0 && ruin <= x1) { const x = pl + (ruin - x0) / (x1 - x0) * (W - pl - 8); s += `<line x1="${x}" x2="${x}" y1="6" y2="${H - pb}" stroke="#f87171" stroke-dasharray="4 3"/><text x="${x + 4}" y="14" fill="#f87171" font-size="10">ruin ${ruin}%</text>`; }
  return s + '</svg>';
}
function gauge(score) {
  const a = (score + 10) / 20 * Math.PI, cx = 100, cy = 92, r = 74, nx = cx - r * .86 * Math.cos(a), ny = cy - r * .86 * Math.sin(a);
  const arc = (t0, t1, col) => { const p = t => [cx - r * Math.cos(t * Math.PI), cy - r * Math.sin(t * Math.PI)]; const [x0, y0] = p(t0), [x1, y1] = p(t1); return `<path d="M${x0} ${y0} A${r} ${r} 0 0 1 ${x1} ${y1}" stroke="${col}" stroke-width="12" fill="none" opacity=".85"/>`; };
  return `<svg viewBox="0 0 200 118" width="170">${arc(0, .33, '#f87171')}${arc(.33, .67, '#fbbf24')}${arc(.67, 1, '#34d399')}<line x1="${cx}" y1="${cy}" x2="${nx}" y2="${ny}" stroke="#e6ebf4" stroke-width="3" stroke-linecap="round"/><circle cx="${cx}" cy="${cy}" r="6" fill="#e6ebf4"/><text x="${cx}" y="${cy + 22}" fill="#e6ebf4" font-size="20" font-weight="700" text-anchor="middle">${sgn(score, 0)}</text><text x="22" y="112" fill="#5b667a" font-size="9">-10</text><text x="178" y="112" fill="#5b667a" font-size="9" text-anchor="end">+10</text></svg>`;
}
const checksTable = ch => `<div class="tscroll"><table><tbody>${(ch || []).map(c => `<tr><td style="width:22px">${c.passed ? (c.warn ? '⚠️' : '✅') : '❌'}</td><td class="mono" style="white-space:nowrap">${esc(c.name)}</td><td class="mut">${esc(c.detail)}</td></tr>`).join('')}</tbody></table></div>`;

/* ---- agent 12 · per-trade Monte Carlo (ladder 10 / 100 / 1000 / 5000 runs) */
const VK = v => v === 'GOOD' ? 'up' : v === 'MARGINAL' ? 'warn' : (v === 'BAD' || v === 'ERROR') ? 'dn' : 'mut';
function ladderChart(v, W = 520, H = 230) {
  const L = v.sim.lenses.neutral.ladder, ev = v.evidence || {}, be = v.sim.breakeven_used_pct ?? v.sim.breakeven_cost_adj_pct;
  const pl = 38, pr = 14, pt = 14, pb = 30;
  const vals = [be, 0, ...L.flatMap(p => p.win_rate_pct == null ? [] : p.win_rate_ci95), ...(ev.win_rate_pct != null ? [ev.win_rate_pct, ...(ev.win_rate_ci95 || [])] : [])];
  const hi = Math.min(100, Math.ceil(Math.max(...vals) / 10) * 10 + 5), lo = 0;
  const X = i => pl + (i + .5) / L.length * (W - pl - pr), Y = x => pt + (hi - x) * (H - pt - pb) / (hi - lo);
  let s = `<svg viewBox="0 0 ${W} ${H}" width="100%">`;
  for (let k = 0; k <= 4; k++) { const x = lo + (hi - lo) * k / 4; s += `<line x1="${pl}" x2="${W - pr}" y1="${Y(x)}" y2="${Y(x)}" stroke="#141c2b"/><text x="${pl - 4}" y="${Y(x) + 3}" fill="#5b667a" font-size="9" text-anchor="end">${fmt(x, 0)}%</text>`; }
  if (ev.win_rate_pct != null) {
    const c = ev.win_rate_ci95 || [ev.win_rate_pct, ev.win_rate_pct];
    s += `<rect x="${pl}" y="${Y(c[1])}" width="${W - pl - pr}" height="${Math.max(1, Y(c[0]) - Y(c[1]))}" fill="rgba(52,211,153,.12)"/><line x1="${pl}" x2="${W - pr}" y1="${Y(ev.win_rate_pct)}" y2="${Y(ev.win_rate_pct)}" stroke="#34d399" stroke-dasharray="2 3"/><text x="${W - pr}" y="${Y(ev.win_rate_pct) - 3}" fill="#34d399" font-size="9" text-anchor="end">strategy ${fmt(ev.win_rate_pct, 1)}% (95% CI band)</text>`;
  }
  s += `<line x1="${pl}" x2="${W - pr}" y1="${Y(be)}" y2="${Y(be)}" stroke="#f87171" stroke-dasharray="5 3"/><text x="${pl + 4}" y="${Y(be) - 3}" fill="#f87171" font-size="9">break-even ${fmt(be, 1)}%</text>`;
  const pts = L.map((p, i) => p.win_rate_pct == null ? null : [X(i), Y(p.win_rate_pct)]).filter(Boolean);
  if (pts.length > 1) s += `<polyline fill="none" stroke="#4fd1c5" stroke-width="1.5" points="${pts.map(q => q.join(',')).join(' ')}"/>`;
  L.forEach((p, i) => {
    s += `<text x="${X(i)}" y="${H - 12}" fill="#9aa6bb" font-size="10" text-anchor="middle">${p.runs} runs</text>`;
    if (p.win_rate_pct == null) return;
    const x = X(i), a = Y(p.win_rate_ci95[1]), b = Y(p.win_rate_ci95[0]);
    s += `<line x1="${x}" x2="${x}" y1="${a}" y2="${b}" stroke="#4fd1c5" opacity=".6"/><line x1="${x - 5}" x2="${x + 5}" y1="${a}" y2="${a}" stroke="#4fd1c5" opacity=".6"/><line x1="${x - 5}" x2="${x + 5}" y1="${b}" y2="${b}" stroke="#4fd1c5" opacity=".6"/><circle cx="${x}" cy="${Y(p.win_rate_pct)}" r="4" fill="#4fd1c5"><title>${p.runs} runs: ${p.win_rate_pct}% (95% CI ${p.win_rate_ci95[0]}–${p.win_rate_ci95[1]})</title></circle><text x="${x + 8}" y="${Y(p.win_rate_pct) + 3}" fill="#e6ebf4" font-size="10">${fmt(p.win_rate_pct, 1)}%</text>`;
  });
  return s + '</svg>';
}
function outcomeBar(mix) {
  const keys = [['TP', '#34d399', 'TP first'], ['SL', '#f87171', 'SL first'], ['TIMEOUT', '#fbbf24', 'time-out'], ['UNFILLED', '#5b667a', 'never filled']], tot = keys.reduce((a, [k]) => a + (mix[k] || 0), 0) || 1;
  let x = 0, s = `<svg viewBox="0 0 520 34" width="100%">`;
  keys.forEach(([k, c]) => { const w = (mix[k] || 0) / tot * 520; s += `<rect x="${x}" y="2" width="${w}" height="22" fill="${c}" opacity=".85"><title>${k}: ${mix[k] || 0}</title></rect>`; x += w; });
  return s + `</svg><div class="row2" style="gap:12px">${keys.map(([k, c, l]) => `<span class="mono" style="color:${c};font-size:11px">■ ${l} ${mix[k] || 0} (${fmt((mix[k] || 0) / tot * 100, 1)}%)</span>`).join('')}</div>`;
}
function rHist(h, W = 520, H = 150) {
  if (!h || !h.counts || !h.counts.length) return '<div class="hint">no filled trades</div>';
  const c = h.counts, e = h.edges, mx = Math.max(...c, 1), pl = 8, pb = 22, bw = (W - pl - 8) / c.length, x0 = e[0], x1 = e[e.length - 1];
  let s = `<svg viewBox="0 0 ${W} ${H}" width="100%">`;
  c.forEach((n, i) => { const hh = n / mx * (H - pb - 10), neg = e[i + 1] <= 0; s += `<rect x="${pl + i * bw + 1}" y="${H - pb - hh}" width="${bw - 2}" height="${hh}" rx="2" fill="${neg ? 'rgba(248,113,113,.65)' : 'rgba(79,209,197,.6)'}"><title>${e[i]}R to ${e[i + 1]}R : ${n}</title></rect>`; if (i % 2 === 0) s += `<text x="${pl + i * bw}" y="${H - 6}" fill="#5b667a" font-size="9">${e[i]}R</text>`; });
  if (x0 < 0 && x1 > 0) { const x = pl + (0 - x0) / (x1 - x0) * (W - pl - 8); s += `<line x1="${x}" x2="${x}" y1="4" y2="${H - pb}" stroke="#2a3850" stroke-dasharray="3 3"/>`; }
  return s + '</svg>';
}
function ladderTable(L) {
  return `<div class="tscroll"><table><thead><tr><th class="num">Runs</th><th class="num">Fill %</th><th class="num">TP</th><th class="num">SL</th><th class="num">Time-out</th><th class="num">Unfilled</th><th class="num">Win rate (95% CI)</th><th class="num">Expectancy</th><th class="num">R p5 / p50 / p95</th></tr></thead><tbody>${L.map(p => `<tr><td class="num mono"><b>${p.runs}</b></td><td class="num">${fmt(p.fill_prob_pct, 1)}</td><td class="num up">${p.tp}</td><td class="num dn">${p.sl}</td><td class="num">${p.timeout}</td><td class="num mut">${p.unfilled}</td><td class="num">${p.win_rate_pct == null ? '–' : `${fmt(p.win_rate_pct, 1)}% <span class="mut">(${p.win_rate_ci95[0]}–${p.win_rate_ci95[1]})</span>`}</td><td class="num ${cls(p.expectancy_r)}">${p.expectancy_r == null ? '–' : sgn(p.expectancy_r, 3) + 'R'}</td><td class="num mut">${p.r_pct ? `${p.r_pct.p5} / ${p.r_pct.p50} / ${p.r_pct.p95}` : '–'}</td></tr>`).join('')}</tbody></table></div>`;
}
function validationHTML(v, o = {}) {
  if (!v || !v.sim || !v.sim.lenses) return `<div class="hint">${v && v.reasons && v.reasons.length ? esc(v.reasons.join(' | ')) : 'Validator not reached: the Inspector or the Backtest gate refused this setup first (or validator.enabled is false).'}</div>`;
  const sim = v.sim, N = sim.lenses.neutral, D = sim.lenses.sample_drift, top = N.ladder[N.ladder.length - 1], ev = v.evidence || {}, pl = v.plan || {}, G = o.compact ? 'stack' : 'grid g2';
  const tile = (l, val, s, c = '') => `<div class="card kpi"><div class="l">${l}</div><div class="v ${c}" style="font-size:18px">${val}</div><div class="s">${s}</div></div>`;
  let h = `<div class="row2" style="margin-bottom:10px">${badge(v.verdict, VK(v.verdict))}${v.approved ? badge(v.risk_mult !== 1 ? `APPROVED · risk ×${v.risk_mult}` : 'APPROVED', 'up') : badge('REFUSED', 'dn')}<span class="mono mut">${esc(pl.side || '')} E ${fmt(pl.entry)} · SL ${fmt(pl.sl)} · TP ${fmt(pl.tp)} · R:R ${fmt(sim.rr, 2)}</span><span class="right hint">${top.runs} simulated futures · block-bootstrap of ${sim.hist_bars} real 1h bars · vol scale ×${sim.vol_scale}</span></div>
   <div class="${o.compact ? 'grid g2' : 'grid g6'}">${tile('Break-even win rate', fmt(sim.breakeven_used_pct ?? sim.breakeven_cost_adj_pct, 1) + '%', esc(sim.breakeven_basis || `${fmt(sim.breakeven_pct, 1)}% before costs`))}${tile('Luck-only win rate', top.win_rate_pct == null ? '–' : fmt(top.win_rate_pct, 1) + '%', `@ ${top.runs} runs, no edge`)}${tile('Strategy win rate', ev.win_rate_pct == null ? '–' : fmt(ev.win_rate_pct, 1) + '%', ev.n ? `n=${ev.n} · CI ${(ev.win_rate_ci95 || []).join('–')}` : 'no evidence')}${tile('Strategy expectancy', ev.expectancy_r == null ? '–' : sgn(ev.expectancy_r, 2) + 'R', v.stress && v.stress.stressed_expectancy_r != null ? `${sgn(v.stress.stressed_expectancy_r, 2)}R at costs ×${v.stress.cost_mult}` : '', cls(ev.expectancy_r))}${tile('Fill probability', fmt(top.fill_prob_pct, 1) + '%', 'entry touched in window')}${tile('Mean cost', top.mean_cost_r == null ? '–' : fmt(top.mean_cost_r, 3) + 'R', 'spread+slip+comm+swap')}</div>
   <div class="${G}" style="margin-top:12px"><div><b>Every run count, same trade</b>${ladderTable(N.ladder)}</div><div><b>Win rate vs number of runs</b>${ladderChart(v)}<div class="hint">dot = simulated win rate · whiskers = 95% CI · red = break-even · green = what the strategy achieved historically (agent 11)</div></div></div>
   <div class="${G}" style="margin-top:12px"><div><b>Outcome of ${top.runs} simulated futures</b>${outcomeBar(N.outcome_mix)}</div><div><b>Distribution of the trade result (R)</b>${rHist(N.r_hist)}</div></div>
   <div style="margin-top:10px"><b>Checks</b>${checksTable(v.checks)}</div>`;
  if (v.sizing_note) h += `<div class="infobox" style="margin-top:8px">${esc(v.sizing_note)}</div>`;
  if (v.reasons && v.reasons.length) h += `<div class="warnbox" style="margin-top:10px"><b>Reasons:</b>${list(v.reasons)}</div>`;
  if (v.llm_review) h += `<div class="hint" style="margin-top:8px">LLM sceptic: veto=${v.llm_review.veto}${v.llm_review.concerns.length ? ' · ' + esc(v.llm_review.concerns.join('; ')) : ''}</div>`;
  h += `<details style="margin-top:10px"><summary class="hint" style="cursor:pointer">Second lens: if the historical drift of the sample continued (information only)</summary>${ladderTable(D.ladder)}</details>
   <div class="hint" style="margin-top:8px">Win = trade closed with R &gt; 0 after costs, the same definition in the backtester and the validator. How to read it: the simulation is a random-walk market with the instrument’s real volatility, so its win rate is what this exact entry/SL/TP wins by <b>luck</b> (≈ the break-even rate, minus costs). A trade is GOOD only if the strategy’s historical win rate beats both that luck baseline and the break-even rate with 95% confidence, and still earns money when costs are ×${v.stress && v.stress.cost_mult || 2}. It cannot predict the future.</div>`;
  return h;
}
function symbolsTable(syms, editable) {
  const rows = Object.values(syms || {});
  if (!rows.length) return '';
  return `<h3 style="margin-top:14px">Symbols found on this account ${editable ? '' : '<span class="hint">(the swarm will use these names, lot sizes and tick values)</span>'}</h3><div class="tscroll"><table><thead><tr><th>Instrument</th><th>Broker symbol</th>${editable ? '<th>Override</th>' : ''}<th class="num">Lot min / step / max</th><th class="num">Value / price unit / lot</th><th class="num">Spread</th><th></th></tr></thead><tbody>${rows.map(p => `<tr><td class="mono"><b>${esc(p.symbol)}</b></td><td class="mono">${p.broker_symbol ? esc(p.broker_symbol) : '–'}${(p.candidates || []).length > 1 ? ` <span class="mut">(${p.candidates.length} matches)</span>` : ''}</td>${editable ? `<td><input data-symmap="${esc(p.symbol)}" placeholder="auto" style="width:130px" value="${esc((S.state.account && S.state.account.symbol_map && S.state.account.symbol_map[p.symbol]) || '')}"></td>` : ''}<td class="num">${p.ok ? `${p.volume_min} / ${p.volume_step} / ${p.volume_max}` : '–'}</td><td class="num">${p.ok ? fmt(p.per_unit, 4) : '–'}</td><td class="num">${p.ok ? fmt(p.spread_price, 4) : '–'}</td><td>${p.ok ? badge('ok', 'up') : badge(p.message || 'not found', 'dn')}</td></tr>`).join('')}</tbody></table></div>${editable ? '<div class="row2" style="margin-top:8px"><button class="btn" id="symmap-save">Save symbol overrides & re-read</button><span class="hint">Leave empty for auto-detect. Use it when your broker has several variants (e.g. XAUUSDm vs XAUUSD).</span></div>' : ''}`;
}
function accountStateCard(st) {
  const a = st.account_state, f = st.feasibility || [];
  const t = (l, v, c = '') => `<div class="card kpi"><div class="l">${l}</div><div class="v ${c}" style="font-size:17px">${v}</div></div>`;
  let h = `<div class="card" style="margin-bottom:16px"><h3>Account size · what every agent sees ${a ? badge(a.source === 'broker' ? 'read from broker' : 'paper simulator', a.source === 'broker' ? 'up' : 'info') : badge('not read yet', 'warn')}<span class="right"><button class="btn" id="acct-refresh">↻ Refresh from broker</button></span></h3>`;
  if (a && a.ok) h += `<div class="grid g6">${t('Equity', '$' + fmt(a.equity_usd))}${t('Balance', a.balance == null ? '–' : fmt(a.balance))}${t('Free margin', a.free_margin == null ? '–' : fmt(a.free_margin))}${t('Floating P&L', a.floating_pnl == null ? '–' : sgn(a.floating_pnl), cls(a.floating_pnl))}${t('Currency · kind', esc(a.currency) + ' · ' + esc(a.kind))}${t('Open positions', a.open_positions ?? '–')}</div>
    ${a.server ? `<div class="grid g4" style="margin-top:10px">${t('Server', esc(a.server))}${t('Account', esc(a.account_name || '') + (a.leverage ? ' · 1:' + a.leverage : ''))}${t('Broker', esc(a.company || a.broker))}${t('Can trade', a.trade_allowed === false ? '<span class="dn">read-only</span>' : 'yes')}</div>` : ''}
    <div class="grid g4" style="margin-top:10px">${t('Today (equity)', a.day_pnl_pct == null ? '–' : sgn(a.day_pnl_pct, 2) + '%', cls(a.day_pnl_pct))}${t('This week', a.week_pnl_pct == null ? '–' : sgn(a.week_pnl_pct, 2) + '%', cls(a.week_pnl_pct))}${t('Below peak', a.drawdown_pct == null ? '–' : fmt(a.drawdown_pct, 2) + '%')}${t('Peak equity', a.peak_equity == null ? '–' : '$' + fmt(a.peak_equity))}</div>
    ${(a.warnings || []).length ? `<div class="warnbox" style="margin-top:10px">${a.warnings.map(esc).join('<br>')}</div>` : ''}<div class="hint" style="margin-top:8px">${esc(a.message || '')} · daily / weekly / drawdown are equity-based (they include floating loss); snapshots start the first time the swarm sees the account.</div>`;
  else h += `<div class="hint">${a && !a.ok ? '<span class="dn">' + esc(a.message) + '</span> · ' : ''}Nothing about the account size is typed in or hard-coded for a linked broker: it is read at the start of every run (or press Refresh).</div>`;
  if (a && a.symbols) h += symbolsTable(a.symbols, true);
  h += `<h3 style="margin-top:16px">Can this account trade each market at all?</h3><div class="tscroll"><table><thead><tr><th>Symbol</th><th class="num">Min lot</th><th class="num">Typical ATR(1h)</th><th class="num">Min stop</th><th class="num">Risk at min lot</th><th class="num">Risk budget</th><th></th></tr></thead><tbody>${f.map(x => `<tr><td class="mono"><b>${x.symbol}</b></td><td class="num">${x.min_lot}</td><td class="num">${fmt(x.typical_atr_1h)}</td><td class="num">${fmt(x.min_stop)}</td><td class="num">$${fmt(x.risk_at_min_lot_usd)}${x.min_risk_pct_needed != null ? ` <span class="mut">(${fmt(x.min_risk_pct_needed, 1)}%)</span>` : ''}</td><td class="num">${x.risk_budget_usd == null ? '–' : '$' + fmt(x.risk_budget_usd)}</td><td>${x.tradable == null ? '' : x.tradable ? badge('tradable', 'up') : badge('too small', 'dn')}</td></tr>`).join('')}</tbody></table></div><div class="hint">“Too small” = the smallest lot the broker allows would already risk more than your per-trade budget on a normal-sized stop, so the Inspector will (correctly) refuse every trade. Raise the account, use a micro/cent account, or raise risk % knowingly.</div></div>`;
  return h;
}

/* ------------------------------------------------------------------ shell: nav / top bar / routing */
const ROUTES = [['overview', '◉', 'Overview'], ['pipeline', '⛓', 'Agent pipeline'], ['symbols', '📈', 'Symbols'], ['lab', '🧪', 'Backtest lab'], ['journal', '📒', 'Journal & Critic'], ['account', '🔐', 'Account & broker']];
function renderNav() {
  const cur = (location.hash.slice(2) || 'overview').split('/')[0];
  $('#nav').innerHTML = `<div class="sec">Workspace</div>` + ROUTES.map(([k, i, l]) => `<a href="#/${k}" class="${cur === k ? 'on' : ''}"><span>${i}</span>${l}</a>`).join('');
  const st = S.state; $('#llm-pill').innerHTML = `<span class="dot ${st?.llm_ready ? 'on' : 'off'}"></span>${st?.llm_ready ? 'Claude API key found' : 'no ANTHROPIC_API_KEY'}`;
}
function renderTop(title) {
  const a = S.state?.account;
  const chip = a ? `${a.type === 'real' ? '<span class="badge b-real">● REAL</span>' : badge('DEMO', 'info')} <span class="mono mut">${esc(a.broker)} · ${esc(a.label)}</span> <span class="mono">${S.state.equity != null ? '$' + fmt(S.state.equity, S.state.equity < 1000 ? 2 : 0) : 'equity: read at run'}</span>` : badge('no account', 'warn');
  $('#top').innerHTML = `<h1>${esc(title)}</h1><div class="grow"></div>${chip}<a class="btn" href="#/account" style="text-decoration:none">${a ? 'Account' : 'Set up account'}</a>`;
}
async function refreshState() { S.state = await api('/api/state'); }
async function loadLatestRun() { try { S.run = await api('/api/runs/latest'); } catch (e) { S.run = null; } }
async function route() {
  renderNav();
  const [page, arg] = (location.hash.slice(2) || 'overview').split('/');
  $('#drawer').classList.remove('open');
  try {
    if (!TOKEN) { $('#view').innerHTML = `<div class="warnbox">Missing token. Open the URL printed by <span class="mono">python main.py serve</span> (it contains <span class="mono">?token=…</span>).</div>`; return; }
    if (!S.state) await refreshState();
    if (!S.state.configured && page !== 'account') { location.hash = '#/account'; return; }
    ({ overview: pageOverview, pipeline: pagePipeline, symbols: pageSymbols, lab: pageLab, journal: pageJournal, account: pageAccount }[page] || pageOverview)(arg);
  } catch (e) { $('#view').innerHTML = `<div class="warnbox">${esc(e.message)}</div>`; }
}
window.addEventListener('hashchange', route);
$('#drawer-x').onclick = () => $('#drawer').classList.remove('open');

/* ------------------------------------------------------------------ account / broker wizard (first screen) */
function wizInit() {
  const a = S.state.account || {};
  S.wiz = { step: 1, type: a.type || 'demo', broker: a.broker || 'mt5', sandbox: a.sandbox ?? true, creds: {}, label: a.label || '', equity: a.equity || 100,
    risk: a.risk_per_trade_pct || 0.5, open: a.max_total_open_risk_pct || 0.5, daily: a.max_daily_loss_pct || 2, weekly: a.max_weekly_loss_pct || 4, dd: a.max_equity_drawdown_pct || 10, confirm: '', test: null };
}
function pageAccount() {
  renderTop('Account & broker');
  if (!S.wiz) wizInit();
  const w = S.wiz, st = S.state, B = st.brokers, acct = st.account;
  const steps = ['Account type', 'Broker', 'Credentials', 'Capital & risk', 'Test & save'];
  let h = '';
  if (acct && !w.editing) {
    h += `<div class="card glow" style="margin-bottom:16px"><h3>Linked account</h3><div class="row2">${acct.type === 'real' ? '<span class="badge b-real">● REAL</span>' : badge('DEMO', 'info')}<b>${esc(acct.label)}</b><span class="mono mut">${esc(B[acct.broker]?.label || acct.broker)}${acct.sandbox ? ' · testnet/sandbox' : ''}</span>
      <span class="right mono">${acct.broker === 'paper' ? `start capital $${fmt(acct.equity, 0)}` : 'size read from broker'} · risk/trade ${acct.risk_per_trade_pct}% · max open ${acct.max_total_open_risk_pct}% · daily ${acct.max_daily_loss_pct}% · weekly ${acct.max_weekly_loss_pct ?? 4}% · DD ${acct.max_equity_drawdown_pct ?? 10}%</span></div>
      <div class="row2" style="margin-top:12px">${st.has_credentials[acct.broker] || acct.broker === 'paper' ? badge('credentials in memory', 'up') : badge('credentials not loaded (re-enter after restart)', 'warn')}
      ${acct.type === 'real' ? (st.allow_live ? badge('live sending ENABLED by server flag', 'dn') : badge('live sending disabled (server started without --allow-live)', 'mut')) : badge('orders simulated in journal', 'mut')}
      <button class="btn right" id="w-edit">Edit / re-link</button><button class="btn danger" id="w-reset">Reset</button></div></div>`;
    h += accountStateCard(st);
    $('#view').innerHTML = h; $('#acct-refresh').onclick = async () => { $('#acct-refresh').disabled = true; try { const r = await api('/api/account/refresh', { method: 'POST' }); S.state.account_state = r.account_state; S.state.feasibility = r.feasibility; S.state.equity = r.account_state.equity_usd; if (!r.account_state.ok) toast(r.account_state.message, true); } catch (e) { toast(e.message, true); } pageAccount(); };
    if ($('#symmap-save')) $('#symmap-save').onclick = async () => { const m = {}; $$('[data-symmap]').forEach(i => m[i.dataset.symmap] = i.value.trim()); try { const r = await api('/api/account/symbols', { method: 'POST', body: { symbol_map: m } }); S.state.account = r.account; $('#acct-refresh').click(); } catch (e) { toast(e.message, true); } };
    $('#w-edit').onclick = () => { S.wiz.editing = true; S.wiz.step = 1; pageAccount(); };
    $('#w-reset').onclick = async () => { if (confirm('Remove the saved account?')) { await api('/api/account', { method: 'DELETE' }); S.wiz = null; await refreshState(); pageAccount(); } };
    return;
  }
  h += `<div class="steps">${steps.map((s, i) => `<span class="${w.step === i + 1 ? 'on' : w.step > i + 1 ? 'done' : ''}">${i + 1} · ${s}</span>`).join('')}</div>`;
  if (!acct) h += `<div class="infobox" style="margin-bottom:16px"><b>Welcome.</b> Start here: choose whether this workspace runs on a <b>Demo</b> or a <b>Real</b> account and link a broker. Demo is the safe default: orders are simulated in the journal, nothing is ever sent to a broker.</div>`;
  const bro = B[w.broker], needCreds = w.broker !== 'paper';
  if (w.step === 1) h += `<div class="card"><h3>1 · Account type</h3><div class="cards3">
      <div class="opt ${w.type === 'demo' ? 'on' : ''}" data-type="demo"><b>🧪 Demo</b><small>Simulated orders (paper journal). You can optionally link a broker's demo / testnet account to read equity. No real money, ever.</small></div>
      <div class="opt ${w.type === 'real' ? 'on' : ''}" data-type="real"><b>⚠️ Real</b><small>Links a real-money broker account: equity is read from it and (only if the server runs with <span class="mono">--allow-live</span>) approved orders can be sent. Recommended only after weeks of demo results.</small></div></div>
      <div class="row2" style="margin-top:16px"><button class="btn pri right" data-go="2">Continue →</button></div></div>`;
  if (w.step === 2) h += `<div class="card"><h3>2 · Broker</h3><div class="cards3">${Object.entries(B).filter(([k]) => !(w.type === 'real' && k === 'paper')).sort((x, y) => (y[1].recommended ? 1 : 0) - (x[1].recommended ? 1 : 0)).map(([k, v]) => `<div class="opt ${w.broker === k ? 'on' : ''}" data-broker="${k}"><b>${esc(v.label)}</b> ${v.recommended ? badge('recommended', 'up') : ''}<small>${esc(v.desc)}</small></div>`).join('')}</div>
      <div class="row2" style="margin-top:16px"><button class="btn" data-go="1">← Back</button><button class="btn pri right" data-go="${needCreds ? 3 : 4}">Continue →</button></div></div>`;
  if (w.step === 3) h += `<div class="card"><h3>3 · ${esc(bro.label)} credentials</h3>
      ${bro.sandbox ? `<div class="field"><label>Environment</label><div class="seg"><button data-sb="1" class="${w.sandbox ? 'on' : ''}">Testnet / sandbox</button><button data-sb="0" class="${!w.sandbox ? 'on' : ''}">Live</button></div></div>` : ''}
      <div class="grid g2">${bro.fields.map(f => `<div class="field"><label>${esc(f.label)}</label><input data-cred="${f.key}" type="${f.secret ? 'password' : 'text'}" autocomplete="off" placeholder="${esc(f.default || f.placeholder || '')}" value="${esc(w.creds[f.key] ?? '')}"></div>`).join('')}</div>
      ${w.broker === 'mt5' ? `<div class="infobox"><b>Any broker or funded account:</b> use the MT5 login your broker / prop firm gave you (account number + password + server). For Exness the server looks like <span class="mono">Exness-MT5Trial15</span> (demo) or <span class="mono">Exness-MT5Real…</span>. The swarm then reads balance, equity, symbol names (XAUUSD may be XAUUSDm, GOLD…), lot sizes and prices <b>from that account</b>. A prop firm’s own daily / total loss rules are not known to the swarm: set the limits in step 4 tighter than theirs. An investor (read-only) password works for analysis but cannot trade. <b>Windows only:</b> the MetaTrader5 python package and the MT5 terminal must be installed on the PC that runs this server.</div>` : ''}
      <div class="infobox">🔒 Credentials stay in the memory of your local server process only. They are never written to disk by the dashboard and never sent back to the browser. Use a key with <b>read-only</b> permission unless you intend to trade live.</div>
      <div class="row2" style="margin-top:16px"><button class="btn" data-go="2">← Back</button><button class="btn pri right" data-go="4">Continue →</button></div></div>`;
  if (w.step === 4) h += `<div class="card"><h3>4 · Account size & risk limits</h3>
      ${w.broker === 'paper' ? `<div class="grid g3"><div class="field"><label>Starting capital (USD) · paper simulator</label><input id="w-eq" type="number" value="${w.equity}"></div></div><div class="hint">The simulator’s equity = this starting capital + the realised P&L of its closed paper trades.</div>`
        : `<div class="infobox"><b>The account size is not typed here.</b> At the start of every run the swarm reads the equity, balance, free margin, floating P&L and currency from your <b>${esc(bro.label)}</b> account (${w.type === 'real' ? 'real' : 'demo / testnet'}) and every agent sizes on that number. If the broker cannot be read, the run stops: it never falls back to a typed amount.</div>`}
      <div class="grid g3" style="margin-top:12px"><div class="field"><label>Label</label><input id="w-label" value="${esc(w.label)}" placeholder="${w.type}-${w.broker}"></div>
      <div class="field"><label>Risk per trade %</label><input id="w-risk" type="number" step="0.05" value="${w.risk}"></div>
      <div class="field"><label>Max total open risk %</label><input id="w-open" type="number" step="0.1" value="${w.open}"></div>
      <div class="field"><label>Daily loss stop % (equity, incl. floating)</label><input id="w-daily" type="number" step="0.5" value="${w.daily}"></div>
      <div class="field"><label>Weekly loss stop %</label><input id="w-weekly" type="number" step="0.5" value="${w.weekly}"></div>
      <div class="field"><label>Max drawdown from equity peak %</label><input id="w-dd" type="number" step="1" value="${w.dd}"></div></div>
      <div class="hint">These are HARD limits enforced in code by the Inspector. Percentages apply to whatever the account size turns out to be.</div>
      <div class="row2" style="margin-top:16px"><button class="btn" data-go="${needCreds ? 3 : 2}">← Back</button><button class="btn pri right" data-go="5">Continue →</button></div></div>`;
  if (w.step === 5) {
    const t = w.test;
    h += `<div class="card"><h3>5 · Test connection & save</h3><div class="kv"><span>Type</span><span>${w.type.toUpperCase()}</span><span>Broker</span><span>${esc(bro.label)}${bro.sandbox ? (w.sandbox ? ' · testnet' : ' · live') : ''}</span><span>Account size</span><span>${w.broker === 'paper' ? '$' + fmt(w.equity, 0) + ' (simulated)' : 'read from broker'}</span><span>Risk / trade</span><span>${w.risk}%</span><span>Max open / daily / weekly / DD</span><span>${w.open}% / ${w.daily}% / ${w.weekly}% / ${w.dd}%</span></div><hr>
      <div class="row2"><button class="btn" id="w-test">${needCreds ? 'Test connection' : 'Check simulator'}</button><span id="w-testres">${t ? (t.ok ? badge('connected', 'up') + ` <span class="mono">${esc(t.message)}${t.equity != null ? ` · equity ${fmt(t.equity)} ${esc(t.currency)}` : ''}${t.balance != null ? ` · balance ${fmt(t.balance)}` : ''}${t.free_margin != null ? ` · free margin ${fmt(t.free_margin)}` : ''}${t.floating_pnl != null ? ` · floating ${sgn(t.floating_pnl)}` : ''}${t.open_positions != null ? ` · ${t.open_positions} open` : ''}${t.account_kind ? ' · ' + esc(t.account_kind) : ''}</span>` : badge('failed', 'dn') + ` <span class="mut">${esc(t.message)}</span>`) : '<span class="hint">not tested yet</span>'}</span></div>
      ${t && t.warnings?.length ? `<div class="warnbox" style="margin-top:10px">${t.warnings.map(esc).join('<br>')}</div>` : ''}
      ${t && t.ok && t.symbols ? symbolsTable(t.symbols, false) : ''}
      ${w.type === 'real' ? `<div class="warnbox" style="margin-top:14px"><b>You are linking a REAL-money account.</b><ul class="l"><li>This is an analysis tool, not financial advice; the live order code is <b>untested</b>.</li><li>Orders are only sent if the server was started with <span class="mono">--allow-live</span>, you tick “send to broker” on a run, and the run uses live data + Claude agents.</li><li>Every trade still has to pass the Inspector hard rules and the Backtest gate.</li></ul><div class="field" style="margin-top:8px"><label>Type REAL to confirm</label><input id="w-confirm" value="${esc(w.confirm)}" placeholder="REAL"></div></div>` : ''}
      <div class="row2" style="margin-top:16px"><button class="btn" data-go="4">← Back</button><button class="btn pri right" id="w-save">Save & enter dashboard</button></div></div>`;
  }
  $('#view').innerHTML = h;
  const grab = () => { // persist field values before navigating
    $$('[data-cred]').forEach(i => w.creds[i.dataset.cred] = i.value);
    if ($('#w-label')) { w.label = $('#w-label').value; if ($('#w-eq')) w.equity = +$('#w-eq').value; w.risk = +$('#w-risk').value; w.open = +$('#w-open').value; w.daily = +$('#w-daily').value; w.weekly = +$('#w-weekly').value; w.dd = +$('#w-dd').value; }
    if ($('#w-confirm')) w.confirm = $('#w-confirm').value;
  };
  $$('[data-type]').forEach(e => e.onclick = () => { w.type = e.dataset.type; if (w.type === 'real' && w.broker === 'paper') w.broker = 'binance'; pageAccount(); });
  $$('[data-broker]').forEach(e => e.onclick = () => { w.broker = e.dataset.broker; w.test = null; pageAccount(); });
  $$('[data-sb]').forEach(e => e.onclick = () => { grab(); w.sandbox = e.dataset.sb === '1'; pageAccount(); });
  $$('[data-go]').forEach(e => e.onclick = () => { grab(); w.step = +e.dataset.go; pageAccount(); });
  if ($('#w-test')) $('#w-test').onclick = async () => { grab(); $('#w-testres').innerHTML = '<span class="hint">testing…</span>';
    try { w.test = await api('/api/broker/test', { method: 'POST', body: { broker: w.broker, credentials: w.creds, sandbox: w.sandbox, type: w.type } }); } catch (e) { w.test = { ok: false, message: e.message }; } pageAccount(); };
  if ($('#w-save')) $('#w-save').onclick = async () => { grab();
    try { await api('/api/account', { method: 'POST', body: { type: w.type, broker: w.broker, label: w.label, sandbox: w.sandbox, equity: w.broker === 'paper' ? w.equity : undefined, risk_per_trade_pct: w.risk, max_total_open_risk_pct: w.open, max_daily_loss_pct: w.daily, max_weekly_loss_pct: w.weekly, max_equity_drawdown_pct: w.dd, credentials: w.creds, confirm_real: w.confirm } });
      if (w.broker !== 'paper') { try { await api('/api/account/refresh', { method: 'POST' }); } catch (e) { /* the run will report it */ } }   // read the balance right away
      S.wiz = null; await refreshState(); toast('Account saved'); location.hash = '#/overview'; } catch (e) { toast(e.message, true); } };
}

/* ------------------------------------------------------------------ overview */
function stagesFor(run, sym) {
  const d = run.detail[sym], R = d.reports, o = R.orchestrator, t = R.technical, v = d.verdict, bt = R.backtester;
  const chk = v?.checks || [], only = chk.length === 1 && chk[0].name === 'orchestrator_decision';
  const failed = chk.filter(c => !c.passed).length, pass = chk.length - failed;
  const insp = !chk.length || only ? ['Inspector', 'n/a', 'skip'] : ['Inspector', `${pass}/${chk.length} checks`, failed ? 'no' : (v.llm_audit?.veto ? 'no' : 'ok')];
  return [['Technical', t ? (t.setup !== 'NONE' ? `${t.setup} · ${t.confidence}` : 'no setup') : '–', t && t.setup !== 'NONE' ? 'ok' : 'no'],
    ['Orchestrator', o ? o.decision : '–', o ? (o.decision === 'TRADE' ? 'ok' : o.decision === 'WAIT' ? 'mid' : 'no') : 'skip'], insp,
    ['Backtest gate', bt ? bt.verdict.replace('_', ' ') : (insp[2] === 'ok' ? 'disabled' : 'not reached'), bt ? (bt.verdict === 'PASS' ? 'ok' : bt.verdict === 'PASS_REOPTIMIZED' ? 'mid' : 'no') : 'skip'],
    ['Trade validator', R.trade_validator ? `${R.trade_validator.verdict}${R.trade_validator.risk_mult !== 1 ? ' ×' + R.trade_validator.risk_mult : ''}` : 'not reached', R.trade_validator ? (R.trade_validator.verdict === 'GOOD' ? 'ok' : R.trade_validator.approved ? 'mid' : 'no') : 'skip'],
    ['Execution', d.execution ? `${d.execution.order_type} · ${d.execution.lots} lots` : 'none', d.execution ? 'ok' : 'skip']];
}
const funnel = (run, sym) => `<div class="funnel">${stagesFor(run, sym).map(([a, b, c]) => `<div class="stage ${c}"><small>${a}</small><b>${esc(b)}</b></div>`).join('')}</div>`;

async function pageOverview() {
  renderTop('Overview');
  const st = S.state, a = st.account, o = S.opts;
  if (!S._loaded) { await loadLatestRun(); S._loaded = true; }
  if (!o.symbols) o.symbols = st.instruments.map(i => i.symbol);
  const js = st.journal_stats || {}, ex = st.exposure || {};
  const kpi = (l, v, s, c = '') => `<div class="card kpi"><div class="l">${l}</div><div class="v ${c}">${v}</div><div class="s">${s || ''}</div></div>`;
  let h = `<div class="grid g6">${kpi('Equity', st.equity != null ? '$' + fmt(st.equity, st.equity < 1000 ? 2 : 0) : 'read at run', (st.account_state?.source === 'broker' ? 'read from ' + a.broker : a.broker === 'paper' ? 'paper simulator' : 'not read yet'))}${kpi('Open risk', fmt(ex.open_risk_pct, 2) + '%', `cap ${st.account_cfg.max_total_open_risk_pct}%`, ex.open_risk_pct > 0 ? 'warn' : '')}${kpi('Today P&L', sgn((st.account_state?.day_pnl_pct ?? ex.today_pnl_pct ?? 0), 2) + '%', `equity based · stop at -${st.account_cfg.max_daily_loss_pct}%`, cls(st.account_state?.day_pnl_pct ?? ex.today_pnl_pct))}${kpi('Closed trades', js.closed_trades || 0, 'live journal')}${kpi('Win rate', js.closed_trades ? fmt(js.win_rate_pct, 1) + '%' : '–', 'closed paper/live trades')}${kpi('Total R', js.closed_trades ? sgn(js.total_r, 1) + 'R' : '–', js.closed_trades ? 'avg ' + sgn(js.avg_r) + 'R' : '', cls(js.total_r))}</div><div class="sp"></div>`;
  h += `<div class="card glow"><h3>Run the swarm</h3><div class="grid g2"><div>
      <div class="field"><label>Symbols</label><div class="chips">${st.instruments.map(i => `<span class="chip ${o.symbols.includes(i.symbol) ? 'on' : ''}" data-sym="${i.symbol}">${i.symbol}</span>`).join('')}</div></div>
      <div class="row2"><div><label>Market data</label><div class="seg" id="seg-data"><button data-v="0" class="${!o.demo ? 'on' : ''}">Live</button><button data-v="1" class="${o.demo ? 'on' : ''}">Demo (synthetic)</button></div></div>
      <div><label>Agents</label><div class="seg" id="seg-llm"><button data-v="0" class="${!o.no_llm ? 'on' : ''}">Claude</button><button data-v="1" class="${o.no_llm ? 'on' : ''}">Rule-based (free)</button></div></div></div>
      <div class="field" style="margin-top:12px"><label>Question / focus (optional)</label><input id="q" value="${esc(o.query)}" placeholder="e.g. focus on the post-FOMC setup"></div>
      ${a.type === 'real' ? `<label style="display:flex;gap:8px;align-items:center;text-transform:none;letter-spacing:0;font-size:13px;color:${st.allow_live ? '#ffb4b4' : 'var(--dim)'}"><input type="checkbox" id="live" style="width:auto" ${o.send_live ? 'checked' : ''} ${st.allow_live ? '' : 'disabled'}> Send Inspector + Backtest-approved orders to the REAL broker ${st.allow_live ? '' : '(disabled: start server with --allow-live)'}</label>` : `<div class="hint">Demo account: approved trades are simulated in the paper journal.</div>`}
      <div class="row2" style="margin-top:14px"><button class="btn pri" id="run-btn" ${S.busy ? 'disabled' : ''}>▶ Run 12-agent swarm</button>${!st.llm_ready && !o.no_llm && !o.demo ? badge('no API key → pick Rule-based or Demo', 'warn') : ''}</div></div>
      <div><label>Live log</label><div class="log" id="log">${S.lastLog || '<span class="mut">waiting for a run…</span>'}</div></div></div></div><div class="sp"></div>`;
  const run = S.run;
  if (run) {
    const sm = run.summary;
    h += `<div class="grid g2"><div class="card"><h3>Executive summary ${run.demo ? badge('DEMO DATA', 'warn') : ''}</h3><div style="font-size:15px;font-weight:600;margin-bottom:6px">${esc(sm.headline)}</div><div class="mut">${esc(run.ts.replace('T', ' '))} UTC · ${run.symbols.join(', ')} · LLM calls ${run.usage.calls}</div>${list(sm.ranking)}<div style="margin-top:8px"><b>Risk notes</b>${list(sm.portfolio_risk_notes)}</div><div class="infobox" style="margin-top:10px"><b>Bottom line:</b> ${esc(sm.bottom_line)}</div></div>
      <div class="card"><h3>Macro snapshot</h3><div class="tscroll"><table><thead><tr><th>Asset</th><th class="num">Last</th><th class="num">1d</th><th class="num">5d</th><th class="num">20d</th></tr></thead><tbody>${Object.entries(run.world.macro).filter(([k]) => !k.startsWith('_')).map(([k, v]) => `<tr><td class="mono">${k}</td><td class="num">${fmt(v.last)}</td><td class="num ${cls(v.chg_1d_pct)}">${sgn(v.chg_1d_pct)}%</td><td class="num ${cls(v.chg_5d_pct)}">${sgn(v.chg_5d_pct)}%</td><td class="num ${cls(v.chg_20d_pct)}">${sgn(v.chg_20d_pct)}%</td></tr>`).join('')}</tbody></table></div></div></div><div class="sp"></div>
      <div class="card"><h3>Decision funnel · every trade must survive all gates</h3>${run.symbols.map(s => { const it = run.items.find(x => x.symbol === s), d = run.detail[s], p = d.reports.orchestrator; return `<div style="margin-bottom:14px"><div class="row2" style="margin-bottom:6px"><a href="#/symbols/${s}" style="color:var(--tx);font-weight:700;text-decoration:none;font-size:15px">${s}</a>${p ? decB(p.decision) + dirB(p.direction) : ''}<span class="mono mut">${d.market ? fmt(d.market.last_price) : ''}</span>${it.approved ? badge('APPROVED → EXECUTION', 'up') : badge('NO TRADE', 'mut')}<span class="right mono mut">${p?.entry ? `E ${fmt(p.entry)} · SL ${fmt(p.stop_loss)} · TP ${fmt(p.take_profit)} · R:R ${it.rr ?? '–'}` : ''}</span></div>${funnel(run, s)}${it.rejections?.length && !it.approved ? `<div class="hint" style="margin-top:6px">↳ ${esc(it.rejections.slice(0, 2).join(' | ').slice(0, 260))}</div>` : ''}</div>`; }).join('')}</div><div class="sp"></div>
      <div class="card"><h3>Upcoming calendar (USD)</h3><div class="tscroll"><table><tbody>${run.world.calendar.slice(0, 8).map(e => `<tr><td class="mono mut">${esc(e.time_utc.replace('T', ' '))}</td><td>${esc(e.title)}</td><td>${badge(e.impact, e.impact === 'High' ? 'dn' : 'warn')}</td></tr>`).join('') || '<tr><td class="mut">none</td></tr>'}</tbody></table></div></div>`;
  } else h += `<div class="card"><div class="mut">No run yet. Press <b>Run 12-agent swarm</b> (tip: start with <b>Demo (synthetic)</b> to see the whole flow).</div></div>`;
  $('#view').innerHTML = h;
  $$('[data-sym]').forEach(e => e.onclick = () => { const s = e.dataset.sym; o.symbols = o.symbols.includes(s) ? o.symbols.filter(x => x !== s) : [...o.symbols, s]; e.classList.toggle('on'); });
  $$('#seg-data button').forEach(b => b.onclick = () => { o.demo = b.dataset.v === '1'; pageOverview(); });
  $$('#seg-llm button').forEach(b => b.onclick = () => { o.no_llm = b.dataset.v === '1'; pageOverview(); });
  $('#q').oninput = e => o.query = e.target.value; if ($('#live')) $('#live').onchange = e => o.send_live = e.target.checked;
  $('#run-btn').onclick = async () => {
    if (S.busy) return;
    if (!o.symbols.length) return toast('pick at least one symbol', true);
    if (o.send_live && !confirm('Send approved orders to your REAL broker account?')) return;
    S.busy = true; $('#run-btn').disabled = true; const log = $('#log'); log.innerHTML = '';
    try { await runJob('/api/run', { symbols: o.symbols, demo: o.demo, no_llm: o.no_llm, send_live: o.send_live, query: o.query }, log); S.lastLog = log.innerHTML; await loadLatestRun(); await refreshState(); toast('Run complete'); }
    catch (e) { toast(e.message, true); S.lastLog = log.innerHTML; } finally { S.busy = false; }
    pageOverview();
  };
}

/* ------------------------------------------------------------------ pipeline view */
const NODE_ORDER = [['plan'], ['data_fetcher', 'scanner'], ['PAR', ['news_sentiment', 'technical', 'macro', 'social_onchain']], ['decide'], ['inspector', 'backtester', 'trade_validator', 'execution'], ['critic']];
function nodeInfo(key, d, run, sym) {
  const R = d.reports, t = d.trace.find(x => x.agent === key) || {};
  const meta = S.state.agents.find(a => a.key === (key === 'plan' || key === 'decide' ? 'orchestrator' : key));
  const base = { num: meta.number, emoji: meta.emoji, name: meta.name, src: t.source, secs: t.seconds, st: 'skip', desc: '', data: R[key] };
  switch (key) {
    case 'plan': return { ...base, name: 'Orchestrator · plan', src: 'deterministic', data: run.plan[sym], st: 'ok', desc: `routing · social mode: ${run.plan[sym].social_mode}` };
    case 'data_fetcher': return { ...base, st: R.data_fetcher.quality_ok ? 'ok' : 'no', desc: `${R.data_fetcher.issues.length} issue(s) · last ${fmt(d.market?.last_price)}` };
    case 'scanner': return { ...base, st: R.scanner ? 'ok' : 'skip', src: 'scan', desc: R.scanner ? `opportunity score ${R.scanner.score}` : '' };
    case 'news_sentiment': return { ...base, st: R.news_sentiment.freeze_trading ? 'no' : 'ok', desc: `sentiment ${sgn(R.news_sentiment.score, 0)}${R.news_sentiment.freeze_trading ? ' · FREEZE' : ''}` };
    case 'technical': return { ...base, st: R.technical.setup !== 'NONE' ? 'ok' : 'mid', desc: `${R.technical.setup} · conf ${R.technical.confidence}` };
    case 'macro': return { ...base, st: 'ok', desc: `${R.macro.regime} · ${R.macro.asset_bias}` };
    case 'social_onchain': return { ...base, st: 'ok', desc: `${R.social_onchain.crowd_bias} · contrarian ${R.social_onchain.contrarian_signal}` };
    case 'decide': { const o = R.orchestrator; return { ...base, name: 'Orchestrator · decision', src: 'llm?', data: o, st: o.decision === 'TRADE' ? 'ok' : o.decision === 'WAIT' ? 'mid' : 'no', desc: `${o.decision} ${o.direction} · conf ${o.confidence}` }; }
    case 'inspector': { const v = d.verdict, f = v ? v.checks.filter(c => !c.passed).length : 0; return { ...base, data: v, st: !v ? 'skip' : f ? 'no' : 'ok', desc: v ? `${v.checks.length - f}/${v.checks.length} hard checks` : '' }; }
    case 'backtester': { const b = R.backtester; return { ...base, src: b ? 'deterministic' : '', st: !b ? 'skip' : b.verdict === 'PASS' ? 'ok' : b.verdict === 'PASS_REOPTIMIZED' ? 'mid' : 'no', desc: b ? `${b.verdict} · n=${b.n_trades}` : 'not reached', data: b && { ...b, mc: { ...b.mc, fan: undefined } } }; }
    case 'trade_validator': { const tv = R.trade_validator, tp = tv && tv.sim && tv.sim.lenses ? tv.sim.lenses.neutral.ladder.slice(-1)[0] : null; return { ...base, src: tv ? 'deterministic' : '', st: !tv ? 'skip' : tv.verdict === 'GOOD' ? 'ok' : tv.approved ? 'mid' : 'no', desc: tv ? `${tv.verdict} · ${tp ? tp.runs + ' runs · luck WR ' + tp.win_rate_pct + '%' : ''}` : 'not reached', data: tv }; }
    case 'execution': return { ...base, st: d.execution ? 'ok' : 'skip', src: d.execution ? 'deterministic' : '', desc: d.execution ? `${d.execution.order_type} · ${d.execution.submission.status}` : 'not triggered', data: d.execution };
    case 'critic': return { ...base, st: 'skip', src: '', desc: 'runs on closed trades (Journal tab)', data: null };
  }
}
async function pagePipeline() {
  renderTop('Agent pipeline');
  if (!S._loaded) { await loadLatestRun(); S._loaded = true; }
  const run = S.run; if (!run) { $('#view').innerHTML = '<div class="card mut">Run the swarm first (Overview).</div>'; return; }
  if (!S.sym || !run.symbols.includes(S.sym)) S.sym = run.symbols[0];
  const d = run.detail[S.sym];
  const node = key => { const n = nodeInfo(key, d, run, S.sym); return `<div class="node ${n.st}" data-node="${key}"><div class="h"><span class="n">${String(n.num).padStart(2, '0')}</span><span>${n.emoji}</span><span>${esc(n.name.length > 30 ? n.name.slice(0, 28) + '…' : n.name)}</span></div><div class="d">${esc(n.desc)}</div><div class="f"><span>${esc(n.src || '')}</span><span>${n.secs != null ? n.secs + 's' : ''}</span></div></div>`; };
  const arrow = '<div class="arrow">▼</div>';
  let h = `<div class="row2" style="margin-bottom:14px"><div class="chips">${run.symbols.map(s => `<span class="chip ${S.sym === s ? 'on' : ''}" data-s="${s}">${s}</span>`).join('')}</div><span class="hint right">click a node to inspect that agent’s output</span></div><div class="card"><div class="flow">`;
  NODE_ORDER.forEach((r, i) => {
    if (i) h += arrow;
    if (r[0] === 'PAR') h += `<div class="parallel"><small>parallel</small><div class="row">${r[1].map(node).join('')}</div></div>`;
    else h += `<div class="row">${r.map(node).join('')}</div>`;
  });
  $('#view').innerHTML = h + `</div></div><div class="hint" style="margin-top:10px">Flow: Orchestrator routes → Data Fetcher → Scanner → (News ∥ Technical ∥ Macro ∥ Social/On-chain) → Orchestrator resolves conflicts → Inspector (hard rules) → Backtest/Monte-Carlo gate → Trade Validator (per-trade Monte Carlo at 10/100/1000/5000 runs) → Execution (only if all passed) → Critic learns from closed trades.</div>`;
  $$('[data-s]').forEach(e => e.onclick = () => { S.sym = e.dataset.s; pagePipeline(); });
  $$('[data-node]').forEach(e => e.onclick = () => { const n = nodeInfo(e.dataset.node, d, run, S.sym); openDrawer(`${n.emoji} ${n.num}. ${n.name}`, `<div class="row2">${srcB(n.src)}<span class="mono mut">${n.secs ?? ''}${n.secs != null ? 's' : ''}</span></div><pre class="code">${esc(JSON.stringify(n.data, null, 2) ?? 'no output (agent did not run for this symbol)')}</pre>`); });
}

/* ------------------------------------------------------------------ symbol detail: chart + all agent cards */
const tr = (d, k) => (d.trace.find(x => x.agent === k) || {});
const head = (n, name, d, key) => `<h3><span class="n">${String(n).padStart(2, '0')}</span>${esc(name)}<span class="right">${srcB(tr(d, key).source)}</span></h3>`;
async function pageSymbols(arg) {
  renderTop('Symbols');
  if (!S._loaded) { await loadLatestRun(); S._loaded = true; }
  const run = S.run; if (!run) { $('#view').innerHTML = '<div class="card mut">Run the swarm first (Overview).</div>'; return; }
  S.sym = arg && run.symbols.includes(arg) ? arg : (S.sym && run.symbols.includes(S.sym) ? S.sym : run.symbols[0]);
  const d = run.detail[S.sym], R = d.reports, o = R.orchestrator, t = R.technical, v = d.verdict, bt = R.backtester;
  const p = o && o.entry != null ? o : null, lv = [];
  const plan = p ? { e: p.entry, s: p.stop_loss, t: p.take_profit } : t && t.setup !== 'NONE' ? { e: t.entry, s: t.stop_loss, t: t.take_profit } : null;
  if (plan) lv.push({ k: 'entry', v: plan.e, t: 'ENT' }, { k: 'sl', v: plan.s, t: 'SL' }, { k: 'tp', v: plan.t, t: 'TP' });
  if (d.market) lv.push({ k: 'px', v: d.market.last_price, t: 'PX' });
  const tfs = Object.keys(d.series); if (!tfs.includes(S.tf)) S.tf = tfs.includes('1h') ? '1h' : tfs[0];
  let h = `<div class="row2" style="margin-bottom:12px"><div class="chips">${run.symbols.map(s => `<a class="chip ${S.sym === s ? 'on' : ''}" href="#/symbols/${s}" style="text-decoration:none">${s}</a>`).join('')}</div></div>
   <div class="card glow"><div class="row2" style="margin-bottom:10px"><b style="font-size:20px">${S.sym}</b><span class="mono" style="font-size:18px">${fmt(d.market?.last_price)}</span>${decB(o?.decision)}${dirB(o?.direction)}${v?.approved && d.execution ? badge('APPROVED', 'up') : badge('NOT EXECUTED', 'mut')}
   <div class="seg right">${tfs.map(f => `<button data-tf="${f}" class="${S.tf === f ? 'on' : ''}">${f}</button>`).join('')}</div></div>${candleChart(d.series[S.tf], lv)}
   <div class="hint" style="margin-top:6px">Green/red boxes = fair-value gaps · dashed boxes = order blocks · purple lines = equal highs/lows (liquidity) · blue/red/green = entry / stop / target shading</div></div><div class="sp"></div><div class="grid gauto">`;
  // 1 orchestrator
  h += `<div class="card">${head(1, 'Orchestrator · CIO', d, 'orchestrator')}<div class="row2">${decB(o?.decision)}${dirB(o?.direction)}<span class="mono">conf ${o?.confidence}</span></div>${confBar(o?.confidence)}<p>${esc(o?.thesis)}</p><b>Conflicts resolved</b>${list(o?.conflicts_resolved)}<div class="hint">Invalidation: ${esc(o?.invalidation || '–')}</div></div>`;
  // 4 technical
  h += `<div class="card">${head(4, 'Technical · SMC', d, 'technical')}<div class="row2">${biasB(t.bias)}${dirB(t.setup)}${t.zone_type ? badge(t.zone_type, 'vio') : ''}<span class="mono">conf ${t.confidence}</span></div>${confBar(t.confidence)}${kv({ Entry: fmt(t.entry), 'Stop loss': fmt(t.stop_loss), 'Take profit': fmt(t.take_profit), Zone: t.entry_zone ? t.entry_zone.join(' – ') : '–' })}${list(t.reasoning)}<div class="hint">Invalidation: ${esc(t.invalidation)}</div></div>`;
  // 3 news
  h += `<div class="card">${head(3, 'News & Sentiment', d, 'news_sentiment')}<div class="row2" style="justify-content:space-between">${gauge(R.news_sentiment.score)}<div>${R.news_sentiment.freeze_trading ? badge('TRADING FREEZE', 'dn') : badge('no freeze', 'up')}</div></div><p class="mut">${esc(R.news_sentiment.summary)}</p><b>Key events</b>${list(R.news_sentiment.key_events)}<b>Upcoming</b>${list(R.news_sentiment.upcoming_risks)}</div>`;
  // 5 macro
  h += `<div class="card">${head(5, 'Macro-Economic', d, 'macro')}<div class="row2">${badge(R.macro.regime.replace('_', '-'), R.macro.regime === 'RISK_ON' ? 'up' : R.macro.regime === 'RISK_OFF' ? 'dn' : 'mut')}${biasB(R.macro.asset_bias)}</div><p class="mut">${esc(R.macro.summary)}</p>${list(R.macro.drivers)}</div>`;
  // 8 social
  const so = R.social_onchain;
  h += `<div class="card">${head(8, 'Social & On-chain', d, 'social_onchain')}<div class="row2">${badge(so.crowd_bias, so.crowd_bias === 'EUPHORIC' || so.crowd_bias === 'GREEDY' ? 'warn' : so.crowd_bias === 'NEUTRAL' ? 'mut' : 'info')}${badge('contrarian: ' + so.contrarian_signal, so.contrarian_signal === 'LONG' ? 'up' : so.contrarian_signal === 'SHORT' ? 'dn' : 'mut')}</div><div style="margin:8px 0">strength ${so.strength}${confBar(so.strength)}</div>${list(so.evidence)}<div class="hint">${esc(so.summary)}</div></div>`;
  // 6 scanner
  const sc = R.scanner; h += `<div class="card">${head(6, 'Opportunity Scanner', d, 'scanner')}${sc ? `<div>score ${sc.score}${confBar(sc.score)}</div>${list(sc.signals)}<div class="hint">${esc(sc.comment)}</div>` : '<div class="hint">no scan data</div>'}</div>`;
  // 7 inspector
  h += `<div class="card">${head(7, 'Inspector · Risk Manager', d, 'inspector')}${v ? `<div class="row2">${v.checks.every(c => c.passed) && !(v.llm_audit?.veto) ? badge('HARD RULES PASSED', 'up') : badge('REJECTED', 'dn')}${v.rr ? badge('R:R ' + v.rr, 'info') : ''}${v.lots ? badge(v.lots + ' lots · $' + v.risk_usd, 'mut') : ''}</div>${checksTable(v.checks)}${v.llm_audit ? `<div class="hint">LLM audit: veto=${v.llm_audit.veto} · false-signal risk ${v.llm_audit.false_signal_risk}${v.llm_audit.concerns.length ? ' · ' + esc(v.llm_audit.concerns.join('; ')) : ''}</div>` : ''}` : '<div class="hint">not run</div>'}</div>`;
  // 11 backtester
  h += `<div class="card" style="grid-column:1/-1">${head(11, 'Backtest · Monte Carlo · Optimisation gate', d, 'backtester')}`;
  if (bt) {
    const m = bt.metrics || {}, mc = bt.mc || {}, vk = bt.verdict === 'PASS' ? 'up' : bt.verdict === 'PASS_REOPTIMIZED' ? 'warn' : 'dn';
    h += `<div class="row2" style="margin-bottom:10px">${badge(bt.verdict.replace('_', ' '), vk)}<span class="mut">${esc(bt.sample_basis)} · n=${bt.n_trades} · history ${bt.cache_info.bars || '–'} × 1h bars (${esc((bt.cache_info.bars_from || '').slice(0, 10))} → ${esc((bt.cache_info.bars_to || '').slice(0, 10))})</span></div>
     <div class="grid g6">${[['Win rate', fmt(m.win_rate_pct, 1) + '%', 'CI ' + (m.win_rate_ci95 || []).join('–')], ['Realised R:R', fmt(m.realized_rr), 'avg win/loss'], ['Expectancy', sgn(m.expectancy_r, 2) + 'R', 'after costs', cls(m.expectancy_r)], ['Profit factor', fmt(m.profit_factor), ''], ['Max DD', fmt(m.max_dd_r, 1) + 'R', 'streak ' + m.max_losing_streak], ['P(loss) MC', fmt(mc.prob_loss_pct, 1) + '%', `${mc.runs} paths × ${mc.horizon}`]].map(([l, vv, s, c]) => `<div class="card kpi"><div class="l">${l}</div><div class="v ${c || ''}" style="font-size:19px">${vv}</div><div class="s">${s}</div></div>`).join('')}</div>
     <div class="grid g2" style="margin-top:12px"><div><b>Monte Carlo equity fan (${mc.runs} simulations)</b>${fanChart(mc)}<div class="hint">median ${sgn(mc.final_return_pct?.p50, 1)}% · 5th pct ${sgn(mc.final_return_pct?.p5, 1)}% · 95th pct ${sgn(mc.final_return_pct?.p95, 1)}% at ${mc.horizon} trades (risk ${mc.risk_pct}%/trade)</div></div>
     <div><b>Max-drawdown distribution</b>${histChart(mc.dd_hist, mc.ruin_level_pct)}<div class="hint">p50 ${mc.max_dd_pct?.p50}% · p95 ${mc.max_dd_pct?.p95}% · P(ruin ≥ ${mc.ruin_level_pct}%) ${mc.prob_ruin_pct}%</div></div></div>
     <div style="margin-top:10px"><b>Gate checks</b>${checksTable(bt.checks)}</div>${bt.optimization_note ? `<div class="infobox" style="margin-top:10px">⚙️ ${esc(bt.optimization_note)}${bt.suggested_params ? `<pre class="code">${esc(JSON.stringify(bt.suggested_params))}</pre>` : ''}</div>` : ''}
     ${bt.reasons?.length ? `<div class="warnbox" style="margin-top:10px"><b>Refused because:</b>${list(bt.reasons)}</div>` : ''}${bt.llm_review ? `<div class="hint" style="margin-top:8px">LLM sceptic: veto=${bt.llm_review.veto} · overfit risk ${bt.llm_review.overfit_risk}${bt.llm_review.concerns.length ? ' · ' + esc(bt.llm_review.concerns.join('; ')) : ''}</div>` : ''}`;
  } else h += `<div class="hint">Gate not reached: the Inspector did not approve this setup (or the backtest is disabled in config.yaml).</div>`;
  h += `</div>`;
  // 12 trade validator
  h += `<div class="card" style="grid-column:1/-1">${head(12, 'Trade Validator · per-trade Monte Carlo (10 / 100 / 1000 / 5000 runs)', d, 'trade_validator')}${validationHTML(R.trade_validator)}</div>`;
  // 9 execution
  const ex = d.execution;
  h += `<div class="card" style="grid-column:1/-1">${head(9, 'Execution · broker payloads', d, 'execution')}${ex ? `<div class="row2">${badge(ex.order_type, 'info')}${badge(ex.lots + ' lots', 'mut')}${badge(ex.mode.toUpperCase(), ex.mode === 'live' ? 'dn' : 'up')}${badge(ex.submission.status, ex.submission.status.startsWith('LIVE_SENT') ? 'dn' : 'mut')}<span class="mut">${esc(ex.reason)}</span></div>
     <div class="grid g2">${Object.entries(ex.payloads).map(([b, pl]) => `<div><div class="row2"><b>${b}</b>${b === ex.primary_broker ? badge('primary', 'vio') : ''}<span class="right">${copyBtn(JSON.stringify(pl, null, 2))}</span></div><pre class="code">${esc(JSON.stringify(pl, null, 2))}</pre></div>`).join('')}</div>` : `<div class="hint">Not triggered: no setup survived all gates (Inspector hard rules + Backtest gate).</div>`}</div>`;
  // 2 data
  const dq = R.data_fetcher, mk = d.market;
  h += `<div class="card" style="grid-column:1/-1">${head(2, 'Data Fetcher', d, 'data_fetcher')}<div class="row2">${dq.quality_ok ? badge('data OK', 'up') : badge('DATA PROBLEM', 'dn')}<span class="mut">${esc(dq.summary)}</span></div>${list(dq.issues)}${mk ? `<div class="grid g2"><div class="tscroll"><table><thead><tr><th>TF</th><th class="num">ATR(14)</th><th class="num">candles</th><th class="num">spikes removed</th><th class="num">age (min)</th></tr></thead><tbody>${Object.entries(mk.quality).map(([k, q]) => `<tr><td class="mono">${k}</td><td class="num">${fmt(mk.atr[k])}</td><td class="num">${q.rows_out ?? '–'}</td><td class="num">${q.dropped_spikes ?? '–'}</td><td class="num">${q.minutes_since_last_candle ?? '–'}</td></tr>`).join('')}</tbody></table></div>${kv({ 'Spread': fmt(mk.spread), ...Object.fromEntries(Object.entries(mk.levels || {}).slice(0, 6).map(([k, x]) => [k.replace(/_/g, ' '), fmt(x)])) })}</div>` : ''}</div>`;
  $('#view').innerHTML = h + '</div>';
  $$('[data-tf]').forEach(b => b.onclick = () => { S.tf = b.dataset.tf; pageSymbols(S.sym); });
}

/* ---- lab: trade simulator (agent 12 maths on any entry / SL / TP you type) */
function simCard() {
  const z = S.sim = S.sim || { side: 'LONG', entry: '', sl: '', tp: '', price: '', runs: [10, 100, 1000, 5000], out: '', res: null };
  const o = S.run && S.run.detail[S.labSym] && S.run.detail[S.labSym].reports.orchestrator;
  if (o && o.entry != null && z.entry === '' && z.sl === '') { z.side = o.direction === 'SHORT' ? 'SHORT' : 'LONG'; z.entry = o.entry; z.sl = o.stop_loss; z.tp = o.take_profit; }
  return `<div class="card"><h3>🎲 Trade simulator · test any trade with 10 / 100 / 1000 / 5000 runs ${badge('agent 12 maths', 'vio')}</h3><div class="grid g4">
    <div class="field"><label>Side</label><div class="seg" id="sim-side"><button data-v="LONG" class="${z.side === 'LONG' ? 'on' : ''}">LONG</button><button data-v="SHORT" class="${z.side === 'SHORT' ? 'on' : ''}">SHORT</button></div></div>
    <div class="field"><label>Entry</label><input id="sim-e" type="number" step="any" value="${z.entry}"></div><div class="field"><label>Stop loss</label><input id="sim-s" type="number" step="any" value="${z.sl}"></div><div class="field"><label>Take profit</label><input id="sim-t" type="number" step="any" value="${z.tp}"></div></div>
    <div class="row2" style="margin-top:8px"><div class="chips">${[10, 100, 1000, 5000, 20000].map(n => `<span class="chip ${z.runs.includes(n) ? 'on' : ''}" data-sr="${n}">${n} runs</span>`).join('')}</div><div class="field" style="margin:0"><input id="sim-p" type="number" step="any" placeholder="current price (optional)" value="${z.price}" style="width:190px"></div><button class="btn pri right" id="sim-go">▶ Simulate</button></div>
    <div class="hint">Uses the ${S.labDemo ? 'demo' : 'live'} 1h history of ${S.labSym} (cached) and the strategy evidence of its last backtest. Nothing is traded.</div><div id="sim-out" style="margin-top:12px">${z.res ? validationHTML(z.res) : z.out}</div></div>`;
}
function bindSim() {
  const z = S.sim; if (!z) return;
  const grab = () => { z.entry = $('#sim-e').value; z.sl = $('#sim-s').value; z.tp = $('#sim-t').value; z.price = $('#sim-p').value; };
  $$('#sim-side button').forEach(b => b.onclick = () => { grab(); z.side = b.dataset.v; pageLab(); });
  $$('[data-sr]').forEach(e => e.onclick = () => { grab(); const n = +e.dataset.sr; z.runs = z.runs.includes(n) ? z.runs.filter(x => x !== n) : [...z.runs, n].sort((a, b) => a - b); pageLab(); });
  $('#sim-go').onclick = async () => { grab(); if (!z.runs.length) return toast('pick at least one run count', true); $('#sim-out').innerHTML = '<span class="hint">simulating…</span>';
    try { const r = await api('/api/validate', { method: 'POST', body: { symbol: S.labSym, demo: S.labDemo, side: z.side, entry: +z.entry, sl: +z.sl, tp: +z.tp, price: z.price ? +z.price : undefined, runs: z.runs } }); z.res = r.validation; z.out = ''; $('#sim-out').innerHTML = `<div class="hint" style="margin-bottom:8px">price used ${fmt(r.price)} · ATR(1h) ${fmt(r.atr_1h)}</div>` + validationHTML(r.validation); }
    catch (e) { z.res = null; z.out = `<div class="warnbox">${esc(e.message)}</div>`; $('#sim-out').innerHTML = z.out; } };
}

/* ------------------------------------------------------------------ backtest lab */
async function pageLab() {
  renderTop('Backtest lab');
  const st = S.state, bc = st.backtest_cfg; if (!S.labSym) S.labSym = st.instruments[0].symbol;
  let data = null, err = '';
  try { data = await api(`/api/backtest/${S.labSym}?demo=${S.labDemo}`); } catch (e) { err = e.message; }
  let h = `<div class="card glow"><h3>Strategy lab · replay, Monte Carlo, walk-forward optimisation</h3><div class="row2"><div class="chips">${st.instruments.map(i => `<span class="chip ${S.labSym === i.symbol ? 'on' : ''}" data-ls="${i.symbol}">${i.symbol}</span>`).join('')}</div>
    <div class="seg"><button data-ld="0" class="${!S.labDemo ? 'on' : ''}">Live history</button><button data-ld="1" class="${S.labDemo ? 'on' : ''}">Demo history</button></div>
    <button class="btn pri right" id="lab-run" ${S.busy ? 'disabled' : ''}>${data ? '↻ Recompute' : '▶ Run backtest'}</button></div>
    <div class="hint" style="margin-top:8px">Replays the rule-based SMC strategy bar-by-bar on ${bc.history_bars} × 1h bars (no look-ahead, spread charged), then ${bc.mc_runs} Monte-Carlo paths × ${bc.mc_horizon_trades} trades, then a ${data?.optimization?.grid_size || 24}-set walk-forward optimisation (train ${Math.round(bc.train_frac * 100)}% → validation ${Math.round(bc.val_frac * 100)}% → untouched final test ${Math.round((1 - bc.train_frac - bc.val_frac) * 100)}%).</div><div class="log" id="lab-log" style="margin-top:10px;display:none"></div></div><div class="sp"></div>${simCard()}<div class="sp"></div>`;
  if (!data) h += `<div class="card mut">${esc(err || 'No backtest yet.')} — press “Run backtest”.</div>`;
  else {
    const m = data.metrics, mc = data.mc, sg = data.strategy_gate, op = data.optimization;
    h += `<div class="grid g6">${[['Trades', m.n, `${data.signals} signals · ${data.unfilled} unfilled`], ['Win rate', fmt(m.win_rate_pct, 1) + '%', 'CI ' + m.win_rate_ci95.join('–')], ['Realised R:R', fmt(m.realized_rr), `win ${sgn(m.avg_win_r)} / loss ${sgn(m.avg_loss_r)}`], ['Expectancy', sgn(m.expectancy_r, 3) + 'R', 'per trade after spread', cls(m.expectancy_r)], ['Profit factor', fmt(m.profit_factor), 'total ' + sgn(m.total_r, 1) + 'R'], ['Max drawdown', fmt(m.max_dd_r, 1) + 'R', 'worst streak ' + m.max_losing_streak]].map(([l, v, s, c]) => `<div class="card kpi"><div class="l">${l}</div><div class="v ${c || ''}" style="font-size:21px">${v}</div><div class="s">${s}</div></div>`).join('')}</div><div class="sp"></div>
     <div class="grid g2"><div class="card"><h3>Equity curve (cumulative R)</h3>${lineChart(data.equity_curve)}</div><div class="card"><h3>Monte Carlo · ${mc.runs} simulated futures</h3>${fanChart(mc)}<div class="hint">P(loss) ${mc.prob_loss_pct}% · median ${sgn(mc.final_return_pct.p50, 1)}% · 5th pct ${sgn(mc.final_return_pct.p5, 1)}%</div></div></div><div class="sp"></div>
     <div class="grid g2"><div class="card"><h3>Max-drawdown distribution</h3>${histChart(mc.dd_hist, mc.ruin_level_pct)}<div class="hint">p50 ${mc.max_dd_pct.p50}% · p95 ${mc.max_dd_pct.p95}% · risk of ruin (≥${mc.ruin_level_pct}%) ${mc.prob_ruin_pct}%</div></div>
     <div class="card"><h3>Strategy-level gate ${badge(sg.passed ? 'WOULD PASS' : 'WOULD FAIL', sg.passed ? 'up' : 'dn')}</h3><div class="hint">Using all trades and a typical R:R of ${fmt(sg.assumed_rr, 1)}. A real setup is judged on its own R:R / side.</div>${checksTable(sg.checks)}</div></div><div class="sp"></div>
     <div class="card"><h3>Walk-forward optimisation ${badge(op.robust ? 'robust set found' : 'no robust set', op.robust ? 'up' : 'warn')}</h3><div class="hint">Three separate slices: parameters are <b>chosen on TRAIN</b> (first ${Math.round(bc.train_frac * 100)}%), must also earn money on <b>VALIDATION</b> (next ${Math.round(bc.val_frac * 100)}%), and are only <b>reported on the FINAL TEST</b> (last ${Math.round((1 - bc.train_frac - bc.val_frac) * 100)}%) which never influences any choice. A ${op.splits?.embargo_bars}-bar embargo at each boundary stops a trade from resolving on bars of the next slice (${op.splits ? `${op.splits.n_train} / ${op.splits.n_val} / ${op.splits.n_test} evaluation points, ${op.splits.dropped_for_embargo} dropped` : ''}).</div>
      <div class="tscroll"><table><thead><tr><th>SL buffer</th><th>Min stop</th><th>TP cap</th><th>PD filter</th><th class="num">Train n</th><th class="num">Train exp</th><th class="num">Valid. n</th><th class="num">Valid. exp</th><th class="num">Valid. PF</th></tr></thead><tbody>${op.rows.map((r, i) => `<tr style="${op.selected_row && JSON.stringify(r.params) === JSON.stringify(op.selected_row.params) ? 'background:rgba(79,209,197,.07)' : ''}"><td class="mono">${r.params.sl_buffer_atr}</td><td class="mono">${r.params.min_stop_atr}</td><td class="mono">${r.params.tp_cap_r}R</td><td>${r.params.require_pd_zone ? '✓' : '–'}</td><td class="num">${r.is.n}</td><td class="num ${cls(r.is.expectancy_r)}">${sgn(r.is.expectancy_r, 2)}</td><td class="num">${r.val.n}</td><td class="num ${cls(r.val.expectancy_r)}">${sgn(r.val.expectancy_r, 2)}</td><td class="num">${fmt(r.val.profit_factor)}</td></tr>`).join('')}</tbody></table></div>
      ${op.selected ? `<div class="infobox" style="margin-top:10px"><b>Selected set on the untouched FINAL TEST:</b> ${op.final && op.final.n ? `${op.final.n} trades · win rate ${fmt(op.final.win_rate_pct, 1)}% · expectancy ${sgn(op.final.expectancy_r, 2)}R · PF ${fmt(op.final.profit_factor)}` : 'no trades in the final slice'} ${op.final && op.final.expectancy_r != null ? badge(op.final.expectancy_r > 0 ? 'holds up' : 'does NOT hold up', op.final.expectancy_r > 0 ? 'up' : 'dn') : ''}</div>` : ''}</div><div class="sp"></div>
     <div class="grid g2"><div class="card"><h3>Gate thresholds (config.yaml → backtest)</h3>${kv({ 'min trades': data.thresholds.min_trades, 'win-rate floor': data.thresholds.min_win_rate_pct + '%', 'margin over break-even': data.thresholds.breakeven_margin_pct + 'pp', 'min expectancy': data.thresholds.min_expectancy_r + 'R', 'min profit factor': data.thresholds.min_profit_factor, 'max P(loss)': data.thresholds.max_mc_prob_loss_pct + '%', 'max DD p95': data.thresholds.max_mc_dd_p95_pct + '%', 'max risk of ruin': data.thresholds.max_mc_ruin_pct + '%' })}</div>
     <div class="card"><h3>Assumptions & limits</h3>${list(data.assumptions)}<div class="row2" style="margin-top:8px">${data.feed ? badge('feed: ' + data.feed.feed, 'info') + (data.feed.proxy ? badge('PROXY feed — not your broker’s own prices', 'warn') : badge('broker feed', 'up')) : ''}</div>${data.cost_model ? kv({ 'spread': fmt(data.cost_model.spread, 3), 'slippage (stop entry / SL exit)': fmt(data.cost_model.slip, 3), 'commission / lot': '$' + fmt(data.cost_model.comm_per_lot), 'swap / lot / day': '$' + fmt(data.cost_model.swap_day) }) : ''}<div class="hint">Computed ${esc(data.created_at)} in ${data.compute_seconds}s · ${data.bars} bars ${esc(data.bars_from.slice(0, 10))} → ${esc(data.bars_to.slice(0, 10))}. Past performance does not guarantee future results; the discretionary LLM plan is not replayed bar-by-bar.</div></div></div>`;
  }
  $('#view').innerHTML = h;
  bindSim();
  $$('[data-ls]').forEach(e => e.onclick = () => { S.labSym = e.dataset.ls; pageLab(); });
  $$('[data-ld]').forEach(e => e.onclick = () => { S.labDemo = e.dataset.ld === '1'; pageLab(); });
  $('#lab-run').onclick = async () => { if (S.busy) return; S.busy = true; const log = $('#lab-log'); log.style.display = 'block'; $('#lab-run').disabled = true;
    try { await runJob('/api/backtest', { symbol: S.labSym, demo: S.labDemo }, log); toast('Backtest finished'); } catch (e) { toast(e.message, true); } finally { S.busy = false; } pageLab(); };
}

/* ------------------------------------------------------------------ journal & critic */
const valMini = v => !v ? '<span class="mut">–</span>' : !v.sim || !v.sim.lenses ? badge(v.verdict, VK(v.verdict)) : `${badge(v.verdict, VK(v.verdict))} <span class="mono mut" style="font-size:11px">${v.sim.lenses.neutral.ladder.map(p => p.win_rate_pct == null ? '–' : fmt(p.win_rate_pct, 0) + '%').join(' → ')}</span>`;
async function pageJournal() {
  renderTop('Journal & Critic');
  const j = await api(`/api/journal?demo=${S.jrDemo}`);
  const closed = j.trades.filter(t => t.status === 'CLOSED').sort((a, b) => a.ts < b.ts ? -1 : 1);
  let cum = 0; const pts = [[0, 0], ...closed.map((t, i) => [i + 1, +(cum += t.outcome.r_multiple).toFixed(2)])];
  const js = j.stats;
  let h = `<div class="row2" style="margin-bottom:12px"><div class="seg"><button data-jd="0" class="${!S.jrDemo ? 'on' : ''}">Live-data journal</button><button data-jd="1" class="${S.jrDemo ? 'on' : ''}">Demo journal</button></div>
    <button class="btn pri right" id="rev-btn" ${S.busy ? 'disabled' : ''}>🧠 Resolve open trades + run Critic</button></div>
    <div class="grid g4"><div class="card kpi"><div class="l">Closed trades</div><div class="v">${js.closed_trades || 0}</div></div><div class="card kpi"><div class="l">Win rate</div><div class="v">${js.closed_trades ? fmt(js.win_rate_pct, 1) + '%' : '–'}</div></div><div class="card kpi"><div class="l">Expectancy</div><div class="v ${cls(js.expectancy_r)}">${js.closed_trades ? sgn(js.expectancy_r) + 'R' : '–'}</div></div><div class="card kpi"><div class="l">Open risk</div><div class="v">${fmt(j.exposure.open_risk_pct)}%</div></div></div>
    <div class="log" id="rev-log" style="display:none;margin-top:12px"></div><div class="sp"></div>
    <div class="grid g2"><div class="card"><h3>Realised equity (R)</h3>${lineChart(pts)}</div><div class="card"><h3>Lessons the Critic feeds back to the agents</h3>${j.lessons ? `<pre class="code" style="white-space:pre-wrap">${esc(j.lessons)}</pre>` : '<div class="hint">No lessons yet - they appear after trades close and the Critic reviews them.</div>'}</div></div><div class="sp"></div>
    <div class="card"><h3>Trades</h3><div class="tscroll"><table><thead><tr><th>Time</th><th>Symbol</th><th>Side</th><th class="num">Entry</th><th class="num">SL</th><th class="num">TP</th><th>Status</th><th class="num">R</th><th>Backtest</th><th>Validator (win rate @ 10 → 5000 runs)</th><th>Critic</th></tr></thead><tbody>${[...j.trades].reverse().map(t => { const p = t.proposal || {}, o = t.outcome || {}; return `<tr data-tid="${esc(t.id)}" style="cursor:pointer"><td class="mono mut">${esc((t.ts || '').slice(5, 16).replace('T', ' '))}</td><td><b>${t.symbol}</b></td><td>${dirB(p.direction)}</td><td class="num">${fmt(p.entry)}</td><td class="num">${fmt(p.stop_loss)}</td><td class="num">${fmt(p.take_profit)}</td><td>${badge(t.status + (o.result ? ' ' + o.result : ''), t.status === 'CLOSED' ? (o.result === 'WIN' ? 'up' : 'dn') : t.status === 'REJECTED' ? 'mut' : 'info')}</td><td class="num ${cls(o.r_multiple)}">${o.r_multiple != null ? sgn(o.r_multiple) : ''}</td><td>${t.backtest ? esc(t.backtest.verdict) : ''}</td><td>${valMini(t.validation)}</td><td class="mut">${t.critic ? esc(t.critic.root_cause) : ''}</td></tr>`; }).join('') || '<tr><td colspan="11" class="mut">no trades yet</td></tr>'}</tbody></table></div></div>`;
  $('#view').innerHTML = h;
  $$('[data-jd]').forEach(e => e.onclick = () => { S.jrDemo = e.dataset.jd === '1'; pageJournal(); });
  $$('[data-tid]').forEach(r => r.onclick = () => { const t = j.trades.find(x => x.id === r.dataset.tid); if (!t) return; const p = t.proposal || {}, ac = t.account || {};
    openDrawer(`${t.symbol} · ${(t.ts || '').slice(0, 16).replace('T', ' ')}`, `<div class="row2">${dirB(p.direction)}${badge(t.status, t.status === 'REJECTED' ? 'mut' : 'info')}<span class="mono mut">account ${esc(ac.broker || '')} ${ac.equity_usd != null ? '$' + fmt(ac.equity_usd) : ''}</span></div><div class="hint" style="margin:6px 0 10px">${esc(p.thesis || '')}</div>${validationHTML(t.validation, { compact: true })}`); });
  $('#rev-btn').onclick = async () => { if (S.busy) return; S.busy = true; const log = $('#rev-log'); log.style.display = 'block'; $('#rev-btn').disabled = true;
    try { await runJob('/api/review', { demo: S.jrDemo, no_llm: S.opts.no_llm || S.jrDemo }, log); toast('Review finished'); await refreshState(); } catch (e) { toast(e.message, true); } finally { S.busy = false; } pageJournal(); };
}

/* ------------------------------------------------------------------ boot */
(async function boot() { renderNav(); renderTop('Loading…'); try { await refreshState(); } catch (e) { $('#view').innerHTML = `<div class="warnbox">${esc(e.message)}</div>`; return; } route(); })();
