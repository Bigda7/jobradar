# Work.ua Detail Refresh Review

Reviewed on 2026-10-03. Local fix only; no publication or production rollout.

## Production Evidence

Read-only SQL and filtered worker-log inspection verified:

- Run 6046, 06:33-06:35 UTC: 48 discoveries, five detail failures, zero recorded errors.
- Run 6066, 13:03-13:05 UTC: 25 discoveries, three detail failures, zero recorded errors.
- All three logged detail failures in run 6066 were upstream security challenges, for
  vacancies 7394323, 8145273, and 7208953. Three search-page skips also reported challenges.
- There were 63 active Work.ua listings; seven stored descriptions equaled their search
  summaries while still retaining a previous successful-detail timestamp.

The pre-rollout worker container's logs are no longer available through the current container.
The exact causes of all five failures in run 6046 are therefore not established. The current
three challenge failures are verified, not assumed to explain every historical failure.
No forced source cycle or additional upstream probe was used for this investigation.

## Application Defects and Local Fix

Failed detail refreshes previously replaced saved full text with a short search summary.
Ingestion retained the previous detail timestamp, so that new summary could be reused as
successful detail until the cache expired. A blocked search page was logged but did not
contribute a source-run warning, allowing a partially blocked search to appear successful.

The Work.ua adapter now:

- Preserves previously fetched full text during a failed refresh rather than overwriting it.
- Records `detail_status` in internal source payloads: `complete`, `cached`, or `summary`.
- Does not treat cached fallback text or search summaries as fresh successful detail.
- Reattempts incomplete refreshes on the next normal scheduled cycle, within existing limits.
- Rejects legacy summary-only cache rows even if they retained an old successful timestamp.
- Logs missing-description and HTTP failures, retaining the actual HTTP status rather than
  inferring status from digits in a vacancy URL or exception text.
- Records unavailable search pages as warnings, so existing ingestion reports a partial run
  and existing source-health monitoring can surface it after an approved rollout.

The successful-detail timestamp is not advanced on fallback. No schema migration, frontend
contract, scoring rule, dependency, configured poll interval, retry count, pagination limit,
access method, or notification resend policy changed. The adapter's cache behavior is corrected;
previously misclassified summaries are no longer allowed to suppress scheduled detail refreshes.
Lost historical full text cannot be reconstructed from overwritten rows; it can only be refreshed
when the existing upstream path returns valid details again.

## Verification and Remaining Boundary

Twelve added regression cases cover challenge/missing/404/410/503/timeout fallback, legacy
cache rejection, recovery on the next cycle, exact HTTP status handling, search warnings, and
SQLite ingestion preserving the full description end to end. Full non-integration tests and
static checks were run; results are recorded in the workspace handoff.

Upstream challenges remain an external coverage limit. This fix neither bypasses access controls
nor establishes permission to collect or republish Work.ua content. Keep the existing access
method and configured request volume unchanged. Production still runs the previous image until
a separately approved release/deployment; no existing database row was edited in this package.
