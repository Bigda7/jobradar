# Source Access Policy

Last reviewed: 2026-09-25

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
- Do not use a proxy or reader service to bypass an upstream access restriction.

## Source Register

| Source | Access method | Current position | Attribution and retention | Commercial or multi-user action |
| --- | --- | --- | --- | --- |
| Djinni | Public job pages and JobPosting JSON-LD | Conditional | Keep Djinni attribution and the original URL | Request written permission before commercial use |
| DOU Jobs | Official vacancy RSS feed | Approved | Keep DOU attribution and the original URL | Recheck feed terms before commercial launch |
| Robota.ua | Public search pages and public vacancy details | Conditional | Preserve the original meaning and include a mandatory source link | Confirm commercial reuse before a commercial or multi-user launch |
| Work.ua | Public pages through a read-only text reader | Permission required | Keep Work.ua attribution and the original URL; configured search paths are disallowed by Work.ua robots directives | Do not expand coverage; disable for commercial or multi-user use unless Work.ua grants permission |

## Primary References

- Robota.ua usage notice: https://robota.ua/?goHome=true
- Work.ua robots directives: https://www.work.ua/robots.txt

## Unresolved Production Decisions

1. Confirm commercial reuse terms for Djinni and Robota.ua before any public or commercial use.
2. Work.ua must not be carried into a commercial or multi-user product without written permission.
