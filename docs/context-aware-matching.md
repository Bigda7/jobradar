# Context-aware portfolio matching

Implemented locally on October 16, 2026. Not committed, published or deployed.

Rules version: `bohdan-multi-source-v16-context-aware-portfolio`.

## Evidence and scoring

`matching/requirements.py` is the shared interpretation layer for technology,
experience and language mentions. It preserves HTML/list/paragraph boundaries,
recognizes common section headings (including inline headings), and evaluates each
occurrence independently. A later required occurrence is not cancelled by an earlier
optional one. Company names do not supply positive skill matches.

Evidence classes are absent, company/product domain, negated, optional, unspecified
and required. Required evidence wins conflicts. Explicit supported alternatives
such as Python or Java, or one-of parenthesized framework lists, do not require all
listed technologies. Structured skill tags are a conservative fallback when no
textual evidence is available, not a way to override an explicit optional/negated
description. Generic domain suffixes are removed from the role title used for
specialization checks; the original title remains unchanged in storage and responses.

New demonstrated core skills include FastAPI, Next.js, Node.js, Express, WebSockets
and LLM API integration. Supporting tools are limited to eight combined points;
the overall skill contribution remains capped at 45. Optional positive skills have
reduced weight. Supporting or optional skills alone cannot qualify a main match.
The stale Node-without-Python penalty is removed. NestJS, React Native, smart-contract
engineering, model training and specialized semantic-graph requirements are not
silently treated as demonstrated skills just because Node, React or Python match.

Optional foreign technologies produce a concern, not the old 15-point penalty.
Unspecified foreign mentions retain a bounded warning/penalty. Required foreign
stack is capped below the main threshold. A blockchain company/product is not
itself a blockchain engineering requirement. This mechanism applies to the canonical
technology rules generally, not just Web3.

Experience requirements around two years and mixed Junior/Middle roles retain a
nonzero exploratory evaluation, capped below 55, with explicit concerns. Mandatory
three-plus years, Senior/Lead roles, zero pay, scams and geographic exclusions are
not opened. Optional experience/company age is not a candidate requirement. English
word-number experience and structured seniority are also checked. Czech advertisement
language no longer automatically costs 20 points; actual unsupported working-language
requirements still reject a role. Uncertainty about Czech remains visible.

## Read-only exploration API

`GET /matches` adds optional `max_score` (0..100, default 100). It rejects an upper
bound below `min_score` with 422. Both bounds, source eligibility and hidden-state
filters apply before total counting, ordering and pagination. Existing clients and
response schemas are unchanged. Frontend Explore requests `min_score=35&max_score=54`.
This range is exploratory, not a promise that every record is suitable.

## Verification and snapshot

- 888 backend tests passed, including all 28 PostgreSQL integration cases.
- Eight new PostgreSQL cases cover range boundaries/counting/pagination and
  v15-to-v16 sent/queued/failed notification transitions.
- Ruff formatting/lint, strict mypy (65 source files), and Bandit passed.
- 101 frontend tests, ESLint, TypeScript and production build passed.
- Browser checks used synthetic opportunities in an isolated in-memory SQLite API,
  not production: desktop/mobile, light/dark, keyboard selection, URL reload,
  match concerns and leaving Explore through ordinary tiers.

Production public vacancy fields were exported once using a bounded READ ONLY,
REPEATABLE READ transaction and rollback. No application scoring service, source
collection, delivery, settings change or production write was triggered. Export
snapshot: October 16, 2026 13:15:19 UTC (15:15:19 Prague), 8,791 active non-hidden
employment opportunities with an enabled active canonical listing.

The local v15 package was preserved before edits. Both versions scored precisely
the same snapshot without a live exchange-rate provider: main matches 58 -> 60,
12 promoted, 10 demoted (all ten remain in Explore), 275 in Explore 35..54, 692
changed scores. These are algorithmic transitions, not 12 verified job offers or
a complete source-coverage measurement. Final local comparison took 48.410 seconds;
this is not a production worker performance benchmark. Clause marker caching is
candidate-local; vacancy text is not retained in a global scoring cache.

The snapshot deliberately allowlists public matching fields and excludes unrestricted
raw provider data, application URLs and credentials. Currency conversion/application
flow warnings may therefore differ from production evaluations. The comparison is
between identical offline inputs, not a claim to reproduce every production score.
Ignored `.deploy` data and frontend `work` screenshots are local evidence, not release
content. `scripts/preview_matching.py INPUT OUTPUT` repeats the pure local comparison
when each input row contains a baseline result and the input names its rules version.

## Notification and rollout boundaries

The delivery implementation and notification threshold were not changed. Tests use
recording clients, never real Telegram. Already sent identities are not resent after
a rules upgrade; stored queued/failed messages retain retry behavior; a supplied
first-seen cursor does not create a new historical backlog. A deliberately absent
historical cursor is still an operator choice, not made safe by a rules-version bump.

For a separately authorized rollout, deploy the backend and allow versioned scoring
to finish before publishing the frontend that sends `max_score`. An old backend
does not enforce the new upper bound. Keep existing notification scan/retry state;
do not force a historical dispatch. No schema/dependency/source-budget changes are
required by this package. No rollout was performed.

## Limits

This is a deterministic rule parser, not complete natural-language understanding.
Unrecognized headings, unusual negation, distant cross-sentence references, ambiguous
slash lists and technologies outside the canonical rules can still be misinterpreted.
Missing source metadata, incomplete descriptions and stale records remain source
limitations; matching cannot reconstruct them. The comparison is not an independent
human label set or a precision/recall study. The original Spredo ad was not recovered;
its reported product-domain distinction is covered by synthetic regressions.

No external LLM or new dependency is used. Existing residence/language levels and
source access policy remain in force. Robota export access and the separately
recorded tracker status-menu Escape bug are not fixed by this package.
