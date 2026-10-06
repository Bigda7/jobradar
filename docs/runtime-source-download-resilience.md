# Runtime source download resilience

The v1.2.17 image publication stopped twice while collecting native source evidence.
The runner reported a network failure without identifying the native archive. The exact
failed endpoint is therefore unknown. A local GNU GCC download also timed out; the
kernel.org mirror of GCC 12.4.0 was independently downloaded and matched the existing
reviewed SHA256 (83,377,372 bytes). This does not prove that GCC caused both runner failures.

The v1.2.18 packaging fix permits one additional download only for that exact GCC URL
and reviewed checksum. It uses HTTPS on mirrors.kernel.org after a transport failure
or HTTP 500/502/503/504. Other archives have no added fallback. Each attempt retains
the existing 30-second socket timeout and 200 MiB response limit. There is no unbounded
retry, dynamic mirror selection, or fallback for HTTP 403/404/429.

Both endpoints must produce the exact reviewed SHA256. A checksum mismatch, unsafe
redirect, or download validation failure stops collection immediately. The successful
requested endpoint is recorded in artifact provenance, including archive notices.
Diagnostics identify the component, approved hostname and exception class or HTTP
status; they omit exception bodies, credentials and query strings.

The existing immutable image, complete source coverage, artifact checksum, anonymous
availability and registry attestation gates remain mandatory. No dependency, native
library version, license, application behavior, source access policy, database schema,
matching rule, notification setting or polling budget changes. The published v1.2.17
tag remains untouched; v1.2.18 carries the packaging fix and the prior source-resilience
changes.

Regression tests cover primary success, bounded transient fallback, checksum failures
on either endpoint, non-retryable HTTP responses, invalid review/redirect/size failures,
changed checksum rejection, sanitized diagnostics and successful mirror provenance.

Local verification: 62 source-evidence tests and the full 691-test non-integration
suite passed. Formatting, lint, typing, Bandit, lockfile consistency and diff checks
passed. PostgreSQL integration, dependency audits and attested image checks are
required again in CI before publication; this local result does not replace them.
