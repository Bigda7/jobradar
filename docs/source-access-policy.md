# Source Access Policy

Last updated: 2026-10-20 (owner-supplied Robota.ua partner export & IP allowlist confirmation in v1.2.29; DOU reliability in v1.2.28)

The October 20 Robota.ua reply confirms server IP allowlisting, `vacancies_dodomu.json`
schema and active-only closure semantics, and `Europe/Kyiv` export scheduling. Version
`v1.2.29` integrates streaming partner export synchronization, exact closure reconciliation,
and direct `api.robota.ua` detail enrichment.

The October 19 DOU reply clarifies the supported RSS route and project scope.
The [DOU reliability package](dou-rss-reliability.md) implements a separate
90-request rolling-hour safety budget.

October 18 implementation note: the owner requested an all-source discovery
review and correction of missed stack coverage, then explicitly approved publication
and production deployment. The portfolio profile, bounded
rotation and official DOU supplementation are described in
[discovery coverage](source-discovery-coverage.md). The approved rollout expands the
query pool without increasing per-run search counts or polling frequency. Provider
permissions are unchanged. Work.ua
access remains unresolved; no access-bypass method is added.

The subsequent [shared Reader pacing package](reader-request-pacing.md)
reduces burst traffic across Work.ua and Robota without increasing search budgets.

This document records the access method, attribution requirements, retention constraints, and
commercial-use risk for the four sources supported by the current production registry. It is an
engineering decision record, not legal advice. Terms and robots directives can change and must be
checked again before a public or commercial release.

## Operating Rules

- Preserve the canonical source URL and show the source name wherever a listing is displayed.
- Do not increase request frequency or pagination for a permission-required source.
  The October 18 owner-approved query rotation retains those limits; it is not provider permission.
- Prefer documented public APIs, partner exports, and RSS feeds over HTML or undocumented frontend APIs.
- Respect upstream request limits even when the adapter can technically request more data.
- Re-review this table before using a source in any public, commercial, or multi-user product.
- Do not use a proxy or reader service to bypass an upstream access restriction. The
  existing Work.ua reader integration is not an exception or evidence of permission.

## Source Register

| Source | Access method | Current position | Attribution and retention | Commercial or multi-user action |
| --- | --- | --- | --- | --- |
| Djinni | Official filtered RSS plus bounded public JobPosting page metadata enrichment | RSS recommended and full descriptions allowed; missing-field supplementation described as acceptable in the owner-supplied follow-up | Keep attribution and original URL; up to 100 RSS requests/minute; local HTML ceilings: 100/run and at least 2 seconds spacing, with 100/hour persistence across runs; no retention duration specified | Reconfirm terms for a changed commercial or multi-user scope |
| DOU Jobs | Official vacancy RSS feeds with persistent 90/hour safety guard | Owner-supplied October 19 reply supports education, hobby/pet and personal use; no API or expanded fields planned | Keep DOU attribution and original URLs; provider ceiling is 100 RSS requests/hour in total; no retention duration or separate public-description reuse terms specified | Commercial use and commercial services are not provided for in the reply; obtain a separate arrangement or replace this source before commercial use |
| Robota.ua | Hybrid streaming partner JSON export (`vacancies_dodomu.json`), direct `api.robota.ua` detail enrichment, and bounded remote search discovery (`v1.2.29`) | Provider support offered partner export without a contract, allowlisted the server IP, and confirmed active-only snapshot closure semantics and `Europe/Kyiv` update schedule | Preserve canonical `robota.ua` links; close absent IDs only after a complete export snapshot download | Confirm future commercial reuse; do not infer blanket permission from partner export access |
| Work.ua | Search and vacancy pages through a third-party read-only text reader | Enabled for the existing public personal portfolio; permission unverified | Keep Work.ua attribution and the original URL; the public JobRadar API also exposes vacancy descriptions | Retain polling, pagination and per-run limits; October 18 broader query rotation is owner-approved only; seek explicit permission before commercial or multi-user use |

## Robota.ua Partner Export Review

On October 16 the owner supplied a support reply distinguishing the employer-only API
from partner aggregation access. Support supplied JSON export documentation and said
no contract is needed for the described service. In a subsequent reply, support confirmed
that the JobRadar server IP was allowlisted, that `vacancies_dodomu.json` includes title
and HTML description fields, that absence of a previously seen ID in a complete export
snapshot means the vacancy is closed, and that the 5 daily updates follow `Europe/Kyiv`
time with seasonal clock changes. See
[export review](robota-provider-export-review-2026-10-16.md).

## Work.ua Review for the Public Portfolio

The current registry enables Work.ua by default. The adapter requests configured Work.ua search
and vacancy paths through `r.jina.ai`, not directly from Work.ua, and stores parsed vacancy text.
JobRadar's public `/jobs` and `/matches` responses include the stored description, title,
salary, source name, and original URL. This is public redistribution of source content, not only
private job-search automation. The reader's ability to return a page does not establish
permission from Work.ua, and the application cannot verify how the reader handles upstream
security challenges or access controls.

The [published robots directives](https://www.work.ua/robots.txt), checked on 2026-09-28,
include `Disallow: /en/jobs-*-/` under `User-agent: *`. The currently configured
`/en/jobs-remote-<term>/` search paths do not match that specific trailing-hyphen pattern.
This corrects the previous blanket statement that all configured paths were disallowed. It
does not establish permission for automated retrieval, use of a third-party reader, or public
republication. Robots directives, search paths, and bot protections can change independently.

The [published service conditions](https://www.work.ua/about-us/conditions/) do not serve as a
documented permission grant to JobRadar for this use, and this repository contains no record of
such a grant. The engineering status is therefore **unresolved for the intentionally public
portfolio**. On 2026-09-28, the owner decided to keep Work.ua collection and display enabled
for the existing public personal portfolio. This operating decision is not authorization from
Work.ua. On October 18 the owner separately approved broader portfolio query rotation
with unchanged polling, pagination and per-run search limits. This supersedes the
earlier instruction to freeze the exact query list, not the unresolved permission
status. Data reuse remains unchanged. Reassess the
integration if upstream terms, access controls, or product scope change, and review the terms
again before another public release.

## Primary References

Djinni's subsequent owner-supplied reply permits the described previous careful method for
missing metadata, confirms `pubDate` is update/bump time and confirms a 100-item feed without
pagination. This does not authorize unlimited HTML traffic or remove technical stability risks.
The v1.2.15 package adds bounded enrichment and separates source updates from publication dates;
it does not use HTML as an RSS-failure fallback. The owner confirmed Prague, Czechia, and
matching checks structured residence restrictions and positive Prague office/hybrid evidence.
Unknown geography and legal work authorization are not inferred. See
[provider clarification](djinni-provider-clarification-2026-10-05.md).

- Djinni official RSS: https://djinni.co/jobs/rss/
- Djinni supported search filters: https://djinni.co/jobs/
- Djinni support reply supplied by the project owner on 2026-10-05; the response and actual
  RSS behavior are summarized in [Djinni RSS review](djinni-rss-review.md).
- Robota.ua partner export review: [`docs/robota-provider-export-review-2026-10-16.md`](robota-provider-export-review-2026-10-16.md)
- Work.ua robots directives: https://www.work.ua/robots.txt
- Work.ua service conditions: https://www.work.ua/about-us/conditions/

## Open Source Access Questions

1. Confirm Robota.ua commercial reuse terms and re-review Djinni and DOU terms before changing the
   current project into a commercial or multi-user product.
2. Work.ua permission for automated collection and public display remains unverified under the
   owner's decision to keep the existing portfolio integration enabled. Commercial or multi-user
   reuse requires a separate review and explicit permission.
