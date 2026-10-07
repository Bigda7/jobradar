# Runtime zlib security update

## Scope

Version 1.2.21 explicitly installs Alpine zlib `1.3.2-r1` in the runtime stage.
The pinned Python base image contains `1.3.2-r0`; keeping the base digest alone
would retain the vulnerable package. No broad `apk upgrade` is used. Python
dependencies, other package pins, source settings, matching, notification logic,
schema and frontend are unchanged by this security fix.

The release also includes the already merged Djinni coverage fixes from 1.2.20,
which was published but not deployed after its runtime vulnerability gate failed.

## Reviewed evidence

On 2026-10-07 the official [Alpine v3.24 security database](https://secdb.alpinelinux.org/v3.24/main.json)
lists `1.3.2-r1` as the fix for `CVE-2026-85091`. The official
[aarch64 package index](https://dl-cdn.alpinelinux.org/alpine/v3.24/main/aarch64/APKINDEX.tar.gz)
identifies its source build as `0afa2da0e8c8051c6f8f64a7a388e5a259904245`,
origin `zlib`, license `Zlib`. The exact version, license and build commit are
recorded in `runtime-package-review.json`; all other reviewed components remain
unchanged. The source collector must obtain that build's recipe, original
sources, checksummed patches and notices, rather than reuse the r0 source review.

The finding also appeared in the deployed 1.2.19 image when rescanned. This is
not evidence of exploitation, nor proof that application inputs reach the issue.

## Release gates

Regression tests preserve the exact runtime pin and its source/license identity.
Before deployment, verify full CI, published corresponding-source coverage and
checksums, ARM64 provenance/SBOM, an unfiltered runtime vulnerability scan, a
fresh restored/encrypted database backup and an isolated rollout rehearsal.
After switching, verify the installed APK revision and a zlib compression
round-trip, runtime/API health, a completed worker cycle and notification
idempotency. A Python `zlib.ZLIB_VERSION` value alone cannot distinguish Alpine
package revisions r0 and r1.

A clean scan is a point-in-time result, not a guarantee against future findings.
Do not overwrite or retag 1.2.20, suppress findings, or deploy it as a shortcut.
