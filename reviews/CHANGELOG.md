# Proposal log

Every proposal the review makes, and what happened to it. Rejected ideas stay here so they are not
re-proposed until they pass by chance.

| Date | ID | Proposal | Stage | Decision | Notes |
|---|---|---|---|---|---|
| 2026-10-04 | – | None. Gate closed (0 of 60 trades, 0 of 12 weeks). | – | – | First review. |
| 2026-10-24 | – | No proposal this week. Gate closed (11 of 60 trades, 1 of 12 weeks). | – | – | 21 sessions, 82 distinct trades; first 20-session data, all from one date (25 Sep). |

## Open observations (not proposals)

- 2026-10-04 — `forward_log.js` de-duplicates across the whole log, so a name logged as a
  single-panel signal and promoted to TAKE within 5 sessions is never scored as a TAKE
  (PFE, 30 Sep). This slows the official 20-TAKE test. Fixing it changes the scorer, so it needs
  approval and a decision on whether to restart the log.
- 2026-10-24 — Same scorer quirk now affects four TAKEs (PFE, ZS, MRNA, ADBE). The 2026-10-17 review pull request is still unmerged.
