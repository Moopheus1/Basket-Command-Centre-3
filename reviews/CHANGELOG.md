# Proposal log

Every proposal the review makes, and what happened to it. Rejected ideas stay here so they are not
re-proposed until they pass by chance.

| Date | ID | Proposal | Stage | Decision | Notes |
|---|---|---|---|---|---|
| 2026-10-04 | – | None. Gate closed (0 of 60 trades, 0 of 12 weeks). | – | – | First review. |
| 2026-11-28 | – | No proposal this week. Gate closed (best group 93 of 60 trades but 6 of 12 weeks). | – | – | Every 20-session window so far falls in one falling market. |

## Open observations (not proposals)

- 2026-10-04 — `forward_log.js` de-duplicates across the whole log, so a name logged as a
  single-panel signal and promoted to TAKE within 5 sessions is never scored as a TAKE
  (PFE, 30 Sep). This slows the official 20-TAKE test. Fixing it changes the scorer, so it needs
  approval and a decision on whether to restart the log.
- 2026-11-28 — The official scorer now skips 10 of 17 logged TAKEs (same cause as above). The review's
  "sessions to 20 scored TAKEs" estimate counts all logged TAKEs, so it reads ~28 sessions; counting only
  TAKEs the scorer keeps, it is roughly 100 sessions. Not changed; noted for Jerome.
