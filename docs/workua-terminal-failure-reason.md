# Work.ua terminal search-failure reason

Date: October 13, 2026. Status: implemented and verified locally, not published or deployed.

## Cause

When every configured search raised `WorkUaSourceError`, the adapter raised a new
generic terminal error. Ingestion stores the terminal error instead of the collected
warnings, so `source_runs.error_message` and `sources.last_error` lost the actual
search-failure reason. Detailed source logs retained it, but the persisted result did not.

## Change

The adapter retains its first search error and the existing bounded, redacted warning
summary. If all searches fail, it includes that summary in the terminal message and
preserves the first error's HTTP status and reason. For example:

```text
Every configured Work.ua search page failed. Work.ua search page unavailable (challenge): https://www.work.ua/en/jobs-remote-python/.
```

This reports the first failure, not an exhaustive claim that every search failed for
that same reason. Other search warnings remain in the existing metrics/logging path.
Raw exception text is not copied into the new summary; sensitive URL values are
redacted before the existing 300-character URL bound. Ingestion's additional redaction
and 2,000-character result bound remain unchanged.

Shared-stop handling for rate limits, Retry-After, request budgets and deadlines is
unchanged. Successful empty responses remain distinct from raised search failures.
Partially successful searches continue yielding their existing results.

## Verification

- Six new source cases: challenge, HTTP403, HTTP502, timeout, transport failure and
  unavailable response. They verify the first reason survives a different later
  failure, typed status/reason, URL redaction and unchanged request counts.
- Three new isolated SQLite ingestion cases: challenge, HTTP403 and timeout. They
  verify persisted run/source reasons, unchanged failure metrics, descriptions,
  active state, raw data, content hash, last-seen/detail timestamps, and a protected
  historical sent-delivery record without creating another delivery.
- Work.ua and ingestion suite:120 passed.
- Complete non-integration backend suite:764 passed;20 PostgreSQL tests deselected.
- Ruff lint/format, strict mypy, application Bandit, offline lock consistency and
  whitespace checks passed. No schema or database-engine-specific logic changed;
  PostgreSQL integration tests were not rerun for this package.

HTTP behavior is mocked and fixture databases are isolated. No production or live
source request, forced collection, real notification send, publication or deployment.

## Unchanged boundaries

No change to searches, retries, polling, pacing, request ceilings, reader/transport,
cache, reconciliation, matching, schema, dependencies, frontend or notification
policy. Missing-listing deactivation remains disabled. This change improves error
evidence; it does not restore Work.ua access or guarantee complete source coverage.
