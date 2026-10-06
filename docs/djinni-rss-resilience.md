# Djinni RSS Resilience

Status: approved local implementation on 2026-10-06; not published or deployed.
Baseline application v1.2.16, schema 20261005_0021. No migration or API contract change.

## Transient Requests

- RSS HTTP 408/500/502/503/504 and transport/timeouts get at most three attempts in total.
  Root, additional and subdivision feeds use the same policy. Metadata HTML is not retried.
- Each actual attempt consumes the existing persistent RSS reservation, pacing, per-run request
  cap and byte budget. The 512-request setting now counts attempts, including retries and rechecks,
  not merely distinct logical feed visits. Failed attempts count as pages in run metrics.
- Backoff is one then two seconds, or a longer valid Retry-After seconds/date value. A wait above
  30 seconds, nonfinite value or wait exceeding the logical deadline stops traversal; it is not
  shortened into an early retry. Invalid headers use normal backoff. Logical RSS requests have
  a 120-second ceiling inside the unchanged overall run deadline and per-attempt timeout.
- HTTP 403/429 still stop traversal without retry or HTML fallback. Redirects, invalid/unsafe XML,
  oversized bodies and nontransient HTTP errors are not retried. No challenge bypass was added.
- Successfully recovered RSS requests have a bounded informational event without URLs, headers,
  response bodies or raw transport exception text. Exhausted errors remain visible in run health.

## Subdivision Consistency

- All child feeds are included in the parent coverage comparison, including children already
  scheduled as an additional feed. Deduplicating fetch scheduling must not discard coverage edges.
- After the initial traversal, a parent missing one to three previously fetched IDs may receive
  one bounded recheck pass over its children, only when those children were originally fetched.
  There are at most 12 logical recheck visits per run, with at most three attempts each and the
  same shared 512-attempt/time/byte/provider ceilings. Failed original children are not requeued
  for a second retry series. Larger inconsistencies retain the warning without extra traversal.
- Original parent/child records remain retained; new valid IDs found by a recheck are ingested
  once. Unioned observations preserve records that disappear between responses. No original
  data is deleted or replaced with an empty response.
- A discrepancy resolves only when the missing IDs actually appear in fetched child data.
  Disappearance from a refreshed parent or presence somewhere else is not treated as proof.
  Resolved consistency checks produce an informational event rather than a failure warning.
- Unresolved/failed/ignored-filter rechecks remain observable. A newly saturated rechecked feed
  with no previously explored subdivision remains limited; reproducing parent IDs cannot certify
  its unknown older tail. No completeness claim is made for all platform vacancies.

## Boundaries and Tradeoffs

- RSS changes between serial reads; incomplete provider filters can also cause mismatches.
  Today's precise provider-side cause is not proven by old summaries, which lack full snapshots.
  The scheduled-child comparison defect is reproduced locally, not claimed as the sole cause
  of every production warning. Stable provider-side inconsistencies will still alert.
- Retries/rechecks can extend a scan and consume room formerly used for new feeds or metadata.
  Caps are not increased. Fifteen-minute eligibility does not promise an exact wall-clock start.
- HTML metadata keeps 100/hour, 100/run and two-second pacing. Metadata deferral remains a separate
  counter rather than a failed collection. Matching, notifications, other sources, dependencies,
  project license and frontend are unchanged.
- All HTTP/Telegram test traffic is mocked. Real production reliability remains unverified until
  a separately approved publication/deployment and natural source run.

## Local Verification

- 620 non-integration tests and 19 real PostgreSQL integration tests passed (639 total).
  Regressions cover successful/exhausted retries, transient versus blocked statuses, backoff,
  Retry-After dates/nonfinite/excessive values, request/deadline/window limits, retained records,
  stable/resolved/failed/newly saturated rechecks, ignored filters and already scheduled children.
- A real PostgreSQL retry test verifies reservation commit after both a failed and successful
  HTTP attempt and rejection of a replacement adapter once the same rolling window is full.
- Full Alembic chain, model parity, downgrade to 0019 and re-upgrade passed on a disposable
  localhost-only PostgreSQL instance. No schema change is required by this package.
- Ruff format/lint, mypy (62 source files), Bandit, offline lock and Git whitespace checks passed.
  One Windows mypy executable trampoline failed; the same installed checker passed via
  `uv run --frozen --offline python -m mypy src/jobradar`. No dependency was changed to fix tooling.
- Disposable test container/volume removed after label verification. Existing Docker resources
  preserved. Frontend contract inspected but unchanged; no frontend build/browser check rerun.
- No commit, push, PR, release, image publication, production mutation or real notification send.
