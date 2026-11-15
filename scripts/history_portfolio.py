#!/usr/bin/env python3
"""
$100k PORTFOLIO SIMULATION of the dashboard panels' trades over the replayed history.

Inputs : backtest/history.json (scripts/history_fetch.py), backtest/signals.jsonl (scripts/history_replay.js)
Outputs: backtest/results.json and a printed report. Nothing here changes any panel.

Trade rule = the live forward log's own rule (scripts/forward_log.js, outcome()):
  signal after the close of day i -> buy the OPEN of day i+1 -> target = entry + mult x "normal daily move"
  (mult 1.5 for panel names, 2.0 for TAKE) -> sell at the target if the day's high reaches it, otherwise at the CLOSE of the
  20th session counting the entry day -> NO stop (optional stop variants are reported separately).
Account rules (assumptions, all printed): START dollars, at most N open positions, each sized equal-weight from the
  account's invested-at-cost + cash value at entry (HALF-size names get half), cash used at the open can only be freed by
  exits from earlier days, round-trip cost COST per side (default 0.10% = slippage + commission, a guess - set your own).
Compared against: SPY buy-and-hold, equal-weight buy-and-hold of the whole basket (survivorship-flattered), and RANDOM entries
  with the same rules and the same account (many seeds), which is the fair "did the panels pick better than chance" test.
"""
import argparse
import json
import math
import os
import random
import statistics as st
from collections import defaultdict
from datetime import datetime, timezone

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
HOLD, PANEL_MULT, TAKE_MULT = 20, 1.5, 2.0
BENCH = {'SPY', 'QQQ', 'IWM', 'VOO'}
day = lambda t: datetime.fromtimestamp(t, timezone.utc).date().isoformat()


def load(hist_path, sig_path):
    H = json.load(open(hist_path))['tickers']
    bars, didx = {}, {}
    for s, d in H.items():
        b = d['bars']
        bars[s] = b
        didx[s] = {day(x[0]): i for i, x in enumerate(b)}
    sig = [json.loads(l) for l in open(sig_path)]
    return bars, didx, sig


def atr14(b, i):
    if i < 15:
        return None
    tr = []
    for k in range(i - 13, i + 1):
        tr.append(max(b[k][2] - b[k][3], abs(b[k][2] - b[k - 1][4]), abs(b[k][3] - b[k - 1][4])))
    return sum(tr) / 14


def trade(b, i, move, mult, stop=None):
    """Signal on bar i. Returns dict(entry_i, exit_i, entry, exit, status) or None if the data ends first."""
    if i + 1 >= len(b):
        return None
    e = b[i + 1][1]
    tg = e + mult * move
    sp = e * (1 - stop) if stop else None
    last = min(i + HOLD, len(b) - 1)
    for m in range(i + 1, last + 1):
        if sp is not None and b[m][3] <= sp:
            return dict(entry_i=i + 1, exit_i=m, entry=e, exit=min(sp, b[m][1]) if m > i + 1 else sp, status='STOP')
        if b[m][2] >= tg:
            return dict(entry_i=i + 1, exit_i=m, entry=e, exit=tg, status='TP')
    if i + HOLD < len(b):
        return dict(entry_i=i + 1, exit_i=i + HOLD, entry=e, exit=b[i + HOLD][4], status='EXP')
    return dict(entry_i=i + 1, exit_i=len(b) - 1, entry=e, exit=b[-1][4], status='OPEN')


def simulate(cands_by_day, bars, didx, dates, start, n_pos, cost, stop=None, rng=None):
    """cands_by_day: date -> list of dict(sym, move, mult, w). Returns summary + daily equity list."""
    cash = float(start)
    opens = []                       # dict(sym, shares, cost_basis, exit_date, exit_px, status, idx)
    equity_curve, trades = [], []
    pending = []                     # signals from the previous session, entered at today's open
    for d in dates:
        # 1) entries at today's open, using cash that existed at the start of the day
        invested = sum(p['cost_basis'] for p in opens)
        acct = cash + invested
        free = n_pos - len(opens)
        for c in pending:
            if free <= 0:
                break
            s = c['sym']
            if any(p['sym'] == s for p in opens):
                continue
            i0 = didx[s].get(c['sig_date'])
            if i0 is None:
                continue
            tr = trade(bars[s], i0, c['move'], c['mult'], stop)
            if not tr or day(bars[s][tr['entry_i']][0]) != d:
                continue
            size = min(cash, acct / n_pos * c['w'])
            if size < 100:
                continue
            fee = size * cost
            shares = (size - fee) / tr['entry']
            cash -= size
            opens.append(dict(sym=s, shares=shares, cost_basis=size, exit_i=tr['exit_i'], exit_px=tr['exit'],
                              status=tr['status'], exit_date=day(bars[s][tr['exit_i']][0]), entry=tr['entry']))
            free -= 1
        pending = []
        # 2) exits that happen today
        still = []
        for p in opens:
            if p['exit_date'] == d and p['status'] != 'OPEN':
                proceeds = p['shares'] * p['exit_px'] * (1 - cost)
                cash += proceeds
                trades.append(dict(sym=p['sym'], pnl=proceeds - p['cost_basis'], status=p['status'], size=p['cost_basis'],
                                   ret=proceeds / p['cost_basis'] - 1, date=d))
            else:
                still.append(p)
        opens = still
        # 3) mark to market at today's close
        mtm = cash
        for p in opens:
            i = didx[p['sym']].get(d)
            px = bars[p['sym']][i][4] if i is not None else p['entry']
            mtm += p['shares'] * px
        equity_curve.append((d, mtm))
        # 4) today's signals (known after the close) enter tomorrow
        pending = [dict(c, sig_date=d) for c in cands_by_day.get(d, [])]
    # end: open positions marked at last close (already in equity_curve)
    return equity_curve, trades


def summarize(curve, trades, start):
    eq = [v for _, v in curve]
    peak, mdd, mdd_i = eq[0], 0.0, 0
    for k, v in enumerate(eq):
        peak = max(peak, v)
        dd = v - peak
        if dd < mdd:
            mdd, mdd_i = dd, k
    yrs = max(len(eq) / 252.0, 1e-9)
    end = eq[-1]
    by_year = {}
    prev = start
    for y in sorted({d[:4] for d, _ in curve}):
        last = [v for d, v in curve if d[:4] == y][-1]
        by_year[y] = round(last - prev, 0)
        prev = last
    wins = [t for t in trades if t['pnl'] > 0]
    return dict(final=round(end, 0), profit=round(end - start, 0), cagr_pct=round(((end / start) ** (1 / yrs) - 1) * 100, 1) if end > 0 else None,
                max_drawdown=round(mdd, 0), max_drawdown_pct=round(mdd / peak * 100, 1) if peak else None,
                years=round(yrs, 2), trades=len(trades), win_rate=round(len(wins) / len(trades), 3) if trades else None,
                avg_trade=round(sum(t['pnl'] for t in trades) / len(trades), 0) if trades else None,
                worst_trade=round(min((t['pnl'] for t in trades), default=0), 0), by_year=by_year,
                stopped=sum(1 for t in trades if t['status'] == 'STOP'), expired=sum(1 for t in trades if t['status'] == 'EXP'))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--history', default=os.path.join(ROOT, 'backtest', 'history.json'))
    ap.add_argument('--signals', default=os.path.join(ROOT, 'backtest', 'signals.jsonl'))
    ap.add_argument('--out', default=os.path.join(ROOT, 'backtest', 'results.json'))
    ap.add_argument('--start', type=float, default=100000)
    ap.add_argument('--positions', type=int, default=5)
    ap.add_argument('--cost', type=float, default=0.001, help='per side, fraction of position (0.001 = 0.10%%)')
    ap.add_argument('--seeds', type=int, default=30)
    a = ap.parse_args()

    bars, didx, sig = load(a.history, a.signals)
    dates = [s['date'] for s in sig]
    pool = [s for s in bars if s not in BENCH]

    def cands(filter_fn, mult_fn):
        out = defaultdict(list)
        for day_rec in sig:
            rows = [r for r in day_rec['rows'] if r['size'] != 'IGNORE' and r['move'] and filter_fn(r)]
            rows.sort(key=lambda r: (-r['take'], -r['n'], r['cmf20'] if r['cmf20'] is not None else 9))
            for r in rows:
                out[day_rec['date']].append(dict(sym=r['sym'], move=r['move'], mult=mult_fn(r), w=0.5 if r['size'] == 'HALF' else 1.0))
        return out

    panel_mult = lambda r: TAKE_MULT if r['take'] else PANEL_MULT
    policies = {
        'TAKE only (2+ panels, top 2 by CMF)': cands(lambda r: r['take'] == 1, lambda r: TAKE_MULT),
        'All panel names (TAKE first)': cands(lambda r: True, panel_mult),
    }
    for name in ('Golden Cross', 'Weekly Pick', 'RS Leader', 'Bull Flag', 'Intraday Breakout'):
        policies[name + ' only'] = cands(lambda r, n=name: n in r['panels'].split('+'), lambda r: PANEL_MULT)

    res = dict(config=dict(start=a.start, positions=a.positions, cost_per_side=a.cost, hold=HOLD, first_day=dates[0], last_day=dates[-1],
                           sessions=len(dates), tickers=len(pool)), policies={}, notes=[])
    for stop in (None, 0.15):
        for name, c in policies.items():
            key = name + (' | 15% stop' if stop else ' | no stop')
            curve, trades = simulate(c, bars, didx, dates, a.start, a.positions, a.cost, stop)
            res['policies'][key] = summarize(curve, trades, a.start)
            res['policies'][key]['signal_days'] = sum(1 for d in dates if c.get(d))

    # random entries: same account, same rules, same number of new entries per day cap (2), same stops
    def random_run(seed, stop):
        rng = random.Random(seed)
        c = defaultdict(list)
        for d in dates:
            names = [s for s in pool if d in didx[s] and didx[s][d] >= 60]
            rng.shuffle(names)
            for s in names[:2]:
                mv = atr14(bars[s], didx[s][d])
                if mv:
                    c[d].append(dict(sym=s, move=mv, mult=PANEL_MULT, w=1.0))
        curve, trades = simulate(c, bars, didx, dates, a.start, a.positions, a.cost, stop)
        return summarize(curve, trades, a.start)
    for stop in (None, 0.15):
        runs = [random_run(s, stop) for s in range(a.seeds)]
        fin = sorted(r['final'] for r in runs)
        res['policies']['RANDOM entries | ' + ('15% stop' if stop else 'no stop')] = dict(
            seeds=a.seeds, final_median=fin[len(fin) // 2], final_p10=fin[int(len(fin) * 0.1)], final_p90=fin[int(len(fin) * 0.9)],
            profit_median=round(fin[len(fin) // 2] - a.start, 0), max_drawdown_median=st.median(r['max_drawdown'] for r in runs),
            win_rate_median=st.median(r['win_rate'] for r in runs), years=runs[0]['years'])

    # benchmarks over the same window
    i0 = didx['SPY'][dates[0]]
    i1 = didx['SPY'][dates[-1]]
    spy = bars['SPY']
    res['benchmarks'] = dict(SPY_buy_hold_profit=round(a.start * (spy[i1][4] / spy[i0][1] - 1), 0))
    ew = []
    for s in pool:
        if dates[0] in didx[s] and dates[-1] in didx[s]:
            ew.append(bars[s][didx[s][dates[-1]]][4] / bars[s][didx[s][dates[0]]][1] - 1)
    res['benchmarks']['equal_weight_basket_buy_hold_profit'] = round(a.start * st.mean(ew), 0) if ew else None
    res['benchmarks']['basket_names_in_average'] = len(ew)
    res['notes'] = [
        'Basket = today\'s ticker list, chosen with hindsight: survivors and winners are over-represented (flatters every buy-and-hold and panel result).',
        'Panel signals are replayed from the page\'s own code; earnings flags and company facts are missing for past dates; Intraday Breakout is an end-of-day approximation.',
        'Costs are a guess (0.10% per side). Fills assume the target is hit at the limit price and the next open is available.',
        'Overlapping positions are correlated: several signals fire on the same market-wide dips.',
    ]
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    json.dump(res, open(a.out, 'w'), indent=1)
    print(json.dumps(res, indent=1))


if __name__ == '__main__':
    main()
