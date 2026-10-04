#!/usr/bin/env python3
"""Valuation vs own history - writes docs/valuation.json for the Performance overview column.

DISPLAY-ONLY. Never touches data.json, the panels, TAKE or the forward log.

WHAT IT ANSWERS: "is this stock expensive or cheap compared with how the market has usually
priced it?" It does NOT say whether the stock is oversold in the short-term sense (that is
what the RSI columns are for), and nothing here has been shown to predict the next 20 sessions.

METHOD (no tuned parameters):
  1. From the company's own SEC filings (XBRL "company facts"), build trailing-12-month
     revenue, net income and free cash flow (operating cash flow minus capital spending) at
     every quarter end, plus shareholders' equity and the diluted share count.
  2. For every month of the last 10 years: market value (month-end price x shares) divided by
     each of those four figures as they stood at least 45 days earlier (filings arrive late).
     That gives a 10-year history of four multiples: price/earnings, price/sales, price/book,
     price/free-cash-flow. Months where the figure was zero or negative are skipped.
  3. Per yardstick: the MEDIAN multiple x today's figure / today's shares = what the share
     would cost at its usual multiple; and the PERCENTILE of today's multiple in that history.
  4. Fair value = the middle (median) of the available yardstick values. Valuation percentile =
     the middle of the yardstick percentiles (0 = cheapest of the last 10 years, 100 = dearest).

TESTED 2026-10-04 against GuruFocus's published GF Value on 41 watchlist names: rank agreement
0.88, same under/fair/over verdict on 31, opposite on 1 (AVGO), typical dollar gap 21%. A
growth adjustment was tried and REJECTED: fitted on half the names it made the other half
worse (22% -> 32%). Known blind spot: a business whose normal multiple has permanently
changed (AMZN reads as far cheaper than it is).

DATA: SEC EDGAR (free, official) + Yahoo month-end prices and split history via yfinance.
The SEC requires automated users to identify themselves with a contact email; it is read
from the SEC_CONTACT_EMAIL environment variable (a GitHub secret) and never written to disk.
Without it the script exits cleanly and leaves the existing file alone.
US-dollar SEC filers only: foreign companies reporting in another currency are skipped.
"""
import datetime as dt
import json
import os
import statistics
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TICKERS_PATH = os.path.join(ROOT, "tickers.txt")
DATA_PATH = os.path.join(ROOT, "docs", "data.json")
OUT_PATH = os.path.join(ROOT, "docs", "valuation.json")
CIK_MAP_URL = "https://raw.githubusercontent.com/jadchaar/sec-cik-mapper/main/mappings/stocks/ticker_to_cik.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK%010d.json"

YEARS = 10
FILING_LAG_DAYS = 45      # a quarter's figures are treated as known 45 days after it ends
MIN_MONTHS = 24           # a yardstick needs at least this many usable months of history
MAX_AGE_DAYS = 250        # skip a yardstick if the latest figure is older than this

D = dt.date.fromisoformat
TAGS = {
    "rev": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet",
            "RevenuesNetOfInterestExpense", "RevenueFromContractWithCustomerIncludingAssessedTax",
            "SalesRevenueGoodsNet", "Revenue"],
    "ni": ["NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic",
           "ProfitLossAttributableToOwnersOfParent"],
    "cfo": ["NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
            "CashFlowsFromUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets",
              "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"],
    "eq": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
           "EquityAttributableToOwnersOfParent", "Equity"],
    "sh": ["WeightedAverageNumberOfDilutedSharesOutstanding", "WeightedAverageNumberOfSharesOutstandingBasic",
           "WeightedAverageShares"],
}
LEG_NAMES = {"pe": "ni", "ps": "rev", "pb": "eq", "pfcf": "fcf"}


def raw(facts, tags, unit):
    """All facts for the tags, one per (start, end): first tag in the list wins, then the latest filing."""
    out = {}
    for pri, tag in enumerate(tags):
        for ns in ("us-gaap", "ifrs-full"):
            f = facts.get("facts", {}).get(ns, {}).get(tag)
            if not f:
                continue
            for x in f.get("units", {}).get(unit, []):
                if x.get("val") is None or "end" not in x:
                    continue
                k = (x.get("start"), x["end"])
                cand = (pri, x.get("filed", ""), x["val"])
                cur = out.get(k)
                if cur is None or pri < cur[0] or (pri == cur[0] and cand[1] > cur[1]):
                    out[k] = cand
    return out


def ttm_series(r):
    """end date -> trailing-12-month total, for each fiscal-year end and each quarter end that can be built.

    A quarter's trailing total = last full fiscal year + this year-to-date - the same year-to-date a year ago.
    """
    ann, ytd = {}, []
    for (s, e), (_, _filed, v) in r.items():
        if not s:
            continue
        n = (D(e) - D(s)).days
        if 350 <= n <= 380:
            ann[e] = (s, v)
        elif 75 <= n < 350:
            ytd.append((s, e, v, n))
    out = {e: v for e, (s, v) in ann.items()}
    ann_ends = {D(e): v for e, (s, v) in ann.items()}
    for s, e, v, n in ytd:
        ds, de = D(s), D(e)
        prev = [fv for fe, fv in ann_ends.items() if abs((ds - fe).days - 1) <= 6]
        if not prev:
            continue
        py = [v2 for s2, e2, v2, n2 in ytd if abs((de - D(e2)).days - 365) <= 10 and abs(n2 - n) <= 10]
        if not py:
            continue
        out[e] = prev[0] + v - py[0]
    return out


def inst_series(r):
    return {e: v for (s, e), (_, _filed, v) in r.items() if not s}


def shares_series(r, splits, sh_ref):
    """end date -> diluted shares restated to today's split basis.

    A figure filed before a split is multiplied by that split. Some companies report the count
    in thousands or millions; a value under 1/300th of today's count is treated as such and
    rescaled. (A genuine 300-fold rise in share count within the window would be misread.)
    """
    out = {}
    for (s, e), (_, filed, v) in r.items():
        if not s or v <= 0:
            continue
        fac = 1.0
        for sd, ratio in splits:
            if filed and sd > filed:
                fac *= ratio
        v = v * fac
        if sh_ref and v < sh_ref / 300:           # far too small to be a real count: reported in thousands/millions
            v = min((v * k for k in (1e3, 1e6)), key=lambda x: abs(_log10(x / sh_ref)))
        n = (D(e) - D(s)).days
        if e not in out or n < out[e][1]:       # prefer the shortest period ending on that date
            out[e] = (v, n)
    return {e: v for e, (v, n) in out.items()}


def _log10(x):
    import math
    return math.log10(x) if x > 0 else 99


def asof(series, d, lag):
    """Latest value whose period ended at least `lag` days before d, as (end_date, value)."""
    best = None
    for e, v in series.items():
        if D(e) + dt.timedelta(days=lag) <= d and (best is None or e > best[0]):
            best = (e, v)
    return best


def evaluate(facts, px, price, mcap):
    """px: list of [YYYY-MM-DD, month close (split-adjusted), split ratio or 0], oldest first."""
    splits = [(d, r) for d, c, r in px if r and r > 0]
    rev = ttm_series(raw(facts, TAGS["rev"], "USD"))
    if not rev:
        return {"status": "No US-dollar SEC filings (foreign company, or too newly listed)"}
    ni = ttm_series(raw(facts, TAGS["ni"], "USD"))
    cfo = ttm_series(raw(facts, TAGS["cfo"], "USD"))
    capex = ttm_series(raw(facts, TAGS["capex"], "USD"))
    eq = inst_series(raw(facts, TAGS["eq"], "USD"))
    fcf = {e: cfo[e] - capex.get(e, 0) for e in cfo}
    sh_now = mcap / price if (mcap and price) else None
    sh = shares_series(raw(facts, TAGS["sh"], "shares"), splits, sh_now)
    if not sh:
        return {"status": "No share count in SEC filings"}
    today = D(px[-1][0])
    start = today - dt.timedelta(days=int(365.25 * YEARS))
    sh_filing = asof(sh, today, 0)[1]
    if not sh_now:
        sh_now = sh_filing
    ratio = sh_filing / sh_now
    if not 0.75 <= ratio <= 1.33:
        return {"status": "Share count in filings does not tie to market value (several share classes or an ADR)"}
    series = {"pe": ni, "ps": rev, "pb": eq, "pfcf": fcf}
    legs = {}
    for leg, ser in series.items():
        if not ser:
            continue
        hist = []
        for d, c, _ in px:
            dd = D(d)
            if dd < start or dd >= today.replace(day=1):
                continue
            a, s_ = asof(ser, dd, FILING_LAG_DAYS), asof(sh, dd, FILING_LAG_DAYS)
            if not a or not s_ or a[1] <= 0 or (dd - D(a[0])).days > 500:
                continue
            hist.append(c * s_[1] / a[1])
        now = asof(ser, today, 0)
        if len(hist) < MIN_MONTHS or not now or now[1] <= 0 or (today - D(now[0])).days > MAX_AGE_DAYS:
            continue
        med = statistics.median(hist)
        cur = price * sh_now / now[1]
        legs[leg] = {"pct": round(100.0 * sum(1 for h in hist if h <= cur) / len(hist)),
                     "value": round(med * now[1] / sh_now, 2), "now": round(cur, 2), "median": round(med, 2),
                     "months": len(hist), "asof": now[0]}
    if not legs:
        return {"status": "Not enough history yet (needs 2 years of positive sales, earnings, book value or cash flow)"}
    return {"status": "ok",
            "pct": round(statistics.median(l["pct"] for l in legs.values())),
            "fair": round(statistics.median(l["value"] for l in legs.values()), 2),
            "legs": legs,
            "fundamentalsTo": max(l["asof"] for l in legs.values())}


# ---------------------------------------------------------------- data fetching
def read_tickers():
    out = []
    with open(TICKERS_PATH) as f:
        for line in f:
            s = line.strip().upper()
            if s and not s.startswith("#") and s not in out:
                out.append(s)
    return out


def http_json(url, ua, timeout=45, tries=3):
    err = None
    for a in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept-Encoding": "identity"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:      # noqa: BLE001 - any failure is retried, then reported
            err = e
            time.sleep(2 + 3 * a)
    raise RuntimeError(str(err)[:120])


def month_prices(sym):
    import warnings
    import yfinance as yf
    warnings.filterwarnings("ignore")
    h = yf.Ticker(sym).history(period="12y", interval="1mo", auto_adjust=False, actions=True)
    return [[d.strftime("%Y-%m-%d"), float(r.Close), float(r["Stock Splits"])]
            for d, r in h.iterrows() if r.Close == r.Close]


def main():
    email = (os.environ.get("SEC_CONTACT_EMAIL") or "").strip()
    if not email:
        print("SEC_CONTACT_EMAIL is not set - nothing fetched, nothing changed.")
        return 0
    ua = "BCC3-Dashboard " + email

    with open(DATA_PATH) as f:
        data = json.load(f).get("tickers", {})
    try:
        with open(OUT_PATH) as f:
            prior = json.load(f)
    except (OSError, ValueError):
        prior = {}
    prior_t = prior.get("tickers", {})
    cik_map = dict(prior.get("cik", {}))
    try:
        cik_map.update({k: int(v) for k, v in http_json(CIK_MAP_URL, "BCC3-Dashboard").items()})
    except Exception as e:      # noqa: BLE001
        print("CIK list not refreshed (%s) - using the saved one" % e)

    tickers = read_tickers()
    out, used_cik, n_ok, n_kept = {}, {}, 0, 0
    for sym in tickers:
        e = data.get(sym) or {}
        bars = e.get("bars") or []
        if e.get("error") or not bars:
            continue
        if e.get("mcap") is None:
            out[sym] = {"status": "etf"}
            continue
        cik = cik_map.get(sym)
        if not cik:
            out[sym] = {"status": "Not found in the SEC company list (very new listing)"}
            continue
        used_cik[sym] = cik
        try:
            facts = http_json(FACTS_URL % int(cik), ua)
            time.sleep(0.2)                       # SEC asks for no more than 10 requests a second
            px = month_prices(sym)
            time.sleep(0.3)
            if len(px) < 6:
                raise RuntimeError("no price history")
            res = evaluate(facts, px, bars[-1][4], e.get("mcap"))
        except Exception as ex:     # noqa: BLE001 - one bad name must not stop the run
            if (prior_t.get(sym) or {}).get("status") == "ok":
                out[sym] = prior_t[sym]           # keep last good value rather than blanking it
                n_kept += 1
                print("%s: fetch failed (%s) - kept previous value" % (sym, str(ex)[:80]))
            else:
                out[sym] = {"status": "Data fetch failed"}
                print("%s: fetch failed (%s)" % (sym, str(ex)[:80]))
            continue
        if res["status"] == "ok":
            res["computed"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
            res["priceUsed"] = bars[-1][4]
            n_ok += 1
        out[sym] = res

    stocks = sum(1 for v in out.values() if v.get("status") != "etf")
    if stocks and n_ok + n_kept < 0.5 * stocks and prior_t:
        print("::error::only %d of %d stocks computed - keeping the previous file" % (n_ok, stocks))
        return 1
    result = {"updated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "method": "Median of 10-year price/earnings, price/sales, price/book and price/free-cash-flow history, from SEC filings",
              "coverage": {"stocks": stocks, "withValue": n_ok + n_kept},
              "tickers": dict(sorted(out.items())), "cik": dict(sorted(used_cik.items()))}
    with open(OUT_PATH, "w") as f:
        json.dump(result, f, separators=(",", ":"))
        f.write("\n")
    print("Computed %d, kept %d from last run, of %d stocks." % (n_ok, n_kept, stocks))
    return 0


if __name__ == "__main__":
    sys.exit(main())
