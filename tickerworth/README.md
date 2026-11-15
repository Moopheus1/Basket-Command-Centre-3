# TickerWorth snapshot for BCC3 tickers

`tickerworth_take.csv` holds figures read from each ticker's public TickerWorth analysis page
(`https://tickerworth.com/analysis/<TICKER>`) for every ticker in `tickers.txt` (125 symbols).

- Snapshot taken: 15 Nov 2026. TickerWorth prices are delayed (last quote 13 Nov 2026 close).
- Display-only data. Nothing in the dashboard panels, TAKE or the forward log reads this file.
- Structured numbers only; TickerWorth's written commentary is not copied.

## Columns

| Column | Meaning |
|---|---|
| covered | 1 if TickerWorth has a page for the ticker, 0 if the page returns 404 |
| grade, score | TickerWorth Score (letter and /100). It weighs six things, not just valuation |
| label | UNDERVALUED / OVERVALUED / FAIR VALUE, when TickerWorth shows one |
| fair_value, tw_price, gap_pct, gap_dir | "Worth $X vs price $Y", and how far the price sits above/below it |
| verdict, model_value / range_low, range_high | "Priced above/below/within" its one-model value or value range |
| bear, base, bull | 12-month bear/base/bull scenario values (conditions, not forecasts) |
| analyst_mean, analyst_count | Mean analyst target and number of analysts |
| no_fair_value | 1 if TickerWorth says it does not publish a fair value for the company |

## Known gaps

- Blank cells mean the field was not shown on the page or was not captured by the parser. They are not zeros.
- Only about half the tickers have any fair-value figure. 33 are flagged `no_fair_value`.
- 13 tickers have no TickerWorth page: AIPO, BOTT, BUG, DRAM, EWY, FOTO, HACK, IGV, JETS, LYTE, NCLD, POWR, SMH.
- AMAT, CEG, GOOGL, GEV, GLW, LITE, VRT, VST show no "What we think" block, so label/verdict are blank.
- Several tickers (for example AMD, ASML, DELL, ONTO) have a label but no "Priced above/below" line captured.
- TickerWorth's own disclaimer: model output, not advice or a price target.
