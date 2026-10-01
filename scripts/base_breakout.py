#!/usr/bin/env python3
"""
BASE & BREAKOUT WATCH - "a strong stock rests, then launches again".
Display-only panel: it never feeds TAKE, Weekly Picks or the v2 forward log.

Rules (fixed 1 Oct 2026 - change them and you must bump RULES_VERSION and start a new log):
  Checklist, on completed daily bars
    M  Market    SPY close > SPY EMA50                       (a red flag, not a filter: shown, not gated)
    L  Leader    63-session return vs SPY in the basket's top 30% that day
    A  ADR%      20-session average of (high/low - 1) >= 5%
    T  Trend     close > EMA8 > EMA21 > EMA50, EMA21 and EMA50 rising vs 5 sessions ago,
                 close above BOTH the 200 EMA and the 200 SMA
    W  Weekly    close > 30-week (150-session) SMA, and that SMA rising vs 20 sessions ago
  Base, in the 5-20 sessions before the bar being judged (tightest window that passes):
    prior move  >= +25% from the 60-session low before the base to the base high
    tight       base range <= 2.5 x ADR and <= 20%
    holds 21    every base close >= EMA21 x 0.98
    dry volume  base average volume below the 50-session average
  Pivot = highest high of the base.
  Trigger      close > pivot, volume >= 1.5 x 50-session average, close > open,
               close in the top 25% of the day's range
  Red flags shown: market below EMA50, earnings within 5 days, entry gap > 3% above the pivot.

Backtest on this basket's own data (Dec 2025 - Sep 2026, 19 triggers, entry next open, market gate on):
  1.5 x ATR target hit within 20 sessions 83.3% vs 64.1% for random entries (p = 0.07 - not conclusive);
  thesis exit (stop = breakout-day low, exit on a close below EMA21 or at 40 sessions) averaged +1.46R
  vs +0.20R random, win rate 47%. Without the two best trades (HYMC +11.6R, ARM +7.1R) the other 15
  averaged +0.41R - the edge is a few big winners, which is how this style is meant to work, and also
  exactly what a short sample can fake.
1000-session check (Oct 2022 - Sep 2026, 54 triggers, split-adjusted Yahoo bars): vs random ADR>=5% names on the
  same days, a 5%+ run within 20 sessions 85% vs 83% (no edge); with identical stops WORSE (+5% before -5%:
  37% vs 49%; 1.5xATR race 46% vs 52%). Median 20-session return +6.6% vs +3.7% for checklist-only; the mean
  (+17%) is carried by five trades. 2023 and 2024 averaged negative.

Outputs
  docs/bb.json         today's state: setups (checklist + base), confirmed triggers, live provisional triggers
  docs/bb_log.csv      after each close: every setup and trigger, once per session
  docs/bb_summary.json triggers scored after 40 sessions, against random entries on the same dates
"""
import csv
import datetime as dt
import json
import os
import statistics as st
import sys
from zoneinfo import ZoneInfo

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
DOCS = os.path.join(ROOT, 'docs')
DATA = os.path.join(DOCS, 'data.json')
OUT = os.path.join(DOCS, 'bb.json')
LOG = os.path.join(DOCS, 'bb_log.csv')
SUM = os.path.join(DOCS, 'bb_summary.json')
RULES_VERSION = 'bb-v1-2026-10-01'
ET = ZoneInfo('America/New_York')
OPEN_MIN, CLOSE_MIN = 9 * 60 + 30, 16 * 60
ETF = {'SPY', 'QQQ', 'IWM', 'VOO', 'SMH', 'IGV', 'HACK', 'BUG', 'BOT', 'BOTT', 'EWY', 'DRAM'}
LOG_COLS = ['session', 'rules_version', 'sym', 'kind', 'close', 'pivot', 'base_days', 'base_range_pct',
            'adr_pct', 'stop', 'market_above_ema50', 'days_to_earnings', 'logged_at']

day = lambda t: dt.datetime.fromtimestamp(t, dt.timezone.utc).date().isoformat()


def ema_s(c, n):
    k = 2 / (n + 1); out = [c[0]]
    for x in c[1:]:
        out.append(x * k + out[-1] * (1 - k))
    return out


def sma_s(c, n):
    out, s = [], 0.0
    for i, x in enumerate(c):
        s += x
        if i >= n:
            s -= c[i - n]
        out.append(s / n if i >= n - 1 else None)
    return out


def series(bars):
    t = [day(x[0]) for x in bars]
    o, h, l, c, v = ([x[i] for x in bars] for i in range(1, 6))
    tr = [h[0] - l[0]] + [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, len(bars))]
    atr = [tr[0]]
    for x in tr[1:]:
        atr.append(atr[-1] + (x - atr[-1]) / 14)
    adr = [None] * len(bars)
    for i in range(19, len(bars)):
        adr[i] = st.mean(h[j] / l[j] - 1 for j in range(i - 19, i + 1) if l[j] > 0) * 100
    return dict(t=t, o=o, h=h, l=l, c=c, v=v, atr=atr, adr=adr, e8=ema_s(c, 8), e21=ema_s(c, 21), e50=ema_s(c, 50),
                e200=ema_s(c, 200), s200=sma_s(c, 200), s150=sma_s(c, 150), v50=sma_s(v, 50))


def checklist(x, i, spy, k, is_leader):
    c = x['c']
    return {
        'leader': is_leader,
        'adr': x['adr'][i] is not None and x['adr'][i] >= 5,
        'stacked': c[i] > x['e8'][i] > x['e21'][i] > x['e50'][i] and x['e21'][i] > x['e21'][i - 5] and x['e50'][i] > x['e50'][i - 5],
        'above200': c[i] > x['e200'][i] and x['s200'][i] is not None and c[i] > x['s200'][i],
        'weekly': x['s150'][i] is not None and c[i] > x['s150'][i] and x['s150'][i] > x['s150'][i - 20],
    }


def find_base(x, i):
    """Tightest qualifying base in the 5-20 bars ending at i-1 (bar i may be the breakout)."""
    h, l, c, v = x['h'], x['l'], x['c'], x['v']
    adr = x['adr'][i]
    if adr is None or x['v50'][i - 1] is None:
        return None
    best = None
    for n in range(5, 21):
        a, z = i - n, i - 1
        if a < 61:
            break
        hi, lo = max(h[a:z + 1]), min(l[a:z + 1])
        rng = (hi - lo) / hi * 100
        if rng > min(20, 2.5 * adr):
            continue
        if any(c[j] < x['e21'][j] * 0.98 for j in range(a, z + 1)):
            continue
        if st.mean(v[a:z + 1]) >= x['v50'][z]:
            continue
        pre_lo = min(l[a - 60:a])
        if hi / pre_lo - 1 < 0.25:
            continue
        if best is None or rng < best['range_pct']:
            best = dict(days=n, pivot=round(hi, 4), base_low=round(lo, 4), range_pct=round(rng, 2),
                        prior_move_pct=round((hi / pre_lo - 1) * 100, 1))
    return best


def is_trigger(x, i, base, vol_scale=1.0):
    h, l, c, o, v = x['h'], x['l'], x['c'], x['o'], x['v']
    rng = h[i] - l[i]
    return (c[i] > base['pivot'] and v[i] * vol_scale >= 1.5 * x['v50'][i] and c[i] > o[i]
            and rng > 0 and (c[i] - l[i]) / rng >= 0.75)


def leader_set(S, spy, date):
    k = spy['t'].index(date)
    spy_r = spy['c'][k] / spy['c'][k - 63] - 1
    vals = []
    for s, x in S.items():
        if date not in x['idx']:   # SPY included (excess 0), same as the backtest
            continue
        j = x['idx'][date]
        if j >= 63:
            vals.append((x['c'][j] / x['c'][j - 63] - 1 - spy_r, s))
    vals.sort(reverse=True)
    return {s for _, s in vals[:max(1, int(len(vals) * 0.30))]}


def outcome(x, i, stop):
    """Entry next open. A) 1.5xATR hit in 20 sessions (no stop). B) stop / close<EMA21 / 40 sessions."""
    o, h, l, c = x['o'], x['h'], x['l'], x['c']
    if i + 41 > len(c) - 1:
        return None
    e = o[i + 1]; tgt = e + 1.5 * x['atr'][i]
    hit = any(h[j] >= tgt for j in range(i + 1, i + 21))
    risk = e - stop
    if risk <= 0:
        return None
    exitp = None
    for j in range(i + 1, i + 41):
        if l[j] <= stop:
            exitp = min(o[j], stop) if j > i + 1 else stop; break
        if c[j] < x['e21'][j]:
            exitp = c[j]; break
    if exitp is None:
        exitp = c[i + 40]
    return dict(hit=hit, R=(exitp - e) / risk, ret=(exitp / e - 1) * 100)


def main():
    now = dt.datetime.now(ET)
    J = json.load(open(DATA))
    T = J.get('tickers') or {}
    raw_spy = (T.get('SPY') or {}).get('bars') or []
    if len(raw_spy) < 260:
        print('SPY history too short'); return
    today = now.date().isoformat()
    minutes = now.hour * 60 + now.minute
    partial = day(raw_spy[-1][0]) == today and minutes < CLOSE_MIN + 20
    frac = min(1.0, max(0.05, (minutes - OPEN_MIN) / (CLOSE_MIN - OPEN_MIN))) if partial else 1.0

    S, live = {}, {}
    for s, d in T.items():
        b = (d or {}).get('bars') or []
        if partial and b and day(b[-1][0]) == today:
            live[s] = b[-1]; b = b[:-1]
        if len(b) < 260:
            continue
        x = series(b); x['idx'] = {t: i for i, t in enumerate(x['t'])}
        x['nextE'] = d.get('nextEarnings'); x['name'] = d.get('name'); x['sector'] = d.get('sector')
        S[s] = x
    spy = S['SPY']; session = spy['t'][-1]; k = len(spy['t']) - 1
    market_ok = spy['c'][k] > spy['e50'][k]
    leaders = leader_set(S, spy, session)
    d_today = now.date()

    setups, triggers, live_trig = [], [], []
    for s, x in S.items():
        if s == 'SPY' or x['t'][-1] != session:
            continue
        i = len(x['c']) - 1
        chk = checklist(x, i, spy, k, s in leaders)
        ne = x['nextE']
        dte = (dt.date.fromisoformat(ne[:10]) - d_today).days if ne else None
        base = find_base(x, i)
        stop_for = lambda j: x['l'][j]
        row = dict(sym=s, name=x['name'], sector=x['sector'], close=round(x['c'][i], 4), adr_pct=round(x['adr'][i] or 0, 2),
                   checks=chk, n_checks=sum(chk.values()), days_to_earnings=dte,
                   ema8=round(x['e8'][i], 4), ema21=round(x['e21'][i], 4), ema50=round(x['e50'][i], 4))
        if all(chk.values()) and base and is_trigger(x, i, base):
            row.update(base=base, stop=round(stop_for(i), 4), stop_pct=round((x['c'][i] - x['l'][i]) / x['c'][i] * 100, 2))
            triggers.append(row)
        # setup = checklist all true and a base ENDING TODAY, so the next bar can break its pivot.
        # find_base(x, j) looks at bars before j and reads ADR at j, so pad one bar with today's ADR.
        y = dict(x); y['adr'] = x['adr'] + [x['adr'][i]]; y['v50'] = x['v50'] + [x['v50'][i]]
        nb = find_base(y, i + 1)
        if all(chk.values()) and nb and x['c'][i] <= nb['pivot']:
            r2 = dict(row); r2.update(base=nb, to_pivot_pct=round((nb['pivot'] / x['c'][i] - 1) * 100, 2))
            setups.append(r2)
            if s in live:
                lb = live[s]  # [t, o, h, l, c, v] partial bar
                lx = dict(o=x['o'] + [lb[1]], h=x['h'] + [lb[2]], l=x['l'] + [lb[3]], c=x['c'] + [lb[4]], v=x['v'] + [lb[5]], v50=x['v50'])
                if lx['c'][-1] > nb['pivot']:
                    rng = lb[2] - lb[3]
                    pace = lb[5] / frac
                    live_trig.append(dict(sym=s, price=lb[4], pivot=nb['pivot'], above_pivot_pct=round((lb[4] / nb['pivot'] - 1) * 100, 2),
                                          vol_pace_x=round(pace / x['v50'][i], 2) if x['v50'][i] else None,
                                          close_in_range=round((lb[4] - lb[3]) / rng, 2) if rng > 0 else None,
                                          green=lb[4] > lb[1], day_low=lb[3],
                                          looks_valid=bool(pace >= 1.5 * x['v50'][i] and lb[4] > lb[1] and rng > 0 and (lb[4] - lb[3]) / rng >= 0.75)))
    setups.sort(key=lambda r: r['to_pivot_pct'])
    out = dict(rules_version=RULES_VERSION, session=session,   # no timestamp: an unchanged result means no commit
               market=dict(spy=round(spy['c'][k], 2), ema50=round(spy['e50'][k], 2), ema21=round(spy['e21'][k], 2), above_ema50=market_ok),
               partial_session=partial, setups=setups, triggers=triggers, live=live_trig)
    json.dump(out, open(OUT, 'w'), separators=(',', ':'))

    # ---- log once per completed session, then score ----
    if not partial:
        done = set()
        if os.path.exists(LOG):
            with open(LOG) as f:
                done = {(r['session'], r['sym'], r['kind']) for r in csv.DictReader(f)}
        new = not os.path.exists(LOG)
        stamp = dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')
        with open(LOG, 'a', newline='') as f:
            w = csv.writer(f)
            if new:
                w.writerow(LOG_COLS)
            for kind, rows in (('TRIGGER', triggers), ('SETUP', setups)):
                for r in rows:
                    if (session, r['sym'], kind) in done:
                        continue
                    w.writerow([session, RULES_VERSION, r['sym'], kind, r['close'], r['base']['pivot'], r['base']['days'],
                                r['base']['range_pct'], r['adr_pct'], r.get('stop', ''), int(market_ok),
                                '' if r['days_to_earnings'] is None else r['days_to_earnings'], stamp])
        score(S)
    print(f"session {session} partial={partial} market_above_ema50={market_ok} setups={[r['sym'] for r in setups]} "
          f"triggers={[r['sym'] for r in triggers]} live={[r['sym'] for r in live_trig]}")


def score(S):
    with open(LOG) as f:
        rows = [r for r in csv.DictReader(f) if r['rules_version'] == RULES_VERSION and r['kind'] == 'TRIGGER']
    sig, rnd_dates = [], set()
    for r in rows:
        x = S.get(r['sym'])
        if not x or r['session'] not in x['idx']:
            continue
        i = x['idx'][r['session']]
        o = outcome(x, i, x['l'][i])
        if o:
            sig.append(o); rnd_dates.add(r['session'])
    rnd = []
    for s, x in S.items():
        if s == 'SPY':
            continue
        for d in rnd_dates:
            if d in x['idx']:
                o = outcome(x, x['idx'][d], x['l'][x['idx'][d]])
                if o:
                    rnd.append(o)
    agg = lambda a: dict(n=len(a), hit_pct=round(100 * sum(o['hit'] for o in a) / len(a), 1), avg_R=round(st.mean(o['R'] for o in a), 2),
                         median_R=round(st.median(o['R'] for o in a), 2), win_pct=round(100 * sum(o['R'] > 0 for o in a) / len(a), 1),
                         avg_ret_pct=round(st.mean(o['ret'] for o in a), 2)) if a else dict(n=0)
    out = dict(rules_version=RULES_VERSION, logged_triggers=len(rows), scored=len(sig), triggers=agg(sig), random_same_dates=agg(rnd),
               success_test='Scored triggers must beat random entries on the same dates by >= 0.30R average AND >= 10 points of '
                            '1.5xATR hit rate, on >= 20 scored triggers. Fixed 1 Oct 2026.',
               backtest='Dec 2025 - Sep 2026: 19 triggers, hit 83.3% vs 64.1% random; +1.46R vs +0.20R; +0.41R without the best two trades.',
               updated=dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'))
    if len(sig) < 20:
        out['verdict'] = f'NOT ENOUGH DATA YET - {len(sig)}/20 scored triggers (each needs 40 sessions after the breakout)'
    else:
        t, r = out['triggers'], out['random_same_dates']
        ok = t['avg_R'] - r['avg_R'] >= 0.30 and t['hit_pct'] - r['hit_pct'] >= 10
        out['verdict'] = ('PASS' if ok else 'FAIL') + f" - triggers {t['avg_R']:+.2f}R / {t['hit_pct']}% vs random {r['avg_R']:+.2f}R / {r['hit_pct']}%"
    json.dump(out, open(SUM, 'w'), indent=1)


if __name__ == '__main__':
    main()
