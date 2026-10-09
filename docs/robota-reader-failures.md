# Robota.ua reader failures

## Verified on October 9, 2026

Three consecutive production discoveries returned no cards from the seven configured
searches. Bounded probes of the existing reader returned HTTP 200 with an empty
`Markdown Content` body. A separate direct public-page probe from the owner's local
network returned HTTP 403 with Cloudflare and JavaScript-required markers. These
observations do not prove that every historical reader failure has the same cause.
Historical response bodies were not retained.

## Local handling change

- Distinguish an empty reader body from a rendered search containing no matching cards.
- Retry only an empty body, on the same configured reader URL and within the existing
  attempt ceiling. Wait at least one second or the configured detail delay, whichever
  is longer. No new requests, filters, pages or transport are introduced.
- Preserve the HTTP status in error messages and include the first search failure
  reason in the terminal failure stored by ingestion.
- Treat explicit reader/upstream access restrictions as failures. Do not follow an
  API restriction with a vacancy-page fallback. Existing bounded HTTP 429 backoff
  remains unchanged; this is not permission to bypass rate limits.
- Continue allowing the existing detail-page fallback after ordinary API failures
  such as HTTP 502. Recognize HTTP 404/410 in that fallback correctly.
- Keep missing-listing deactivation disabled. A failed discovery does not establish
  that existing vacancies have disappeared.

Searches, six-hour polling, pagination, item ceiling, matching, storage schema and
notification delivery are unchanged. This package improves diagnosis and safe
failure handling; it does **not** claim to restore upstream access or full coverage.
Do not add alternate readers, proxy rotation, challenge-solving or forced discovery.
