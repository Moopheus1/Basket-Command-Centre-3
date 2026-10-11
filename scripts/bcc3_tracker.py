#!/usr/bin/env python3
"""
Basket Command Centre 3 -- dashboard panel forward-test tracker (v3, built 2026-10-11).

Replaces the Basket Command Centre 2 tracker. Membership is NOT re-derived here: it is read from
the dashboard's own after-close logs, so each row is exactly what the page showed that day:
  forward_log.csv  Golden Cross, RS Leader, Bull Flag, Intraday Breakout, Weekly Pick, TAKE (2+ panels)  (from 2026-09-25)
  bb_log.csv       Base & Breakout: Setup / Trigger / Washout                                             (from 2026-10-01)
  flow_log.csv     Options Flow: Bullish / Bearish                                                        (from 2026-10-01)
Sessions before each log started (back to 2026-09-10) come from the embedded BACKFILL table, rebuilt once
on 2026-10-11 by running the dashboard's own rules on data.json truncated to each past session. Those rows
are marked BACKFILL (BACKFILL-APPROX for Weekly Pick and TAKE, whose rebuild matched the live log less well).
Options Flow cannot be backfilled.

Measurement is unchanged from the BCC2 tracker: entry = close on the signal day, forward close-to-close return
after 5 / 10 / 20 sessions, UP/DOWN/FLAT at +/-0.5%, the same return vs SPY, and a next-open entry net of
10 bps round trip. Raw drift after a flag -- no stop, target or sizing. Not a record of real trades.
"""
import csv
import io
import json
import math
import os
import sys
import urllib.request
from datetime import datetime, timezone

import openpyxl
from openpyxl import Workbook

BASE = "https://moopheus1.github.io/Basket-Command-Centre-3/"
if "--base" in sys.argv:
    BASE = sys.argv[sys.argv.index("--base") + 1]
TRACKER_PATH = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "bcc3_tracker.xlsx"
EXPORT_DIR = sys.argv[sys.argv.index("--export") + 1] if "--export" in sys.argv else None

HORIZONS = [5, 10, 20]
UP_THRESH, DOWN_THRESH = 0.5, -0.5
SLIPPAGE_BPS_PER_SIDE = 5
BENCH = "SPY"
PANEL_LOG_START = "2026-09-25"
BB_LOG_START = "2026-10-01"

PANEL_CATS = {"Golden Cross": "Golden Cross", "RS Leader": "RS Leader", "Bull Flag": "Bull Flag",
              "Intraday Breakout": "Intraday Breakout", "Weekly Pick": "Weekly Pick"}
TAKE_CAT = "TAKE (2+ panels)"
BB_CATS = {"SETUP": "Base & Breakout: Setup", "TRIGGER": "Base & Breakout: Trigger", "WASHOUT": "Base & Breakout: Washout"}
FLOW_CATS = {"BULLISH": "Options Flow: Bullish", "BEARISH": "Options Flow: Bearish"}
CATEGORIES = (list(PANEL_CATS.values()) + [TAKE_CAT] + list(BB_CATS.values()) + list(FLOW_CATS.values()))
APPROX = {"Weekly Pick", TAKE_CAT}

LOG_HDR = ["Date", "Category", "Ticker", "EntryPrice"]
for _h in HORIZONS:
    LOG_HDR += [f"Fwd{_h}D_Ret%", f"Fwd{_h}D_Status"]
LOG_HDR += ["Notes"]
for _h in HORIZONS:
    LOG_HDR += [f"Fwd{_h}D_vsSPY%", f"Fwd{_h}D_NextOpenNet%"]

# {date: {category: [tickers]}} -- rebuilt once on 2026-10-11, never recomputed.
BACKFILL = {
    "2026-09-10": {"Base & Breakout: Washout": ["OKLO", "PL", "TE"], "Golden Cross": ["IGV", "MSFT", "NOW", "PFE", "PLTR", "PYPL", "SPOT", "TEAM"], "Weekly Pick": ["MRNA"]},
    "2026-09-11": {"Base & Breakout: Setup": ["MRNA"], "Base & Breakout: Washout": ["OKLO", "PL", "TE"], "Golden Cross": ["CRM", "IGV", "MSFT", "NOW", "PFE", "PLTR", "PYPL", "SPOT", "TEAM"], "Intraday Breakout": ["MRNA"], "TAKE (2+ panels)": ["MRNA"], "Weekly Pick": ["MRNA"]},
    "2026-09-14": {"Base & Breakout: Setup": ["CRCL", "HOOD", "MRNA", "TEAM"], "Base & Breakout: Trigger": ["CRWD"], "Base & Breakout: Washout": ["OKLO", "PL", "TE"], "Golden Cross": ["CRM", "IGV", "MSFT", "NOW", "PFE", "PLTR", "PYPL", "SPOT", "TEAM"], "RS Leader": ["BUG", "COIN", "HACK", "NET", "TEAM"], "TAKE (2+ panels)": ["TEAM"], "Weekly Pick": ["MRNA"]},
    "2026-09-15": {"Base & Breakout: Setup": ["MRNA", "TEAM"], "Base & Breakout: Washout": ["APLD", "OKLO", "PL", "TE"], "Golden Cross": ["CRM", "IGV", "MSFT", "NOW", "PFE", "PLTR", "PYPL", "SPOT", "TEAM"], "RS Leader": ["HACK", "META", "S", "SPOT"], "TAKE (2+ panels)": ["SPOT"], "Weekly Pick": ["BUG", "MRNA"]},
    "2026-09-16": {"Base & Breakout: Setup": ["MRNA", "NET"], "Base & Breakout: Washout": ["HYMC", "INDI", "OKLO", "PL", "TE"], "Golden Cross": ["CRM", "MSFT", "NOW", "PFE", "PLTR", "PYPL", "SPOT", "TEAM", "ZS"], "RS Leader": ["DELL", "META"], "Weekly Pick": ["BE", "MRNA"]},
    "2026-09-17": {"Base & Breakout: Setup": ["MRNA", "NET"], "Base & Breakout: Washout": ["OKLO", "PL", "TE"], "Golden Cross": ["CRM", "MSFT", "NOW", "PFE", "PLTR", "PYPL", "SPOT", "TEAM", "ZS"], "Weekly Pick": ["BUG", "CRWD"]},
    "2026-09-18": {"Base & Breakout: Setup": ["MRNA", "NET"], "Base & Breakout: Washout": ["OKLO", "PL", "TE"], "Bull Flag": ["BUG", "HACK"], "Golden Cross": ["CRM", "MSFT", "NOW", "PFE", "PLTR", "SPOT", "TEAM", "ZS"], "TAKE (2+ panels)": ["BUG", "HACK"], "Weekly Pick": ["BUG", "HACK"]},
    "2026-09-21": {"Base & Breakout: Setup": ["NET"], "Base & Breakout: Washout": ["OKLO", "PL", "TE"], "Golden Cross": ["CRM", "MSFT", "NOW", "PFE", "PLTR", "SPOT", "TEAM", "ZS"], "Intraday Breakout": ["AMD", "ARM", "MRNA"], "TAKE (2+ panels)": ["ARM"], "Weekly Pick": ["ARM", "CRWD"]},
    "2026-09-22": {"Base & Breakout: Setup": ["NET"], "Base & Breakout: Washout": ["OKLO", "PL", "TE"], "Golden Cross": ["CRM", "MSFT", "NOW", "PFE", "PLTR", "QCOM", "SPOT", "TEAM", "ZS"], "Intraday Breakout": ["MRNA", "MU"], "TAKE (2+ panels)": ["ZS"], "Weekly Pick": ["INTC", "ZS"]},
    "2026-09-23": {"Base & Breakout: Setup": ["NET"], "Base & Breakout: Washout": ["OKLO", "PL", "TE"], "Golden Cross": ["CRM", "MSFT", "NOW", "PFE", "PLTR", "QCOM", "SPOT", "ZS"], "RS Leader": ["MRNA", "NET"], "Weekly Pick": ["MU", "TSM"]},
    "2026-09-24": {"Base & Breakout: Setup": ["AXTI"], "Base & Breakout: Washout": ["OKLO", "PL", "TE"], "Golden Cross": ["CRM", "META", "MSFT", "NOW", "PFE", "PLTR", "QCOM", "SPOT", "ZS"], "Intraday Breakout": ["MRNA"], "TAKE (2+ panels)": ["MRNA"], "Weekly Pick": ["KLAC", "MRNA"]},
    "2026-09-25": {"Base & Breakout: Setup": ["AEHR", "AXTI", "BE", "COIN", "HOOD", "LITE", "NET"], "Base & Breakout: Washout": ["INDI", "OKLO", "PL", "TE"]},
    "2026-09-28": {"Base & Breakout: Setup": ["AEHR", "CRWD", "MRNA", "NET"], "Base & Breakout: Washout": ["HYMC", "OKLO", "PL", "TE"]},
    "2026-09-29": {"Base & Breakout: Setup": ["AEHR", "AXTI", "CRWD", "LITE", "MRNA", "NET", "S"], "Base & Breakout: Washout": ["HYMC", "INDI", "OKLO", "PL", "TE"]},
    "2026-09-30": {"Base & Breakout: Setup": ["AEHR", "AXTI", "LITE", "MRNA", "NET"], "Base & Breakout: Washout": ["HYMC", "INDI", "OKLO", "PL", "TE"]},
}


def fetch(name):
    if BASE.startswith("http"):
        req = urllib.request.Request(BASE + name, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read().decode("utf-8")
    with open(os.path.join(BASE, name), encoding="utf-8") as f:
        return f.read()


def fetch_csv(name):
    try:
        return list(csv.DictReader(io.StringIO(fetch(name))))
    except Exception as e:  # a missing log must not stop the others
        print(f"WARNING could not read {name}: {e}")
        return []


def bar_date(b):
    return datetime.fromtimestamp(b[0], tz=timezone.utc).strftime("%Y-%m-%d")


def clean_bars(bars):
    """Drop any bar with a missing / NaN field (an unfinished or broken bar is never used)."""
    ok = lambda v: v is not None and not (isinstance(v, float) and math.isnan(v))
    return [b for b in (bars or []) if all(ok(v) for v in b[1:6])]


def num(s):
    try:
        v = float(s)
        return None if math.isnan(v) else v
    except (TypeError, ValueError):
        return None


def status(ret):
    if ret > UP_THRESH:
        return "UP"
    if ret < DOWN_THRESH:
        return "DOWN"
    return "FLAT"


def collect_rows():
    """-> ordered list of (date, category, ticker, log_close, note)."""
    rows, seen = [], set()

    def add(d, cat, t, close, note):
        if (d, cat, t) not in seen:
            seen.add((d, cat, t)); rows.append((d, cat, t, close, note))

    for d in sorted(BACKFILL):
        for cat in CATEGORIES:
            for t in BACKFILL[d].get(cat, []):
                add(d, cat, t, None, "BACKFILL-APPROX" if cat in APPROX else "BACKFILL")
    fwd = fetch_csv("forward_log.csv")
    for r in fwd:
        if r.get("sym") in (None, "", "(none)"):
            continue
        for p in (r.get("panels") or "").split("+"):
            if p in PANEL_CATS:
                add(r["signal_date"], PANEL_CATS[p], r["sym"], num(r.get("close")), "LIVE")
        if r.get("take") == "1":
            add(r["signal_date"], TAKE_CAT, r["sym"], num(r.get("close")), "LIVE")
    bb = fetch_csv("bb_log.csv")
    for r in bb:
        if r.get("kind") in BB_CATS:
            add(r["session"], BB_CATS[r["kind"]], r["sym"], num(r.get("close")), "LIVE")
    flow = fetch_csv("flow_log.csv")
    for r in flow:
        if r.get("label") in FLOW_CATS:
            add(r["session"], FLOW_CATS[r["label"]], r["sym"], num(r.get("close")), "LIVE")
    last = {"forward_log": max((r["signal_date"] for r in fwd), default=None),
            "bb_log": max((r["session"] for r in bb), default=None),
            "flow_log": max((r["session"] for r in flow), default=None)}
    rows.sort(key=lambda r: (r[0], CATEGORIES.index(r[1]), r[2]))
    return rows, last


def build(rows, bars_by):
    spy = bars_by.get(BENCH) or []
    spy_idx = {bar_date(b): i for i, b in enumerate(spy)}
    idx_by = {t: {bar_date(b): i for i, b in enumerate(bs)} for t, bs in bars_by.items()}
    cost = 2 * SLIPPAGE_BPS_PER_SIDE / 100
    out = []
    for d, cat, t, log_close, note in rows:
        bars = bars_by.get(t) or []
        i = idx_by.get(t, {}).get(d)
        rec = {h: None for h in LOG_HDR}
        rec.update({"Date": d, "Category": cat, "Ticker": t, "Notes": note})
        if i is None:
            rec["EntryPrice"] = round(log_close, 2) if log_close else None
            rec["Notes"] = note + "; no completed bar in data.json yet"
            out.append(rec); continue
        c0 = bars[i][4]
        rec["EntryPrice"] = round(c0, 2)
        si = spy_idx.get(d)
        for h in HORIZONS:
            if i + h >= len(bars):
                continue
            fwd = bars[i + h][4]
            ret = (fwd - c0) / c0 * 100
            rec[f"Fwd{h}D_Ret%"] = round(ret, 2)
            rec[f"Fwd{h}D_Status"] = status(ret)
            if si is not None and si + h < len(spy):
                rs = (spy[si + h][4] - spy[si][4]) / spy[si][4] * 100
                rec[f"Fwd{h}D_vsSPY%"] = round(ret - rs, 2)
            o = bars[i + 1][1]
            rec[f"Fwd{h}D_NextOpenNet%"] = round((fwd - o) / o * 100 - cost, 2)
        out.append(rec)
    return out


def stats_table(recs):
    hdr = ["Category", "N_Logged", "N_Backfill"]
    for h in HORIZONS:
        hdr += [f"N_{h}D", f"WinRate_{h}D", f"AvgRet_{h}D", f"AvgVsSPY_{h}D", f"BeatSPY%_{h}D", f"AvgNextOpenNet_{h}D"]
    avg = lambda xs: round(sum(xs) / len(xs), 2) if xs else None
    table = [hdr]
    for cat in CATEGORIES:
        cr = [r for r in recs if r["Category"] == cat]
        row = [cat, len(cr), sum(1 for r in cr if r["Notes"].startswith("BACKFILL"))]
        for h in HORIZONS:
            rets = [r[f"Fwd{h}D_Ret%"] for r in cr if r[f"Fwd{h}D_Ret%"] is not None]
            vs = [r[f"Fwd{h}D_vsSPY%"] for r in cr if r[f"Fwd{h}D_vsSPY%"] is not None]
            no = [r[f"Fwd{h}D_NextOpenNet%"] for r in cr if r[f"Fwd{h}D_NextOpenNet%"] is not None]
            n = len(rets)
            row += [n, round(sum(1 for x in rets if x > 0) / n * 100, 1) if n else None, avg(rets), avg(vs),
                    round(sum(1 for x in vs if x > 0) / len(vs) * 100, 1) if vs else None, avg(no)]
        table.append(row)
    table.append([
        "Notes: membership is the dashboard's own after-close logs (LIVE). N_Backfill rows were rebuilt on 2026-10-11 from "
        "data.json for sessions before each log began (Weekly Pick and TAKE backfill is approximate). Categories with N < 20 "
        "at a horizon are insufficient sample. A name that stays in a panel is logged every day it is there, so the effective "
        "sample is smaller than N. NextOpenNet = entry at next session open, exit at close h sessions after the signal day, "
        f"minus {2 * SLIPPAGE_BPS_PER_SIDE} bps round-trip. Base & Breakout and Options Flow are display-only TEST panels. "
        "Options Flow rows are counted here but not listed in the Log or membership files (see the dashboard's flow_log.csv)."])
    return table


def main():
    raw = json.loads(fetch("data.json"))
    asof = raw.get("asof", "")
    bars_by = {t: clean_bars(v.get("bars")) for t, v in raw["tickers"].items()}
    spy_dates = [bar_date(b) for b in bars_by.get(BENCH, [])]
    rows, last = collect_rows()
    recs = build(rows, bars_by)
    D = last["forward_log"] or (spy_dates[-1] if spy_dates else None)
    table = stats_table(recs)

    wb = Workbook()
    ws = wb.active; ws.title = "Log"
    ws.append(LOG_HDR)
    for r in recs:
        ws.append([r[h] for h in LOG_HDR])
    st = wb.create_sheet("Stats")
    for row in table:
        st.append(row)
    wb.save(TRACKER_PATH)

    print(f"asof={asof} last_complete_bar={D} last_clean_bar_in_data={spy_dates[-1] if spy_dates else None}")
    print(f"log_latest_session={last}")
    for k, v in last.items():
        if v != D:
            print(f"WARNING {k} has no rows for {D} (latest {v})")
    if spy_dates and D and spy_dates[-1] < D:
        print(f"WARNING data.json has no usable closing bar for {D}; entry prices for that day come from the logs")
    print(f"rows_total={len(recs)} live={sum(1 for r in recs if r['Notes'].startswith('LIVE'))} "
          f"backfill={sum(1 for r in recs if r['Notes'].startswith('BACKFILL'))}")
    print("resolved=" + str({h: sum(1 for r in recs if r[f'Fwd{h}D_Ret%'] is not None) for h in HORIZONS}))
    for cat in CATEGORIES:
        print(f"{cat}: {[r['Ticker'] for r in recs if r['Date'] == D and r['Category'] == cat]}")

    if EXPORT_DIR:
        os.makedirs(EXPORT_DIR, exist_ok=True)
        # Options Flow rows (~50 a day) stay in the xlsx and in Stats but are left out of the exported log and
        # membership CSVs: the dashboard's own flow_log.csv already holds them row by row.
        slim = [r for r in recs if r["Category"] not in FLOW_CATS.values()]
        with open(os.path.join(EXPORT_DIR, "log.csv"), "w", newline="") as f:
            w = csv.writer(f); w.writerow(LOG_HDR); w.writerows([r[h] for h in LOG_HDR] for r in slim)
        with open(os.path.join(EXPORT_DIR, "stats.csv"), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow([f"BCC3 forward-test stats | data asof {asof} | last logged session {D}"])
            w.writerows(table)
        with open(os.path.join(EXPORT_DIR, f"membership_{D}.csv"), "w", newline="") as f:
            w = csv.writer(f); w.writerow(LOG_HDR[:4])
            w.writerows([r[h] for h in LOG_HDR[:4]] for r in slim if r["Date"] == D)
        print(f"exported to {EXPORT_DIR}")


if __name__ == "__main__":
    main()
