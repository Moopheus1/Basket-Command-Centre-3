"""Daily SPY previous-day high / low (PDH / PDL) ping to Discord.

Display-only: reads docs/data.json (already refreshed by the Update market data workflow), never writes
anything, never touches TAKE, any panel rule or the forward log. Uses the same DISCORD_WEBHOOK_URL secret
as alerts.py. Run with --dry-run to print the message instead of sending it.
"""
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

SITE = "https://moopheus1.github.io/Basket-Command-Centre-3/"
ET = ZoneInfo("America/New_York")


def bar_date(t):
    return datetime.fromtimestamp(t, tz=timezone.utc).date()


def build_message(data, now_utc):
    spy = data["tickers"]["SPY"]
    bars = [b for b in spy["bars"] if b[2] is not None and b[3] is not None]
    now_et = now_utc.astimezone(ET)
    today = now_et.date()
    after_close = now_et.hour * 60 + now_et.minute >= 16 * 60 + 20
    # previous completed session = last bar dated before today (or today's bar once the session is over)
    done = [b for b in bars if bar_date(b[0]) < today or (bar_date(b[0]) == today and after_close)]
    if not done:
        return None
    pd = done[-1]
    pdh, pdl, pdc = pd[2], pd[3], pd[4]
    live = bars[-1] if bar_date(bars[-1][0]) == today and bars[-1] is not pd else None
    lines = [f"**SPY PDH / PDL for {now_et.strftime('%a %d %b')}'s session**",
             f"PDH **${pdh:,.2f}**  |  PDL **${pdl:,.2f}**",
             f"Previous session: {bar_date(pd[0]).strftime('%a %d %b')} (close ${pdc:,.2f}, range ${pdh - pdl:,.2f})"]
    if live is not None:
        px = live[4]
        if px > pdh:
            pos = f"ABOVE the PDH by ${px - pdh:,.2f}"
        elif px < pdl:
            pos = f"BELOW the PDL by ${pdl - px:,.2f}"
        else:
            pos = f"inside the range: ${px - pdl:,.2f} above the PDL, ${pdh - px:,.2f} below the PDH"
        lines.append(f"Latest price ${px:,.2f} (pre-market / live, from 1-minute data): {pos}")
    else:
        lines.append("No pre-market price in the data yet; levels only.")
    sess = touched = 0
    for i in range(1, len(done)):
        sess += 1
        if done[i][2] >= done[i - 1][2] or done[i][3] <= done[i - 1][3]:
            touched += 1
    if sess >= 60:
        lines.append(f"Why watched: one or both lines were touched on {touched} of {sess} sessions ({round(touched / sess * 100)}%) in this dashboard's daily data; daily bars, not the overnight range.")
    asof = data.get("asof")
    try:
        age_h = (now_utc - datetime.fromisoformat(asof)).total_seconds() / 3600
        if age_h > 30:
            lines.append(f"WARNING: data.json is {age_h:.0f} hours old, levels may be stale.")
    except Exception:
        pass
    lines.append(f"Charts: {SITE} (SPY PDH/PDL panel, top of the page)")
    return "\n".join(lines)


def main():
    with open("docs/data.json") as f:
        data = json.load(f)
    msg = build_message(data, datetime.now(timezone.utc))
    if msg is None:
        print("[spy_levels_ping] no completed SPY session found; nothing to send")
        return 0
    if "--dry-run" in sys.argv:
        print(msg)
        return 0
    url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not url:
        print("[spy_levels_ping] DISCORD_WEBHOOK_URL not set - would have sent:\n" + msg)
        return 1
    req = urllib.request.Request(url, data=json.dumps({"content": msg}).encode("utf-8"),
                                 headers={"Content-Type": "application/json", "User-Agent": "bcc3-spy-levels"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            print(f"[spy_levels_ping] Discord webhook responded {resp.status}")
        return 0
    except urllib.error.HTTPError as e:
        print(f"[spy_levels_ping] Discord webhook failed: HTTP {e.code} {e.read()[:200]}")
    except Exception as e:
        print(f"[spy_levels_ping] Discord webhook failed: {e}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
