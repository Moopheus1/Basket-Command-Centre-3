#!/usr/bin/env python3
"""
OPTIONS FLOW BIAS - end-of-day options positioning per basket ticker, plus its own forward test.

WHAT IT MEASURES (per ticker, once per completed session)
  Source: Yahoo option chains (free, same provider as data.json). Expiries within 45 days.
  Only OUT-OF-THE-MONEY contracts count (OTM calls = upside bets, OTM puts = downside bets).
  In-the-money flow is dropped: it is dominated by hedges, stock replacement and rolls.
  "Unusual" = the contract traded more contracts today than its open interest at the start
  of the day (volume > OI, and volume >= 100). That is the best free proxy for NEW positions
  being opened rather than old ones being closed.
  Premium = volume x last price x 100.

  Side estimate: if Yahoo has a bid/ask for the contract, the last trade's position in the
  spread decides it - near the ask = bought (kept as is), near the bid = SOLD (direction
  flipped: a sold call counts bearish, a sold put bullish), middle = excluded. With no
  bid/ask the raw direction is kept and counted as "side unknown" (side_known shows the share).
  This only looks at the LAST trade of the day, so it is a rough guide, not a real tape read.

  bias    = (bullish unusual premium - bearish unusual premium) / (sum of both), -1 .. +1
  rel     = bias minus the basket's median bias that day. Calls dominate in a bull tape
            (median bias was +0.61 on 30 Sep 2026), so an absolute cut would call most
            names bullish. Labels are therefore RELATIVE to the rest of the basket:
  label   = BULLISH if rel >= +0.30, BEARISH if rel <= -0.30, else NEUTRAL;
            THIN when unusual premium < $250k (too little activity to read anything into)
  act_x   = today's total OTM premium / the ticker's own 20-session median (needs ~5 days
            of history before it shows; rises over time)

WHAT IT CANNOT SEE (read before using)
  - Whether a trade was BOUGHT or SOLD. A big OTM call print can be someone selling calls
    (bearish/neutral). Paid feeds infer side from bid/ask; Yahoo end-of-day data cannot.
  - The other legs of spreads, or the stock hedge behind an option trade.
  - Intraday timing. This is one end-of-day snapshot.

FORWARD TEST
  Every non-THIN reading is appended to docs/flow_log.csv. After 5 and 10 sessions it is
  scored against the stock's own close, and compared with the average move of the whole
  basket over the same window. Results in docs/flow_summary.json. This panel is display-only:
  it does NOT feed TAKE, Weekly Picks or the panel forward log, so the v2 test is untouched.
"""
import csv
import datetime as dt
import json
import math
import os
import statistics
import sys
import time
from zoneinfo import ZoneInfo

import yfinance as yf

ROOT = os.path.join(os.path.dirname(__file__), '..')
DOCS = os.path.join(ROOT, 'docs')
TICKERS = os.path.join(ROOT, 'tickers.txt')
DATA = os.path.join(DOCS, 'data.json')
OUT = os.path.join(DOCS, 'flow.json')
HIST = os.path.join(DOCS, 'flow_history.json')
LOG = os.path.join(DOCS, 'flow_log.csv')
SUM = os.path.join(DOCS, 'flow_summary.json')

RULES_VERSION = 'flow-v1-2026-10-01'   # bump + start a new log if any threshold below changes
MAX_DTE = 45
MIN_UNUSUAL_VOL = 100
REL_CUT = 0.30
THIN_PREMIUM = 250_000
HORIZONS = (5, 10)
ET = ZoneInfo('America/New_York')
_utc = lambda t: dt.datetime.fromtimestamp(t, dt.timezone.utc)
LOG_COLS = ['session', 'rules_version', 'sym', 'label', 'bias', 'rel', 'side_known', 'unusual_call_prem', 'unusual_put_prem',
            'otm_call_prem', 'otm_put_prem', 'pc_vol_ratio', 'act_x', 'close', 'logged_at']


def read_tickers():
    out = []
    with open(TICKERS) as f:
        for line in f:
            s = line.strip().upper()
            if s and not s.startswith('#') and s not in out:
                out.append(s)
    return out


def load_json(p, default):
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return default


def series(data, sym):
    """(dates, closes) from data.json bars [time, open, high, low, close, volume]."""
    bars = ((data.get('tickers') or {}).get(sym) or {}).get('bars') or []
    return ([_utc(b[0]).strftime('%Y-%m-%d') for b in bars], [b[4] for b in bars])


def last_session_date(data):
    """Latest completed session in data.json (SPY bar), as YYYY-MM-DD."""
    for sym in ('SPY', 'QQQ'):
        ds, _ = series(data, sym)
        if ds:
            return ds[-1]
    return None


def num(x):
    try:
        x = float(x)
        return x if math.isfinite(x) else 0.0
    except Exception:
        return 0.0


def chain_reading(sym, today):
    t = yf.Ticker(sym)
    exps = []
    for e in (t.options or []):
        dte = (dt.date.fromisoformat(e) - today).days
        if 0 <= dte <= MAX_DTE:
            exps.append(e)
    if not exps:
        return {'error': 'no listed options within 45 days'}
    spot = None
    try:
        spot = num(t.fast_info.get('lastPrice'))
    except Exception:
        pass
    agg = dict(otm_call_prem=0.0, otm_put_prem=0.0, unusual_call_prem=0.0, unusual_put_prem=0.0,
               call_vol=0.0, put_vol=0.0, bull=0.0, bear=0.0, side_known=0.0, side_total=0.0)
    top = []
    for e in exps:
        ch = None
        for attempt in range(3):
            try:
                ch = t.option_chain(e)
                break
            except Exception:
                time.sleep(2 + attempt * 3)
        if ch is None:
            continue
        if not spot:
            spot = num(getattr(ch, 'underlying', {}).get('regularMarketPrice') if hasattr(ch, 'underlying') else 0)
        for kind, df in (('C', ch.calls), ('P', ch.puts)):
            if df is None or df.empty:
                continue
            for r in df.itertuples(index=False):
                vol, oi, px, k = num(r.volume), num(r.openInterest), num(r.lastPrice), num(r.strike)
                bid, ask = num(getattr(r, 'bid', 0)), num(getattr(r, 'ask', 0))
                if vol <= 0 or px <= 0 or not spot:
                    continue
                if kind == 'C':
                    agg['call_vol'] += vol
                else:
                    agg['put_vol'] += vol
                otm = (k > spot) if kind == 'C' else (k < spot)
                if not otm:
                    continue
                prem = vol * px * 100
                agg['otm_call_prem' if kind == 'C' else 'otm_put_prem'] += prem
                if vol >= MIN_UNUSUAL_VOL and vol > oi:
                    agg['unusual_call_prem' if kind == 'C' else 'unusual_put_prem'] += prem
                    side = '?'
                    if bid > 0 and ask > bid:
                        pos = (px - bid) / (ask - bid)
                        side = 'B' if pos >= 0.6 else ('S' if pos <= 0.4 else 'M')
                    agg['side_total'] += prem
                    if side != '?':
                        agg['side_known'] += prem
                    if side != 'M':
                        bullish = (kind == 'C') == (side != 'S')
                        agg['bull' if bullish else 'bear'] += prem
                    top.append({'type': kind, 'side': side, 'strike': k, 'exp': e, 'vol': int(vol), 'oi': int(oi), 'prem': round(prem)})
    if not spot:
        return {'error': 'no spot price'}
    u = agg['unusual_call_prem'] + agg['unusual_put_prem']
    bb = agg['bull'] + agg['bear']
    bias = (agg['bull'] - agg['bear']) / bb if bb > 0 else 0.0
    label = 'THIN' if u < THIN_PREMIUM else None   # BULLISH/BEARISH/NEUTRAL set later, relative to the basket
    top.sort(key=lambda x: -x['prem'])
    return {
        'spot': round(spot, 4), 'label': label, 'bias': round(bias, 3),
        'side_known': round(agg['side_known'] / agg['side_total'], 2) if agg['side_total'] > 0 else None,
        'unusual_call_prem': round(agg['unusual_call_prem']), 'unusual_put_prem': round(agg['unusual_put_prem']),
        'otm_call_prem': round(agg['otm_call_prem']), 'otm_put_prem': round(agg['otm_put_prem']),
        'pc_vol_ratio': round(agg['put_vol'] / agg['call_vol'], 3) if agg['call_vol'] > 0 else None,
        'expiries': len(exps), 'top': top[:3],
    }


def score(data, session_idx_by_sym):
    """Score logged rows whose 5/10-session windows have closed; compare with basket average."""
    if not os.path.exists(LOG):
        return None
    with open(LOG) as f:
        rows = [r for r in csv.DictReader(f) if r.get('rules_version') == RULES_VERSION]
    dates, closes = {}, {}
    for s in (data.get('tickers') or {}):
        ds, cs = series(data, s)
        if ds:
            dates[s], closes[s] = ds, cs

    def fwd(sym, session, h):
        ds = dates.get(sym)
        if not ds or session not in ds:
            return None
        i = ds.index(session)
        if i + h >= len(ds):
            return None
        c0, c1 = closes[sym][i], closes[sym][i + h]
        return (c1 / c0 - 1) * 100 if c0 else None

    basket_cache = {}

    def basket(session, h):
        k = (session, h)
        if k not in basket_cache:
            v = [x for x in (fwd(s, session, h) for s in closes) if x is not None]
            basket_cache[k] = statistics.mean(v) if v else None
        return basket_cache[k]

    out = {'rules_version': RULES_VERSION, 'logged_rows': len(rows),
           'logged_sessions': len({r['session'] for r in rows}), 'by_label': {}}
    for h in HORIZONS:
        for lab in ('BULLISH', 'BEARISH', 'NEUTRAL'):
            rets, excess = [], []
            for r in rows:
                if r['label'] != lab:
                    continue
                a, b = fwd(r['sym'], r['session'], h), basket(r['session'], h)
                if a is None or b is None:
                    continue
                rets.append(a)
                excess.append(a - b)
            if not rets:
                continue
            sign = -1 if lab == 'BEARISH' else 1   # bearish "works" if the stock lags
            out['by_label'].setdefault(lab, {})[f'{h}d'] = {
                'n': len(rets),
                'avg_return_pct': round(statistics.mean(rets), 2),
                'avg_vs_basket_pct': round(statistics.mean(excess), 2),
                'right_direction_vs_basket_pct': round(100 * sum(1 for e in excess if sign * e > 0) / len(excess), 1),
            }
    b10 = out['by_label'].get('BULLISH', {}).get('10d', {})
    if b10.get('n', 0) < 40:
        out['verdict'] = f"NOT ENOUGH DATA YET - {b10.get('n', 0)}/40 scored BULLISH readings at 10 sessions"
    elif b10['avg_vs_basket_pct'] >= 1.0 and b10['right_direction_vs_basket_pct'] >= 55:
        out['verdict'] = 'PASS - BULLISH flow beat the basket by 1+ point on average and in 55%+ of cases'
    else:
        out['verdict'] = 'FAIL - BULLISH flow did not beat the basket by enough to be worth using'
    out['success_test'] = ('BULLISH readings must beat the basket average over 10 sessions by >= 1.0 point on '
                           'average AND in >= 55% of cases, on >= 40 scored readings. Fixed 1 Oct 2026.')
    return out


def main():
    force = '--force' in sys.argv
    now = dt.datetime.now(ET)
    data = load_json(DATA, {})
    session = last_session_date(data)
    prev = load_json(OUT, {})
    if not force:
        if now.hour * 60 + now.minute < 16 * 60 + 20 and session == now.strftime('%Y-%m-%d'):
            print('Session not closed yet - skipping'); return
        if prev.get('session') == session:
            print(f'Flow already recorded for {session} - skipping'); return

    tickers = read_tickers()
    today = now.date()
    hist = load_json(HIST, {})
    readings, t0 = {}, time.time()
    for i, sym in enumerate(tickers):
        try:
            r = chain_reading(sym, today)
        except Exception as e:
            r = {'error': str(e)[:120]}
        if 'error' not in r:
            h = [x for x in hist.get(sym, []) if x.get('session') != session]
            past = [x['otm_prem'] for x in h[-20:] if x.get('otm_prem')]
            tot = r['otm_call_prem'] + r['otm_put_prem']
            r['act_x'] = round(tot / statistics.median(past), 2) if len(past) >= 5 and statistics.median(past) > 0 else None
            h.append({'session': session, 'otm_prem': tot, 'bias': r['bias']})
            hist[sym] = h[-40:]
        readings[sym] = r
        time.sleep(0.4)
        if (i + 1) % 20 == 0:
            print(f'{i + 1}/{len(tickers)} done, {time.time() - t0:.0f}s')

    live = [r for r in readings.values() if 'error' not in r and r['label'] != 'THIN']
    med = statistics.median([r['bias'] for r in live]) if live else 0.0
    for r in live:
        r['rel'] = round(r['bias'] - med, 3)
        r['label'] = 'BULLISH' if r['rel'] >= REL_CUT else ('BEARISH' if r['rel'] <= -REL_CUT else 'NEUTRAL')

    ok = sum(1 for r in readings.values() if 'error' not in r)
    if ok < len(tickers) * 0.5:
        print(f'Only {ok}/{len(tickers)} chains fetched - not writing (likely rate-limited)'); sys.exit(1)

    with open(OUT, 'w') as f:
        json.dump({'session': session, 'rules_version': RULES_VERSION, 'median_bias': round(med, 3),
                   'generated': dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat(timespec='seconds') + 'Z', 'tickers': readings}, f, separators=(',', ':'))
    with open(HIST, 'w') as f:
        json.dump(hist, f, separators=(',', ':'))

    new = not os.path.exists(LOG)
    logged_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat(timespec='seconds') + 'Z'
    with open(LOG, 'a', newline='') as f:
        w = csv.writer(f)
        if new:
            w.writerow(LOG_COLS)
        for sym, r in readings.items():
            if 'error' in r or r['label'] == 'THIN':
                continue
            closes = series(data, sym)[1]
            w.writerow([session, RULES_VERSION, sym, r['label'], r['bias'], r['rel'], r['side_known'], r['unusual_call_prem'], r['unusual_put_prem'],
                        r['otm_call_prem'], r['otm_put_prem'], r['pc_vol_ratio'], r['act_x'],
                        closes[-1] if closes else '', logged_at])

    summary = score(data, None)
    if summary:
        summary['updated'] = logged_at
        with open(SUM, 'w') as f:
            json.dump(summary, f, indent=1)
    labels = {}
    for r in readings.values():
        labels[r.get('label', 'ERROR')] = labels.get(r.get('label', 'ERROR'), 0) + 1
    print(f'Session {session}: {ok}/{len(tickers)} chains, labels {labels}, {time.time() - t0:.0f}s')


if __name__ == '__main__':
    main()
