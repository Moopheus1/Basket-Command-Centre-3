#!/usr/bin/env python3
"""
One-off long-history download for the panel replay (scripts/history_replay.js).
Writes backtest/history.json in the same bar format as docs/data.json: [ts, open, high, low, close, volume].
Runs in GitHub Actions (history-backtest.yml), where yfinance already works for the daily data job.
Closes are split-adjusted by Yahoo but NOT dividend-adjusted (auto_adjust=False), same as the live dashboard.
A ticker that fails is recorded under "failed" and skipped; the run never aborts on one bad symbol.
"""
import json
import math
import os
import sys
import time
from datetime import datetime, timezone

import yfinance as yf

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
PERIOD = os.environ.get('HISTORY_PERIOD', '7y')
OUT = os.path.join(ROOT, 'backtest', 'history.json')


def read_tickers(path):
    out = []
    for line in open(path):
        line = line.strip().upper()
        if line and not line.startswith('#') and line not in out:
            out.append(line)
    return out


def fin(v):
    return isinstance(v, (int, float)) and math.isfinite(v)


def fetch(sym):
    hist = yf.Ticker(sym).history(period=PERIOD, interval='1d', auto_adjust=False)
    bars = []
    for ts, row in hist.iterrows():
        o, h, l, c, v = row['Open'], row['High'], row['Low'], row['Close'], row['Volume']
        if all(fin(x) and x > 0 for x in (o, h, l, c)):
            bars.append([int(ts.timestamp()), round(float(o), 4), round(float(h), 4), round(float(l), 4),
                         round(float(c), 4), int(v) if fin(v) else 0])
    return bars


def main():
    tickers = read_tickers(os.path.join(ROOT, 'tickers.txt'))
    meta = {}
    p = os.path.join(ROOT, 'docs', 'data.json')
    if os.path.exists(p):
        for s, d in (json.load(open(p)).get('tickers') or {}).items():
            meta[s] = {k: d.get(k) for k in ('name', 'sector', 'industry', 'mcap', 'beta')}
    res, failed = {}, {}
    for i, s in enumerate(tickers):
        for attempt in (1, 2, 3):
            try:
                bars = fetch(s)
                if len(bars) < 60:
                    raise ValueError('only %d bars' % len(bars))
                res[s] = dict(meta.get(s, {}), bars=bars)
                break
            except Exception as e:  # noqa: BLE001
                if attempt == 3:
                    failed[s] = str(e)[:120]
                else:
                    time.sleep(3 * attempt)
        time.sleep(0.25)
        if (i + 1) % 25 == 0:
            print('fetched', i + 1, 'of', len(tickers), 'failed so far', len(failed), flush=True)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(dict(asof=datetime.now(timezone.utc).isoformat(timespec='seconds'), period=PERIOD, tickers=res,
                   failed=failed), open(OUT, 'w'), separators=(',', ':'))
    n = [len(v['bars']) for v in res.values()]
    print('ok', len(res), 'failed', len(failed), 'bars min/median/max', min(n) if n else 0,
          sorted(n)[len(n) // 2] if n else 0, max(n) if n else 0)
    if len(res) < len(tickers) * 0.6:
        print('too many failures'); sys.exit(1)


if __name__ == '__main__':
    main()
