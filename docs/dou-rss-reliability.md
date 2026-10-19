# DOU RSS reliability and Telegram presentation

Prepared for release v1.2.28, October19, 2026. Publication and production rollout
were subsequently approved by the owner; this document is not deployment evidence.
No dependencies or database schema changes.

## Provider clarification

The owner supplied DOU's support response on October19: there is no vacancy API;
RSS is available, with at most 100 requests/hour across feeds in total. Education,
hobby/pet projects and personal use are possible. Commercial use or commercial
services are not provided for in this response. Expanded fields are not planned.
This is a record of the supplied response, not a broader redistribution license.

## Collection

- Default processing cap increases from 100 to 200 records/run. An explicit
  deployment environment value of 100 still overrides the default; change it
  only in a separately approved rollout.
- The 13-feed portfolio pool, eight feeds/run, two pinned feeds, rotation,
  1800-second polling baseline, jitter and two-second spacing stay unchanged.
- Deduplication and uncached-identity priority stay unchanged. Exactly 200
  retained records no longer imply truncation; more than 200 does.
- A separate `dou_rss` window reserves at most 90 attempts in a rolling hour.
  Ingestion uses the existing database-backed source row lock and commits each
  reservation before network access. Failed requests and each redirect hop
  consume a reservation. A worker restart or concurrent budget instance cannot
  reset the stored window. No migration is needed.
- Earlier successful feeds are retained if the hourly guard stops traversal,
  with an explicit partial-run warning. Exhaustion before any successful feed
  produces an observable failed run, not a misleading successful empty result.
- Standalone adapters use an instance-local memory budget. Separate diagnostic
  programs and manual requests are not covered by the production source row;
  the ten-request headroom is not permission to ignore their traffic.
- Djinni's RSS 100/minute and metadata 100/hour budgets do not change.

## Remote context and matching

An explicit choice of remote or hybrid work can now retain a remote-headline
listing. Mandatory nonlocal office attendance, ambiguous hybrid requirements
and explicit denial of remote work are not treated as unrestricted remote.
Prague office/hybrid still requires positive location evidence. Country-residence
restrictions and experience/language requirements still apply. These bounded
language rules do not establish work authorization or perfect interpretation.

Talent Acquisition and User Acquisition job titles no longer qualify from
incidental stack keywords. Developer/engineer roles and company/product-domain
mentions are not blanket-rejected. Shared rules version is now
`bohdan-multi-source-v17-dou-and-role-context`; this correction applies across
sources. Existing skills, score weights and thresholds are unchanged.

## Telegram

Explore messages start with `[Source] Вакансия: 36/100 (Explore)` (or the project
heading for freelance). The long English preamble is removed. Known English
matching explanations are translated in the notification presentation layer,
including pending/stale/unknown residence metadata, stretch experience and
optional technologies. Technology names, employer content and unknown messages
remain unchanged; dynamic text is HTML-escaped after translation.

API explanation strings and stored evaluations are unchanged by presentation
localization. It does not implement frontend-wide translation. Existing sent
messages and queued/failed delivery snapshots are not rewritten or resent.
Explore activation time, score range 35–54, main threshold 55, per-cycle limits,
delivery registry and retry behavior stay unchanged. Increased discovery may
create more genuinely first-seen records, including older upstream publications;
it is not a guarantee of an unchanged notification volume.

## Offline coverage evidence

The October19 captured RSS bodies were replayed without contacting DOU. For the
same 11-ordinal cycle and eight feeds/run, the baseline retained 100 records in
every run, 193 unique IDs overall and up to seven-run refresh gaps. The candidate
retained 118–151/run, 198 unique IDs, no cap truncation and at most two-run gaps.
All 193 baseline IDs remain reachable. Five additional remote-option records
survive source filtering; this does not mean five suitable jobs. One is a User
Acquisition role correctly rejected by the new shared matcher.

These fixed-window results are not proof of complete live coverage or a
permanent refresh-time guarantee. RSS windows remain finite and missing records
do not prove closure; missing-listing deactivation remains disabled.

## Verification boundary

Regression tests cover remote choices and mandatory attendance, country
restrictions, cap boundaries, redirect/failed-request accounting, hourly window
expiry, partial-feed retention, row-lock concurrency, repeated ingestion above
the previous cap, profile upgrades without historical resends, stored retries,
Explore cutoffs, localization and escaping. HTTP and Telegram clients are mocks.
Final check results and the actual local PostgreSQL version are recorded in the
workspace completion report. No production collection or delivery is triggered.

Final local verification:1430 non-integration tests,123 integration tests on the
pre-existing native PostgreSQL18.3, and the same full123 integration tests on the
reviewed Linux/AMD64 PostgreSQL17.11 fixture all passed. Existing migrations reached
head in the17.11 fixture. The task-owned container and anonymous test volume were
removed. This is local compatibility evidence, not exact-commit GitHub CI or a
production deployment/ARM64 smoke test; those remain release-stage checks.
