#!/usr/bin/env node
// =====================================================================================
// FORWARD LOG - an honest out-of-sample test that nobody can fit to.
//
// After each completed US session this script:
//   1. Runs the dashboard's OWN panel code (docs/index.html) on docs/data.json, so the
//      signals logged are exactly what the page showed - no second copy of the rules.
//   2. Appends every name in Weekly Picks + the four ranked panels to docs/forward_log.csv,
//      with the card's size label, whether it was a TAKE (2+ panels), and the market
//      conditions that day (QQQ vs its 50-day, share of the basket above its 50-day).
//   3. Scores every logged signal whose 20 sessions have passed, using the page's tested
//      rule: buy next open, sell at entry + 1.5 x normal daily move, or at the close of
//      session 20; no stop. Also scores a RANDOM baseline (every basket stock on the same
//      dates) so each group is compared with what chance would have done.
//   4. Writes docs/forward_results.csv and docs/forward_summary.json.
//
// RULES FOR USING THIS HONESTLY
//   - Do not change the panel, sizing or TAKE rules while the log is running. If you do,
//     start a new log file; mixing rule versions makes the result meaningless.
//   - Decide in advance what counts as success (see the summary's "verdict" logic below)
//     and do not move the goal posts once numbers come in.
//   - Wait for at least ~100 scored signals and ~20 scored TAKEs before trusting anything.
// =====================================================================================
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.join(__dirname, '..');
const HTML = path.join(ROOT, 'docs', 'index.html');
const DATA = path.join(ROOT, 'docs', 'data.json');
const LOG = path.join(ROOT, 'docs', 'forward_log.csv');
const RES = path.join(ROOT, 'docs', 'forward_results.csv');
const SUM = path.join(ROOT, 'docs', 'forward_summary.json');
const RULES_VERSION = 'v1-2026-09-26';     // bump + start a new log file if any rule changes
const SLOTS = 2;                            // TAKE = up to 2 names in 2+ panels, lowest CMF first
const HOLD = 20, TARGET_MULT = 1.5;
const MIN_ET_MINUTES_AFTER_CLOSE = 16 * 60 + 20;   // only log once the close has settled

// ---------- 1. load the page's script into a sandbox with a stub DOM ----------
function stubEl() {
  const el = { innerHTML: '', textContent: '', value: '', style: {}, dataset: {}, children: [], classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } } };
  el.querySelector = () => stubEl(); el.querySelectorAll = () => [];
  el.addEventListener = () => {}; el.appendChild = () => {}; el.setAttribute = () => {};
  el.getBoundingClientRect = () => ({ width: 0, height: 0, top: 0, left: 0 });
  return el;
}
const els = {};
const sandbox = {
  console, Math, Date, JSON, Intl, Map, Set, Array, Object, Number, String, Promise, isFinite, parseFloat, parseInt,
  document: { getElementById: id => (els[id] = els[id] || stubEl()), querySelector: () => stubEl(), querySelectorAll: () => [],
              addEventListener: () => {}, createElement: () => stubEl(), body: stubEl(), documentElement: stubEl() },
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
  fetch: () => Promise.reject(new Error('no fetch in forward log')),
  setInterval: () => 0, setTimeout: () => 0, clearInterval: () => {}, clearTimeout: () => {},
  requestAnimationFrame: () => 0, getComputedStyle: () => ({}),
  location: { href: '', search: '' }, navigator: { userAgent: 'node' },
};
sandbox.window = sandbox; sandbox.self = sandbox;
sandbox.addEventListener = () => {}; sandbox.removeEventListener = () => {}; sandbox.innerWidth = 1400; sandbox.innerHeight = 900;
vm.createContext(sandbox);
const html = fs.readFileSync(HTML, 'utf8');
const scripts = [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/gi)].map(m => m[1]);
for (const s of scripts) vm.runInContext(s, sandbox, { filename: 'index.html' });

const J = JSON.parse(fs.readFileSync(DATA, 'utf8'));
sandbox.__J = J;

// ---------- 2. compute today's signals with the page's own functions ----------
const out = vm.runInContext(`(() => {
  const j = __J;
  for (const sym of Object.keys(j.tickers || {})) {
    const e = j.tickers[sym];
    if (e.error || !e.bars || e.bars.length < 2) { data[sym] = { error: e.error || 'no bars' }; continue; }
    data[sym] = computeMetrics(e.bars); data[sym].cross = computeCrossStatus(e.bars);
    fund[sym] = { name: e.name, sector: e.sector, industry: e.industry, mcap: e.mcap, beta: e.beta, targetMean: e.targetMean,
                  nextEarnings: e.nextEarnings || null, premarketGapPct: e.premarketGapPct, earningsFlag: e.earningsFlag || null };
  }
  clearDerivedCaches();
  const stocks = Object.keys(data).filter(s => !BENCHMARKS.has(s) && data[s] && !data[s].error);
  renderWeeklyPicks(stocks); renderRSDivergence(stocks); renderIntradayBreakout(stocks); renderBullFlag(stocks); renderGoldenCross(stocks);
  const items = [...panelMembers.entries()].map(([sym, srcs]) => ({ sym, srcs, p: computeTradePlan(sym) })).filter(x => x.p);
  const multi = items.filter(x => x.srcs.length >= 2 && !x.p.ignore).sort((a, b) => (a.p.cmf20 ?? 9) - (b.p.cmf20 ?? 9));
  const take = new Set(multi.slice(0, ${SLOTS}).map(x => x.sym));
  const et = nowET();
  const spyBars = j.tickers.SPY.bars;
  return {
    etMinutes: et.getHours() * 60 + et.getMinutes(), etDow: et.getDay(), todayET: tpTodayET(),
    lastBarDate: tpBarDate(spyBars[spyBars.length - 1][0]),
    rows: items.map(x => ({ sym: x.sym, panels: x.srcs.map(t => t.split(' #')[0]).join('+'), n_panels: x.srcs.length,
      take: take.has(x.sym) ? 1 : 0,
      size: x.p.ignore ? 'IGNORE' : x.p.halfSize ? 'HALF' : 'FULL',
      close: x.p.px, move: x.p.atr, target_ref: x.p.px + ${TARGET_MULT} * x.p.atr, ext9: x.p.ext9, cmf20: x.p.cmf20,
      trend: x.p.trend.split(' — ')[0], pullback: x.p.pullback.split(' — ')[0], bar_date: tpBarDate(x.p.asOf) }))
  };
})()`, sandbox);

// ---------- market conditions for the signal date ----------
function closesBy(sym) { const m = new Map(); for (const b of (J.tickers[sym] || {}).bars || []) m.set(new Date(b[0] * 1000).toISOString().slice(0, 10), b); return m; }
const barMaps = {}; for (const s of Object.keys(J.tickers)) if (J.tickers[s].bars) barMaps[s] = closesBy(s);
function sma50Above(sym, date) {
  const bars = (J.tickers[sym] || {}).bars || []; const i = bars.findIndex(b => new Date(b[0] * 1000).toISOString().slice(0, 10) === date);
  if (i < 49) return null; let s = 0; for (let k = i - 49; k <= i; k++) s += bars[k][4]; return bars[i][4] > s / 50 ? 1 : 0;
}
const BENCH = new Set(['SPY', 'QQQ', 'IWM', 'VOO']);
function regime(date) {
  let up = 0, n = 0;
  for (const s of Object.keys(J.tickers)) { if (BENCH.has(s)) continue; const a = sma50Above(s, date); if (a == null) continue; n++; up += a; }
  const spy = barMaps.SPY && barMaps.SPY.get(date);
  return { qqq_above_50d: sma50Above('QQQ', date), breadth_above_50d: n ? +(up / n).toFixed(3) : null, spy_close: spy ? spy[4] : null };
}

// ---------- CSV helpers ----------
const LOG_COLS = ['signal_date', 'rules_version', 'sym', 'panels', 'n_panels', 'take', 'size', 'close', 'move', 'target_ref', 'ext9', 'cmf20', 'trend', 'pullback', 'qqq_above_50d', 'breadth_above_50d', 'spy_close', 'logged_at'];
function readCsv(file) {
  if (!fs.existsSync(file)) return [];
  const [head, ...lines] = fs.readFileSync(file, 'utf8').trim().split('\n');
  if (!head) return [];
  const cols = head.split(',');
  return lines.filter(Boolean).map(l => { const v = l.split(','); const o = {}; cols.forEach((c, i) => o[c] = v[i]); return o; });
}
function writeCsv(file, cols, rows) {
  const esc = v => v == null ? '' : String(v).replace(/,/g, ';');
  fs.writeFileSync(file, cols.join(',') + '\n' + rows.map(r => cols.map(c => esc(r[c])).join(',')).join('\n') + (rows.length ? '\n' : ''));
}
const r4 = v => v == null || !isFinite(v) ? '' : (+v).toFixed(4);

// ---------- 3. append today's signals (only once the session is complete) ----------
const log = readCsv(LOG);
const seen = new Set(log.map(r => r.signal_date + '|' + r.sym));
const complete = out.lastBarDate < out.todayET || out.etDow === 0 || out.etDow === 6 || out.etMinutes >= MIN_ET_MINUTES_AFTER_CLOSE;
let added = 0;
if (!complete) {
  console.log(`Session ${out.lastBarDate} still in progress (ET ${Math.floor(out.etMinutes / 60)}:${String(out.etMinutes % 60).padStart(2, '0')}) - not logging.`);
} else {
  const rg = regime(out.lastBarDate);
  for (const r of out.rows) {
    if (r.bar_date !== out.lastBarDate) continue;             // plan built on an older bar (e.g. symbol missing today)
    const key = out.lastBarDate + '|' + r.sym;
    if (seen.has(key)) continue;
    log.push({ signal_date: out.lastBarDate, rules_version: RULES_VERSION, sym: r.sym, panels: r.panels, n_panels: r.n_panels, take: r.take, size: r.size,
               close: r4(r.close), move: r4(r.move), target_ref: r4(r.target_ref), ext9: r4(r.ext9), cmf20: r4(r.cmf20), trend: r.trend, pullback: r.pullback,
               qqq_above_50d: rg.qqq_above_50d, breadth_above_50d: rg.breadth_above_50d, spy_close: rg.spy_close, logged_at: new Date().toISOString() });
    seen.add(key); added++;
  }
  // Also record days with NO signal, so "stayed in cash" days are visible in the record.
  if (!out.rows.some(r => r.bar_date === out.lastBarDate) && !seen.has(out.lastBarDate + '|(none)')) {
    const rg2 = regime(out.lastBarDate);
    log.push({ signal_date: out.lastBarDate, rules_version: RULES_VERSION, sym: '(none)', n_panels: 0, take: 0, qqq_above_50d: rg2.qqq_above_50d, breadth_above_50d: rg2.breadth_above_50d, spy_close: rg2.spy_close, logged_at: new Date().toISOString() });
    added++;
  }
  log.sort((a, b) => a.signal_date.localeCompare(b.signal_date) || a.sym.localeCompare(b.sym));
  writeCsv(LOG, LOG_COLS, log);
  console.log(`Logged ${added} new row(s) for ${out.lastBarDate}. Log now has ${log.length} rows.`);
}

// ---------- 4. score everything whose 20 sessions are complete ----------
function outcome(sym, date, move) {
  const bars = (J.tickers[sym] || {}).bars; if (!bars) return null;
  const i = bars.findIndex(b => new Date(b[0] * 1000).toISOString().slice(0, 10) === date);
  if (i < 0 || i + HOLD >= bars.length) return null;          // not enough sessions yet
  const lastIsLive = new Date(bars[bars.length - 1][0] * 1000).toISOString().slice(0, 10) === out.todayET && !complete;
  if (lastIsLive && i + HOLD >= bars.length - 1) return null;
  const e = bars[i + 1][1], tg = e + TARGET_MULT * move;
  let low = Infinity;
  for (let m = i + 1; m <= i + HOLD; m++) {
    low = Math.min(low, bars[m][3]);
    if (bars[m][2] >= tg) return { hit: 1, days: m - i, ret: tg / e - 1, dip: low / e - 1, entry: e };
  }
  return { hit: 0, days: HOLD, ret: bars[i + HOLD][4] / e - 1, dip: low / e - 1, entry: e };
}
function atrAt(sym, date) {
  const bars = (J.tickers[sym] || {}).bars; if (!bars) return null;
  const i = bars.findIndex(b => new Date(b[0] * 1000).toISOString().slice(0, 10) === date); if (i < 15) return null;
  let a = bars[0][2] - bars[0][3];
  for (let k = 1; k <= i; k++) { const tr = Math.max(bars[k][2] - bars[k][3], Math.abs(bars[k][2] - bars[k - 1][4]), Math.abs(bars[k][3] - bars[k - 1][4])); a += (tr - a) / 14; }
  return a;
}
// A name that stays in a panel for several days is ONE trade, not several: count it at most once per
// 5 sessions (the first day it appears), the same de-duplication the page's backtests used.
const barIndex = (sym, date) => ((J.tickers[sym] || {}).bars || []).findIndex(b => new Date(b[0] * 1000).toISOString().slice(0, 10) === date);
const lastScored = {};
const results = [];
for (const r of log) {
  if (r.sym === '(none)') continue;
  const bi = barIndex(r.sym, r.signal_date);
  if (bi >= 0 && lastScored[r.sym] != null && bi - lastScored[r.sym] < 5) continue;
  const o = outcome(r.sym, r.signal_date, +r.move);
  if (!o) continue;
  lastScored[r.sym] = bi;
  results.push({ ...r, entry: r4(o.entry), hit: o.hit, days: o.days, ret: r4(o.ret), dip: r4(o.dip) });
}
writeCsv(RES, LOG_COLS.concat(['entry', 'hit', 'days', 'ret', 'dip']), results);

// Random baseline: every basket stock on each date that produced a scored signal.
const dates = [...new Set(results.map(r => r.signal_date))];
const base = [];
for (const d of dates) for (const s of Object.keys(J.tickers)) {
  if (BENCH.has(s)) continue; const a = atrAt(s, d); if (!a) continue; const o = outcome(s, d, a); if (o) base.push(o);
}
function stats(rows) {
  if (!rows.length) return { n: 0 };
  const hit = rows.reduce((s, r) => s + +r.hit, 0) / rows.length, avg = rows.reduce((s, r) => s + +r.ret, 0) / rows.length;
  const dips = rows.map(r => +r.dip).sort((a, b) => a - b);
  return { n: rows.length, hit_rate: +hit.toFixed(3), avg_return: +avg.toFixed(4), median_dip: +dips[Math.floor(dips.length / 2)].toFixed(4), bad_case_dip: +dips[Math.floor(dips.length * 0.25)].toFixed(4) };
}
const by = (f) => { const g = {}; for (const r of results) { const k = f(r); (g[k] = g[k] || []).push(r); } return Object.fromEntries(Object.entries(g).map(([k, v]) => [k, stats(v)])); };
const panelRows = {}; for (const r of results) for (const p of String(r.panels).split('+')) (panelRows[p] = panelRows[p] || []).push(r);
const B = stats(base), T = stats(results.filter(r => +r.take === 1)), A = stats(results);
// Pre-registered success test (fixed now, before any forward data): TAKE signals must beat the random
// baseline by at least 10 points of hit rate AND have a higher average return, on at least 20 scored TAKEs.
let verdict = 'NOT ENOUGH DATA YET — need 20+ scored TAKE signals';
if (T.n >= 20) verdict = (T.hit_rate - B.hit_rate >= 0.10 && T.avg_return > B.avg_return)
  ? 'PASS — TAKE signals beat random entries by 10+ points of hit rate with a higher average return'
  : 'FAIL — TAKE signals did not clearly beat random entries';
const summary = {
  rules_version: RULES_VERSION, updated: new Date().toISOString(), logged_rows: log.length,
  logged_days: new Set(log.map(r => r.signal_date)).size, scored_signals: results.length,
  success_test: 'TAKE hit rate >= random + 10 points AND TAKE average return > random, on >= 20 scored TAKEs',
  verdict,
  random_baseline: B, all_signals: A, take: T,
  by_n_panels: by(r => +r.n_panels >= 2 ? '2+ panels' : '1 panel'),
  by_size: by(r => r.size), by_panel: Object.fromEntries(Object.entries(panelRows).map(([k, v]) => [k, stats(v)])),
  by_qqq_above_50d: by(r => r.qqq_above_50d === '1' ? 'QQQ above 50d' : 'QQQ below 50d'),
  by_breadth: by(r => +r.breadth_above_50d > 0.5 ? 'breadth > 50%' : 'breadth <= 50%'),
};
fs.writeFileSync(SUM, JSON.stringify(summary, null, 1));
console.log(`Scored ${results.length} signal(s); random baseline n=${B.n}. Verdict: ${verdict}`);
