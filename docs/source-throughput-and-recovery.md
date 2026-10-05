# Source Throughput and Recovery

Status: approved and implemented locally on 2026-10-05; not published or deployed by this package.
Deployed baseline at implementation start: backend v1.2.15, frontend v1.1.8, schema 20261005_0020.

## Djinni

- Default scan eligibility is 900 seconds after the previous start, without Djinni polling jitter.
  The worker still serializes collection and sleeps between cycles. This is not an exact wall-clock
  schedule or a continuous 95-request/minute stream. Other-source intervals and jitter are unchanged.
- RSS pacing targets 95 starts/minute. A separate database reservation enforces at most 100 attempts
  per rolling 60 seconds. All initial, additional and subdivision feeds share the source budget.
- HTML metadata retains a 100-request per-run ceiling and at least two-second pacing. A new, separate
  persisted ceiling is 100 attempts per rolling hour. Failed requests consume the same reservation;
  worker restart, forced collection and another adapter instance do not reset it. This HTML ceiling
  is a local safety choice, not an interpretation of the provider's RSS allowance.
- PostgreSQL locks the source row while reserving and commits before network access. The existing
  worker advisory lock remains the production no-overlap boundary. Standalone adapter probes use
  an in-memory budget unless explicitly configured with the database implementation.
- Waiting for an RSS budget is bounded by the existing whole-run deadline. Metadata budget exhaustion
  retains discovered RSS records and cached metadata rather than reporting an upstream failure.
- `metadata_deferred_count` counts unattempted eligible metadata refreshes from the latest scan.
  It is separate from failures and discovery-limit warnings, and is shown on Sources even when a real
  error also exists. A deferral alone does not trigger the partial-source alert. Real RSS/detail errors
  still do. The counter is not a persisted per-vacancy queue or a guarantee of eventual refresh:
  a vacancy must be rediscovered, and continued new/bumped vacancies can delay routine refreshes.
- Description, date semantics, matching v15 and historical notification protections are unchanged.

## Work.ua

- Retries are scoped to this adapter. Timeout/transport failures and HTTP 429/500/502/503/504 can
  retry up to the existing configured attempt count; other HTTP failures are not retried.
- Backoff starts at one second. A valid Retry-After number or HTTP date is honored only if its wait
  fits the request deadline and is at most 30 seconds. An excessive/nonfinite wait stops the request;
  it is never shortened into an early retry. Invalid headers use normal backoff.
- Network attempts share a 600-second source deadline and a derived finite request cap; each
  logical request has at most a 120-second deadline and each attempt retains the configured timeout.
  These network bounds do not claim that ingestion/database processing always completes within 600s.
- Sanitized actual HTTP statuses or allowlisted challenge/timeout/transport/deadline/budget/content
  reasons are retained in run summaries and listing `detail_error`. Raw response bodies, credentials
  and transport exception text are not copied into those summaries.
- Failed detail refresh retains a stored full description, or the search summary when no stored
  full description exists. It does not advance the successful-detail timestamp. A future successful
  refresh clears `detail_error`. Failed details now make the run partial rather than silently successful.
- Health messages include failed-detail counts. Recovery describes only the latest collection;
  it does not claim all previously missed vacancies were recovered. Legacy successful runs containing
  detail failures receive qualified wording instead of a claim of error-free collection.
- Protective challenges are not solved. Existing HTML/Markdown reader handling, enabled state,
  filters, six-hour interval and jitter remain unchanged. There is no new durable per-page catch-up
  queue, proxy rotation or origin-access method. An upstream challenge can therefore still cause a
  partial run; bounded retries cannot guarantee collection of a vacancy that disappears meanwhile.

## Migration and Publication Boundary

Migration 20261005_0021 adds `sources.request_budget` and `source_runs.metadata_deferred_count`.
It initializes recent stored Djinni metadata attempt timestamps (latest 100 within one hour) and
holds RSS reservations for 60 seconds because old code did not persist exact RSS request times.
This cannot reconstruct attempts lost in a pre-migration crash. Future attempts are persisted first.
Existing listing IDs, payloads, opportunities and sent deliveries are not changed. Downgrade removes
only these new fields; budget history/backlog counts are lost and must not be treated as reusable
allowance during a rollback/redeployment.

Both new columns keep database defaults so old application inserts remain schema-compatible.
Stop the old worker before migrating/replacing it: old code does not obey the new persisted budgets,
and simultaneous old/new collectors would invalidate the rollout's shared-limit assumptions.

A separately approved rollout must include a verified backup, migration, backend/frontend release
and explicit deployment settings `DJINNI_POLL_INTERVAL_SECONDS=900` and
`DJINNI_REQUEST_DELAY_SECONDS=0.632`; an existing deployment with explicit old environment values
will not acquire the faster settings merely from changed code defaults. Leave HTML ceilings and
other-source settings unchanged. Recheck natural collection, persisted limits and absence of old
notification replay after deployment. No release, production mutation or real Telegram send was
performed while implementing this package.
