# Work.ua Response Resilience

Date: 2026-10-06. Status: approved, implemented and verified locally; not committed or deployed.

## Fixed Behavior

The adapter previously rejected an entire response on any raw Cloudflare marker, before
checking whether the response contained usable vacancy data. A valid HTML response with a
normal Turnstile script could therefore be discarded and replaced by a blocked fallback.

Search and detail responses now supply parsed-content evidence to challenge classification.
Search evidence requires a card with a trusted source URL; untrusted links cannot override
challenge detection. Weak raw markers such as a Cloudflare script reference do not reject a
response with usable listing content. A marker without usable content remains a challenge.
Explicit English/Ukrainian security-verification phrases in visible text remain a challenge
even if the response also contains listing-shaped markup. Encoded characters, whitespace and
inline tags are handled. This is deliberately conservative: quoting an exact verification
phrase in visible job text can still produce a challenge classification.

HTML search summaries and job descriptions exclude script, style and template content.
Those elements and comments also do not provide visible security-text evidence. Self-closing
non-content elements do not accidentally hide the subsequent vacancy content.

Each parsed response logs `workua_response_classified` with page kind, response format,
classification (`content`, `challenge`, `empty`) and usable parsed-item count. This event does
not contain URLs, response bodies, raw exception text or request credentials. `empty` is a
response classification, not proof that the complete source has no vacancies; existing run
failure handling is unchanged.

## Existing Resilience Verified, Not Newly Introduced

Blocked detail refreshes retain cached full descriptions and their last successful timestamp,
or retain the search summary if no full description exists. Failed search pages do not discard
other searches' results or deactivate existing listings. A fully blocked search run is recorded
as failed rather than successful. It does not stop another source in the same worker cycle.
After access returns, a subsequent run refreshes the description and succeeds without duplicating
stored listings. Actual failures remain visible in run metrics and source-health alerts.

## Unchanged Boundaries

No dependency, frontend, API/schema, matching, notification, reader/transport, source enablement,
polling, pacing, retry ceiling, cache TTL or search-coverage setting changed. Existing format
fallback is unchanged; no additional fallback or protective-access bypass was added. Accepting
available HTML can avoid an unnecessary fallback request, but does not increase request ceilings.

This package cannot make Work.ua provide a page while access is blocked. New listings can still
be missed and cached descriptions can remain stale. Local fixtures do not prove the cause of
every historical warning or guarantee full provider inventory. No live platform request or
production change was performed during implementation.

## Verification

- 44 new regression cases: 42 source/classification/parser cases and two worker/database cases.
- Combined local backend: 664 non-integration tests plus 19 real PostgreSQL integration tests,
  683 total, including the earlier locally prepared Djinni resilience package.
- Full Alembic upgrade chain, model parity, downgrade to 20260928_0019 and upgrade to head passed.
- Ruff formatting/lint, mypy, Bandit, offline lock consistency and diff whitespace checks passed.
- HTTP and Telegram behavior was isolated/mocked. Worker preservation/recovery tests used
  in-memory SQLite; PostgreSQL tests used a dedicated disposable local database, not production.
- The temporary PostgreSQL container and volume were removed after exact name, task-label and
  mount validation. Existing Docker resources and unrelated working-tree edits were preserved.
- Frontend remains unchanged; frontend tests were not rerun because no client contract changed.

The combined packages still require explicit approval immediately before publication/deployment.
Production remains on backend v1.2.16/frontend v1.1.9; no new commit, push, release or deployment
was made for this package.
