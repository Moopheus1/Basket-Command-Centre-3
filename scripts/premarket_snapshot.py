#!/usr/bin/env python3
"""Premarket snapshot for the BCC3 Premarket page (docs/premarket.html).

Display-only. Reads tickers.txt, pulls the last 5 days of 1-minute bars INCLUDING
pre-market and after-hours from Yahoo Finance (yfinance, prepost=True), and writes
docs/premarket.json with, per ticker:

  price        last trade price in the data (any session)
  session      "pre" (before 9:30 ET), "regular", or "post" (after 16:00 ET) for that last bar
  last_et      timestamp of that last bar, US Eastern
  prev_close   close of the last REGULAR session before the day being shown
  pm_high/low  high and low of today's 04:00-09:30 ET bars
  (Yahoo reports 0 volume for pre-market bars, so no pre-market volume is recorded.)

It never touches data.json, the panels, TAKE, Weekly Picks or the forward log.
If the download fails outright the old file is left alone and the script exits 1.
"""
import json
import math
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

ET = ZoneInfo("America/New_York")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "premarket.json")


def read_tickers():
    out = []
    with open(os.path.join(ROOT, "tickers.txt"), encoding="utf-8") as f:
        for line in f:
            t = line.strip()
            if t and not t.startswith("#"):
                out.append(t.upper())
    seen, uniq = set(), []
    for t in out:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq


def session_of(ts):
    m = ts.hour * 60 + ts.minute
    if m < 9 * 60 + 30:
        return "pre"
    if m < 16 * 60:
        return "regular"
    return "post"


def num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(v) or math.isinf(v)) else round(v, 4)


def prep(df):
    """Clean one ticker's 1-minute frame and convert its index to US Eastern."""
    if df is None or df.empty:
        return None
    df = df.dropna(subset=["Close"])
    if df.empty:
        return None
    idx = df.index
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    df = df.copy()
    df.index = idx.tz_convert(ET)
    return df


def summarize(df, day):
    """df: prepared 1-minute bars for one ticker; day: the trading date being shown (the newest date
    seen across ALL tickers, so a thinly traded name with no bars yet today is not compared with a
    stale day). Returns a dict. price is None when the ticker has had no trade yet on `day`."""
    mins = df.index.hour * 60 + df.index.minute
    dates = df.index.date

    prior = df[(dates < day) & (mins < 16 * 60)]
    prev_close = num(prior["Close"].iloc[-1]) if not prior.empty else None

    today = df[dates == day]
    if today.empty:
        return {"price": None, "session": None, "last_et": None, "prev_close": prev_close,
                "pm_high": None, "pm_low": None}
    last_ts = today.index[-1]
    pm = today[(today.index.hour * 60 + today.index.minute) < 9 * 60 + 30]
    return {
        "price": num(today["Close"].iloc[-1]),
        "session": session_of(last_ts),
        "last_et": last_ts.strftime("%Y-%m-%d %H:%M"),
        "prev_close": prev_close,
        "pm_high": num(pm["High"].max()) if not pm.empty else None,
        "pm_low": num(pm["Low"].min()) if not pm.empty else None,
    }


def main():
    tickers = read_tickers()
    print(f"[premarket_snapshot] {len(tickers)} tickers")
    try:
        raw = yf.download(
            tickers, period="5d", interval="1m", prepost=True,
            group_by="ticker", auto_adjust=False, progress=False, threads=True,
        )
    except Exception as e:  # network / Yahoo failure: keep the previous file
        print(f"[premarket_snapshot] download failed: {e}")
        return 1
    if raw is None or raw.empty:
        print("[premarket_snapshot] empty download; leaving the previous file alone")
        return 1

    frames = {}
    for t in tickers:
        try:
            frames[t] = prep(raw[t] if len(tickers) > 1 else raw)
        except Exception as e:
            frames[t] = None
            print(f"[premarket_snapshot] {t}: {e}")
    days = [f.index[-1].date() for f in frames.values() if f is not None]
    if not days:
        print("[premarket_snapshot] no usable data; leaving the previous file alone")
        return 1
    day = max(days)

    out, bad = {}, []
    for t in tickers:
        f = frames[t]
        if f is None:
            bad.append(t)
            out[t] = {"error": "no data"}
        else:
            out[t] = summarize(f, day)

    good = [v for v in out.values() if "error" not in v and v.get("price") is not None]
    if len(good) < max(1, len(tickers) // 2):
        print(f"[premarket_snapshot] only {len(good)}/{len(tickers)} tickers returned data; leaving the previous file alone")
        return 1

    newest = max(v["last_et"] for v in good)
    nopm = [t for t, v in out.items() if "error" not in v and v.get("price") is None]
    doc = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "asof_et": newest,
        "asof_date": newest[:10],
        "tickers": out,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(doc, f, separators=(",", ":"))
    print(f"[premarket_snapshot] wrote {len(good)} priced tickers; no trade yet today: {len(nopm)} ({', '.join(nopm[:12])}); no data: {len(bad)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
