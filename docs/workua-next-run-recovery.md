# Work.ua next-run recovery verification

Status: verified locally on October 17, 2026; test/documentation changes only.

## Finding

An additional persistent queue is not needed to retry configured search pages after
ordinary page-specific failures. Each scheduled run starts the configured search list
again and visits page two when page one provides new cards. A failed page stops only
that search's remaining pagination, unless an existing shared stop condition applies.

The worker creates new adapter instances between cycles, primes stored listing data,
and applies the existing source polling schedule. The source does not deactivate
missing listings. Cached/summary detail status also prevents a failed detail refresh
from being mistaken for a successful fresh description in the next run.

## Added regressions

Eight mocked-transport cases combine a missing second search page with a blocked
detail refresh, an available independent search, and later recovery. They cover
challenge content, HTTP 403, HTTP 502 and a read timeout, with either a reused adapter
or a new adapter instance and re-primed cached listings.

The cases confirm that:

- Results from available pages remain available during the partial run.
- A failed detail refresh retains the previous full description and success timestamp.
- The next fetch requests the missed second page and obtains its additional vacancy.
- The failed description refresh is retried and becomes complete when access returns.
- An unchanged successful description from the other search is reused without a new
  detail request. IDs within the recovered result remain unique.
- Partial-run warnings and detail failure metrics remain visible; the recovered run
  has no such warnings or failures.

Focused Work.ua tests: 114 passed. Full non-integration backend suite: 1246 passed;
115 PostgreSQL integration cases were excluded, not claimed as newly executed.
Ruff lint/format and strict mypy passed. All HTTP responses in these regressions
were mocked; no live Work.ua request, forced production scan or notification was sent.

## Limits and unchanged behavior

This verification does not remove Work.ua access challenges. A second page cannot
be reached while its first page is unavailable, and a transient vacancy may disappear
before a later successful run. Existing pagination, item, deadline and request limits
still bound coverage; no complete provider inventory or recovery of every historical
missed vacancy is guaranteed.

No collector runtime code, source activation, transport, frequency, retry ceiling,
search coverage, database schema, matching, notification policy or dependency changed.
No CAPTCHA solver, identity/IP rotation, alternate access route or protection bypass
was introduced. Stable upstream access remains a separate unresolved provider issue.
