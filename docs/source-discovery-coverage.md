# Portfolio discovery coverage

This package fixes discovery configuration drift, not upstream access restrictions.
Publication and production rollout require explicit owner approval. Dependencies, schema,
matching weights/rules version, notification thresholds/history and polling intervals
are unchanged. The frontend/API contract is unchanged.

## Verified production baseline, October18

| Source | Effective discovery | Finding |
| --- | --- | --- |
| Djinni | Category-wide remote RSS plus Czech office RSS;10000items/512requests/600s | No technology allowlist; retain broad traversal |
| Work.ua | Python/Django/React/JavaScript/Shopify;2pages/75items/every6h | Old environment overrides hid wider defaults |
| Robota.ua | Seven generic development/Python/React searches;2pages/100items/every6h | Generic roles help, but dedicated stack coverage is incomplete; access still fails |
| DOU | One remote RSS;100item local ceiling/every30min | Observed feed contains25records; early remote-only filtering loses Prague office/hybrid |

Expanded matching skills did not automatically expand environment-pinned discovery.

## Canonical profile

`SkillRule.discovery` marks primary discovery skills in the existing canonical profile.
Queries reuse its first aliases; matching keywords are not duplicated into new lists.
The flag has no scoring effect. Current discovery skills: React,JavaScript,TypeScript,
Python,Django,Shopify/Liquid,FastAPI,Next.js,Node.js,Express,LLM API integration.
Seven generic development-role queries cover titles without explicit technologies.
Companion skills (DRF,PostgreSQL,REST,Vite,HTML/CSS,SQLAlchemy,WebSockets and supporting
tools) remain covered indirectly through those role/stack queries, not standalone
promises of exhaustive source coverage or new candidate qualifications.

`SOURCE_SEARCH_PROFILE=portfolio` merges canonical searches with operator additions.
An old five-query environment cannot silently restrict the baseline. Compose and
`.env.example` contain optional additions, not copied keyword catalogs. Explicit
`custom` mode preserves exact operator lists. Empty enabled custom lists are invalid.
Effective profile/query/feed counts are logged, never credentials/resolved settings.

## Reader-source rotation: breadth versus freshness

The pool has18queries per platform. Work.ua selects at most5/run and Robota7/run,
retaining their prior deployed search counts. Broad developer search is pinned;
other queries rotate by the persisted per-source run ordinal. Recreating an adapter
or restarting the worker does not reset it; unrelated source runs do not shift it.
Failed runs advance the rotation too. Existing pagination, item caps, retries,
deadlines and cache preservation remain bounded.

Work.ua visits its rotating pool over5ordinary runs (~30h at6-hour polling); Robota
over3 (~18h). Actual elapsed time includes jitter, runtime and failures. A particular
technology-specific search is LESS frequent than a former every-run search: this is
a deliberate breadth/freshness tradeoff, NOT faster exhaustive coverage. Broad generic
search remains every run. Response counts, sizes and detail traffic can still vary.
Do not increase batches/frequency without a separate review. Shared Reader pacing and
403 are NOT fixed here; no IP/identity rotation or access bypass was added.

## DOU supplementation

The13-feed pool comprises the general remote feed, Prague city feed and11remote
keyword feeds with `descr=1`. At most8are read/run,2seconds apart; root and Prague
are pinned, other feeds rotate over2ordinary runs (~1h). The default200-item ceiling is
shared, duplicate IDs are removed and uncached identities take priority. One failed
feed retains valid others and remains observable as a warning/partial source.

Downloads retain5MB/document,100MB/run and600s bounds; expired traversal does not
keep sleeping/requesting remaining feeds. Configured feeds must use the official
HTTPS DOU RSS endpoint. Prague office/hybrid requires positive headline-location
evidence, not a Prague office mentioned in company prose. Other offices remain
excluded. Explicit remote/hybrid/onsite mode reaches existing matching/geography
checks; legal work authorization is never inferred.

The October19 [local reliability package](dou-rss-reliability.md) raises the
processing default from100to200 without extra feed requests, interprets explicit
remote choices and adds a persistent90-attempt rolling-hour DOU guard (provider
ceiling100/hour total). Feed-query rotation is not the same as per-record refresh:
the captured11-ordinal baseline had up to7-run refresh gaps under the100-item cap;
the candidate replay reduced that to2, without promising complete live coverage.
An explicit deployment value100 remains effective until a separately approved rollout.

Three bounded official RSS reads on October18 verified: general remote25records,
FastAPI25(all contain FastAPI), Prague11. Their union had35distinct IDs absent from
the general feed. These are additional reachable records, NOT35new publications or
suitable matches. No production ingestion/notification was triggered. Other generated
queries have local tests but not every live response was fetched. RSS windows do not
guarantee complete historical inventories.

## Verification and rollout boundary

Tests cover each discovery skill, old settings, explicit customization, complete
bounded rotation, unrelated-run isolation/recreation, real PostgreSQL persistence,
unchanged scores, broad Djinni legacy-URL conversion, DOU dedup/new-item priority,
partial failures, endpoint/deadline guards, Prague evidence and service isolation.
Automated HTTP/Telegram tests use mocks. Final suite results are in the workspace
review record. Verification does not trigger production collections or test deliveries.

Before any future rollout preserve the exact existing Explore activation timestamp,
cursor and sent registry. Newly DISCOVERED older upstream publications can become
first-seen records after coverage expansion, affecting initial notifications. Review
that effect before activation. Existing ceilings/deduplication remain, but do not
claim that expanded coverage cannot increase notifications.
