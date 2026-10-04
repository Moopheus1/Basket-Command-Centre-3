#!/usr/bin/env python3
"""Fetch GuruFocus intrinsic-value figures for the watchlist and write docs/gf_value.json.

DISPLAY-ONLY. Feeds one column in the Performance overview table. It never touches
data.json, the panels, TAKE, or the forward log.

WHICH NUMBER: GuruFocus's "Intrinsic Value: Projected FCF" (field
intrinsic_value_projected_fcf in the /stocks/{symbol}/valuations response). This is NOT
the headline "GF Value": that one lives only in the /rankings endpoint, which returns 404
for a free-plan key (tested 2026-10-04). Three sister figures from the same response are
stored for the hover text: Peter Lynch fair value, Graham number, median price-to-sales value.

Why this is a slow rotation rather than a daily refresh of everything:
the free GuruFocus Data API plan allows 100 requests a MONTH, and the watchlist holds
~107 stocks (ETFs are skipped). So each run refreshes only the few stalest names, and a
full lap of the list takes about five weeks. Every value is stored with the date it was
fetched and the financial period it was computed from; the dashboard shows both.

Budget guards (all overridable by env):
  GF_MAX_PER_RUN   names to fetch per run            (default 3  -> ~90 a month)
  GF_MONTHLY_CAP   hard stop on requests per month   (default 95, leaves 5 spare)
  GF_ONLY          comma-separated tickers to fetch instead of the stalest ones (testing)
The month's request count is kept inside gf_value.json, so it survives between runs.

The API key is read from the GURUFOCUS_API_KEY environment variable (a GitHub secret).
It is sent only in the Authorization header and is never printed or written to disk.
With no key set the script exits cleanly and changes nothing.
"""
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TICKERS_PATH = os.path.join(ROOT, "tickers.txt")
DATA_PATH = os.path.join(ROOT, "docs", "data.json")
OUT_PATH = os.path.join(ROOT, "docs", "gf_value.json")

API_BASE = "https://api.gurufocus.com/data"
ENDPOINT = "/stocks/{sym}/valuations"
METRIC = "intrinsic_value_projected_fcf"
SECTION = "valuationand_quality"            # GuruFocus's own spelling
EXTRAS = {"lynch": "peter_lynch_fair_value", "graham": "graham_number", "medps": "medpsvalue"}

MAX_PER_RUN = int(os.environ.get("GF_MAX_PER_RUN", "3"))
MONTHLY_CAP = int(os.environ.get("GF_MONTHLY_CAP", "95"))
ONLY = [t.strip().upper() for t in os.environ.get("GF_ONLY", "").split(",") if t.strip()]
TIMEOUT = 30


class StopRun(Exception):
    """Raised when continuing would only waste the monthly allowance."""


def read_tickers():
    out = []
    with open(TICKERS_PATH) as f:
        for line in f:
            s = line.strip().upper()
            if s and not s.startswith("#") and s not in out:
                out.append(s)
    return out


def etf_set():
    """Tickers with no market cap in data.json are ETFs/funds: no intrinsic value exists for them."""
    try:
        with open(DATA_PATH) as f:
            tk = json.load(f).get("tickers", {})
    except (OSError, ValueError):
        return set()
    return {s for s, e in tk.items() if isinstance(e, dict) and e.get("mcap") is None and not e.get("error")}


def load_state():
    try:
        with open(OUT_PATH) as f:
            st = json.load(f)
        if isinstance(st, dict) and isinstance(st.get("tickers"), dict):
            st.setdefault("calls", {})
            return st
    except (OSError, ValueError):
        pass
    return {"source": "GuruFocus Data API", "calls": {}, "tickers": {}}


def to_num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.replace(",", "").replace("$", "").strip())
        except ValueError:
            return None
    return None


def records(payload):
    """Every dated record in the response as (date, source, section_dict), newest first.

    The response holds a 'quarterly' list, an 'annually' list and a 'ttm' block. List order is
    not documented, so records are sorted by their own date rather than trusted by position.
    """
    out = []
    for src in ("quarterly", "annually"):
        for rec in payload.get(src) or []:
            if isinstance(rec, dict) and isinstance(rec.get(SECTION), dict):
                out.append((str(rec.get("date") or ""), src, rec[SECTION]))
    ttm = payload.get("ttm")
    if isinstance(ttm, dict) and isinstance(ttm.get(SECTION), dict):
        out.append((str(ttm.get("date") or ""), "ttm", ttm[SECTION]))
    # newest date first; on a tie prefer quarterly over annual over ttm
    order = {"quarterly": 0, "annually": 1, "ttm": 2}
    out.sort(key=lambda r: order[r[1]])
    out.sort(key=lambda r: r[0], reverse=True)
    return out


def extract(payload):
    """Latest positive Projected-FCF intrinsic value, plus the sister figures from the SAME record.

    Returns None when GuruFocus has no positive figure (loss-making or too little history).
    """
    if not isinstance(payload, dict):
        return None
    for date, src, sec in records(payload):
        v = to_num(sec.get(METRIC))
        if v is None:
            continue                      # this period has no figure: look at the next-newest
        if v <= 0:
            return None                   # newest figure is zero/negative: no usable value
        out = {"iv": round(v, 2), "period": date, "basis": src}
        for short, field in EXTRAS.items():
            n = to_num(sec.get(field))
            if n is not None and n > 0:
                out[short] = round(n, 2)
        return out
    return None


def call(path, key):
    """One API request. Returns (parsed_json_or_None, http_status)."""
    req = urllib.request.Request(API_BASE + path, headers={
        "Authorization": key, "Accept": "application/json", "User-Agent": "bcc3-gf-value/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = r.read().decode("utf-8", "replace")
            status = r.status
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace") if e.fp else ""
        status = e.code
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise StopRun("network error reaching GuruFocus: %s" % e)
    try:
        return json.loads(body), status
    except ValueError:
        return None, status


def main():
    key = (os.environ.get("GURUFOCUS_API_KEY") or "").strip()
    if not key:
        print("GURUFOCUS_API_KEY is not set - nothing fetched, nothing changed.")
        return 0

    today = dt.datetime.now(dt.timezone.utc).date()
    month = today.strftime("%Y-%m")
    state = load_state()
    state.pop("endpoint", None)
    used = int(state["calls"].get(month, 0))
    used_before = used

    tickers = read_tickers()
    etfs = etf_set()
    stocks = [t for t in tickers if t not in etfs]

    # Drop names no longer on the watchlist; mark ETFs so the page can say why they are blank.
    state["tickers"] = {s: v for s, v in state["tickers"].items() if s in tickers}
    for s in etfs & set(tickers):
        state["tickers"][s] = {"status": "etf"}

    def staleness(sym):
        e = state["tickers"].get(sym) or {}
        return (0, "") if not e.get("asof") else (1, e["asof"])   # never-fetched first, then oldest

    queue = [t for t in ONLY if t in stocks] if ONLY else sorted(stocks, key=staleness)[:max(0, MAX_PER_RUN)]

    fetched = 0
    problem = None
    try:
        for i, sym in enumerate(queue):
            if used >= MONTHLY_CAP:
                raise StopRun("monthly cap of %d requests reached for %s" % (MONTHLY_CAP, month))
            payload, status = call(ENDPOINT.format(sym=sym), key)
            used += 1
            state["calls"][month] = used
            if status in (401, 403):
                raise StopRun("GuruFocus rejected the API key or plan (HTTP %d: %s)" % (
                    status, (payload or {}).get("message", "no message") if isinstance(payload, dict) else "no message"))
            if status == 429:
                raise StopRun("GuruFocus rate/quota limit hit (HTTP 429)")
            if status == 404:
                state["tickers"][sym] = {"iv": None, "asof": today.isoformat(), "status": "none"}
                print("%s: not found at GuruFocus (HTTP 404)" % sym)
                continue
            if status != 200 or not isinstance(payload, dict):
                raise StopRun("unexpected HTTP %s for %s" % (status, sym))
            recs = records(payload)
            if i == 0 and not recs:
                raise StopRun("response for %s has no '%s' records - layout changed, parser needs adjusting" % (sym, SECTION))
            cur = str((payload.get("basic_information") or {}).get("currency") or "")
            got = extract(payload)
            if got is not None:
                entry = dict(got, asof=today.isoformat(), status="ok")
                if cur:
                    entry["currency"] = cur
                state["tickers"][sym] = entry
                fetched += 1
                print("%s: %s %s (period %s, %s) lynch=%s graham=%s medps=%s" % (
                    sym, got["iv"], cur or "?", got["period"], got["basis"],
                    got.get("lynch"), got.get("graham"), got.get("medps")))
            else:
                # No positive figure (loss-making, or too little history). Date-stamp it so it
                # goes to the back of the queue instead of being retried every day.
                state["tickers"][sym] = {"iv": None, "asof": today.isoformat(), "status": "none"}
                newest = recs[0] if recs else None
                print("%s: no positive intrinsic value (newest record %s, raw %s)" % (
                    sym, newest[0] if newest else "none", newest[2].get(METRIC) if newest else None))
    except StopRun as e:
        problem = str(e)

    have = sum(1 for s in stocks if (state["tickers"].get(s) or {}).get("iv") is not None)
    state.update({
        "source": "GuruFocus Data API",
        "metric": METRIC,
        "updated": (dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    if used != used_before else state.get("updated")),
        "coverage": {"stocks": len(stocks), "withValue": have},
        "monthlyCap": MONTHLY_CAP,
        "lastError": problem,
    })
    # Keep only the last 3 months of call counts.
    state["calls"] = {m: n for m, n in sorted(state["calls"].items())[-3:]}
    state["tickers"] = dict(sorted(state["tickers"].items()))
    with open(OUT_PATH, "w") as f:
        json.dump(state, f, indent=1, sort_keys=False)
        f.write("\n")

    print("Fetched %d this run. %d of %d stocks have a value. Requests used in %s: %d of %d." % (
        fetched, have, len(stocks), month, used, MONTHLY_CAP))
    if problem:
        print("::error::" + problem)
        # A reached cap is the budget guard working, not a failure.
        return 0 if problem.startswith("monthly cap") else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
