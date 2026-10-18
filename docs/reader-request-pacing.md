# Shared Reader request pacing

Release package: 1.2.26, October18. Publication and rollout were explicitly approved;
check release and operator records for the actually deployed version and live results.

## Evidence and scope

The read-only production review at11:01:50UTC found12recent Work.ua runs partial,
all mentioning challenge content and none mentioning HTTP429. Latest7873 processed43
records with2errors, including1detail failure. The active portfolio pool already has
18queries, with5selected/run;2pages,75items and6-hour polling remain unchanged.
This does not prove excessive request rate caused the upstream challenges.

The implementation previously delayed only detail refreshes. Search pages, retries,
format fallback and redirects had no shared start gate. Robota also uses Reader,
including concurrent searches, so a Work.ua-only delay would not bound combined traffic.

The [official Reader rate table](https://jina.ai/reader/#rate-limit), checked October18,
lists20requests/minute without an API key, enforced by IP. The new worker-local gate
uses3.2seconds between admitted starts, slightly below that ceiling. This is a Reader
limit, not Work.ua permission or evidence of bypassing a challenge.

## Behavior

- `READER_REQUEST_INTERVAL_SECONDS` defaults to3.2; validated range3.1..30seconds.
  It is worker-only in Compose. No API keys, subscriptions or dependencies are added.
- Registry-created Work.ua and Robota adapters share one `ReaderRequestPacer` instance.
  Direct low-level construction retains an optional injected pacer for isolated tests;
  standalone callers should inject the same pacer when sharing Reader traffic.
- The async gate runs immediately before each physical GET, including same-origin
  redirects, every retry, search, HTML/Markdown fallback, API detail and vacancy page.
  Existing retry/redirect ceilings are not increased. Cached detail reuse does not wait.
- The gate serializes admission, not whole downloads. Concurrent requests cannot reserve
  the same start time; waiting is asynchronous and cancellable.
- Waiting, including lock contention, stays inside the existing request/run deadlines.
  If the next slot does not fit, no new request/counter reservation is made. Work.ua
  stops further network work for that run while retaining already collected cards,
  cached full descriptions or search summaries and observable partial-run warnings.
- A canceled waiter releases its lock without spending an unused slot. An admitted
  request still spends its slot even if its transport subsequently fails.
- Valid numeric/date `Retry-After` on429/5xx extends the shared cooldown. A429without
  a usable header gets a conservative60-second cooldown. Zero/past dates do not remove
  the normal spacing. Queued waiters recheck a cooldown extended while they slept.
  Excessive cooldowns are not shortened to fit a request window. Work.ua preserves the
  preceding429as the recorded cause if its retry cannot fit the shared cooldown.
- Work.ua's request ceiling derives from the selected batch, not all18configured
  searches: for5searches/2pages/75items/2attempts it is340physical admissions. This
  restores the intended budget-neutral bound after discovery rotation was introduced.

## Diagnostics

`workua_request_attempt` logs page kind, allowed response format, logical attempt,
outcome, HTTP status, elapsed time including waits and cumulative admitted GET count.
It logs no URL, body, header, cookie or raw transport exception. Existing
`workua_response_classified` remains responsible for usable content versus empty or
challenge content, including challenges returned with HTTP200. These are distinct
events: a successful HTTP transfer is not proof of a usable vacancy page.

Source-run warnings and Telegram source-health logic are not suppressed or relabeled
as success. Existing cached-description preservation and later ordinary-run recovery
remain covered. No fake notifications or forced ingestion are part of verification.

## Boundaries and tradeoffs

This is a cooperative rate limiter, not an access bypass. No new transport, proxy,
identity rotation, challenge solving, cookie forwarding, user-agent impersonation or
fallback route is added. Existing parsing/access classification stays intact.

Slower starts can lengthen a run or make its unchanged600-second budget expire before
all discovered details refresh. Preserved cached/summary data and partial warnings are
intentional; this does not guarantee75new complete records or zero403/challenges.
Search breadth, batch limits, polling intervals, matchingv16, Telegram cutoffs/history,
schema, frontend/API contracts, cache TTL and production memory limits are unchanged.

The gate/cooldown is in memory for one registry lifecycle. It is not a persistent
distributed quota and does not coordinate separate processes, other users of the
same external IP, or cooldowns across a worker restart. Normal worker cycles have
their existing delays, but do not describe this as a global provider-limit guarantee.
Extending it across processes/restarts needs a separate design, not a hidden schema change.

Live upstream recovery must be assessed after a separately approved deployment and
ordinary scheduled runs. Local mocked tests cannot prove it. No live Work.ua/Reader
probe or production mutation was made for this implementation.

## Local verification

- 35 new deterministic cases cover combined start spacing, a rolling minute ceiling,
  redirects/retries/fallback/API paths, shared cooldowns and expiry, canceled/queued
  waiters, deadline/byte-budget rechecks, cache preservation and next-run recovery,
  sanitized diagnostics and the selected-batch request ceiling.
- Final full non-integration suite: 1342 passed; 120 PostgreSQL tests separately excluded.
- Full real PostgreSQL suite: 120 passed after the complete Alembic chain, using an
  isolated loopback-only reviewed17.11image with256MiB/1CPU/128PIDs. The task-owned
  container and its validated anonymous volume were removed; other resources retained.
- Ruff/format, mypy for 69 source files, Bandit, frozen offline lock consistency and diff
  checks passed. Dependencies and the matching profile are unchanged; only the own
  project version is bumped for release preparation.
- An existing classification test now selects classification events explicitly rather
  than assuming every log event has that field; all16challenge cases remain checked.
- Frontend is unchanged and its tests were not rerun for this backend-only package.
  Hosted CI, image publication and actual provider recovery need their own evidence;
  passing local tests alone does not establish those outcomes.
