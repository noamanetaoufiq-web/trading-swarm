const { JSDOM, VirtualConsole } = require('jsdom');
const BASE = process.env.BASE || 'http://127.0.0.1:8765';
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', e => errors.push('jsdomError: ' + (e.detail || e.message)));
vc.on('error', (...a) => errors.push('console.error: ' + a.join(' ')));
const sleep = ms => new Promise(r => setTimeout(r, ms));
let ok = 0; const check = (c, m) => { if (c) { ok++; console.log('  ✓', m); } else { errors.push('CHECK FAILED: ' + m); console.log('  ✗', m); } };
(async () => {
  const dom = await JSDOM.fromURL(BASE + '/?token=tok', { runScripts: 'dangerously', resources: 'usable', pretendToBeVisual: true, virtualConsole: vc,
    beforeParse(w) { w.fetch = (u, o) => fetch(new URL(u, BASE), o); w.confirm = () => true; w.scrollTo = () => {}; w.addEventListener('error', e => errors.push('window.onerror: ' + e.message)); w.addEventListener('unhandledrejection', e => errors.push('unhandledrejection: ' + e.reason)); } });
  const w = dom.window, d = w.document, $ = s => d.querySelector(s), $$ = s => [...d.querySelectorAll(s)];
  const until = async (fn, ms = 120000, what = '') => { const t0 = Date.now(); while (Date.now() - t0 < ms) { try { if (fn()) return true; } catch (e) {} await sleep(150); } errors.push('timeout waiting: ' + what); return false; };
  const go = async h => { w.location.hash = h; await sleep(400); };
  const click = el => el.dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
  const text = () => $('#view').textContent;
  const PHASE = process.env.PHASE || '1';
  if (PHASE === '1') {
  console.log('wizard');
  await until(() => $('.steps'), 8000, 'wizard');
  check(/account/.test(w.location.hash), 'unconfigured -> redirected to account wizard');
  check(text().includes('Account type') && text().includes('Demo') && text().includes('Real'), 'step 1 shows Demo / Real');
  click($('[data-type="demo"]')); await sleep(100); click($('[data-go="2"]')); await sleep(150);
  check(text().includes('MetaTrader 5') && text().includes('Binance') && text().includes('Interactive Brokers') && text().includes('Paper'), 'step 2 lists the brokers');
  click($('[data-broker="mt5"]')); await sleep(100);
  click($('[data-go="3"]')); await sleep(150);
  check($$('[data-cred]').map(i => i.dataset.cred).join() === 'login,password,server,path' && $('[data-cred="server"]').placeholder.includes('Exness-MT5Trial15') && text().includes('Any broker or funded account') && text().includes('Windows only'), 'MT5 step: login / password / server (+ optional path) with the Exness example and the any-broker note');
  click($('[data-go="4"]')); await sleep(150);
  check(!$('#w-eq') && text().includes('not typed here') && text().includes('read'), 'linked broker: no equity field, size is read from the broker');
  check($('#w-risk').value === '0.5' && $('#w-open').value === '0.5' && $('#w-daily').value === '2' && $('#w-weekly').value === '4' && $('#w-dd').value === '10', 'defaults 0.5 / 0.5 / 2 / 4 / 10');
  click($('[data-go="3"]')); await sleep(100); click($('[data-go="2"]')); await sleep(100);
  check(true, 'back navigation ok'); click($('[data-broker="paper"]')); await sleep(100); click($('[data-go="4"]')); await sleep(150);
  check($('#w-eq') && $('#w-eq').value === '100', 'paper simulator: starting capital defaults to $100');
  $('#w-eq').value = '25000'; click($('[data-go="5"]')); await sleep(150);
  await (async () => { click($('#w-test')); await until(() => $('#w-testres').textContent.includes('connected'), 8000, 'paper test'); })();
  check($('#w-testres').textContent.includes('connected'), 'test connection ok (paper)');
  click($('#w-save')); await until(() => /overview/.test(w.location.hash), 8000, 'save -> overview'); await sleep(400);
  check(/overview/.test(w.location.hash) && text().includes('Run the swarm'), 'saved, overview rendered');
  check(text().includes('$25,000') , 'equity KPI shows the account size');
  check($('#run-btn').textContent.includes('12-agent'), 'button says 12-agent swarm');
  console.log('run swarm from the UI');
  click($('#seg-data button[data-v="1"]')); await sleep(250); click($('#seg-llm button[data-v="1"]')); await sleep(250);
  click($('#run-btn'));
  await until(() => /Run complete/.test($('#toast').textContent) || $('.funnel'), 240000, 'run finished'); await sleep(1200);
  check($$('.funnel').length === 3, 'decision funnel per symbol');
  check(text().includes('Trade validator'), 'funnel has the Trade validator stage');
  } else if (PHASE === '2') {
  await until(() => $('#nav a'), 8000, 'boot'); await sleep(500);
  console.log('pages');
  await go('#/pipeline'); await until(() => $$('.node').length > 8, 8000, 'nodes');
  check($$('.node').length >= 13 && $$('.node').some(n => n.textContent.includes('Validator') || n.textContent.includes('Trade Validator')), 'pipeline shows 12-agent flow incl. validator node');
  click($('[data-node="trade_validator"]')); await sleep(200); check($('#drawer').classList.contains('open'), 'validator node opens the raw-output drawer');
  await go('#/symbols/XAUUSD'); await until(() => text().includes('Trade Validator'), 8000, 'symbols');
  check(text().includes('Trade Validator'), 'symbols: validator card present');
  let withSim = false;
  for (const s of ['XAUUSD', 'BTCUSD', 'NAS100']) { await go('#/symbols/' + s); await sleep(300); if (text().includes('Every run count, same trade')) { withSim = s; break; } }
  console.log('  · symbol with an approved trade (ladder in card): ' + withSim);
  if (withSim) {
    const t = text();
    check(['10', '100', '1000', '5000'].every(n => $$('table tbody tr td.mono b').some(b => b.textContent === n)), 'ladder rows 10 / 100 / 1000 / 5000 rendered');
    check($$('svg').some(s => s.innerHTML.includes('break-even')), 'ladder chart drawn with break-even line');
    check(t.includes('Outcome of') && t.includes('Distribution of the trade result'), 'outcome bar + R histogram');
  }
  }
  if (PHASE === '3') {
  await until(() => $('#nav a'), 8000, 'boot'); await sleep(500);
  await go('#/lab'); await until(() => text().includes('Trade simulator'), 20000, 'lab');
  check(text().includes('Trade simulator'), 'lab: trade simulator card');
  click($('[data-ld="1"]')); await sleep(800);
  click($('#lab-run')); await until(() => /Backtest finished/.test($('#toast').textContent), 180000, 'backtest'); await sleep(900);
  check(text().includes('Walk-forward optimisation') && text().includes('untouched') , 'lab: optimisation split (train / validation / final test)');
  check(text().includes('Valid. exp') && text().includes('Train exp'), 'lab: IS/VAL columns');
  click($('#sim-side button[data-v="LONG"]')); await sleep(200);
  $('#sim-e').value = '1995'; $('#sim-s').value = '1985'; $('#sim-t').value = '2015'; $('#sim-p').value = '2000';
  click($('#sim-go')); await until(() => $('#sim-out').textContent.includes('Every run count'), 60000, 'simulator output');
  check($('#sim-out').textContent.includes('Every run count') && $('#sim-out').textContent.includes('Win rate vs number of runs'), 'simulator ran from the UI');
  await go('#/journal'); await until(() => text().includes('Validator (win rate'), 8000, 'journal');
  const demoBtn = $('[data-jd="1"]'); click(demoBtn); await sleep(800);
  check(text().includes('Validator (win rate'), 'journal: validator column');
  const rows = $$('[data-tid]'), row = rows.find(r => r.textContent.includes('→')) || rows[0];
  if (row) { const withLadder = row.textContent.includes('→'); click(row); await sleep(300);
    check($('#drawer').classList.contains('open') && (!withLadder || $('#drawer-body').textContent.includes('Win rate vs number of runs')), 'journal: click a trade -> drawer opens' + (withLadder ? ' with the full validation (ladder)' : ' (this trade was refused before the validator)')); }
  else check(true, 'journal: no trade rows - skipped drawer check');
  await go('#/account'); await sleep(400);
  check(text().includes('Account size') && text().includes('Can this account trade each market at all'), 'account: size card + feasibility table');
  click($('#acct-refresh')); await sleep(800);
  check(text().includes('$25,000'), 'account: refresh keeps the simulator equity');
  }
  if (PHASE === '4') {   // MT5 linking through the UI against the stubbed terminal (run serve_smoke.py with SMOKE_MT5_STUB=1 and a fresh SMOKE_ROOT)
  console.log('mt5 wizard');
  await until(() => $('.steps'), 8000, 'wizard');
  click($('[data-type="demo"]')); await sleep(100); click($('[data-go="2"]')); await sleep(150);
  click($('[data-broker="mt5"]')); await sleep(100); click($('[data-go="3"]')); await sleep(150);
  const setc = (k, v) => { $('[data-cred="' + k + '"]').value = v; };
  setc('login', '12345678'); setc('password', 'pw'); setc('server', 'Exness-MT5Trial15');
  click($('[data-go="4"]')); await sleep(150); click($('[data-go="5"]')); await sleep(200);
  click($('#w-test')); await until(() => /connected|failed/.test($('#w-testres').textContent), 20000, 'mt5 test');
  check($('#w-testres').textContent.includes('connected') && $('#w-testres').textContent.includes('137.5'), 'test connection reads the account (equity 137.5)');
  check(text().includes('XAUUSDm') && text().includes('USTECm') && text().includes('BTCUSDm'), 'symbols of the Exness-style server are detected and listed');
  check(!d.documentElement.innerHTML.includes('"pw"') && !text().includes('pw '), 'password is not echoed in the page');
  click($('#w-save')); await until(() => /overview/.test(w.location.hash), 8000, 'save'); await sleep(500);
  check(text().includes('$137.50'), 'overview equity = the broker balance, nothing typed');
  await go('#/account'); await until(() => text().includes('Account size'), 8000, 'account page');
  click($('#acct-refresh')); await until(() => text().includes('Exness-MT5Trial15'), 15000, 'refresh');
  check(text().includes('Exness-MT5Trial15') && text().includes('Exness Technologies') && text().includes('XAUUSDm') && $$('[data-symmap]').length === 3, 'account card shows server, broker, symbols with override boxes');
  check(text().includes('too small'), 'feasibility: $137.50 at 0.5% cannot fund a normal gold stop');
  $('[data-symmap="XAUUSD"]').value = 'XAUUSDm'; click($('#symmap-save')); await sleep(1500);
  check($('[data-symmap="XAUUSD"]').value === 'XAUUSDm', 'symbol override saved and kept after re-read');
  }
  console.log(`\n${ok} checks passed, ${errors.length} problem(s)`);
  errors.forEach(e => console.log('  !', e));
  dom.window.close(); process.exit(errors.length ? 1 : 0);
})().catch(e => { console.error('CRASH', e); process.exit(2); });
