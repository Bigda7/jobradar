# Runtime License Review

Reviewed: 2026-10-03. Status: technical source/license mapping and release gate implemented
locally; public delivery remains pending. This is an engineering assessment, not completed legal
compliance. This assessment concerns artifacts and upstream license texts, not
legal advice or a certification of the project's legal status.

## Artifact and Findings

The reviewed public registry image is the deployed backend v1.2.12:

```text
ghcr.io/bigda7/jobradar@sha256:183506dbc1db3bc6624a7892604871ccdaba6a7db7fa59a49a8acb7fe5876728
```

Source revision: `3b4dfb8fad2ea3eb8b31ce75ecb5de4a42b443c8`; platform: Linux ARM64.
Docker Scout v1.24.0 read its provenance and SBOM and reported 75 packages and 18 copyleft
findings. Those findings represent 17 distinct packages: `xz-libs` occurs twice for two license
IDs. Fourteen distinct Alpine packages account for 15 findings; three Python distributions
account for the other three. The default policy evaluation remains 6/7, not a legal verdict.

[Docker's policy documentation](https://docs.docker.com/scout/policy/) explains that the
copyleft policy flags the presence of selected licenses. Satisfying distribution obligations
does not remove GPL/LGPL/MPL packages from the image or make the default no-copyleft check pass.
No Scout configuration, package exception, or license metadata was changed during this review.

| Package | Version | Copyleft IDs reported by Scout | Origin |
| --- | --- | --- | --- |
| alpine-baselayout | 3.7.2-r1 | GPL-2.0-only | Alpine base |
| alpine-baselayout-data | 3.7.2-r1 | GPL-2.0-only | Alpine base |
| apk-tools | 3.0.8-r0 | GPL-2.0-only | Alpine base |
| libapk | 3.0.8-r0 | GPL-2.0-only | Alpine base |
| busybox | 1.37.0-r31 | GPL-2.0-only | Alpine base |
| busybox-binsh | 1.37.0-r31 | GPL-2.0-only | Alpine base |
| ssl_client | 1.37.0-r31 | GPL-2.0-only | Alpine base |
| ca-certificates | 20260909-r0 | MPL-2.0 | Alpine base |
| ca-certificates-bundle | 20260909-r0 | MPL-2.0 | Alpine base |
| gdbm | 1.26-r0 | GPL-3.0-or-later | Python base |
| musl-utils | 1.2.6-r2 | GPL-2.0-or-later | Alpine base |
| readline | 8.3.3-r1 | GPL-3.0-or-later | Python base |
| scanelf | 1.3.9-r1 | GPL-2.0-only | Alpine base |
| xz-libs | 5.8.4-r0 | GPL-2.0-or-later; LGPL-2.1-or-later | Python base |
| certifi | 2026.7.22 | MPL-2.0 | Locked Python dependency |
| psycopg | 3.3.4 | LGPL-3.0-only | Locked Python dependency |
| psycopg-binary | 3.3.4 | LGPL-3.0-only | Locked Python dependency |

## What Was Verified in the Running Image

- JobRadar metadata says MIT but did not declare or contain a `License-File`. The Docker builder
  copied the project manifest, lock, README, and code without copying `LICENSE` before building
  the package. The runtime also lacked a separate preserved JobRadar license text.
- Certifi, Psycopg, and Psycopg Binary each contain a license file in their `.dist-info/licenses`
  directory. Presence of these files is not proof that every source/notice obligation is met.
- Base Python contains `/usr/local/lib/python3.13/LICENSE.txt`.
- No license/notice files were found in the inspected `/usr/share` and `/usr/local/share` paths
  to depth three, and `/usr/share/licenses` did not supply the Alpine package license texts.
- `scanelf` confirmed that Python's `readline`, `_gdbm`, and `_lzma` extensions dynamically need
  `libreadline.so.8`, `libgdbm.so.6`, and `liblzma.so.5`, respectively. This is not a blanket claim
  that every GPL package is an unrelated executable. All three Python extensions imported
  successfully. Direct `ldd` on extension objects had unresolved interpreter symbols; that
  diagnostic is not an application failure or proof that the modules cannot load.
- JobRadar application code does not change the listed upstream libraries in this release.
  The native mapping and its evidence limitations are in [runtime-native-review.md](runtime-native-review.md).

## Local Fix for the Missing Project License

The builder now copies the unchanged project `LICENSE` before `uv sync`. The runtime preserves
another copy at `/usr/local/share/licenses/jobradar/LICENSE`. A build-time verifier checks that
the installed JobRadar distribution declares its license file and contains the exact same text.
Missing declarations, missing content, empty source text, or mismatches fail the build. The check
does not hard-code MIT or decide which license the project should adopt in the future.

The runtime test stage receives the verifier, and regression tests cover the failures and Docker
copy/build-check wiring. This fix neither changes third-party license metadata nor resolves all
18 Scout findings. It is local only until a separately approved publication and deployment.

## Why Image Distribution Requires More Than a Notice List

Access to a web service and downloading a binary container image are different activities.
The reviewed image was anonymously readable from GHCR, so this review treats public binary
distribution as relevant; it does not assume that the portfolio's server-only use removes those
obligations. Public application source on GitHub is not the corresponding source of BusyBox,
Readline, GDBM, or other separately distributed components.

Relevant primary references:

- [GPLv3 sections 0, 1, 5, and 6](https://gcc.gnu.org/onlinedocs/libstdc++/manual/appendix_gpl.html)
  distinguish network interaction, aggregation, combined works, and corresponding-source access.
- [FSF GPL FAQ](https://www.gnu.org/licenses/gpl-faq.en.html) discusses aggregation and exact
  corresponding sources for network binary distribution. Source and binary may use different
  servers under GPLv3 with clear directions and maintained equivalent access; an arbitrary
  upstream homepage or changing branch is not a version-matched source delivery plan.
- [Psycopg 3.3.4 LGPLv3 text](https://raw.githubusercontent.com/psycopg/psycopg/3.3.4/LICENSE.txt)
  permits combined works under conditions that include notices, GPL/LGPL texts, and suitable
  replacement/relinking or corresponding-code arrangements. The binary wheel's bundled native
  libraries and source/build process must be reviewed too.
- [Mozilla MPL FAQ](https://www.mozilla.org/en-US/MPL/2.0/FAQ/) and
  [MPL sections 3.1-3.4](https://www.mozilla.org/en-US/MPL/2.0/) describe source availability,
  preservation of notices, and file-level obligations for distributed covered software.

Co-location in a container alone does not establish that all of JobRadar must be relicensed.
Conversely, it does not prove that all dependencies can be ignored. Exact linkage, package
contents, licenses, distribution method, and any modifications matter. Project relicensing from
MIT to AGPL is a separate deferred decision and would not remove dependency obligations.

## Version-Matched Alpine Source Recipes

The following build commits were read from the running image's installed APK database. Multiple
subpackages share one origin recipe. Use the recorded commit, patches, configuration, checksums,
and upstream sources, not the latest version of the origin package.

| Origin | Aports build commit |
| --- | --- |
| alpine-baselayout | 60a7585bbab2fa0f762504eb617dbca90216e31f |
| apk-tools | 4588b452722bd4800efdc6cce4f6e980e02a997f |
| busybox | c3ef5d10e6ef6528852c51f0564963e2f8c1be19 |
| ca-certificates | eb7078df3a16d666e8f7723f94bb92e00d4b9236 |
| gdbm | 1ea03990396db618edfcc8a9a0bc6b72662bac8f |
| musl | f5640d3a10f664c9119720c60515265d3d6f6d01 |
| readline | a854c03acdac188901fb012f7acbee70a36e8041 |
| pax-utils | c61801eeacb3ffcd9c2025b09e402153bb93fb39 |
| xz | 0c088d609f9fe69eedd6b4c7f4b99cdad847c20a |

For example, the [exact BusyBox recipe](https://raw.githubusercontent.com/alpinelinux/aports/c3ef5d10e6ef6528852c51f0564963e2f8c1be19/main/busybox/APKBUILD)
lists Alpine patches and build configurations; the upstream BusyBox tarball alone omits them.
The [exact musl recipe](https://raw.githubusercontent.com/alpinelinux/aports/f5640d3a10f664c9119720c60515265d3d6f6d01/main/musl/APKBUILD)
must be inspected at file level because `musl-utils` has a mixed license expression.

### XZ Metadata Needs File-Level Treatment

Alpine records a mixed expression for the entire XZ origin. Only `liblzma` shared library files
were listed in the installed `xz-libs` subpackage. Upstream
[XZ 5.8.4 COPYING](https://raw.githubusercontent.com/tukaani-project/xz/v5.8.4/COPYING) identifies
liblzma as 0BSD; its GPL/LGPL portions concern other scripts, tools, and build files. The
[matching Alpine recipe](https://raw.githubusercontent.com/alpinelinux/aports/0c088d609f9fe69eedd6b4c7f4b99cdad847c20a/main/xz/APKBUILD)
and shipped files support treating the two Scout entries as overbroad origin metadata requiring
review, not automatically as two restrictive licenses on the library. No binary metadata was
rewritten and no exception was silently granted.

## Completion Criteria and Current Boundary

Recommended route: keep the working base and dependencies; comply with the applicable licenses
rather than replacing production libraries solely to remove a broad scanner finding.

1. Implemented locally: assemble version-matched license texts, copyright notices, and source/build evidence for the
   actual image contents, including Psycopg Binary's vendored native components and Python's
   GPL-linked extensions. Review exact upstream license exceptions where applicable.
2. Implemented locally: prepare a checksummed, reproducible corresponding-source bundle with Alpine patches/configs,
   exact upstream sources, Python/base build instructions, and relevant wheel sources. Keep it
   separate from `.env`, deployment credentials, databases, logs, and vacancy data.
3. Implemented locally: automate inventory-to-bundle coverage checks before publication. Unknown packages, version/license drift, missing
   source files, checksum failures, or missing notices must fail preparation. Do not assume that
   an SBOM or a passing CVE scan proves legal compliance.
4. Pending approval and actual delivery verification: publish the source bundle/notices with clear digest-to-source directions, including the still
   published v1.2.12 image; fixing a later image does not retroactively change the old artifact.
   External publication or visibility changes require explicit approval immediately beforehand.
5. Only after the evidence is complete, decide whether to keep the default no-copyleft report as
   an informative finding or use narrowly reviewed, exact-package exceptions in a project policy.
   A custom-policy pass must not be described as a pass of the unchanged default policy.

No GPL/LGPL/MPL package replacement, project license change, source-bundle publication, GHCR
visibility change, or production rollout was performed as part of this local review and fix.

## Follow-Up: Automatic Notices and Source Draft

Local runtime builds now collect all installed Python/CPython notice texts and public package
inventory automatically. Missing third-party Python notice text fails the build. Source preparation
was exercised against the exact public v1.2.12 ARM64 digest, not merely a newly built candidate.
It preserves all 19 Alpine origin source/recipe sets, 31 locked Python sdists, a hash-pinned
Psycopg 3.3.4 repository, and checksum-matched CPython sources/build-history evidence.
The initial 322-artifact draft was incomplete and correctly failed the readiness check.
Commands, limits, and the publication boundary are in [runtime-source-bundle.md](runtime-source-bundle.md).

The new installed-file inventory finds 16 vendored native shared-library files: twelve inside
Psycopg Binary, two inside Greenlet, and one each inside Pydantic Core and Watchfiles. Their
exact source/notice/build mapping is now preserved and checked against installed file hashes;
wheel SBOMs remain evidence inputs, not a substitute for this review. Psycopg's wheel includes OpenSSL 3.5.5 while the
Alpine runtime has OpenSSL 3.5.9. A fresh targeted Docker Scout CVE scan found zero known
vulnerabilities among eight selected OpenSSL/Psycopg packages. No dependency update followed
from this inventory finding.

## Native Mapping and Pre-Publication Gate

All 16 vendored files now map to preserved upstream sources, notices, and build evidence.
The next source set for the old public image contains 512 artifacts and coverage for all 78
inventoried third-party components. Its only issue is the already published image's missing
JobRadar notice, so it still fails readiness rather than claiming that a local packaging fix
altered an old image. The final review and binary-identification report are versioned alongside
the mappings; see [runtime-native-review.md](runtime-native-review.md).

`publish-image.yml` builds without a push, checks the immutable candidate and its source set,
delivers checksummed sources on the existing release, and only then pushes that same image.
The source asset URL is recorded in the image label. These workflow changes remain local;
actual GitHub/GHCR delivery and retention are not verified until publication is approved.
The original unpinned musl-cross-make checkout and full native binary reproducibility remain
explicit evidence limitations. No claim is made that automated coverage settles legal questions
about GPL-linked Python extensions, LGPL replacement conditions, or every attribution clause.
