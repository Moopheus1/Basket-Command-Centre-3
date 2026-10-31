# Proposal log

Every proposal the review makes, and what happened to it. Rejected ideas stay here so they are not
re-proposed until they pass by chance.

| Date | ID | Proposal | Stage | Decision | Notes |
|---|---|---|---|---|---|
| 2026-10-04 | – | None. Gate closed (0 of 60 trades, 0 of 12 weeks). | – | – | First review. |
| 2026-10-31 | – | No proposal this week. Gate closed (29 of 60 trades, 2 of 12 weeks). | – | – | 26 sessions, 99 distinct trades; 20-session data from 5 dates. 17 Oct and 24 Oct review PRs still unmerged. |

## Open observations (not proposals)

- 2026-10-04 — `forward_log.js` de-duplicates across the whole log, so a name logged as a
  single-panel signal and promoted to TAKE within 5 sessions is never scored as a TAKE
  (PFE, 30 Sep). This slows the official 20-TAKE test. Fixing it changes the scorer, so it needs
  approval and a decision on whether to restart the log.
- 2026-10-31 — Same scorer quirk now affects five TAKEs (adds BUG, 30 Oct). Official test still 0 of 20 scored.
