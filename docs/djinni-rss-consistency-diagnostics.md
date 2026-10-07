# Djinni RSS Consistency Diagnostics

The diagnostic-only package below is historical and was deployed in v1.2.19. The v1.2.20
behavior follow-up is documented in [RSS coverage](djinni-rss-coverage.md).
That package changes traversal/recheck allocation, not these original
diagnostic-only guarantees. Consult the workspace handoff for the current production state.

Status: implemented and verified on 2026-10-06; owner authorized combined v1.2.19 publication
and deployment. Runtime rollout evidence is recorded separately in the workspace handoff.
Production baseline is v1.2.18. No migration, API contract, dependency or source-policy change.

## Purpose

The post-deployment review found five partial Djinni runs with one parent RSS ID not
reproduced by child feeds. Existing warning summaries cannot identify the ID, filters or
recheck outcome. These diagnostics add evidence for future ordinary runs; they do not
retroactively establish the cause of those five discrepancies or resolve the discrepancy.

## Events

`djinni_rss_consistency_diagnostic` is an informational event for an unresolved mismatch
or a parent selected for rechecking, including successfully resolved parents.

- `missing_count` and bounded `missing_ids` describe the union of the discrepancy at
  recheck selection and the final discrepancy. `remaining_missing_count` and
  `remaining_missing_ids` describe the final comparison only.
- `parent` and each child contain a stable feed fingerprint and sanitized filters. Category
  values are fingerprints, not arbitrary strings. Experience, English, employment and
  editorial values use explicit allowlists. Country accepts only three uppercase letters.
  Unknown query keys are omitted and invalid known values are redacted. Full URLs, titles,
  descriptions, bodies, headers and exception messages are not included in these new events.
- `parent_read`, child `initial` and child `recheck` record logical visit sequence, actual
  HTTP attempt count, request elapsed time, HTTP error status when available, response item
  count, accepted ID count and target IDs present in that response. Attempt counts can be
  zero if the local request budget prevents an HTTP attempt. A missing initial/recheck is null.
- Outcomes distinguish processed responses, failed requests, ignored categories and
  processing stopped by time/item budgets. `recheck_scheduled` is separate from an actual
  visit, so a queued recheck stopped by the shared budget is not presented as completed.
- `added_ids` and `removed_ids` are counts comparing two processed responses only. They are
  null for a failed, ignored or incomplete comparison, not evidence of provider-side removal.
  They do not delete database records; existing union-based coverage remains authoritative.
- `recheck_selection` distinguishes eligibility, an unreached consistency phase, unobserved
  children and a missing-ID count above the existing recheck threshold. `stop_reason` shows
  completed traversal, request/time/item budget or source stop. The eligible state does not
  promise every child was scheduled: the existing 12-visit ceiling still applies.

At most 20 parents are logged per run, with 16 children and 10 target IDs per sample and
four values per filter. Attempted rechecks and failed/unprocessed initial children take
priority in child samples. Full aggregate counts and omitted counts remain visible.
`djinni_rss_consistency_diagnostics_truncated` records additional omitted parents.
Truncation never suppresses the existing warnings or changes run health.

## Unchanged Behavior and Limits

No extra requests, retries, parent refreshes, live probes or metadata fetches are introduced.
The collector keeps its current schedule, request/time/byte limits, pacing, deduplication,
retained parent descriptions and warning text. Matching, notification delivery and frontend
are unchanged. There is modest additional in-memory bookkeeping and bounded log volume.
These events describe responses, not why Djinni returned them. A moving feed, unsupported
filter or unavailable child cannot be inferred from a summary alone.

## Verification and Next Step

121 Djinni source/diagnostic tests passed, including ten diagnostic scenarios and bounded
redaction/truncation regressions. All 704 non-integration backend tests passed; 19 PostgreSQL
integration tests were excluded and not rerun for this observability-only package. Ruff
lint/format, strict mypy on 63 source files, Bandit, lock consistency and Git whitespace
checks passed. HTTP and notification test traffic was mocked; no live collection or send.
Frontend remains clean and was not rebuilt because its contract did not change.

After verified publication/deployment, inspect ordinary source-run logs and
correlate target IDs, feed fingerprints, visit order and actual recheck results. Propose
behavior changes only after that evidence. Successful rechecks do not prove exhaustive
coverage of all Djinni vacancies. This document does not itself grant deployment authority.
