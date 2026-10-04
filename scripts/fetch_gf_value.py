#!/usr/bin/env python3
"""Fetch GuruFocus "GF Value" for the watchlist and write docs/gf_value.json.

DISPLAY-ONLY. Feeds one column in the Performance overview table. It never touches
data.json, the panels, TAKE, or the forward log.

Why this is a slow rotation rather than a daily refresh of everything:
the free GuruFocus Data API plan allows 100 requests a MONTH, and the watchlist holds
~107 stocks (ETFs are skipped: they have no GF Value). So each run refreshes only the
few stalest names, and a full lap of the list takes about five weeks. Every value is
stored with the date it was fetched, and the dashboard shows that date.

Budget guards (all overridable by env):
  GF_MAX_PER_RUN   names to fetch per run            (default 3  -> ~90 a month)
  GF_MONTHLY_CAP   hard stop on requests per month   (default 95, leaves 5 spare)
The month's request count is kept inside gf_value.json, so it survives between runs.

The API key is read from the GURUFOCUS_API_KEY environment variable (a GitHub secret).
It is sent only in the Authorization header and is never printed or written to disk.
With no key set the script exits cleanly and changes nothing.

Response parsing: GuruFocus does not publish the response layout on a page that can be
read without a key, so the parser looks for the GF Value field by name anywhere in the
response instead of assuming a fixed layout. If the first response of a run has no
recognisable GF Value, the run STOPS (exit 1) after that single request and prints the
response's field names (names only, no values) so the parser can be fixed without
burning the monthly allowance.
"""
import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TICKERS_PATH = os.path.join(ROOT, "tickers.txt")
DATA_PATH = os.path.join(ROOT, "docs", "data.json")
OUT_PATH = os.path.join(ROOT, "docs", "gf_value.json")

API_BASE = "https://api.gurufocus.com/data"
# Tried in order for a ticker until one yields a GF Value. The one that works is
# remembered in gf_value.json ("endpoint") so later runs spend one request per ticker.
ENDPOINTS = ["/stocks/{sym}/rankings", "/stocks/{sym}/valuations"]

MAX_PER_RUN = int(os.environ.get("GF_MAX_PER_RUN", "3"))
MONTHLY_CAP = int(os.environ.get("GF_MONTHLY_CAP", "95"))
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
    """Tickers with no market cap in data.json are ETFs/funds: no GF Value exists for them."""
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
    return {"source": "GuruFocus Data API", "endpoint": None, "calls": {}, "tickers": {}}


def norm(k):
    return re.sub(r"[^a-z0-9]", "", str(k).lower())


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


def find_gf_value(obj):
    """Depth-first search for the GF Value number.

    Accepts a field whose normalised name is exactly 'gfvalue' (gf_value, GF Value,
    gfValue...). Deliberately does NOT accept look-alikes such as price_to_gf_value,
    gf_value_rank or gf_score, which are different quantities.
    """
    if isinstance(obj, dict):
        for k, v in obj.items():
            if norm(k) == "gfvalue":
                n = to_num(v)
                if n is not None and n > 0:
                    return n
                if isinstance(v, dict):  # e.g. {"gf_value": {"value": 123.4}}
                    for kk in ("value", "current", "gfvalue"):
                        for k2, v2 in v.items():
                            if norm(k2) == kk:
                                n = to_num(v2)
                                if n is not None and n > 0:
                                    return n
        for v in obj.values():
            r = find_gf_value(v)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = find_gf_value(v)
            if r is not None:
                return r
    return None


def key_paths(obj, prefix="", depth=0, out=None):
    """Field NAMES only (no values) - safe to print in a public Actions log."""
    if out is None:
        out = []
    if depth > 4 or len(out) > 200:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = prefix + "." + str(k) if prefix else str(k)
            out.append(p + " <" + type(v).__name__ + ">")
            key_paths(v, p, depth + 1, out)
    elif isinstance(obj, list) and obj:
        key_paths(obj[0], prefix + "[]", depth + 1, out)
    return out


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

    queue = sorted(stocks, key=staleness)
    if state.get("endpoint") not in ENDPOINTS:
        # Parser not proven yet: lead with a large, long-listed company that certainly has a
        # GF Value, so "no value found" on the first request can only mean the layout is
        # different - not that the first name on the list happens to have none.
        for anchor in ("AAPL", "MSFT", "GOOGL", "WMT", "PEP", "MCD", "IBM"):
            if anchor in queue:
                queue.remove(anchor)
                queue.insert(0, anchor)
                break
    queue = queue[:max(0, MAX_PER_RUN)]
    # Once an endpoint has been proven to carry GF Value, use only that one: a name with no
    # GF Value must cost one request, not one per endpoint.
    endpoints = [state["endpoint"]] if state.get("endpoint") in ENDPOINTS else ENDPOINTS[:]

    fetched = 0
    problem = None
    try:
        for i, sym in enumerate(queue):
            got = None
            for ep in endpoints:
                if used >= MONTHLY_CAP:
                    raise StopRun("monthly cap of %d requests reached for %s" % (MONTHLY_CAP, month))
                payload, status = call(ep.format(sym=sym), key)
                used += 1
                state["calls"][month] = used
                if status in (401, 403):
                    raise StopRun("GuruFocus rejected the API key or plan (HTTP %d: %s)" % (
                        status, (payload or {}).get("message", "no message") if isinstance(payload, dict) else "no message"))
                if status == 429:
                    raise StopRun("GuruFocus rate/quota limit hit (HTTP 429)")
                if status == 404:
                    # endpoint or symbol not found: say so (message only, never the key), then try the next
                    print("%s: HTTP 404 from %s - %s" % (sym, ep.split("/")[-1],
                          json.dumps(payload)[:300] if payload is not None else "no body"))
                    continue
                if status != 200 or payload is None:
                    raise StopRun("unexpected HTTP %s from %s" % (status, ep.split("/")[-1]))
                got = find_gf_value(payload)
                if got is not None:
                    if state.get("endpoint") != ep:
                        state["endpoint"] = ep
                        endpoints = [ep]
                    break
                if i == 0 and state.get("endpoint") is None:
                    # First ticker, parser never proven: show the layout so it can be fixed.
                    print("No GF Value field found in %s response. Field names returned:" % ep.split("/")[-1])
                    for p in key_paths(payload):
                        print("   ", p)
            if got is None and i == 0 and state.get("endpoint") is None:
                raise StopRun("could not find a GF Value in any endpoint for %s - parser needs adjusting" % sym)
            if got is not None:
                state["tickers"][sym] = {"gfValue": round(got, 2), "asof": today.isoformat(), "status": "ok"}
                fetched += 1
                print("%s: GF Value fetched" % sym)
            else:
                # GuruFocus publishes no GF Value for some names (too little history, losses).
                # Date-stamp it so it goes to the back of the queue instead of being retried daily.
                state["tickers"][sym] = {"gfValue": None, "asof": today.isoformat(), "status": "none"}
                print("%s: no GF Value available" % sym)
    except StopRun as e:
        problem = str(e)

    have = sum(1 for s in stocks if (state["tickers"].get(s) or {}).get("gfValue") is not None)
    state.update({
        "source": "GuruFocus Data API",
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

    print("Fetched %d this run. %d of %d stocks have a GF Value. Requests used in %s: %d of %d." % (
        fetched, have, len(stocks), month, used, MONTHLY_CAP))
    if problem:
        print("::error::" + problem)
        # A reached cap is the budget guard working, not a failure.
        return 0 if problem.startswith("monthly cap") else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
