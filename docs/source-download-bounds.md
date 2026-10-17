# Bounded source and supporting-data downloads

The October 17 local S08 package routes all four active source adapters and the
supporting NBU exchange-rate reader through `jobradar.http_download.bounded_get`.
No source is enabled/disabled, no poll interval or provider request limit changes,
and no matching rule or notification send policy changes.

## Limits and their basis

| Path | Decoded response limit | Download time window |
| --- | --- | --- |
| Djinni RSS | Existing 5,000,000 bytes | Existing configured request timeout/run deadline |
| Djinni metadata | Existing 1,000,000 bytes | Existing request timeout/run deadline |
| DOU RSS | Existing 5,000,000 bytes, now enforced while downloading | Existing configured request timeout as a total download deadline |
| Work.ua and Robota.ua Reader pages/details | 5,000,000 bytes | Configured request timeout per attempt, within retry/run windows |
| NBU public exchange-rate JSON | 1,000,000 bytes | Existing configured request timeout as a total download deadline |

The Reader ceiling reuses the established source-document ceiling, with substantial
headroom over the embedded ordinary page fixtures. The NBU ceiling reuses the
smaller supporting-document class. These are conservative engineering defaults,
not measured provider maxima or proof that every valid response will fit. A valid
larger document is rejected explicitly, never truncated into a successful parse.
Review actual observed limit failures before changing ceilings.

Djinni retains its existing 100 MB decoded run budget and configured run window,
rate limiter and persistent RSS/metadata request budgets. Work.ua retains its
600-second network run window and request count guard, and adds a shared 100 MB
decoded budget. Robota.ua gains equivalent 600-second/100 MB download budgets
shared across concurrent searches and details. A single-feed DOU run is bounded
by its one download. Time windows do not surround an async generator's yields:
database work in the consuming ingestion task must not be cancelled by an adapter
timer. They are download windows, not a hard wall-clock bound on the whole worker
or synchronous parsing. Existing pacing is preserved.

## Transfer and compression safety

HTTPX streaming raw bytes avoids buffering a complete response. Automatic
`aiter_bytes` decompression is not used: its decoder can allocate a large expanded
chunk before application code checks its size. The shared reader uses standard
library zlib with a maximum output length of the remaining allowance plus one
overflow-detection byte. It accepts identity, gzip (including concatenated members),
standard deflate and common raw-deflate framing. Gzip/deflate completion/checksum
errors fail the response; partially decoded bodies never reach parsers.

Only gzip/deflate are advertised. Brotli, Zstandard and stacked/unknown encodings
are explicitly rejected, without installing optional codecs or pretending their
bytes are plain text. The wire limit is twice the decoded limit plus 65,536 bytes
of framing headroom; it also bounds compressed streams that produce little data.
Content-Length is only an early hint; actual raw and decoded counts are checked.
Malformed/missing/incorrect lengths cannot disable the body counters. Failed
decoding/size checks also spend decoded run budget for bytes actually produced.

The reader returns a complete bounded response for existing parsers, preserving
content type/charset and status/Retry-After headers; consumed encoding and length
headers are rebuilt for the decoded body. HTTP error bodies are not loaded. Buffered
injected test transports may already have decoded/allocated content before entry;
they are checked, but that prior allocation cannot be undone by this helper.

This does not cap total worker RAM at the response limit: transport chunks, copies,
parser objects, caches and concurrent downloads also consume memory. Service-level
resource limits and large-JSON partner export integration are separate work.

## Redirects, retries and cancellation

Redirects are followed manually, not by HTTPX's auto-follow path, which may read
redirect bodies before returning control. Bodies are closed without reading them.
At most five hops are allowed, under the same total deadline. They must retain
the original origin, or upgrade default-port HTTP to HTTPS on the same hostname;
credentials, port changes, cross-host redirects and HTTPS downgrades are rejected.
Djinni keeps its existing no-redirect policy. Work.ua counts each redirected
physical request against its existing request budget. No block/challenge bypass
or fallback to an unapproved service is introduced.

Robota's shared 429 retry helper now has a bounded overall window and refuses
nonfinite/excessive Retry-After values or waits beyond its remaining run deadline.
Existing retry counts and normal delays are retained. Work.ua/Djinni keep their
own bounded retry policies. Context cleanup closes responses; task cancellation
propagates rather than turning into success. Deadlines are cooperative asyncio
deadlines, not a preemptive OS sandbox around arbitrary hostile Python code.

## Preservation and observability

Adapter-specific failures/warnings and existing failed/partial run records remain
the output contract. No content is silently cut to fit a limit. Stale Work.ua
descriptions retain their previous text and fetch time when refresh fails;
unsuccessful searches do not erase stored listings or notification records.
The generic snapshot reconciler now refuses missing-listing deactivation when
there are errors, failed details or a reached limit. Two regressions reproduced
its former unsafe behavior. Active production adapters already disable
missing-listing deactivation; that configuration remains unchanged.

The workspace `SOURCE_DOWNLOAD_SECURITY_PACKAGE_2026-10-17.md` records the actual
test results, including real loopback HTTP checks. This document is not a claim
that production now contains these fixes, upstream access is reliable, provider
permissions changed, or every vacancy is collected. Publication/deployment is
separate and requires explicit authorization.
