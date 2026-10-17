# Local Caddy security prototype

This is a local build and verification package, not an installation or deployment tool.
It does not alter JobRadar dependencies, production configuration, certificates, database,
collection budgets or notification behavior. Do not install the candidate without a
separately reviewed production transition and explicit deployment approval.

## Pinned inputs

- Caddy2.11.7 source commit `72dd0fb067f6d7826c7f79907670ba4a713bfe37`.
- Source tar SHA256 `e86c49311c0c9f7e9b3b80527f0139e5739dfe28effeaa41c444dd743f0bbd7e`.
- Go1.26.9 Bookworm image index
  `sha256:d9c68c2c51161e12fd77e4c6320687c9cd86e1af1e3ad6e6cd63ff970641453c`.
- Approved Go module changes: x/net0.61.0, x/crypto0.58.0, x/sys0.49.0,
  x/term0.47.0, x/text0.43.0, x/sync0.24.0 and x/tools0.51.0.

The complete dependency graph is compared before compilation. Additional module
additions/removals, replacements or other version changes fail the build. Go checksums
are verified. The custom version label is `v2.11.7-jobradar.1`; Caddy's own source code
is unchanged. A repeated ARM64 compilation is byte-compared within the same build
environment; this is not an independent reproducible-build attestation.
The official release's `nobadger,nomysql,nopgx` build tags are preserved to avoid
introducing its deliberately excluded database drivers into the candidate.

## Run

Requires the existing Docker Desktop/Scout, uv, and `jobradar-release-check` builder.
ARM64 execution uses Docker Desktop emulation on this Windows AMD64 machine.

```powershell
./deploy/caddy-prototype/check_candidate.ps1
```

Use `-VerifyOnly` to test existing exported artifacts without rebuilding Caddy.
The default command downloads/checks pinned source, builds both architectures, verifies
the exact module changes and ELF targets, runs seven dependency-guard cases, retains
full SARIF/SBOM reports, rejects High/Critical findings and runs proxy tests on both
architectures. Any native-command failure stops the script.

The test servers run as an unprivileged user, with no external network, no published
ports, a read-only filesystem, dropped capabilities and bounded memory/processes.
Only temporary synthetic certificates and a synthetic bearer value are used.
The 1KB request limit is a test fixture, not a proposed production configuration change.
Successful and failed containers are removed automatically; local images, artifacts
and builder caches are retained for further review. No broad cache pruning occurs.

## Coverage and limitations

The fixture checks TLS1.2/1.3, rejects legacy TLS, HTTP1.1/HTTP2 proxying, host/query/body
and authorization pass-through, HTTPS redirect, unmatched-host non-forwarding, configured
request size limit, intact 1MiB responses, upstream errors, separate cookies, POST event
streams idle for65 seconds, config reload, graceful shutdown and restart.

`http_port`/`https_port` are internal listener settings; redirected client URLs keep
the standard HTTPS port. Unmatched routes may return an empty200 in Caddy; the security
assertion verifies they never reach the upstream and expose no response body.

The full scanner retains GO-2026-5932 against the x/crypto module. Import graphs for
both architectures prove its affected OpenPGP packages are absent. Do not erase the
warning or claim x/crypto's deprecated API was repaired.

The local main module is `(devel)` in Go build metadata, so Docker Scout does not index
Caddy's own module as a versioned dependency. Pinned upstream commit/version and manual
review of official Caddy advisories supplement the dependency scan; the scan alone
is not a complete Caddy source audit. Exported files are not independently signed.

Root license/notice files are collected for the module graph. The artifact verifier
reports coverage specifically for linked modules. Redistribution still needs final
packaging, notices and provenance review; no public release bundle is produced here.
Real production Caddyfile/systemd/ACME renewal compatibility, native ARM64 hardware,
production rollout and rollback are not verified by this local fixture.
