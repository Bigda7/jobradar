# PostgreSQL source-evidence bundle

This local tool prepares source/notice/build evidence for the two exact approved
PostgreSQL 17.11 clean images. It does not publish, deploy, run PostgreSQL, read
working databases, install dependencies or alter images. The project's MIT license
is unchanged. Engineering coverage is not a legal-compliance certificate.

Run from the backend root with the existing Python environment and Docker:

```powershell
.venv/Scripts/python.exe -m scripts.prepare_postgres_sources --architecture arm64 --revision FULL_COMMIT_SHA --output .deploy/postgres-sources-arm64
.venv/Scripts/python.exe -m scripts.prepare_postgres_sources --architecture arm64 --revision FULL_COMMIT_SHA --output .deploy/postgres-sources-arm64 --verify
```

Use `amd64` for the other approved image. Without `--revision`, preparation creates
an explicitly incomplete draft and exits 1. A new destination is required for each
attempt. `--reuse-draft` reuses checksum-verified Alpine sources by exact origin and
build commit; it does not reuse another draft's readiness claim. Incomplete origin
sets are not reused. Archives and downloaded recipes are data, never executed.

The gate checks the exact image/platform and installed package inventory, including
the virtual payload-free dependency package. It preserves exact Alpine APKBUILDs,
source files, patches and helpers at installed build commits. `nss_wrapper` comes
from Alpine's `community` repository; no repository path is inferred from user input.
`krb5-conf` and `alpine-base` generate their payload in APKBUILD without an external
source tarball. Inline and multiline checksum declarations are parsed without shell
expansion. Source checksums must match the recipes.

PostgreSQL 17.11 sources are checked against the installed `PG_SHA256` and the
fixed upstream Dockerfile at docker-library/postgres commit
`2603e26e245e558218728ee14e0a42dcb020dc7f`. Installed entrypoint scripts must match
that commit's files. Custom gosu sources, updated module files, licenses and build
metadata come from the exact image; libxml2 sources/backports must match the saved
project build recipes. Go 1.26.9 source SHA256 is pinned from the official Go
download metadata, and both Go module source ZIPs must match the installed go.sum
`h1` checksums, not merely an unchecked download SHA256.

## Explicit inline-notice cases

Some reviewed packages do not ship a separately named LICENSE/COPYING file.
Only the exact origin/build-commit pairs in `INLINE_NOTICES` receive supplemental
license-text mappings. Complete original source files and recipe comments are
preserved without inventing author names or copyright statements:

- alpine-baselayout: generated filesystem layout and source scripts; GPL-2.0-only
  full text accompanies their existing file-level notices, including source data.
- alpine-keys: public verification-key data and the generating recipe; MIT text
  accompanies the recipe's existing authorship metadata, not fabricated key ownership.
- alpine-base: generated release metadata in APKBUILD, with its MIT declaration.
- krb5-conf: configuration generated directly in APKBUILD, with its MIT declaration.
- ca-certificates: complete Mozilla certdata and utility sources; the original
  certdata MPL header remains in the source archive. Full MPL-2.0 and MIT texts are
  preserved for the declared combined package license. Certificate data is public
  source material, not any deployment certificate/private key.

Supplemental texts are pinned by SHA256 to SPDX license-list-data v3.27.0. Their
presence alone does not approve another component or override original attribution.
Unknown or missing source/notice mappings fail the gate.

## Revision and publication boundary

`recipe_revision` identifies the real Git commit containing the delivered build
recipes. It is NOT represented as an original OCI provenance label on images built
before that commit. Verification compares every delivered recipe file against Git
and every inventory field against the exact local image. Runtime binary hashes and
the image ID are retained separately. No registry reference is invented.

The source ZIP has deterministic entry timestamps/order/permissions and a manifest
with SHA256 per artifact. This does not prove byte-for-byte reconstruction of
upstream binaries or compiler bootstrap completeness. Prototype build helpers have
explicit local input-ID guards; recreating and accepting replacement inputs requires
review, not silently bypassing those guards. Custom backports/toolchains require
continued maintenance. A future image/version needs a new inventory and review.

Actual GitHub/GHCR source delivery, public availability, retention, image publication
and production rollout require separate authorization and verification. No passing
local source gate proves that those external steps already happened.
