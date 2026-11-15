#!/usr/bin/env python3
"""
RSI OVERSOLD - a daily-bar copy of StockKit's "Ram Jet" idea, tested on this basket.
Display-only panel: it never feeds TAKE, Weekly Picks or the v2 forward log.

Rules (fixed 15 Nov 2026 - change them and you must bump RULES_VERSION and start a new log):
  Signal   RSI(14, Wilder smoothing - the TradingView / StockKit definition) CROSSES below 30 on a completed
           daily bar: yesterday >= 30, today < 30.
           NOTE: the Performance table's RSI column uses a plain 14-day average, so its number can differ a few
           points from this panel's. This panel uses Wilder so it matches what you see on TradingView.
  Entry    next session's open (the signal is only known after the close).
  Target   entry + 3%. A resting limit order: filled at the target, or at the open if the stock gaps above it.
  Stop     NONE (StockKit's rule).
  Exit     target, or the close of the 10th session after entry (entry day counts as session 1).
  Size     reported per $1,000 position so results are in dollars.

Pre-registered forward test (docs/rsi_log.csv -> docs/rsi_summary.json):
  every fresh signal is logged once per completed session and scored when its 10 sessions have passed, against
  random entries (every other basket stock, same next open, same rule) on the same dates.
  PASS = at least 30 scored signals AND hit rate at least 10 points above the same-date random entries AND a higher
  average dollar result per $1,000. Worst single trade is always reported next to it: with no stop the
  average can look fine while one trade loses many winners' worth.

Outputs
  docs/rsi.json          today's state: fresh crosses, other names below 30, the last 15 sessions' signals and how they went
  docs/rsi_log.csv       forward log, once per completed session
  docs/rsi_summary.json  scored forward signals vs random entries
  (docs/rsi_backtest.json is written by scripts/rsi_backtest.py and only read here)
"""
import csv
import datetime as dt
import json
import os
import statistics as st
from zoneinfo import ZoneInfo

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
DOCS = os.path.join(ROOT, 'docs')
DATA = os.path.join(DOCS, 'data.json')
OUT = os.path.join(DOCS, 'rsi.json')
LOG = os.path.join(DOCS, 'rsi_log.csv')
SUM = os.path.join(DOCS, 'rsi_summary.json')
BT = os.path.join(DOCS, 'rsi_backtest.json')
RULES_VERSION = 'rsi-v1-2026-11-15'
RSI_N, RSI_LEVEL, TP_PCT, HOLD, POS = 14, 30.0, 0.03, 10, 1000.0
ET = ZoneInfo('America/New_York')
OPEN_MIN, CLOSE_MIN = 9 * 60 + 30, 16 * 60
ETF = {'SPY', 'QQQ', 'IWM', 'VOO', 'SMH', 'IGV', 'HACK', 'BUG', 'BOT', 'BOTT', 'EWY', 'DRAM', 'JETS', 'NCLD', 'FOTO', 'AIPO'}
SPLIT_RATIOS = (1/2, 1/3, 1/4, 1/5, 1/10, 2, 3, 4, 5, 10)
LOG_COLS = ['session', 'rules_version', 'sym', 'close', 'rsi', 'rsi_prev', 'tp_ref', 'logged_at']

day = lambda t: dt.datetime.fromtimestamp(t, dt.timezone.utc).date().isoformat()


# ---------------------------------------------------------------- core logic (shared with rsi_backtest.py)
def series(bars):
    """bars = [ts, open, high, low, close, volume]. Drops rows with missing or non-positive prices."""
    t, o, h, l, c = [], [], [], [], []
    for b in bars:
        try:
            if all(isinstance(v, (int, float)) and v > 0 for v in b[1:5]):
                t.append(b[0]); o.append(b[1]); h.append(b[2]); l.append(b[3]); c.append(b[4])
        except (TypeError, IndexError):
            continue
    return dict(t=t, o=o, h=h, l=l, c=c)


def wilder_rsi(c, n=RSI_N):
    """RSI with Wilder smoothing (seed = simple mean of the first n changes). None until index n."""
    out = [None] * len(c)
    if len(c) <= n:
        return out
    g = l = 0.0
    for i in range(1, n + 1):
        d = c[i] - c[i - 1]
        g += d if d > 0 else 0.0
        l += -d if d < 0 else 0.0
    ag, al = g / n, l / n
    out[n] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(n + 1, len(c)):
        d = c[i] - c[i - 1]
        ag = (ag * (n - 1) + (d if d > 0 else 0.0)) / n
        al = (al * (n - 1) + (-d if d < 0 else 0.0)) / n
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def split_idx(c):
    """Indexes where the close moved by almost exactly a split ratio (a likely unadjusted split)."""
    bad = []
    for i in range(1, len(c)):
        r = c[i] / c[i - 1]
        if any(abs(r / s - 1) < 0.04 for s in SPLIT_RATIOS):
            bad.append(i)
    return bad


def crosses(rsi, level=RSI_LEVEL):
    return [i for i in range(1, len(rsi)) if rsi[i] is not None and rsi[i - 1] is not None
            and rsi[i - 1] >= level and rsi[i] < level]


def simulate(x, i, tp=TP_PCT, hold=HOLD, stop=None):
    """Signal on completed bar i. Enter at open of bar i+1. Returns None if bar i+1 does not exist yet.
    status: 'TP' (target hit), 'EXP' (held the full `hold` sessions), 'STOP', or 'OPEN' (not finished yet).
    The stop (if any) is checked before the target on the same bar - the pessimistic order."""
    n = len(x['c'])
    if i + 1 >= n:
        return None
    entry = x['o'][i + 1]
    tgt = entry * (1 + tp)
    stp = entry * (1 - stop) if stop else None
    low = entry
    for d in range(1, hold + 1):
        j = i + d
        if j >= n:
            last = n - 1
            return dict(status='OPEN', entry=entry, tgt=tgt, exit=x['c'][last], days=last - i, exit_idx=last, mae=low / entry - 1)
        low = min(low, x['l'][j])
        if stp is not None and x['l'][j] <= stp:
            px = min(stp, x['o'][j])
            return dict(status='STOP', entry=entry, tgt=tgt, exit=px, days=d, exit_idx=j, mae=low / entry - 1)
        if x['h'][j] >= tgt:
            return dict(status='TP', entry=entry, tgt=tgt, exit=max(tgt, x['o'][j]) if j > i + 1 else tgt,
                        days=d, exit_idx=j, mae=low / entry - 1)
        if d == hold:
            return dict(status='EXP', entry=entry, tgt=tgt, exit=x['c'][j], days=d, exit_idx=j, mae=low / entry - 1)
    return None


def pnl(tr, size=POS):
    return size * (tr['exit'] / tr['entry'] - 1)


# ---------------------------------------------------------------- panel / log / forward score
def sma(c, n):
    return sum(c[-n:]) / n if len(c) >= n else None


def build_series(T, drop_today):
    S = {}
    for s, d in T.items():
        b = (d or {}).get('bars') or []
        if drop_today and b:
            b = b[:-1]
        x = series(b)
        if len(x['c']) < 40:
            continue
        x['rsi'] = wilder_rsi(x['c'])
        x['split'] = split_idx(x['c'])
        x['d'] = d
        S[s] = x
    return S


def recent_signals(S, session_idx_by_sym, lookback=15):
    rows = []
    for s, x in S.items():
        n = len(x['c'])
        for i in crosses(x['rsi']):
            if i < n - 1 - lookback + 1 or any(i - 15 <= k <= i + HOLD + 1 for k in x['split']):
                continue
            tr = simulate(x, i)
            row = dict(sym=s, signal=day(x['t'][i]), close=round(x['c'][i], 2), rsi=round(x['rsi'][i], 1))
            if tr is None:
                row.update(status='ENTRY NEXT OPEN', tp_ref=round(x['c'][i] * (1 + TP_PCT), 2))
            else:
                row.update(status=tr['status'], entry=round(tr['entry'], 2), tgt=round(tr['tgt'], 2),
                           exit=round(tr['exit'], 2), days=tr['days'], pnl1000=round(pnl(tr), 0),
                           mae1000=round(tr['mae'] * POS, 0))
            rows.append(row)
    rows.sort(key=lambda r: (r['signal'], r['sym']), reverse=True)
    return rows


def forward_score(S):
    if not os.path.exists(LOG):
        return
    with open(LOG) as f:
        rows = [r for r in csv.DictReader(f) if r['rules_version'] == RULES_VERSION]
    scored, base_all = [], []
    sess_done = {}
    for r in rows:
        x = S.get(r['sym'])
        if not x:
            continue
        try:
            i = x['t'].index(next(t for t in x['t'] if day(t) == r['session']))
        except StopIteration:
            continue
        tr = simulate(x, i)
        if tr is None or tr['status'] == 'OPEN':
            continue
        scored.append((r['session'], r['sym'], tr))
        sess_done.setdefault(r['session'], set()).add(r['sym'])
    for sess, syms in sess_done.items():
        for s, x in S.items():
            if s in syms:
                continue
            ix =[i for i, t in enumerate(x['t']) if day(t) == sess]
            if not ix:
                continue
            i = ix[0]
            if any(i - 15 <= k <= i + HOLD + 1 for k in x['split']):
                continue
            tr = simulate(x, i)
            if tr and tr['status'] != 'OPEN':
                base_all.append((sess, s, tr))

    def stats(lst):
        if not lst:
            return None
        p = [pnl(t) for _, _, t in lst]
        return dict(n=len(p), hit_rate=round(sum(1 for _, _, t in lst if t['status'] == 'TP') / len(p), 4),
                    avg_pnl_per_1000=round(sum(p) / len(p), 2), median_pnl_per_1000=round(st.median(p), 2),
                    worst_pnl_per_1000=round(min(p), 2))
    a, b = stats(scored), stats(base_all)
    verdict = 'WAITING (need 30 scored signals)'
    if a and a['n'] >= 30 and b:
        ok = a['hit_rate'] >= b['hit_rate'] + 0.10 and a['avg_pnl_per_1000'] > b['avg_pnl_per_1000']
        verdict = 'PASS' if ok else 'FAIL'
    json.dump(dict(rules_version=RULES_VERSION, signals=a, random_same_dates=b, verdict=verdict,
                   criteria='>=30 scored signals; hit rate >=10 points above same-date random entries; higher average $ per $1,000'),
              open(SUM, 'w'), separators=(',', ':'))


def main():
    now = dt.datetime.now(ET)
    J = json.load(open(DATA))
    T = J.get('tickers') or {}
    spy = (T.get('SPY') or {}).get('bars') or []
    if len(spy) < 40:
        print('SPY history too short'); return
    today = now.date().isoformat()
    minutes = now.hour * 60 + now.minute
    partial = day(spy[-1][0]) == today and minutes < CLOSE_MIN + 20
    S = build_series(T, partial)
    if 'SPY' not in S:
        print('no SPY series'); return
    session = day(S['SPY']['t'][-1])

    fresh, below = [], []
    for s, x in S.items():
        n = len(x['c']); k = n - 1
        if day(x['t'][k]) != session or x['rsi'][k] is None or x['rsi'][k - 1] is None:
            continue
        if x['rsi'][k] >= RSI_LEVEL:
            continue
        run = 0
        for i in range(k, -1, -1):
            if x['rsi'][i] is not None and x['rsi'][i] < RSI_LEVEL:
                run += 1
            else:
                break
        c = x['c'][k]
        d = x['d'] or {}
        ne = d.get('nextEarnings')
        dte = (dt.date.fromisoformat(ne[:10]) - dt.date.fromisoformat(session)).days if ne else None
        s200 = sma(x['c'], 200)
        rec = dict(sym=s, name=d.get('name'), close=round(c, 2), rsi=round(x['rsi'][k], 1), rsi_prev=round(x['rsi'][k - 1], 1),
                   days_below=run, tp_ref=round(c * (1 + TP_PCT), 2), tp_gain=round(c * TP_PCT, 2),
                   drop_5d=round(c - x['c'][k - 5], 2) if k >= 5 else None,
                   below_sma200=(c < s200) if s200 else None, sma200=round(s200, 2) if s200 else None,
                   earnings_days=dte, etf=s in ETF, split_suspect=any(k - 15 <= j <= k for j in x['split']))
        (fresh if run == 1 else below).append(rec)
    fresh.sort(key=lambda r: r['rsi']); below.sort(key=lambda r: r['rsi'])
    n_t = sum(1 for x in S.values() if day(x['t'][-1]) == session)
    bt = json.load(open(BT)) if os.path.exists(BT) else None
    out = dict(asof=J.get('asof'), session=session, partial_session=partial, rules_version=RULES_VERSION,
               rules=dict(rsi_period=RSI_N, level=RSI_LEVEL, target_pct=TP_PCT * 100, max_hold=HOLD, stop=None, entry='next open'),
               market=dict(n_tickers=n_t, n_below_30=len(fresh) + len(below), n_fresh=len(fresh)),
               fresh=fresh, below=below, recent=recent_signals(S, None), backtest=bt)
    # Daily-bar signals cannot change intraday, so skip the write (and the commit / Pages deploy it would trigger)
    # when nothing but the data timestamp moved.
    same = False
    if os.path.exists(OUT):
        try:
            old = json.load(open(OUT))
            same = {k: v for k, v in old.items() if k != 'asof'} == {k: v for k, v in out.items() if k != 'asof'}
        except Exception:
            same = False
    if not same:
        json.dump(out, open(OUT, 'w'), separators=(',', ':'))

    if not partial:
        done = set()
        if os.path.exists(LOG):
            with open(LOG) as f:
                done = {(r['session'], r['sym']) for r in csv.DictReader(f)}
        new = not os.path.exists(LOG)
        stamp = dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')
        with open(LOG, 'a', newline='') as f:
            w = csv.writer(f)
            if new:
                w.writerow(LOG_COLS)
            for r in fresh:
                if r['split_suspect'] or (session, r['sym']) in done:
                    continue
                w.writerow([session, RULES_VERSION, r['sym'], r['close'], r['rsi'], r['rsi_prev'], r['tp_ref'], stamp])
        forward_score(S)
    print(f"session {session} partial={partial} tickers={n_t} fresh={[r['sym'] for r in fresh]} below={[r['sym'] for r in below]}")


if __name__ == '__main__':
    main()
