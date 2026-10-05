# Djinni RSS Migration

Reviewed: 2026-10-05. Initial adaptive RSS is deployed in v1.2.14; the approved enrichment/date
follow-up is implemented locally and still needs separate publication/deployment approval.

The historical RSS-only verification below is not evidence for the later enrichment stage.
See [provider clarification and current implementation](djinni-provider-clarification-2026-10-05.md).

Current local enrichment/date verification: 502 non-integration backend tests plus 16 isolated
PostgreSQL tests passed, as did the full migration chain, model/schema parity, downgrade/upgrade,
Ruff, mypy, Bandit, offline lock and Git whitespace checks. Frontend passed 79 tests, lint,
TypeScript and production build. Live sample: three RSS records and three public pages,
all enriched successfully, without persistence or notification delivery. Automatic residence-based
eligibility is still pending owner country confirmation; production capacity and mobile/browser
visual checks were not performed for this package. Production remains RSS-only v1.2.14.

## Historical Initial RSS Migration

## Provider Reply

The owner supplied a reply signed by Serhii at Djinni. It recommends the official
[RSS feed](https://djinni.co/jobs/rss/) and filters from the [jobs page](https://djinni.co/jobs/).
The reply states a limit of 100 RSS requests per minute and permits displaying full vacancy
descriptions. It discourages the previous HTML/JSON-LD access method and warns that the
page representation may change. No independent mailbox/header authentication or legal
certification is claimed. A retention period and future commercial-product scope are not
specified by this reply.

## Observed Feed Behavior

Bounded read-only checks on October 5 found:

- RSS 2.0 with `title`, `link`, `description`, `pubDate`, `guid`, and repeated `category` fields.
- The base feed and the remote/non-HR filtered feed each returned 100 items.
- `page=2` returned the same 100-item filtered response, not another page.
- `employment=remote` and `employment=office` returned different listing sets. The jobs page
  advertises an RSS URL carrying `employment=remote` when that filter is explicitly selected.
- `primary_keyword=Python` returned 95 items; `primary_keyword=JavaScript` returned 25.
  These probes establish supported filtering, not exhaustive category coverage.
- The legacy `/jobs/l-nonhr/remote/` page advertised RSS with only `editorial=nonhr`;
  the adapter explicitly sets `employment=remote` instead of trusting that legacy path.
- No separate company, salary, geographic eligibility or employment-type fields were present.
  Descriptions may contain these facts, but the adapter does not invent structured values.
- The initial single-feed adapter normalized 100 unique remote-filtered listings. After the
  authorized adaptive extension, a live traversal normalized 4506 unique listings in 311 RSS
  requests, with no malformed items, warnings or unresolved limit flag. A subsequent end-to-end
  check stored and evaluated 4507 listings using 311 requests; the site changed between snapshots.
- The RSS channel advertised 170 category strings. Some are headings, not effective query values:
  for example, `primary_keyword=Development` repeated the general feed. Category partitions whose
  returned items do not identify that category are excluded from claimed partition coverage.
- Marketing's base feed returned 100 items; experience partitions returned distinct older items
  (no experience: 12, one year: 87, two years: 98, three years: 47, four years: 22, five years: 20
  in the bounded discovery probes). Repeated category query parameters act as a union; combining
  Python and React.js yielded the 100-item cap, so selected categories also need separate feeds.
- Experience (`no_exp`, `1y`..`10y`) and English filter values were verified from the official
  public jobs filter form for this implementation. The runtime adapter never fetches that HTML.

The support reply's statement about more than 200 vacancies does not demonstrate that a
single current feed returns more than 100. A subsequent provider clarification confirms that
RSS `pubDate` is update/bump time, not original publication. The feed is rolling, not a full
inventory. Disappearance from it does not deactivate previously saved vacancies.

## Implementation and Compatibility

- Preserve the source name `djinni` and numeric vacancy IDs extracted from trusted HTTPS
  Djinni listing URLs. No schema migration or historical-row rewrite is required.
- Fetch only RSS. Do not follow redirects or fall back to HTML, JSON-LD, readers or detail pages.
- Keep `DJINNI_JOBS_URL` as the existing configuration key. The new default is
  `https://djinni.co/jobs/rss/?editorial=nonhr&employment=remote`. Convert the previous default
  path and supported plain jobs/remote paths, preserving query filters. Reject unsupported
  legacy paths rather than silently discarding their filter meaning.
- Force `employment=remote` when `DJINNI_REMOTE_ONLY=true`. With remote-only disabled, the
  configured filter determines work mode; an unfiltered feed does not imply remote work.
- Retain `DJINNI_MAX_PAGES` and the corresponding constructor argument as ignored compatibility
  inputs. RSS pagination is not implemented because the observed page parameter was ineffective.
- Remove the hard settings ceiling of 200: default `DJINNI_MAX_ITEMS=10000`, accepted range
  1..10000. An existing explicit deployment value of 200 still applies. This is a local safety
  ceiling, not a promise that the upstream feed contains that many items.
- Preserve RSS HTML in raw evidence and convert descriptions to readable plain text for
  matching/display, preserving paragraphs, lists, inline words and entities, excluding script,
  style and template content. Prefer standard `content:encoded` over a shorter description when
  present. Blank fresh descriptions never erase saved text: use a marked cache fallback and
  warn, or skip an unknown incomplete item with a malformed-item warning.
  Preserve previously stored company, salary, employment type and location metadata for known
  IDs, recording `metadata_origin=previously_stored_metadata`. These retained fields may be
  stale; RSS cannot refresh or verify them. New listings leave unavailable structured fields empty.
  This can reduce cross-source duplicate confidence and change scores due to missing input data,
  although matching rules and their version are unchanged.
- Apply the unchanged polling schedule and automatically subdivide a feed with at least 100
  items: first its RSS-advertised category catalog (or the owner's selected categories), then
  experience, then English. Preserve all unrelated query filters and do not replace a single
  already-selected experience or English value. Group/catalog entries ignored by the provider
  are not expanded. An ignored explicit initial category is an error, not silently broader data.
- Deduplicate by numeric ID across every response. Keep already-yielded parent records even if
  later partitions fail. Verify that child responses reproduce their parent's observed IDs;
  if not, warn and mark limited coverage rather than claiming a successful partition replacement.
- Enforce `DJINNI_MAX_FEED_REQUESTS=512`, `DJINNI_REQUEST_DELAY_SECONDS=0.8` (minimum 0.7), and
  `DJINNI_RUN_TIMEOUT_SECONDS=600`, in addition to the unique-item cap. The limiter spaces starts
  within an adapter, including repeat fetches; concurrent fetches on the same instance are rejected.
  At the minimum spacing, a rolling minute contains at most 86 starts, below the provider's 100.
  The worker's existing advisory lock prevents overlapping worker cycles. No distributed
  account-wide limiter across independently launched processes/databases is claimed.
- Do not immediately retry failing feeds. A 403/429 stops the remaining traversal. An isolated
  failed partition does not discard later valid feeds; three consecutive failures stop traversal.
  Record failures and incomplete coverage through existing source-run warnings/health mechanisms.
- Stream with a 5,000,000-byte decompressed-response ceiling, a 100,000,000-byte aggregate response
  budget per traversal, and a whole-response deadline bounded by remaining traversal time.
  Parse with defusedxml, rejecting DTDs/entities. Validate item URLs and normalization before
  yielding; malformed items generate a recorded warning, not silent data loss.
- Set `limit_reached` for unresolved saturated terminal feeds, incomplete partitions, safety-budget
  stops or the local item cap, not merely a saturated parent successfully covered by subdivisions.
  Preserve existing health-alert semantics; no incomplete-coverage alert is suppressed.

## Remaining Provider and Rollout Boundaries

Adaptive RSS collection is implemented and verified beyond 200 records. In the live checks every
visited terminal partition was below the observed saturation threshold, the child-coverage checks
passed, and no safety budget was reached. This is evidence about those RSS snapshots, not proof
of every vacancy on Djinni, an atomic platform inventory, future filter stability, or access to
hidden/removed jobs. Future saturated leaves remain explicitly limited; no indefinite subdivision
or access-control bypass is implemented.

The default collection now takes minutes and makes hundreds of requests, not one request. It can
store considerably more listings and create a backlog of newly discovered matching notifications.
Existing notification queue/cycle limits and sent-opportunity deduplication remain unchanged;
do not disable delivery to conceal this rollout consequence. More input increases DB/storage,
bandwidth and worker resource use; production performance/capacity is not proven by an in-memory
SQLite check. The security plan's production-resource work remains separate.

Before a separately authorized rollout, set the deployment's unique-item limit to the intended
10000 and review the new request/time budgets. An explicit old `DJINNI_MAX_ITEMS=200` is deliberately
not overridden by code, and would still truncate collection. The owner does not need to edit
configuration or search vacancies manually; perform the approved rollout adjustments for them.

Company, salary, geographic eligibility and employment-type metadata absent from RSS cannot be
reliably reconstructed for every job. Description-based existing rules still apply, but restrictions
available only in omitted source metadata can be missed. A focused provider follow-up can request
these structured fields and clarify retention or official traversal/export. Do not reintroduce
HTML/JSON-LD scraping as an undocumented enrichment fallback or guess missing fields.

The metadata gap was checked against the current adapter on October 5. A focused Ukrainian
support reply and field-impact summary are prepared in
[the RSS metadata follow-up](djinni-rss-support-followup.md). The owner subsequently reported
sending it and supplied a response: RSS will not be expanded yet, but the previous careful
method may supplement missing fields; `pubDate` is bump/update time and RSS has no pagination.
See [the clarification and proposed package](djinni-provider-clarification-2026-10-05.md).
Deployed v1.2.14 is still RSS-only. Enrichment, separate source update dates, historical date repair
and frontend labels are now implemented locally, not deployed. The owner subsequently confirmed
Prague, Czechia: v15 matching reads structured candidate residence requirements and accepts office/
hybrid work only with positive Prague evidence. Czech-office RSS discovery supplements remote
feeds within the same budgets. Unknown geography remains explicitly uncertain; this is not an
automatic work-authorization check or a claim of complete Czech/Prague market coverage. See the
latest dated clarification for verification of this local follow-up.

## Verification Boundary

Final expanded verification: 484 non-integration backend tests and 10 isolated real-PostgreSQL
integration tests passed (494 total). Targeted adapter/configuration coverage includes 70 cases.
Ruff formatting/lint, mypy across 59 source files, Bandit, the frozen lock check and Git whitespace
checks passed. No dependency versions changed. PostgreSQL tests used the driver's supported Windows
Selector event-loop policy and the already-pinned PostgreSQL 17 image in a dedicated test container.
The dedicated test container and its anonymous volume were removed after verification.
Docker Desktop remains running; the pre-existing builder container was left untouched.

Synthetic tests cover legacy filter conversion, full text, IDs, duplicate removal, more than
200 items if returned by upstream, cached metadata, rolling-feed semantics, malformed items,
HTTP errors, redirects, XML attacks, byte caps, response deadlines and ingestion migration.
A notification regression checks that a changed content hash after migration does not resend
a previously sent match. Telegram uses an isolated mock transport, not a real delivery.
Additional regressions cover adaptive category/experience/English traversal, preserved configured
filters, ignored category headings, missing parent items, partial failures, rate limiting, request/
aggregate-byte/time budgets, readable/hidden-content handling, standard full-content extensions,
cached empty-description recovery and overlapping fetch protection.

An isolated in-memory SQLite end-to-end run using real RSS created 4507 listings/opportunities,
evaluated all 4507, and had zero errors, warnings, duplicates or deactivations. Replaying the same
captured snapshot created zero rows and left all 4507 listings/evaluations unchanged. No second
network traversal was used for replay. Notification-delivery row count was zero; no Telegram client
or real delivery was used. The temporary database existed only in process memory and was disposed.

No dependencies, other source adapters, matching rules, API contracts, production settings,
stored production data, GitHub releases or notification delivery were changed by this work.
The October 4 security-fix plan is deferred, not completed; this adapter's bounded RSS fetch
does not close the audit finding about other sources' unbounded responses.
