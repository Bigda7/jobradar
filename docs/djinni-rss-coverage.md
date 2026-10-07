# Djinni RSS Uncategorized Coverage and Fair Rechecks

Package prepared for backend v1.2.20 on 2026-10-07. Publication and deployment evidence
are recorded separately in the workspace handoff. No API, schema, dependency, matching
or notification change.

## Verified Problem

The ordinary production review found one uncategorized root RSS item behind eight partial
runs. Its description was retained, but category-only subdivisions could not reproduce it.
Catalog headings rejected as query categories also prevented otherwise valid child rechecks.
Separately, three affected parents received 11, one and zero rechecks from a global budget
of 12 because scheduling exhausted each parent's children before considering the next.

## Local Behavior

- A saturated feed that normally splits into categories and contains an item without a
  nonblank category keeps its category traversal and adds an independent experience split.
  Saturated experience fallback feeds split by English, never back into categories.
- These fallback feeds preserve all configured filters, including remote/office, country,
  repeated selected categories and existing experience/English constraints. No invented
  uncategorized query parameter or HTML fallback is used. No RSS pagination is introduced.
- Empty, whitespace-only and absent item categories trigger the same fallback decision.
  Category values in stored raw evidence are not rewritten or guessed.
- Parent records are yielded before their child traversal. The existing numeric-ID
  deduplication retains one listing/description per run, including parent-only items.
- Ignored category responses still contribute no IDs to coverage and are not recursively
  traversed. Their known outcome no longer prevents rechecking other processed children.
  Failed/unvisited child requests remain distinct from ignored categories and cannot
  masquerade as completed reads. Configured-category and rechecked-category errors remain
  visible through the existing warnings. Ignored filters in actual experience/English
  subdivisions are explicitly reported, not excused as catalog headings even when their
  parent IDs happen to be reproduced elsewhere.
- Eligible parents receive one unique child recheck per round. Shared child URLs consume
  one visit. The existing ceiling remains 12; more than 12 affected parents or a shared
  budget exhausted early cannot guarantee a visit for every parent.
- Retained parent/child ID unions remain authoritative. Persistent mismatches, failed
  requests, saturated terminal feeds and exhausted limits remain visible; no mismatches
  are considered resolved merely because their descriptions were saved.
- A sanitized informational djinni_rss_uncategorized_coverage event records the parent
  fingerprint/allowlisted filters and fallback-child count. It does not claim completion.
  Existing bounded consistency diagnostics and real source warnings remain enabled.

## Limits and Tradeoffs

Schedules, pacing, shared persisted RSS windows, feed-request/item/time/byte ceilings,
metadata ceilings and notification idempotency are unchanged. A fallback branch may add
up to 11 experience and 88 English visits when starting without those constraints; all
consume the existing run budgets. A busy run can consequently leave less room for other
partitions or metadata before its existing deadline. This is extra coverage work, not
extra capacity or a higher allowed request rate.

Fallback is triggered by evidence in a fetched saturated feed. If no uncategorized item
appears in that response, the collector cannot infer an unseen uncategorized tail.
Provider responses may change between requests or ignore filters. Even a clean run is
not proof of exhaustive coverage of all Djinni vacancies. No live collection was triggered
to validate the new algorithm, and its production effect remains unverified until a
separately authorized deployment and observation of ordinary runs.

## Verification

Regression scenarios cover discovery beyond the root 100 items, office/Czechia and
repeated-category filter preservation, English fallback, a missing category catalog,
request-ceiling exhaustion, 403/429/502 fallback failures, ignored headings, persistent
missing IDs, saturated leaves, fair 4/4/4 recheck allocation and early stop after 1/1/1
actual rechecks. A SQLite ingestion regression checks expansion from 100 to 101 listings,
repeat-run idempotency, retained descriptions and no deactivation.

The new PostgreSQL regression seeds 98 persisted RSS requests and proves the fallback
cannot make a third HTTP call after the two remaining slots are used. Already-fetched
records remain available; the persisted window ends at 100.

All 754 backend tests passed: 734 non-integration and 20 PostgreSQL tests. Full migration
upgrade, model parity and downgrade/upgrade checks passed on the isolated test database.
Ruff lint/format, strict mypy, Bandit, lock consistency and Git whitespace checks passed.
External RSS/HTML/Telegram
traffic in tests is mocked. The disposable PostgreSQL container used only loopback and
tmpfs; it was identity/label/mount checked and removed after verification. Existing local
Docker resources were preserved. Production data and notification delivery were untouched.

## Next Boundary

Local implementation approval does not authorize publication or deployment. A release
would require an explicit request, release-version preparation and the existing release
gates, followed by ordinary-run observation for request volume and coverage. Work.ua
challenge handling and Telegram transport behavior are outside this package.
