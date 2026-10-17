# PostgreSQL APK remediation review

Newest follow-up: the approved final runtime was reassembled into fresh single-layer
images on AMD64/emulated ARM64. Full scans60->0, runtime files/versions preserved;
native ARM64 setid acceptance still unverified. See workspace
`POSTGRES_CLEAN_IMAGE_LOCAL_PACKAGE_2026-10-16.md`. Earlier layered scan results
below remain historical evidence, not current findings in the new images.

Latest libxml2 follow-up: the exact package in
[the libxml2 proposal](postgres-libxml2-local-package-proposal-2026-10-16.md) was
approved and completed on AMD64/emulated ARM64. Final source/APK and compatibility
checks passed; eleven libxml2 findings disappear in each unfiltered full scan
(71 to 60, zero new IDs). Lower-layer findings and native ARM64 setid acceptance
remain open. See workspace `POSTGRES_LIBXML2_LOCAL_PACKAGE_2026-10-16.md`. The historical
three-APK results below remain unchanged. No deployment or publication occurred.

Date: 2026-10-16. Latest status: owner approved the three exact APK updates below;
implemented and verified locally on AMD64/emulated ARM64, not deployed. Both images
passed28 PostgreSQL integrations/eight restore groups and six TLS/UUID groups;
877 ordinary tests/static checks passed. Scans85->71,14 IDs removed/no new IDs.
libxml2 and lower-layer gosu remain open. See workspace
`POSTGRES_APK_LOCAL_PACKAGE_2026-10-16.md` for evidence and limits. Earlier proposal/
simulation wording below records preparation, not current lack of APK approval.
This follows the local gosu rebuild, not a production change or a clean-image claim.

## Verified package transaction

The existing gosu candidates retain PostgreSQL 17.11 on Alpine 3.24.1. Official
Alpine 3.24 package indexes and APKBUILD sources agree on these available versions:

| Installed package | Current | Proposed | Origin |
| --- | --- | --- | --- |
| libcrypto3 | 3.5.7-r0 | 3.5.9-r0 | openssl |
| libssl3 | 3.5.7-r0 | 3.5.9-r0 | openssl |
| libuuid | 2.42.1-r0 | 2.42.3-r1 | util-linux |

`apk --no-cache add --simulate` with these three exact constraints succeeded on
both AMD64 and emulated ARM64. It selected exactly three upgrades and no new or
removed packages. This was a simulation in read-only disposable containers, not an
installation. The installed inventory remained 45 packages. No host software was
installed and no running database, source collector, bot or notification client used.

The retained October16 SARIF associates ten advisory IDs with OpenSSL and four with
util-linux. The official APKBUILD lists fixes in OpenSSL 3.5.8 and util-linux
2.42.3-r0/r1. This motivates the proposed transaction; only a new post-build scan can
establish the resulting findings. Updating these packages does not resolve libxml2
or old gosu bytes retained in lower image layers. It is not a new libuuid advisory
reachability analysis: the installed subpackage originates from util-linux.

The proposed implementation must preserve hashes of PostgreSQL, the approved gosu,
libxml2 and zlib, compare every installed package before/after, and fail on any
additional package/version change. Verify shared-library loading, UUID operations,
TLS startup/authenticated connections and ordinary non-TLS behavior, migrations,
existing-volume restart and compressed backup restore in both directions. Use only
isolated synthetic databases. Production/Compose/CI/backup pins remain unchanged.

## libxml2 is not a safe drop-in version replacement

The official Alpine 3.24 index still offers 2.13.9-r2. Its APKBUILD contains the
CVE-2026-6732 patches, not the later fixes below. A check of the master recipe also
returned 2.13.9-r2; this is a dated observation, not a forecast of package releases.

Upstream 2.13.9's libtool ABI formula produces SONAME 2. Upstream 2.15.4 explicitly
sets its oldest compatible minor to 14 and produces SONAME 16. PostgreSQL currently
links `libxml2.so.2`, and the image also includes libxslt. A symlink from the new
SONAME to the old one is not a compatibility fix. No such replacement was attempted.

Official upstream changes were downloaded as review data and checked with
`git apply --check` against pristine v2.13.9 source, not installed libraries:

| Advisory | Function or path | Upstream commit | Preliminary applicability |
| --- | --- | --- | --- |
| CVE-2026-86137 | regexp lookahead | 76fe08d97de88bfaef2f7d5cd27f11954cc5bee2 | Direct check succeeds |
| CVE-2026-86138 | dictionary string allocation | a4cba4b5b5a8c42e155ed42d2d2a44955465a2e4 | Direct check succeeds |
| CVE-2026-86139 | URI escaping | 8edbbdb09f24d26a2f900141fddc2b9d014f53b0 | Reviewed backport needed |
| CVE-2026-86140 | validation error formatting | d1686f91dbda141a752200419d35639fd6b38340 | Direct check succeeds |
| CVE-2026-86141 | regexp allocation failure | e89a8aae4c9b40cdafcf66b3f9e57c62db37bb55 | Check succeeds after 76fe08d |
| CVE-2026-86142 | XPointer length handling | 6b3a736c0edc74ceec3d82f5252499d7911b3a58 | Reviewed backport needed |
| CVE-2026-86143 | XML output callbacks | 90f293ba74d28b1d570920382e707586f68ebf35 | Reviewed backport needed |
| CVE-2026-86144 | XInclude parser flags | b63cd517afecb76582dd9488c55e54ceaf50de61 | Reviewed backport needed |
| CVE-2026-0989 | RelaxNG inclusion depth | 66c52b3ac6c32ab112ec2a3bf41e6c30948be113 | Reviewed backport needed |
| CVE-2025-8732 | SGML catalogs | 3425dece47c8db600f8d7328ae2d7ddfaa0d7b2d | Reviewed backport needed |
| CVE-2026-11979 | xmlcatalog interactive shell | c2e233fc1b341685fc99621b2768b503f777a72e | Test path absent in this source; runtime utility absent |

No patch was compiled or installed. The single dependent check applied the earlier
regexp patch only to a disposable extracted source fixture inside a network-disabled
container. It did not modify the host archive or candidate image. A first attempt
found no `patch` executable in the existing builder; the actual checks above used its
already installed Git. No compiler or package was added to make this review possible.

The codeload v2.13.9 review archive SHA256 is
`80c96f3d2ae5d3dc115910a99b7da1d4b1fa1a833d38073fccb41a162f2cb6b4`.
It is a Git source snapshot, not the GNOME release tarball used by Alpine. A future
build must verify the actual release archive and retain Alpine's existing patches.
Downloaded review data remains in ignored `.deploy/postgres-libxml-review-20261016/`.

## Reachability observations, not blanket exceptions

`xmlcatalog` and libxml2-utils are absent in the inspected runtime candidates. The
CVE-2026-11979 upstream change is in that utility and its tests, not the library.
This is evidence for a runtime-specific non-applicability classification, not a
reason to remove all libxml2 findings or declare the whole package fixed.

Current JobRadar application/migration search did not identify PostgreSQL XML/XPath
queries; Djinni RSS parsing is a separate Python path. PostgreSQL 17.11's own xml.c
uses XML_PARSE_NOENT/DTDATTR and installs an entity loader that blocks external
resources. This narrows particular external-entity paths, but is not proof that
every libxml2 memory-safety issue is unreachable or that SQL XML is disabled.
No hostile XML/SQL exploitation test was run against production or a live service.

## Recommendation and boundaries

Complete the three official APK upgrades first once their exact versions are approved.
For libxml2, prefer an ABI-preserving reviewed backport to 2.13.9-r2 with explicit
provenance and regression tests over an unverified SONAME substitution. That requires
a separate exact source/patch/build-tool package decision; it is not approved by
this review. Keep all eleven findings visible until fixed or individually justified.
Do not move to edge repositories, change PostgreSQL major, disable XML, fabricate
package security metadata or suppress scanner results as a shortcut.

Native ARM64 setid checks and full-image lower-layer acceptance from the gosu report
remain open. Real backup/S3 checks, rollout/rollback and publication are separate.
The existing local candidates, all other user changes and production are unchanged.

Sources:

- [Alpine OpenSSL recipe](https://raw.githubusercontent.com/alpinelinux/aports/3.24-stable/main/openssl/APKBUILD)
- [Alpine util-linux recipe](https://raw.githubusercontent.com/alpinelinux/aports/3.24-stable/main/util-linux/APKBUILD)
- [Alpine libxml2 recipe](https://raw.githubusercontent.com/alpinelinux/aports/3.24-stable/main/libxml2/APKBUILD)
- [Upstream 2.13.9 ABI configuration](https://raw.githubusercontent.com/GNOME/libxml2/v2.13.9/configure.ac)
- [Upstream 2.15.4 ABI configuration](https://raw.githubusercontent.com/GNOME/libxml2/v2.15.4/configure.ac)
- [PostgreSQL 17.11 XML implementation](https://raw.githubusercontent.com/postgres/postgres/REL_17_11/src/backend/utils/adt/xml.c)
- [CVE record service](https://cveawg.mitre.org/api/cve/CVE-2026-86140)
- [RelaxNG fix reference](https://security-tracker.debian.org/tracker/CVE-2026-0989)
- [SGML fix reference](https://security-tracker.debian.org/tracker/CVE-2025-8732)
