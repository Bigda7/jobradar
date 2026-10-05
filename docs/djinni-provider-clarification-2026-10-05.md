# Djinni Provider Clarification

Recorded: 2026-10-05. Evidence: a support reply signed by Serhii, supplied by the owner
in chat after the owner reported sending the metadata follow-up. The private support-inbox
link is not reproduced. An unauthenticated attempt to open it returned inaccessible; its contents
were not read. Its inbox/admin/conversation path suggests an internal support conversation link,
not public documentation. No independent mailbox authentication is claimed.

## Provider Statements

- RSS cannot currently be expanded with the missing structured fields. Expansion is planned
  without a promised delivery date.
- The described previous careful collection method can be used for these missing fields.
  This is scoped to that method/project, not unlimited scraping or an access-control bypass.
- RSS `pubDate` denotes the last update or bump, not first publication.
- Each feed returns the first 100 vacancies. RSS pagination is unavailable.
- For an existing database, the provider suggests periodically collecting new/bumped records.
  This does not establish an exhaustive historical inventory or guarantee that every vacancy
  will appear between polling intervals.
- No separate numeric HTML request limit, retention period or changed commercial-product
  scope was specified. The earlier 100-per-minute statement applies to RSS, not HTML requests.

## Current Implementation and Consequences

Deployed v1.2.14 remains RSS-only. It does not automatically gain an HTML enrichment stage
from receipt of this reply. Missing company, salary, employment and geographic fields are
still unknown for new records; known cached metadata can be stale.

The deployed v1.2.14 adapter maps RSS `pubDate` into `datePosted` and then `published_at`. That mapping
does not reflect the now-confirmed semantics. It can misrepresent vacancy age and affect
cross-source duplicate confidence, whose date comparison currently allows a seven-day gap.
Stable numeric source IDs and existing delivery protections remain in place; this inspection
does not demonstrate duplicate rows or historical notification re-sends caused by a bump.
No reliable original publication timestamp is inferred from a field name in the old HTML.

RSS remains useful for discovery; explicit filtered feeds can broaden the observed window,
but the 100-item boundary is an upstream limitation, not a broken page parser. Do not add fake
`page=2` traversal or claim a complete platform inventory. Do not reduce working coverage
or change schedules merely because the provider described an incremental collection pattern.

## Approved Package and Local Implementation

The owner subsequently approved implementation. RSS discovery, bounded page metadata enrichment,
separate update timestamps, historical date repair, and frontend labels are implemented locally.
Production remains the released RSS-only v1.2.14 until a separate deployment request.

Enrichment has its own conservative local ceilings: 100 requests/run, at least 2 seconds between
page requests, a 24-hour refresh eligibility threshold, and a 1-MB HTML response cap. Both network
stages share the existing whole-run time/aggregate-byte ceilings. These are engineering choices,
not provider-promised HTML allowances. Cache timestamps, provenance, bump invalidation and persisted
attempt order prevent unnecessary repeat requests and permanent starvation by early failing pages.
New metadata and bumped records have priority over routine TTL refreshes. Budget deferrals and
failures are visible in source metrics/warnings; records and cached fields survive failures.
Successful refreshes remove source-withdrawn fields rather than retaining stale salaries forever.

RSS pubDate becomes source_updated_at. Unknown first-publication dates remain null, including
legacy page datePosted whose original-publication semantics were not verified. Alembic 0020 repairs
existing Djinni rows and canonical date snapshots locally; it preserves identities, first/last-seen
timestamps, sent deliveries and independent other-source dates. Original legacy raw datePosted is
kept as evidence, not used as an asserted publication date. Cross-source automatic deduplication
remains conservative when verified publication dates are missing; no ambiguous rows are merged.

The implementation checklist was:

1. Keep RSS discovery and stored identities. Add explicit bounded enrichment using the described
   prior HTML/JSON-LD method for missing/stale metadata, with cache freshness and provenance.
   Choose budgets and cadence after inspecting the old transport and bounded live behavior;
   avoid a request for every stored vacancy on every run.
2. Keep rate/time/byte ceilings, trusted links, visible failures and immediate 403/429 stops.
   Do not reuse the RSS 100-per-minute allowance as permission for HTML traffic. Failed enrichment
   must not discard successful RSS discovery or erase previously known facts/full text.
3. Represent bump/update time separately from first publication. Preserve a verified original
   timestamp when available; otherwise keep first publication unknown and use local first-seen
   time only with its own meaning. Inspect backend/frontend contracts before choosing the schema
   and preparing any migration; do not rewrite production rows in this documentation step.
4. Test enrichment caching/failures/budgets, geographic restrictions and salary values, and
   prove that a bump retains the same listing/opportunity and does not resend a sent match.
   Evaluate cross-source date/deduplication consequences without automatically merging ambiguous
   production records. Update matching rules/version only if actual scoring rules change.

### Remaining Decision and Verification Boundaries

Live inspection found page metadata on a bounded three-vacancy RSS sample: three companies,
three employment types and three explicit UA candidate restrictions; two published salaries.
No data was persisted and no notification delivered by that probe. Page datePosted on an earlier
one-vacancy comparison matched the RSS bump time, not a verified original publication date.

At completion of the initial enrichment/date step, the matcher only checked textual restrictions
and residence was unconfirmed. That historical boundary is superseded by the owner's explicit
Prague, Czechia confirmation and the local matching follow-up below, not by timezone inference.

Final local verification: 502 non-integration backend tests and 16 isolated PostgreSQL tests
passed (518 total), including six date-repair variants for sole/shared canonical sources, invalid
RSS dates and legacy page dates. The full Alembic chain, model/schema parity and downgrade/upgrade
passed. Ruff format/lint, mypy (59 source files), Bandit, offline frozen-lock validation and Git
whitespace checks passed. Frontend: 79 tests, lint, TypeScript and production build passed.
Regression coverage includes cache/bump/TTL behavior, starvation avoidance, budgets, redirects,
403/429, invalid/untrusted/oversized metadata, request timeout, removed salary fields, API date
ordering and a changed-date/enrichment content hash not resending an already-sent opportunity.
Telegram used mock transport only. The disposable local PostgreSQL container/volume was removed;
Docker Desktop and the pre-existing builder were left running.

Metadata backfill is gradual. At 100 successful page requests per hourly run, 4500 entirely
un-enriched observed rows require at least about 45 runs, before accounting for new/bumped records,
network failures or time-budget exhaustion. This is not a completion ETA or a guarantee of freshness.
Until successful enrichment, absent fields remain unknown and cached fields can be stale.
No exhaustive platform inventory, future HTML stability, long-term production capacity, real
Telegram delivery, or final mobile/browser visual verification is claimed for this local package.
No dependencies, other adapters, commits, pushes, releases, production schema/data/settings or
real notification delivery were changed. Publishing and deployment need separate approval.

## Prague Residence and Work Preferences, Local Follow-Up

The owner explicitly confirmed living in Prague, Czechia and seeking remote work for employers
anywhere, or onsite/hybrid work in Prague. Profile rule v15 implements that preference; salary,
technology, experience, language scoring and notification threshold are unchanged. Structured
applicantLocationRequirements are evaluated as candidate-residence constraints, not employer
headquarters or citizenship/work-visa requirements. Czech names/CZ/CZE and broad Europe/EU/EEA/
EMEA/worldwide labels are recognized. Alternative locations are OR branches; unknown branches
are reported uncertain rather than falsely declared foreign. Known incompatible country/city
requirements reject the vacancy. Existing textual sanity checks remain in effect.

Onsite/hybrid jobs require a structured Prague/Praha city (including numbered city districts),
an explicit location label or a narrow positive office-location statement. Czechia alone is not
Prague. Foreign structured office locations are not overridden by an incidental Prague mention;
negations/headquarters mentions and New Prague are not treated as local office evidence. Unknown
or flexible employment work mode is not assumed remote. An unrecognized region, missing fields,
pending enrichment or bump-stale metadata stays uncertain and visible; arbitrary geographic prose
and legal work authorization are not exhaustively interpreted.

Djinni's public jobs form exposes country=CZE. A read-only October 5 probe of the official
employment=office&country=CZE RSS returned 19 entries. This feed is now an additional discovery
root, with the same ID deduplication, RSS limiter and whole-run request/time/byte ceilings. It is
not a separate unbounded crawl, fake pagination or a guarantee of all Prague jobs. Existing other
sources' remote search filters and all collection schedules are unchanged. Newly unchecked office
metadata precedes the larger unchecked remote backlog; failed bump retries rejoin the persisted
attempt order rather than starving ordinary expired-cache refreshes. Additional configured category
filter failures are observable as partial coverage.

The final live probe fetched only three of those office records and their three detail pages:
all metadata requests succeeded and source update dates were retained. All three failed positive
Prague workplace verification; neither Europe nor Czechia-only office data was guessed to be
Prague. The sample cap deliberately marked coverage limited, not a production collection failure.
No database write or notification was made by the probe.

Final combined local verification: 559 non-integration backend tests plus 16 isolated PostgreSQL
tests passed (575 total); full Alembic chain, model parity and downgrade/upgrade passed. Ruff
format/lint (132 files), mypy (60 source files), Bandit, offline lock (74 packages) and whitespace
checks passed. Frontend: 79 tests, lint, TypeScript and production build passed. New regressions
cover country objects/arrays/alternatives, Europe/Czech versus foreign restrictions, Prague versus
other/unknown offices, negation, missing/stale evidence, extra-feed budgets/security/deduplication,
metadata priority and v14-to-v15 recalculation not resending sent matches. Telegram is mocked.

Delivery reliability is deliberately unchanged: previously queued/failed notifications retain
their original accepted snapshot and continue retries even if later scoring rules change; they
are not silently discarded by this geography update. Sent historical opportunities are not resent
by recalculation. Production was not accessed or changed, and remains the previously verified
RSS-only v1.2.14 snapshot, not freshly verified current live state. No commit, push, release, deploy,
real Telegram delivery, dependency change or recurring automation was performed. The disposable
PostgreSQL container and its test-only volume were removed; the existing builder remains intact.
The separate October 4 security plan stays deferred. Metadata backlog, long-term production
capacity, final browser/mobile rendering and exhaustive platform coverage remain unverified.
