# Source Access Policy

Last reviewed: 2026-09-06

This document records the access method, attribution requirements, retention constraints, and
commercial-use risk for every production source. It is an engineering decision record, not legal
advice. Terms and robots directives can change and must be checked again before a public or
commercial release.

## Operating Rules

- Preserve the canonical source URL and show the source name wherever a listing is displayed.
- Do not increase request frequency or pagination for a permission-required source.
- Prefer documented public APIs and RSS feeds over HTML or undocumented frontend APIs.
- Respect upstream request limits even when the adapter can technically request more data.
- Re-review this table before enabling a source in the multi-tenant SaaS product.
- Do not use a proxy or reader service to bypass a source restriction.

## Source Register

| Source | Access method | Current position | Attribution and retention | SaaS action |
| --- | --- | --- | --- | --- |
| Arbeitnow | Documented public API | Approved | Keep the original listing URL | Recheck API terms before commercial launch |
| Ashby | Documented public job posting API | Approved | Keep the original listing URL | Allowed subject to current API terms |
| Djinni | Public job pages and JobPosting JSON-LD | Conditional | Keep Djinni attribution and the original URL | Request written permission before commercial use |
| DOU Jobs | Official vacancy RSS feed | Approved | Keep DOU attribution and the original URL | Recheck feed terms before commercial launch |
| Freelance.cz | Undocumented frontend API | Permission required | Current terms restrict copying and derived collections | Do not enable for SaaS without written permission |
| Freelancer | Documented OAuth API | Open compliance issue | Cache must refresh at least every 24 hours; stored and served data requires strong encryption; storage is otherwise restricted | Implement source-specific retention or obtain written permission before the next production release |
| Greenhouse | Documented public job board API | Approved | Keep the employer listing URL | Allowed subject to current API terms |
| Himalayas | Documented public jobs API | Approved | Visible Himalayas attribution and link are required | Allowed subject to current API terms |
| Jobicy | Documented public jobs API | Approved | Preserve Jobicy as the source and retain the canonical URL | Allowed for normal integrations under fair-use rules |
| Jobs.cz | Public job pages | Conditional | Keep Jobs.cz attribution and the original URL | Request written permission before commercial use |
| Lever | Documented public postings API | Approved | Keep the employer listing URL | Allowed subject to current API terms |
| Prace.cz | Public job pages | Conditional | Keep Prace.cz attribution and the original URL | Request written permission before commercial use |
| Remotive | Documented public jobs API | Approved with conditions | Mention Remotive and link to the Remotive URL; listings are delayed upstream by 24 hours | Do not place Remotive listings behind a signup gate |
| Robota.ua | Public search pages and public vacancy details | Conditional | Preserve the original meaning and include a mandatory source link | Confirm commercial reuse before SaaS launch |
| Startup.jobs | Documented API with an API key | Personal use only | Link back on every page or screen using its data; republication is limited to non-commercial objectives | Obtain a commercial agreement before SaaS launch |
| StartupJobs.cz | Undocumented frontend API | Permission required | Keep StartupJobs.cz attribution and the original URL | Do not enable for SaaS without written permission |
| The Muse | Documented public API | Approved with conditions | Register the application, obey rate limits, and link every listing back to The Muse | Confirm the production key belongs to a registered application |
| We Work Remotely | Official category RSS feed | Approved | Keep the original listing URL and source attribution | Recheck feed terms before commercial launch |
| Work.ua | Public pages through a reader proxy | Permission required | The configured search paths are disallowed by Work.ua robots directives | Do not expand coverage; disable for SaaS unless Work.ua grants permission |

## Primary References

- Arbeitnow API: https://www.arbeitnow.com/blog/job-board-api
- Ashby public job posting API: https://developers.ashbyhq.com/docs/public-job-posting-api
- Freelancer API terms: https://www.freelancer.com/about/apiterms
- Greenhouse Job Board API: https://docs.greenhouse.io/job-board.html
- Himalayas jobs API: https://himalayas.app/docs/remote-jobs-api
- Jobicy API and fair-use rules: https://jobicy.com/jobs-rss-feed
- Lever Postings API: https://github.com/lever/postings-api
- Remotive API terms: https://remotive.com/remote-jobs/api
- Robota.ua usage notice: https://robota.ua/?goHome=true
- Startup.jobs terms: https://startup.jobs/terms
- The Muse API terms: https://www.themuse.com/developers/api/v2/terms
- Work.ua robots directives: https://www.work.ua/robots.txt
- Freelance.cz terms: https://www.freelance.cz/obchodni-podminky

## Unresolved Production Decisions

1. Freelancer cannot keep the shared 30-day archive policy without written permission or a
   source-specific retention implementation. Deleting existing records is a destructive migration
   and requires a separate approved rollout plan.
2. Work.ua, Freelance.cz, and StartupJobs.cz should not be carried into the SaaS product without
   written permission.
3. The production The Muse API registration must be verified outside the repository without
   exposing its credential.
