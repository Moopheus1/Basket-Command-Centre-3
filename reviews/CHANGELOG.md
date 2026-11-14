# Proposal log

Every proposal the review makes, and what happened to it. Rejected ideas stay here so they are not
re-proposed until they pass by chance.

| Date | ID | Proposal | Stage | Decision | Notes |
|---|---|---|---|---|---|
| 2026-10-04 | – | None. Gate closed (0 of 60 trades, 0 of 12 weeks). | – | – | First review. |
| 2026-11-14 | – | None. Gate closed (best group 68 of 60 trades but 4 of 12 weeks). | – | – | No proposal this week. All 20-session data from one falling market. |

## Open observations (not proposals)

- 2026-10-04 — `forward_log.js` de-duplicates across the whole log, so a name logged as a
  single-panel signal and promoted to TAKE within 5 sessions is never scored as a TAKE
  (PFE, 30 Sep). This slows the official 20-TAKE test. Fixing it changes the scorer, so it needs
  approval and a decision on whether to restart the log.
- 2026-11-14 — The scorer gap above now covers 8 of 14 TAKEs (PFE, ZS, MRNA x2, ADBE, BUG, BB, SPOT).
