# BCC3 review protocol

Fixed on 4 Oct 2026. Changing anything in this file is itself a rule change and needs Jerome's approval.

## What the weekly review is

A read-only measurement of `docs/forward_log.csv`, produced by `scripts/weekly_review.js` and
delivered as a pull request that adds one file under `reviews/`. Merging the pull request files
the report. It changes nothing on the dashboard.

## What the reviewer may and may not do

| May | May not |
|---|---|
| Run `scripts/weekly_review.js` and report its numbers | Edit `docs/index.html`, `scripts/forward_log.js`, `tickers.txt` or any workflow |
| Describe what the numbers show, in plain language | Recommend a trade, or name a stock to buy or sell |
| Point out data problems (missing bars, mixed rule versions, scorer quirks) | Propose a rule change while the evidence gate is closed |
| Propose **one** challenger when the gate is open | Propose something already rejected in `reviews/CHANGELOG.md` without new evidence |
| | Move the gate, the windows or the yardstick |

## The evidence gate

A group (a panel, TAKE, a size label) is eligible for a proposal only when, on the **20-session
window**, it has **at least 60 distinct trades spread over at least 12 calendar weeks**. The 5- and
10-session tables are early warning only.

Why so strict: names flagged on the same day move together, so rows overstate the evidence. Twelve
weeks is the minimum that spans more than one kind of market.

## Stages for any proposed change

Each stage is its own pull request and needs its own approval.

1. **Backtest check** — a filter to kill bad ideas. Passing proves nothing.
2. **Shadow** — the challenger is logged daily to its own file (`docs/challenger_<id>.csv`), shown nowhere.
3. **Pilot panel** — display-only, labelled TEST, not part of TAKE. Requires the gate to be open on the challenger's own shadow record.
4. **Promotion** — changes the live rule. Bumps `RULES_VERSION`, which starts a fresh forward log.

At most one promotion per calendar month.

## Promotion criteria (all must hold, on the shadow/pilot record)

- Gate open (60 trades, 12 weeks, 20-session window).
- Average excess over the equal-weight basket above zero, with t (by date) of 2 or more.
- Positive excess in both halves of the record taken separately.
- Not worse than the rule it replaces on the worst-quarter trade (25th-percentile dip).

## Rollback

- Every promotion pull request states its revert condition up front: *revert if average excess
  versus the basket is below zero after N distinct trades*, with N written in before merge.
- Rollback is GitHub's **Revert** on that pull request. The dashboard is on the old rules at the next data run.
- The ledger is never rolled back. Rows keep the rule version that produced them.
- Basket-Command-Centre-2 stays untouched as the baseline.

## The official test is separate

`scripts/forward_log.js` carries its own pre-registered test (TAKE versus random entries, 20 scored
TAKEs). The weekly review quotes that verdict and does not replace it.
