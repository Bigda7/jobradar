# Source Access Policy

Last reviewed: 2026-10-05 (Djinni update; other source reviews unchanged)

This document records the access method, attribution requirements, retention constraints, and
commercial-use risk for the four sources supported by the current production registry. It is an
engineering decision record, not legal advice. Terms and robots directives can change and must be
checked again before a public or commercial release.

## Operating Rules

- Preserve the canonical source URL and show the source name wherever a listing is displayed.
- Do not increase request frequency or pagination for a permission-required source.
- Prefer documented public APIs and RSS feeds over HTML or undocumented frontend APIs.
- Respect upstream request limits even when the adapter can technically request more data.
- Re-review this table before using a source in any public, commercial, or multi-user product.
- Do not use a proxy or reader service to bypass an upstream access restriction. The
  existing Work.ua reader integration is not an exception or evidence of permission.

## Source Register

| Source | Access method | Current position | Attribution and retention | Commercial or multi-user action |
| --- | --- | --- | --- | --- |
| Djinni | Official filtered RSS plus bounded public JobPosting page metadata enrichment, deployed in v1.2.15; faster scheduling/persistent budgets are a local candidate | RSS recommended and full descriptions allowed; missing-field supplementation described as acceptable in the owner-supplied follow-up | Keep attribution and original URL; up to 100 RSS requests/minute; local HTML ceilings: 100/run and at least 2 seconds spacing, with 100/hour persistence in the local candidate, not a provider allowance; no retention duration specified | Reconfirm terms for a changed commercial or multi-user scope |
| DOU Jobs | Official vacancy RSS feed | Approved | Keep DOU attribution and the original URL | Recheck feed terms before commercial launch |
| Robota.ua | Public search pages and public vacancy details | Conditional | Preserve the original meaning and include a mandatory source link | Confirm commercial reuse before a commercial or multi-user launch |
| Work.ua | Search and vacancy pages through a third-party read-only text reader | Enabled for the existing public personal portfolio; permission unverified | Keep Work.ua attribution and the original URL; the public JobRadar API also exposes vacancy descriptions | Keep current polling and coverage unchanged; reassess terms and seek explicit permission before commercial or multi-user use |

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
Work.ua. Keep the current polling, search coverage, and data reuse unchanged. Reassess the
integration if upstream terms, access controls, or product scope change, and review the terms
again before another public release. This decision changes documentation only; it does not
alter the running source, remove stored listings, or make a legal determination.

## Primary References

Djinni's subsequent owner-supplied reply permits the described previous careful method for
missing metadata, confirms `pubDate` is update/bump time and confirms a 100-item feed without
pagination. This does not authorize unlimited HTML traffic or remove technical stability risks.
The v1.2.15 package adds bounded enrichment and separates source updates from publication dates;
it does not use HTML as an RSS-failure fallback. The owner confirmed Prague, Czechia, and v15
matching checks structured residence restrictions and positive Prague office/hybrid evidence.
Unknown geography and legal work authorization are not inferred. Production rollout status is
recorded separately in the workspace handoff. See
[provider clarification](djinni-provider-clarification-2026-10-05.md).

- Djinni official RSS: https://djinni.co/jobs/rss/
- Djinni supported search filters: https://djinni.co/jobs/
- Djinni support reply supplied by the project owner on 2026-10-05; the response and actual
  RSS behavior are summarized in [Djinni RSS review](djinni-rss-review.md).
- Robota.ua usage notice: https://robota.ua/?goHome=true
- Work.ua robots directives: https://www.work.ua/robots.txt
- Work.ua service conditions: https://www.work.ua/about-us/conditions/

## Open Source Access Questions

1. Confirm Robota.ua commercial reuse terms and re-review Djinni terms before changing the
   current project into a commercial or multi-user product. Djinni's project-specific reply
   recommends RSS and permits full descriptions; it does not specify a retention duration
   or settle every possible future product use.
2. Work.ua permission for automated collection and public display remains unverified under the
   owner's decision to keep the existing portfolio integration enabled. Commercial or multi-user
   reuse requires a separate review and explicit permission.
