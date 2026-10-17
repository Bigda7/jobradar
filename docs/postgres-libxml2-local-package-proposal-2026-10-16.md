# Local libxml2 backport package proposal

Date: 2026-10-16. Latest status: owner approved this exact local package; completed
locally on AMD64 and emulated ARM64. Library/APK, database/restore/XML/TLS checks
and full image scans are verified on each architecture. The earlier utility
failure was a test loader-path error and is corrected. See workspace
`POSTGRES_LIBXML2_LOCAL_PACKAGE_2026-10-16.md` for current evidence and limitations.
The proposal below records the approved scope, not a new approval gate. This is
not deployment approval or complete image security acceptance.

Subsequent approved clean-layer reassembly is complete too; both new full images
scan with zero findings and preserve this exact runtime. See workspace
`POSTGRES_CLEAN_IMAGE_LOCAL_PACKAGE_2026-10-16.md`. Historical layered prototypes
and the libxml2 source/backport evidence below remain unchanged.

## Proposed change

Rebuild the existing libxml2 2.13.9 with its existing Alpine fix plus the eleven
mapped upstream advisory fixes. Package it as `libxml2-2.13.9-r200`, explicitly a
JobRadar-local revision, not an official Alpine release or an upstream 2.13.200.
Preserve SONAME `libxml2.so.2`, existing symbol versions and runtime features.
Do not copy a newer incompatible library under an old SONAME.

Only this APK may change in the new runtime candidate. Keep PostgreSQL 17.11,
libxslt 1.1.43-r3, the approved gosu rebuild, zlib 1.3.2-r1, OpenSSL 3.5.9-r0,
libuuid 2.42.3-r1 and every other runtime package/binary unchanged. The existing
45-package runtime inventory must stay at 45; no compiler, development package,
Python binding or XML command-line utility is to be added to it.

This route means maintaining a small downstream security patch set until an
official compatible package can replace it. Rebuilding successfully alone does
not establish ABI compatibility or CVE closure. The newer upstream branch has a
different SONAME; moving to it would be a separate, substantially broader package.

## Verified preparation

- Downloaded the actual GNOME 2.13.9 release archive, not just the Git snapshot.
  Its SHA512 matches Alpine's recipe:
  `62d4813860124c969f204aaf33b497105dbc32a6c5655f5a86168743660e10987d687d7e5e7ee49fdfdeb8f6ad9fa4503f81fcce2e4d459094895f02436d1b13`.
  SHA256: `a2c9ae7b770da34860050c309f903221c67830c86e4a7e760692b803df95143a`.
- Validated all 4,176 archive entry paths before extraction to the ignored review
  directory. No application source or existing candidate image was overwritten.
- Both existing Alpine CVE-2026-6732 patch downloads match the recipe's SHA512.
  The code patch's context differs from the release source: it mentions
  `XML_PARSE_NOCDATA`, whereas this source selects the callback by a null CDATA
  callback check. Strict Git application rejects that context. This is not proof
  that the installed Alpine binary lacks the fix; Alpine uses a different patch
  preparation process, which was not reproduced here.
- Prepared `deploy/postgres-prototype/libxml2/patches/0001-preserve-CVE-2026-6732.patch`.
  It preserves exactly the four callback argument replacements, without changing
  the original CDATA condition. Strict application and Alpine's original regression
  test patch both succeed in an isolated source fixture. This test was not compiled.
- After that baseline fix, the four direct library patches below apply in sequence
  to actual release source. Verified that none of the four old callback argument
  forms remains in the fixture. No fuzz, rejected-hunk acceptance or source skipping.
- Simulated the exact builder tools below on AMD64 and emulated ARM64. The resolved
  versions match on both. These were `apk --simulate` calls, not installations.

Raw simulation outputs are retained in the ignored review directory as
`apk-simulate-amd64.txt` and `apk-simulate-arm64.txt`.

Source checks used the existing pinned Go builder's Git in a network-disabled,
read-only container with a writable temporary filesystem. An initial full-tree
attempt failed because that container lacks `xz`; existing Windows tar extracted
the verified archive instead. A later check copied only relevant source files,
avoiding slow full-tree copies across the Windows bind mount. Bind mounts expose
the source as executable, causing Git mode warnings, not rejected code hunks.

## Exact upstream scope and required adaptation

| Advisory | Upstream commit | Preparation result / acceptance requirement |
| --- | --- | --- |
| CVE-2026-86137 | `76fe08d97de88bfaef2f7d5cd27f11954cc5bee2` | Direct application; truncated regexp lookahead regression |
| CVE-2026-86141 | `e89a8aae4c9b40cdafcf66b3f9e57c62db37bb55` | Direct after 86137; allocation-failure regression; never ship its prerequisite alone |
| CVE-2026-86138 | `a4cba4b5b5a8c42e155ed42d2d2a44955465a2e4` | Direct; QName allocation bounds and normal dictionary behavior |
| CVE-2026-86140 | `d1686f91dbda141a752200419d35639fd6b38340` | Direct; validation formatting boundary tests, including tiny/empty buffers |
| CVE-2026-86139 | `8edbbdb09f24d26a2f900141fddc2b9d014f53b0` | Backport to atomic allocator context; test zero/oversized length and integer headroom before adding 20 |
| CVE-2026-86142 | `6b3a736c0edc74ceec3d82f5252499d7911b3a58` | Backport to older XPointer body; reject unrepresentable length before adding 1 |
| CVE-2026-86143 | `90f293ba74d28b1d570920382e707586f68ebf35` | Old writer narrows size_t into int earlier; check before narrowing, not only immediately before callback; cover encoded/plain write and flush |
| CVE-2026-86144 | `b63cd517afecb76582dd9488c55e54ceaf50de61` | Old text loader uses xmlLoadExternalEntity, not xmlLoadResource; propagate options through that API and both public wrappers; controlled no-network loader regressions |
| CVE-2026-0989 | `66c52b3ac6c32ab112ec2a3bf41e6c30948be113` | Adapt include depth limit, error returns and tests; preserve old pointer typedefs and symbol map; test normal and excessive nesting |
| CVE-2025-8732 | `3425dece47c8db600f8d7328ae2d7ddfaa0d7b2d` | Adapt catalog recursion depth across every changed caller; self-referential and normal local catalog regressions |
| CVE-2026-11979 | `c2e233fc1b341685fc99621b2768b503f777a72e` | Utility-only; absent at runtime. Backport its shell fix for builder tests; adapt absent test script rather than silently skipping tests |

Only the baseline preservation patch has been saved as a proposed patch file;
no Git commit was made. The other upstream downloads remain ignored
review inputs. Six library backports plus the utility test adaptation have not
been implemented or compiled. Direct patch application is not a regression test
or proof of security. Test boundary cases with synthetic length/allocation stubs
where possible, not multi-gigabyte allocations on the workstation.

## Builder-only dependency proposal

Use a separate stage based on the already verified APK candidate. Pin these direct
tools: `abuild=3.17.0-r0`, `gcc=15.2.0-r5`, `musl-dev=1.2.6-r2`,
`make=4.4.1-r4`, `patch=2.8-r0`, `zlib-dev=1.3.2-r1`, `xz-dev=5.8.4-r0`,
`perl=5.42.2-r1`, `binutils=2.45.1-r1`, `pkgconf=2.5.1-r0`.
The full simulated transaction is exactly these 29 changes on either architecture:

| Builder package | Proposed version | Action |
| --- | --- | --- |
| xz-libs | 5.8.4-r0 | Builder only: 5.8.3-r0 to 5.8.4-r0 |
| libcap2 | 2.78-r0 | Add |
| libcap-getcap | 2.78-r0 | Add |
| fakeroot | 1.37.2-r0 | Add |
| lzip | 1.26-r0 | Add |
| openssl | 3.5.9-r0 | Add command-line tool |
| patch | 2.8-r0 | Add |
| pkgconf | 2.5.1-r0 | Add |
| acl-libs | 2.3.2-r1 | Add |
| tar | 1.35-r5 | Add |
| abuild | 3.17.0-r0 | Add |
| abuild-sudo | 3.17.0-r0 | Add |
| jansson | 2.15.0-r0 | Add |
| binutils | 2.45.1-r1 | Add |
| libgcc-static | 15.2.0-r5 | Add |
| libgomp | 15.2.0-r5 | Add |
| libatomic | 15.2.0-r5 | Add |
| gmp | 6.3.0-r4 | Add |
| isl26 | 0.26-r2 | Add |
| mpfr4 | 4.2.2-r0 | Add |
| mpc1 | 1.3.1-r1 | Add |
| gcc | 15.2.0-r5 | Add |
| make | 4.4.1-r4 | Add |
| musl-dev | 1.2.6-r2 | Add |
| libbz2 | 1.0.8-r6 | Add |
| perl | 5.42.2-r1 | Add |
| xz | 5.8.4-r0 | Add |
| xz-dev | 5.8.4-r0 | Add |
| zlib-dev | 1.3.2-r1 | Add |

Require an exact full before/after inventory guard, not only these direct pins.
Reject any additional upgrade or package. The builder's 45 baseline packages
become 73 (28 additions and one replacement); the runtime must remain 45.
The newer builder liblzma must not be copied into the runtime; verify the output
loads against retained runtime xz-libs 5.8.3-r0 and imports no unavailable symbols.

The proposed tools need no host installation. Alpine repository simulation could
not resolve packages named libasan/libubsan; do not install alternatives or claim
sanitizer coverage. The existing Debian Go builder has GCC 12.2.0 and sanitizer
paths, but no successful sanitizer compilation/link test was performed. Any
sanitizer harness there must be checked separately and cannot substitute for the
Alpine runtime tests. Additional tools beyond the inventory require a new decision.

## Work after the single package approval

1. Implement and strictly apply the complete reviewed patch series with pinned
   original and adapted patch hashes. Preserve licenses, exact release source,
   patch provenance and a source bundle. Do not download floating patches in builds.
2. Build unpatched/patched test fixtures; demonstrate meaningful regressions before
   and after. Run upstream tests including Alpine's existing reader/schema test.
   Keep normal feature behavior, malformed-input handling and allocation failures
   observable. Failed or unavailable tests stay failed/unverified, not skipped green.
3. Build a clearly identified local APK for each architecture. Install only that
   verified artifact into separate runtime candidates through APK rather than
   overwriting package-managed files without updating package inventory.
4. Compare SONAME, exported/versioned symbols, dependencies, feature flags, all
   runtime APKs and unrelated binary hashes. Exercise PostgreSQL SQL XML/XPath,
   XML schema/reader behavior and libxslt loading, not just server startup.
5. Run the existing isolated PostgreSQL integration, migration, restart, backup
   restore, TLS/UUID and gosu checks on AMD64 and emulated ARM64. Run backend unit
   and static checks for any added test/guard code. No real database or notification.
6. Scan both full images sequentially and preserve unfiltered SARIF/SBOM. Do not
   fabricate upstream security metadata or add blanket suppressions. A custom
   backport may still be flagged by version-based matching; source/test evidence
   and raw scanner status must both be reported.

Acceptance is not guaranteed in advance. If a backport fails compatibility or
security regression checks, do not publish it or silently broaden dependencies.

## Unchanged boundaries and remaining work

This preparation did not install builder packages, build/replace libxml2, change
runtime APKs, run a new CVE scan, repeat the application suite, access production,
send notifications, commit, push or deploy. Previous image/test/scan results remain
historical evidence, not new results of this package.

The eleven libxml findings remain open. The separate sixty lower-layer Go/module
findings and native ARM64 setid acceptance also remain open. A local replacement
does not erase old vulnerable bytes from lower image layers. Full-image acceptance,
production rollout/rollback and external publication are not authorized here.

## Sources

- [Alpine 3.24 recipe and source checksums](https://raw.githubusercontent.com/alpinelinux/aports/3.24-stable/main/libxml2/APKBUILD)
- [GNOME release archive](https://download.gnome.org/sources/libxml2/2.13/libxml2-2.13.9.tar.xz)
- [Alpine existing code fix](https://raw.githubusercontent.com/alpinelinux/aports/3.24-stable/main/libxml2/CVE-2026-6732.patch)
- [Alpine existing regression test](https://raw.githubusercontent.com/alpinelinux/aports/3.24-stable/main/libxml2/CVE-2026-6732-test.patch)
- [Upstream commits](https://github.com/GNOME/libxml2/commits/); each exact commit
  above is available at `https://github.com/GNOME/libxml2/commit/<full-sha>`.
- [2.13.9 ABI configuration](https://raw.githubusercontent.com/GNOME/libxml2/v2.13.9/configure.ac)
- [2.15.4 ABI configuration](https://raw.githubusercontent.com/GNOME/libxml2/v2.15.4/configure.ac)

Prior inventory and advisory mapping:
[APK remediation review](postgres-apk-remediation-review-2026-10-16.md).
