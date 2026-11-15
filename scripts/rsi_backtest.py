#!/usr/bin/env python3
"""
Backtest of the RSI OVERSOLD rules (see rsi_oversold.py) on the basket's own daily bars (docs/data.json).

Prints a report and writes docs/rsi_backtest.json (read by the dashboard panel).
All results are in dollars per $1,000 position. Nothing here changes any panel.

Honest limits, repeated in the output:
  * Only ~430 sessions (Mar 2025 - Nov 2026) are stored. That is one bull-market regime.
  * The ticker list was picked in 2026, so it is full of names that survived and ran (hindsight bias).
  * Fills are at the target price / next open with no slippage or commissions.
  * Signals overlap in time across tickers, so t-statistics are overstated.
"""
import json
import os
import statistics as st
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rsi_oversold as R  # noqa: E402


def pct(v, p):
    v = sorted(v)
    if not v:
        return None
    k = (len(v) - 1) * p
    f = int(k)
    c = min(f + 1, len(v) - 1)
    return v[f] + (v[c] - v[f]) * (k - f)


def summarize(trs):
    """trs = list of (sym, signal_idx, sim_result, size)."""
    if not trs:
        return None
    p = [R.pnl(t) for _, _, t in trs]
    wins = [v for v in p if v > 0]
    losses = [v for v in p if v <= 0]
    hit = sum(1 for _, _, t in trs if t['status'] == 'TP')
    mae = [t['mae'] * R.POS for _, _, t in trs]
    return dict(
        n=len(p), hit_rate=round(hit / len(p), 4),
        avg_pnl=round(sum(p) / len(p), 2), median_pnl=round(st.median(p), 2),
        avg_win=round(sum(wins) / len(wins), 2) if wins else 0.0,
        avg_loss=round(sum(losses) / len(losses), 2) if losses else 0.0,
        profit_factor=round(sum(wins) / -sum(losses), 2) if losses and sum(losses) < 0 else None,
        worst=round(min(p), 2), p5=round(pct(p, 0.05), 2),
        avg_days=round(sum(t['days'] for _, _, t in trs) / len(trs), 2),
        expired=sum(1 for _, _, t in trs if t['status'] == 'EXP'),
        avg_expired_pnl=round(st.mean([R.pnl(t) for _, _, t in trs if t['status'] == 'EXP']), 2)
        if any(t['status'] == 'EXP' for _, _, t in trs) else None,
        mae_median=round(st.median(mae), 2), mae_p10=round(pct(mae, 0.10), 2), mae_worst=round(min(mae), 2),
        underwater_100=round(sum(1 for m in mae if m <= -100) / len(mae), 4),
    )


def main():
    J = json.load(open(R.DATA))
    T = J['tickers']
    S = R.build_series(T, drop_today=False)
    last_session = max(R.day(x['t'][-1]) for x in S.values())
    # complete-trade bound: signal index must leave room for entry + 10 sessions
    sym_n = {s: len(x['c']) for s, x in S.items()}

    raw, dedup, base = [], [], []
    by_date_all = defaultdict(list)          # signal date -> every ticker's simulated next-open trade that day
    n_split_skipped = 0
    for s, x in S.items():
        n = len(x['c'])
        last_open_until = -1
        sig = set(R.crosses(x['rsi']))
        for i in range(R.RSI_N + 1, n - R.HOLD - 1):
            if any(i - 15 <= k <= i + R.HOLD + 1 for k in x['split']):
                if i in sig:
                    n_split_skipped += 1
                continue
            tr = R.simulate(x, i)
            if tr is None or tr['status'] == 'OPEN':
                continue
            d = R.day(x['t'][i])
            by_date_all[d].append((s, i, tr, i in sig))
            if x['rsi'][i] is not None and x['rsi'][i] >= R.RSI_LEVEL:
                base.append((s, i, tr))
            if i in sig:
                raw.append((s, i, tr))
                if i > last_open_until:
                    dedup.append((s, i, tr))
                    last_open_until = tr['exit_idx']

    out = {'data_window': dict(first=min(R.day(x['t'][0]) for x in S.values()), last=last_session,
                               sessions=max(sym_n.values()), tickers=len(S)),
           'rules': dict(rsi='Wilder 14', cross_below=R.RSI_LEVEL, entry='next open', target_pct=R.TP_PCT * 100,
                         max_hold=R.HOLD, stop=None, size_per_trade=R.POS)}
    out['signals_raw'] = len(raw)
    out['signals_split_skipped'] = n_split_skipped
    out['primary'] = summarize(dedup)                # one open trade per ticker at a time
    out['all_days_baseline'] = summarize(base)       # any day the stock was NOT oversold, same rules
    # same-date matched baseline: every other ticker's next-open trade on the same signal date
    matched, diffs = [], []
    for s, i, tr in dedup:
        d = R.day(S[s]['t'][i])
        others = [t for (s2, i2, t, sg) in by_date_all[d] if s2 != s and not sg]
        if len(others) >= 5:
            m = st.mean([R.pnl(t) for t in others])
            mh = sum(1 for t in others if t['status'] == 'TP') / len(others)
            matched.append((m, mh))
            diffs.append(R.pnl(tr) - m)
    if diffs:
        sd = st.pstdev(diffs) if len(diffs) > 1 else 0
        out['vs_same_day_random'] = dict(
            n=len(diffs), signal_avg=out['primary']['avg_pnl'], random_avg=round(st.mean(m for m, _ in matched), 2),
            random_hit_rate=round(st.mean(h for _, h in matched), 4),
            avg_edge=round(st.mean(diffs), 2), t_stat=round(st.mean(diffs) / (sd / len(diffs) ** 0.5), 2) if sd else None)

    # stop variant (StockKit has none; shown so you can see what the no-stop choice is worth)
    stopped = []
    for s, i, _ in dedup:
        tr = R.simulate(S[s], i, stop=0.10)
        if tr and tr['status'] != 'OPEN':
            stopped.append((s, i, tr))
    out['with_10pct_stop'] = summarize(stopped)

    # time split and clustering
    ds = sorted(dedup, key=lambda a: R.day(S[a[0]]['t'][a[1]]))
    half = len(ds) // 2
    out['first_half'] = summarize(ds[:half]); out['second_half'] = summarize(ds[half:])
    by_month = defaultdict(list)
    for a in ds:
        by_month[R.day(S[a[0]]['t'][a[1]])[:7]].append(a)
    out['by_month'] = {m: dict(n=len(v), hit=round(sum(1 for _, _, t in v if t['status'] == 'TP') / len(v), 3),
                               avg=round(st.mean(R.pnl(t) for _, _, t in v), 1)) for m, v in sorted(by_month.items())}
    # breadth: how many tickers crossed below 30 on the same date
    cnt = defaultdict(int)
    for s, i, _ in raw:
        cnt[R.day(S[s]['t'][i])] += 1
    wash = [a for a in ds if cnt[R.day(S[a[0]]['t'][a[1]])] >= 8]
    calm = [a for a in ds if cnt[R.day(S[a[0]]['t'][a[1]])] < 8]
    out['market_wide_washout_days'] = summarize(wash)    # 8+ tickers crossed the same day
    out['idiosyncratic_days'] = summarize(calm)
    out['washout_dates'] = sorted(d for d, c in cnt.items() if c >= 8)
    # concentration
    cs = defaultdict(int)
    for s, _, _ in dedup:
        cs[s] += 1
    out['top_tickers_by_signals'] = sorted(cs.items(), key=lambda kv: -kv[1])[:8]
    pnls = sorted((R.pnl(t) for _, _, t in dedup), reverse=True)
    out['avg_pnl_without_best5'] = round(st.mean(pnls[5:]), 2) if len(pnls) > 10 else None
    out['avg_pnl_without_worst5'] = round(st.mean(pnls[:-5]), 2) if len(pnls) > 10 else None
    # sequential equity (every signal taken, $1,000 each), by exit date
    ev = sorted(((S[s]['t'][t['exit_idx']], R.pnl(t)) for s, _, t in dedup))
    cum = peak = dd = 0.0
    for _, p in ev:
        cum += p; peak = max(peak, cum); dd = min(dd, cum - peak)
    out['equity'] = dict(total_pnl=round(cum, 2), max_drawdown=round(dd, 2))
    opens = defaultdict(int)
    for s, i, t in dedup:
        for j in range(i + 1, t['exit_idx'] + 1):
            opens[S[s]['t'][j]] += 1
    out['peak_concurrent_trades'] = max(opens.values()) if opens else 0
    # worst trades
    worst = sorted(((R.pnl(t), s, R.day(S[s]['t'][i])) for s, i, t in dedup))[:6]
    out['worst_trades'] = [dict(pnl=round(p, 1), sym=s, signal=d) for p, s, d in worst]
    out['notes'] = ['One regime (~430 sessions, mostly a bull market).',
                    'Ticker list chosen in 2026 - survivors and winners (hindsight bias).',
                    'No slippage or commissions; target filled at the limit price.',
                    'Signals overlap across tickers, so t-stats are overstated.']
    json.dump(out, open(R.BT, 'w'), indent=1)
    print(json.dumps(out, indent=1))


if __name__ == '__main__':
    main()
