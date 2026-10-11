#!/usr/bin/env node
// =====================================================================================
// WEEKLY REVIEW - read-only. It measures the forward log; it never changes a rule.
//
// What it does
//   1. Reads docs/forward_log.csv (what the page actually showed) and docs/data.json (prices).
//   2. For every logged signal, measures the return from the next session's open to the close
//      5, 10 and 20 sessions later, MINUS the equal-weight basket over the same window.
//      Beating the basket is the test: in a rally every panel "works".
//   3. Groups the result by panel, TAKE, size and market condition, and counts how much
//      INDEPENDENT evidence there is (distinct dates and weeks, not rows - names that fire on
//      the same day move together, so 15 rows on one day is closer to one observation than 15).
//   4. Applies the evidence gate fixed in reviews/PROTOCOL.md. While the gate is closed the
//      review may describe, but may not propose a rule change.
//   5. Writes reviews/<date>.md and reviews/data/<date>.json.
//
// It does NOT replace the official pass/fail test in forward_log.js (target-or-session-20,
// vs random entries). That verdict is quoted here unchanged.
//
// Usage: node scripts/weekly_review.js [--date YYYY-MM-DD]
// =====================================================================================
const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const DATA = path.join(ROOT, 'docs', 'data.json');
const LOG = path.join(ROOT, 'docs', 'forward_log.csv');
const SUM = path.join(ROOT, 'docs', 'forward_summary.json');
const OUT_DIR = path.join(ROOT, 'reviews');

// ---- fixed in advance; see reviews/PROTOCOL.md. Changing these is itself a rule change. ----
const HORIZONS = [5, 10, 20];
const GATE_HORIZON = 20;        // the gate is judged on the 20-session window only
const GATE_MIN_WEEKS = 12;      // distinct calendar weeks with a measured signal in the group
const GATE_MIN_SIGNALS = 60;    // de-duplicated signals in the group
const DEDUPE_SESSIONS = 5;      // a name that stays in a panel is one trade per 5 sessions
const BENCH = new Set(['SPY', 'QQQ', 'IWM', 'VOO']);   // same set forward_log.js excludes

const argDate = (() => { const i = process.argv.indexOf('--date'); return i > 0 ? process.argv[i + 1] : null; })();
const RUN_DATE = argDate || new Date().toISOString().slice(0, 10);

// ---------- load ----------
const J = JSON.parse(fs.readFileSync(DATA, 'utf8'));
const summary = fs.existsSync(SUM) ? JSON.parse(fs.readFileSync(SUM, 'utf8')) : {};
function readCsv(file) {
  if (!fs.existsSync(file)) return [];
  const [head, ...lines] = fs.readFileSync(file, 'utf8').trim().split('\n');
  if (!head) return [];
  const cols = head.split(',');
  return lines.filter(Boolean).map(l => { const v = l.split(','); const o = {}; cols.forEach((c, i) => o[c] = v[i]); return o; });
}
const log = readCsv(LOG);
const dateOf = b => new Date(b[0] * 1000).toISOString().slice(0, 10);

// Bars per symbol, with any unfinished session removed: never measure against a bar later than
// the last date the forward log itself treated as complete.
const lastLogged = log.reduce((m, r) => r.signal_date > m ? r.signal_date : m, '');
const lastComplete = (() => {
  const spy = (J.tickers.SPY || {}).bars || [];
  const last = spy.length ? dateOf(spy[spy.length - 1]) : '';
  const now = new Date();
  const et = new Date(now.toLocaleString('en-US', { timeZone: 'America/New_York' }));
  const todayET = et.getFullYear() + '-' + String(et.getMonth() + 1).padStart(2, '0') + '-' + String(et.getDate()).padStart(2, '0');
  const settled = et.getHours() * 60 + et.getMinutes() >= 16 * 60 + 20;
  return (last === todayET && !settled && spy.length > 1) ? dateOf(spy[spy.length - 2]) : last;
})();
const bars = {}, idx = {};
for (const s of Object.keys(J.tickers)) {
  const b = (J.tickers[s].bars || []).filter(x => dateOf(x) <= lastComplete);
  if (b.length < 2) continue;
  bars[s] = b; idx[s] = new Map(b.map((x, i) => [dateOf(x), i]));
}
const basketSyms = Object.keys(bars).filter(s => !BENCH.has(s));

// ---------- returns ----------
// Buy the next session's open, sell at the close h sessions after the signal date.
function fwd(sym, date, h) {
  const b = bars[sym]; if (!b) return null;
  const i = idx[sym].get(date); if (i == null || i + h >= b.length) return null;
  const e = b[i + 1][1], x = b[i + h][4];
  return (e > 0 && x > 0) ? x / e - 1 : null;
}
const basketCache = new Map();
function basket(date, h) {
  const k = date + '|' + h; if (basketCache.has(k)) return basketCache.get(k);
  let s = 0, n = 0;
  for (const sym of basketSyms) { const r = fwd(sym, date, h); if (r != null) { s += r; n++; } }
  const v = n >= 20 ? { ret: s / n, n } : null;     // refuse a "basket" of a handful of names
  basketCache.set(k, v); return v;
}

// ---------- measure every logged row, then de-duplicate WITHIN each group ----------
// A name that stays in a group for several days is one trade per 5 sessions (first day counts).
// De-duplicating within the group matters: a name usually enters one panel first and becomes a
// TAKE a day or two later. De-duplicating across the whole log would throw that TAKE away
// because the name was already counted as a single-panel signal.
const measured = [];
for (const r of log.slice().sort((a, b) => a.signal_date.localeCompare(b.signal_date) || a.sym.localeCompare(b.sym))) {
  if (r.sym === '(none)' || !idx[r.sym]) continue;
  const i = idx[r.sym].get(r.signal_date); if (i == null) continue;
  const s = { ...r, bar: i, ex: {} };
  for (const h of HORIZONS) {
    const own = fwd(r.sym, r.signal_date, h), bk = basket(r.signal_date, h);
    if (own != null && bk) s.ex[h] = { ret: own, basket: bk.ret, excess: own - bk.ret };
  }
  measured.push(s);
}
function dedupe(rows) {
  const last = {}, out = [];
  for (const r of rows) { if (last[r.sym] != null && r.bar - last[r.sym] < DEDUPE_SESSIONS) continue; last[r.sym] = r.bar; out.push(r); }
  return out;
}
const signals = dedupe(measured);
// TAKE rows the official scorer (forward_log.js) will never score, because it de-duplicates
// across the whole log and the name was already counted on an earlier day.
const takeRows = dedupe(measured.filter(r => +r.take === 1));
const takeLostToOfficialDedupe = takeRows.filter(t => !signals.includes(t));

// ---------- stats ----------
function isoWeek(d) {
  const t = new Date(d + 'T00:00:00Z'); const day = (t.getUTCDay() + 6) % 7; t.setUTCDate(t.getUTCDate() - day + 3);
  const y = t.getUTCFullYear(); const w1 = new Date(Date.UTC(y, 0, 4));
  return y + '-W' + String(1 + Math.round(((t - w1) / 864e5 - 3 + ((w1.getUTCDay() + 6) % 7)) / 7)).padStart(2, '0');
}
const mean = a => a.reduce((s, v) => s + v, 0) / a.length;
const median = a => { const s = a.slice().sort((x, y) => x - y); const m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };
function stats(rows, h) {
  const m = rows.filter(r => r.ex[h]);
  if (!m.length) return { n: 0, dates: 0, weeks: 0 };
  const ex = m.map(r => r.ex[h].excess);
  const byDate = {}; for (const r of m) (byDate[r.signal_date] = byDate[r.signal_date] || []).push(r.ex[h].excess);
  const dm = Object.values(byDate).map(mean);            // one number per date: same-day names are not independent
  const k = dm.length, dmean = mean(dm);
  const sd = k > 1 ? Math.sqrt(dm.reduce((s, v) => s + (v - dmean) ** 2, 0) / (k - 1)) : null;
  const weeks = new Set(m.map(r => isoWeek(r.signal_date))).size;
  return {
    n: m.length, dates: k, weeks,
    avg_return: +mean(m.map(r => r.ex[h].ret)).toFixed(4),
    avg_basket: +mean(m.map(r => r.ex[h].basket)).toFixed(4),
    avg_excess: +mean(ex).toFixed(4), median_excess: +median(ex).toFixed(4),
    beat_basket_rate: +(ex.filter(v => v > 0).length / ex.length).toFixed(3),
    // t-statistic on per-date averages. Windows from neighbouring dates overlap, so even this
    // overstates the evidence; it is a ceiling on confidence, not a measure of it.
    date_avg_excess: +dmean.toFixed(4), t_by_date: (k >= 3 && sd > 0) ? +(dmean / (sd / Math.sqrt(k))).toFixed(2) : null,
  };
}
const G = f => dedupe(measured.filter(f));
const groups = { 'All signals': signals, 'TAKE': takeRows, '2+ panels': G(r => +r.n_panels >= 2), '1 panel': G(r => +r.n_panels === 1) };
const panelNames = [...new Set(measured.flatMap(r => String(r.panels).split('+')))].sort();
for (const p of panelNames) groups['Panel: ' + p] = G(r => String(r.panels).split('+').includes(p));
for (const sz of ['FULL', 'HALF', 'IGNORE']) { const g = G(r => r.size === sz); if (g.length) groups['Size: ' + sz] = g; }
groups['QQQ above 50-day'] = G(r => r.qqq_above_50d === '1');
groups['QQQ below 50-day'] = G(r => r.qqq_above_50d === '0');

const table = {};
for (const [name, rows] of Object.entries(groups)) { table[name] = { logged: rows.length }; for (const h of HORIZONS) table[name][h] = stats(rows, h); }

// ---------- evidence gate ----------
const gate = {};
for (const [name, t] of Object.entries(table)) {
  const s = t[GATE_HORIZON];
  gate[name] = { signals: s.n, weeks: s.weeks, open: s.n >= GATE_MIN_SIGNALS && s.weeks >= GATE_MIN_WEEKS };
}
const anyOpen = Object.values(gate).some(g => g.open);

// ---------- pace: when could the official TAKE test possibly conclude? ----------
const versions = [...new Set(log.map(r => r.rules_version).filter(Boolean))];
const loggedDays = new Set(log.map(r => r.signal_date)).size;
const takeLogged = groups['TAKE'].length;
const takeScored = (summary.take || {}).n || 0;
const takePerSession = loggedDays ? takeLogged / loggedDays : 0;
const sessionsToTwenty = takePerSession > 0 ? Math.ceil((20 - takeLogged) / takePerSession) + 20 : null;

// ---------- write ----------
const result = {
  run_date: RUN_DATE, rules_version: versions.join(', '), mixed_versions: versions.length > 1,
  last_complete_session: lastComplete, last_logged_session: lastLogged,
  logged_rows: log.length, logged_days: loggedDays, deduped_signals: signals.length, basket_size: basketSyms.length,
  gate_rule: { horizon: GATE_HORIZON, min_signals: GATE_MIN_SIGNALS, min_weeks: GATE_MIN_WEEKS },
  gate, any_gate_open: anyOpen,
  official_test: { success_test: summary.success_test || null, verdict: summary.verdict || null, scored_signals: summary.scored_signals ?? null, take_scored: takeScored },
  take_pace: { take_lost_to_official_dedupe: takeLostToOfficialDedupe.map(r => r.signal_date + ' ' + r.sym), take_logged: takeLogged, logged_days: loggedDays, per_session: +takePerSession.toFixed(3), sessions_until_20_scored: sessionsToTwenty },
  table,
};
fs.mkdirSync(path.join(OUT_DIR, 'data'), { recursive: true });
fs.writeFileSync(path.join(OUT_DIR, 'data', RUN_DATE + '.json'), JSON.stringify(result, null, 1) + '\n');

const pct = v => v == null ? '–' : (v >= 0 ? '+' : '') + (v * 100).toFixed(1) + '%';
const md = [];
md.push('# BCC3 weekly review — ' + RUN_DATE, '');
md.push('Read-only measurement of the forward log. Nothing on the dashboard changes because of this file.', '');
md.push('## Where the evidence stands', '');
md.push('| | |', '|---|---|');
md.push('| Rules version | ' + result.rules_version + (result.mixed_versions ? ' **(MIXED — results below are not valid)**' : '') + ' |');
md.push('| Sessions logged | ' + loggedDays + ' (last: ' + lastLogged + ') |');
md.push('| Rows logged / distinct trades (one per name per 5 sessions) | ' + log.length + ' / ' + signals.length + ' |');
md.push('| Basket used as the yardstick | ' + basketSyms.length + ' names, equal weight |');
md.push('| Official test (set in forward_log.js) | ' + (summary.verdict || 'n/a') + ' |');
md.push('| TAKE signals logged / scored | ' + takeLogged + ' / ' + takeScored + ' of the 20 the official test needs |');
md.push('| TAKEs the official scorer will skip (name already counted on an earlier day) | ' + (takeLostToOfficialDedupe.length ? takeLostToOfficialDedupe.map(r => r.sym + ' ' + r.signal_date).join(', ') : 'none') + ' |');
md.push('| At the current pace, 20 scored TAKEs arrive in | ' + (sessionsToTwenty == null ? 'cannot be estimated — no TAKE logged yet' : '~' + sessionsToTwenty + ' sessions (~' + Math.round(sessionsToTwenty / 21) + ' months)') + ' |');
md.push('| **Evidence gate** (≥' + GATE_MIN_SIGNALS + ' signals over ≥' + GATE_MIN_WEEKS + ' weeks, 20-session window) | **' + (anyOpen ? 'OPEN for: ' + Object.entries(gate).filter(([, g]) => g.open).map(([k]) => k).join(', ') : 'CLOSED — no rule change may be proposed this week') + '** |');
md.push('');
for (const h of HORIZONS) {
  md.push('## ' + h + '-session window — return versus the basket', '');
  const any = Object.values(table).some(t => t[h].n);
  if (!any) { md.push('No signal is old enough to measure at ' + h + ' sessions yet.', ''); continue; }
  md.push('| Group | Signals | Dates | Weeks | Avg return | Basket | Excess | Beat basket | t (by date) |', '|---|--:|--:|--:|--:|--:|--:|--:|--:|');
  for (const [name, t] of Object.entries(table)) {
    const s = t[h]; if (!s.n) continue;
    md.push('| ' + name + ' | ' + s.n + ' | ' + s.dates + ' | ' + s.weeks + ' | ' + pct(s.avg_return) + ' | ' + pct(s.avg_basket) + ' | ' + pct(s.avg_excess) + ' | ' + Math.round(s.beat_basket_rate * 100) + '% | ' + (s.t_by_date == null ? '–' : s.t_by_date) + ' |');
  }
  md.push('');
}
md.push('**How to read this.** "Excess" is the signal\'s return minus the equal-weight basket over the same days. "Dates" and "Weeks" are the honest sample size: names flagged on the same day mostly rise and fall together. "t (by date)" above about 2, on 12+ weeks, would be worth attention; anything on fewer weeks is noise however large it looks. The 5- and 10-session windows are shown for early warning only and are never grounds for a change.', '');
md.push('## Gate status by group', '', '| Group | Signals (20-session) | Weeks | Gate |', '|---|--:|--:|---|');
for (const [name, g] of Object.entries(gate)) md.push('| ' + name + ' | ' + g.signals + ' / ' + GATE_MIN_SIGNALS + ' | ' + g.weeks + ' / ' + GATE_MIN_WEEKS + ' | ' + (g.open ? 'OPEN' : 'closed') + ' |');
md.push('', '## Commentary', '', '<!-- COMMENTARY: written by the reviewer each week. See reviews/PROTOCOL.md for what it may and may not say. -->', '');
fs.writeFileSync(path.join(OUT_DIR, RUN_DATE + '.md'), md.join('\n'));
console.log('Review written: reviews/' + RUN_DATE + '.md  | signals ' + signals.length + ' | gate ' + (anyOpen ? 'OPEN' : 'CLOSED'));
