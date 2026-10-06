# Work.ua Shared Run Stop

Status: implemented and verified locally on 2026-10-06; owner authorized combined v1.2.19
publication and deployment. Runtime rollout evidence is recorded separately in the handoff.
Prepared together with the local Djinni consistency-diagnostics package. Production remains
v1.2.18. No schema, API, dependency, frontend, matching or notification policy change.

## Reproduced Defects

Six new regressions failed against the unchanged collector before implementation:

- Exhausting a logical request's HTTP 429 retries did not stop other logical requests to
  the same reader. The collector continued with other searches and vacancy details.
- Rejecting an excessive Retry-After wait did not stop subsequent logical requests either.
  This could request another page before the reader's stated waiting period.
- Run time/request ceilings blocked actual network requests, but the collector continued
  iterating searches and sleeping before each unusable detail request.
- Detail pacing could begin a wait longer than the remaining overall collection window.
- A run stopped before obtaining any cards discarded the original HTTP status in its final
  generic error and stated that every configured page had failed, including unattempted pages.

These are reproduced application defects, not proof that HTTP 429 caused the historical
production alerts. The last verified Work.ua production run reported actual challenge pages;
this package cannot remove those upstream challenges.

## New Behavior

- Existing bounded retries remain available. A recovered 429 does not stop collection.
  Exhausted 429 stops further network requests for this run, not the source permanently.
- Retry-After that cannot be honored within the bounded retry window stops further requests
  in this run. A pending Retry-After on the final failed attempt does so as well. The collector
  does not switch to another URL to avoid the wait. A valid zero/past wait allows the existing
  next-page behavior for non-429 errors.
- Exhausted overall request/time budgets stop network work and further search iteration.
  No pacing delay is performed before a detail refresh that is already blocked. A detail
  pacing wait longer than the remaining run window is rejected without reducing normal pacing.
- Already obtained cards remain ingested once. A deferred refresh retains its stored full
  description and original successful timestamp, or the card summary if full text is absent.
  A genuinely reusable fresh description is still reusable and is not counted as a failed
  refresh merely because other requests stopped.
- Existing detail-failure metrics include refreshes not completed because the run stopped;
  they are not a count of actual HTTP requests. Existing per-card/search warnings remain
  observable. A run with cards and warnings is partial; a stopped run without cards fails with
  its actual status/reason rather than claiming every page was attempted.
- `workua_run_requests_stopped` records one informational event with stop reason, HTTP status
  and actual network-request count. It contains no URLs, bodies, headers or exception text.
- Stop state resets on the next ordinary `fetch`, allowing the existing scheduler to retry
  automatically. Other sources in the same worker cycle remain independent.

## Boundaries and Tradeoffs

Search list, pagination/item ceilings, six-hour polling setting, request pacing, cache TTL,
retry ceiling, reader/format fallback and source enablement are unchanged. No challenge
solver, proxy rotation, direct transport, new endpoint or additional access route is added.
Page-specific challenge/403/404 and ordinary transient errors without a shared stop condition
retain the existing behavior; one blocked page does not automatically retire the source.

A stopped run may collect fewer new vacancies than an aggressive continuation if the reader
would have recovered immediately. This is the intentional tradeoff for respecting throttling
and not repeatedly issuing requests after the shared window ends. Stored descriptions can
still become stale, and new vacancies can remain unavailable behind genuine protection.
Warnings are not hidden and whole-platform completeness is not claimed.

## Verification

- 109 focused Work.ua/retry/worker tests passed. Fifteen net additional cases cover shared
  search/detail stops, cache/summary/timestamp preservation, recovery, fresh-cache reuse,
  final-attempt Retry-After and isolation from another source. The existing budget/deadline
  test now independently exercises each guard because stop reasons persist within one run.
- Combined local packages passed all 738 backend tests: 719 non-integration and 19 real
  PostgreSQL integration tests. All HTTP and notification behavior in tests was mocked.
- Full Alembic upgrade from an empty database to 20261005_0021 passed on dedicated local
  PostgreSQL 17 using the same immutable image as CI. No migration or production database
  mutation was introduced. The test container used only tmpfs and a localhost-only random port.
- Ruff formatting/lint, strict mypy (63 source files), Bandit, lock consistency and Git
  whitespace checks passed. Frontend remained clean; no frontend rebuild was needed.
- Windows executable-trampoline and default async-loop launch errors were worked around by
  invoking installed Alembic/pytest through Python with the compatible Windows selector policy.
  No dependency, application event-loop setting or checked-in launcher was changed.
- Docker Desktop was started for verification. Only the task-labeled temporary test container
  and its disposable in-memory database were removed afterward; pre-existing resources preserved.

Implementation verification performed no live platform probe, forced production scan,
real notification or production mutation. The owner subsequently approved combined
publication/deployment. Djinni's historical discrepancy still needs new diagnostic traces
after the verified deployment and normal collection; this report does not claim it resolved.
