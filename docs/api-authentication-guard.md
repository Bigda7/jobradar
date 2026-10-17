# API authorization input handling

The local October 17 security package fixes audit S06. The configured secret and
the supplied token are encoded consistently as UTF-8 bytes before
`secrets.compare_digest`. The comparison remains constant-time for equal-length
inputs, without exposing credentials. Non-ASCII HTTP header text no longer causes
the string-only comparison to raise a TypeError and produce an HTTP 500.

Missing, empty, malformed, wrong-scheme and wrong-value authorization receive the
existing 401 response and `WWW-Authenticate: Bearer`. Scheme matching remains
case-insensitive. Whitespace handling and token configuration are unchanged.
Encoding tokens is not an authorization bypass: a wrong token still fails.

The protected routes remain `/jobs`, `/matches` and `/sources`. `/health` and
`/ready` stay public probes. Local/test operation without a configured token is
preserved; production still requires its existing configured secret. No secret
rotation, settings/schema change or database permission change is included.

Tests exercise byte-valued non-ASCII headers through the actual FastAPI ASGI app,
not only a helper. Six cases reproduced 500 before the fix. The final matrix
checks eleven invalid forms across three routes, three valid scheme variants and
both public probes. All tests use synthetic credentials.

This is a local implementation, not confirmation of a production fix. The shared
workspace report is `API_PROXY_SECURITY_PACKAGE_2026-10-17.md`. Publication and
deployment remain separate actions requiring explicit authorization.
