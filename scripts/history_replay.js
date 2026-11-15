#!/usr/bin/env node
// =====================================================================================
// HISTORY REPLAY - what would the dashboard's panels have flagged on each past day?
//
// Loads docs/index.html into the same stub-DOM sandbox scripts/forward_log.js uses, then, for every historical
// session, feeds it ONLY the bars up to that day (last 430, like the live feed) and records which names the page's
// own panel code put where (Weekly Picks, Golden Cross, RS Leader, Bull Flag, Intraday Breakout) and which were TAKE
// (2+ panels, 2 slots, lowest CMF first). So the signals are exactly what the page would have shown - no second copy of
// the rules. Nothing here changes any panel, file in docs/, or the live forward log.
//
// Usage: node scripts/history_replay.js [history.json] [out.jsonl]
// Env:   REPLAY_FROM=YYYY-MM-DD  first session to replay (default: 260 bars after the first SPY bar)
//        REPLAY_LAST=N           only replay the last N sessions (benchmarking)
//
// Known limits (also printed in the report): the live page sees partial intraday bars for "Intraday Breakout"; here
// every day is a completed bar, so that panel is an end-of-day approximation. Company facts (sector, market cap,
// earnings dates) come from today's data.json, not from the past: earnings flags are therefore missing in the replay.
// =====================================================================================
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.join(__dirname, '..');
const HTML = path.join(ROOT, 'docs', 'index.html');
const HIST = process.argv[2] || path.join(ROOT, 'backtest', 'history.json');
const OUT = process.argv[3] || path.join(ROOT, 'backtest', 'signals.jsonl');
const SLOTS = 2, WINDOW = 430, WARMUP = 260;

function stubEl() {
  const el = { innerHTML: '', textContent: '', value: '', style: {}, dataset: {}, children: [], classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } } };
  el.querySelector = () => stubEl(); el.querySelectorAll = () => [];
  el.addEventListener = () => {}; el.appendChild = () => {}; el.setAttribute = () => {};
  el.getBoundingClientRect = () => ({ width: 0, height: 0, top: 0, left: 0 });
  return el;
}
const els = {};
const sandbox = {
  console: { log() {}, warn() {}, error() {} }, Math, Date, JSON, Intl, Map, Set, Array, Object, Number, String, Promise, isFinite, parseFloat, parseInt,
  document: { getElementById: id => (els[id] = els[id] || stubEl()), querySelector: () => stubEl(), querySelectorAll: () => [],
              addEventListener: () => {}, createElement: () => stubEl(), body: stubEl(), documentElement: stubEl() },
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
  fetch: () => Promise.reject(new Error('no fetch in replay')),
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

const H = JSON.parse(fs.readFileSync(HIST, 'utf8'));
const T = H.tickers;
const syms = Object.keys(T);
if (!T.SPY) { console.error('SPY missing from history'); process.exit(1); }
const dayOf = ts => new Date(ts * 1000).toISOString().slice(0, 10);
const spyBars = T.SPY.bars;
const dayIdx = {};                      // per ticker: date -> bar index
for (const s of syms) { dayIdx[s] = new Map(); T[s].bars.forEach((b, i) => dayIdx[s].set(dayOf(b[0]), i)); }

let first = WARMUP;
if (process.env.REPLAY_FROM) { const k = spyBars.findIndex(b => dayOf(b[0]) >= process.env.REPLAY_FROM); if (k >= 0) first = Math.max(first, k); }
let last = spyBars.length - 1;
if (process.env.REPLAY_LAST) first = Math.max(first, last - (+process.env.REPLAY_LAST) + 1);

sandbox.__T = T; sandbox.__SLOTS = SLOTS;
const runDay = vm.runInContext(`(function (day, win) {
  for (const k of Object.keys(data)) delete data[k];
  for (const k of Object.keys(fund)) delete fund[k];
  panelMembers.clear();
  for (const sym of Object.keys(__T)) {
    const e = __T[sym], i = win[sym];
    if (i == null) continue;
    const bars = e.bars.slice(Math.max(0, i - ${WINDOW} + 1), i + 1);
    if (bars.length < 2) continue;
    data[sym] = computeMetrics(bars); data[sym].cross = computeCrossStatus(bars);
    fund[sym] = { name: e.name, sector: e.sector, industry: e.industry, mcap: e.mcap, beta: e.beta, targetMean: null, nextEarnings: null, premarketGapPct: null, earningsFlag: null };
  }
  clearDerivedCaches();
  const stocks = Object.keys(data).filter(s => !BENCHMARKS.has(s) && data[s] && !data[s].error);
  renderWeeklyPicks(stocks); renderRSDivergence(stocks); renderIntradayBreakout(stocks); renderBullFlag(stocks); renderGoldenCross(stocks);
  const items = [...panelMembers.entries()].map(([sym, srcs]) => ({ sym, srcs, p: computeTradePlan(sym) })).filter(x => x.p);
  const multi = items.filter(x => x.srcs.length >= 2 && !x.p.ignore).sort((a, b) => (a.p.cmf20 ?? 9) - (b.p.cmf20 ?? 9));
  const take = new Set(multi.slice(0, __SLOTS).map(x => x.sym));
  return items.map(x => ({ sym: x.sym, panels: x.srcs.map(t => t.split(' #')[0]).join('+'), n: x.srcs.length, take: take.has(x.sym) ? 1 : 0,
    size: x.p.ignore ? 'IGNORE' : x.p.halfSize ? 'HALF' : 'FULL', close: x.p.px, move: x.p.atr, cmf20: x.p.cmf20 }));
})`, sandbox);

fs.mkdirSync(path.dirname(OUT), { recursive: true });
const fd = fs.openSync(OUT, 'w');
const t0 = Date.now(); let nDays = 0, nRows = 0, errs = 0;
for (let k = first; k <= last; k++) {
  const day = dayOf(spyBars[k][0]);
  const win = {};
  for (const s of syms) { const i = dayIdx[s].get(day); if (i != null && i >= 60) win[s] = i; }   // must have a bar on this exact day
  let rows = [];
  try { rows = runDay(day, win); } catch (e) { errs++; if (errs <= 3) process.stderr.write('replay error ' + day + ': ' + e.message + '\n'); continue; }
  fs.writeSync(fd, JSON.stringify({ date: day, rows }) + '\n');
  nDays++; nRows += rows.length;
  if (nDays % 50 === 0) process.stderr.write('replayed ' + nDays + ' sessions, ' + ((Date.now() - t0) / 1000).toFixed(0) + 's\n');
}
fs.closeSync(fd);
console.log('replayed ' + nDays + ' sessions (' + errs + ' errors), ' + nRows + ' signal rows, ' + ((Date.now() - t0) / 1000).toFixed(0) + 's -> ' + OUT);
