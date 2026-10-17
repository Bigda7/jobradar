# Local PostgreSQL libxml2 security candidate

The owner approved the exact local source/backport and builder-tool package in
[the proposal](../../../docs/postgres-libxml2-local-package-proposal-2026-10-16.md).
This does not authorize publishing, deployment or production changes. See the
workspace's current libxml2 package report for architecture-specific results;
a successful build is not complete security acceptance.

## Scope and source

The runtime changes only libxml2 2.13.9-r2 to **2.13.9-r200**, a JobRadar-local
revision, not an official Alpine release. PostgreSQL remains 17.11. All 45 runtime
APKs are retained; gosu, OpenSSL, zlib, UUID, libxslt and liblzma hashes are checked
unchanged. SONAME 2 and all original versioned exports are checked during builds;
runtime feature flags are compared separately. Source and patches retain their
license/copyright notices, including files with distinct MIT notices.

The GNOME release archive is SHA512-pinned in the generator/build script.
`source.tar.xz` is ignored by Git; the exact archive is in the exported source
bundle and is available from the official release URL in the proposal.
Eleven original upstream commits remain under `upstream/`, with checksums checked
by the generator and offline build. The existing Alpine reader/schema fix and
regression are retained. Its preservation patch's preparation-only header is
historical provenance, not current approval status.

`generate_backports.py` produces the deterministic patch under `generated/` and
fails on missing/ambiguous contexts or changed upstream checksums. It adapts older
allocator, writer, loader and schema APIs. Local boundary hardening also addresses
integer headroom, empty validation buffers, the older escaping writer and a
growth-failure cleanup. XML features are not intentionally disabled.

## Build

From the backend root with the existing locked Python environment and Docker:

```powershell
.venv/Scripts/python.exe deploy/postgres-prototype/libxml2/generate_backports.py
.venv/Scripts/python.exe deploy/postgres-prototype/libxml2/build_candidate.py --architecture amd64
.venv/Scripts/python.exe deploy/postgres-prototype/libxml2/build_candidate.py --architecture arm64
```

The helper verifies the exact previously accepted APK base identity and platform
before invoking the existing `desktop-linux` builder. It does not pull or publish.
Input identities are in `BASE_IMAGES`; do not override the Dockerfile argument with
another base. Timestamped logs go to `.deploy/postgres-libxml-review-20261016/`.

The builder installs exactly the approved 29 changes (45 to 73 packages). Compiler
flags retain Alpine's stack-clash protection, format checking, no-PLT and packed
relocations, with explicit stack protector, RELRO and immediate binding. Source
compilation/tests/packaging have no network access. A package-signing key exists
only in a BuildKit tmpfs mount, never an image layer or exported bundle. Runtime
installation accepts only the hash-verified local APK with network disabled; no
repository trust policy is changed.

Targets `compiled`, `verified`, `build`, `artifacts` and `candidate` separate source
compilation, tests, packaging and runtime installation. Old/intermediate fixtures
demonstrate selected regressions before their fixes; patched tests must succeed.
Synthetic lengths avoid huge allocations. XML utility tests explicitly load the
new library, not the old builder runtime.

## Verification

For each architecture, replace both architecture/tag arguments consistently:

```powershell
.venv/Scripts/python.exe deploy/postgres-prototype/libxml2/check_xml.py --architecture amd64 --candidate-image jobradar-postgres-libxml2:amd64-20261016
.venv/Scripts/python.exe deploy/postgres-prototype/check_candidate.py --architecture amd64 --candidate-image jobradar-postgres-libxml2:amd64-20261016
.venv/Scripts/python.exe deploy/postgres-prototype/check_tls.py --architecture amd64 --candidate-image jobradar-postgres-libxml2:amd64-20261016
.venv/Scripts/python.exe deploy/postgres-prototype/gosu/check_runtime.py --architecture amd64 --candidate-image jobradar-postgres-libxml2:amd64-20261016
.venv/Scripts/python.exe -m pytest tests/unit/test_postgres_libxml2.py -q
```

The ARM64 gosu checker additionally needs `--emulated-arm64`; it records two native
setid checks as **unverified**, never passed. The SQL checker uses a labeled,
loopback-only synthetic database, checks installed-library behavior, XPath,
XMLTABLE, serialization, malformed XML, xml2 and libxslt, then removes its fixture.
It does not start a collector or notification worker.

`features.c` queries the stable `xmlHasFeature` ABI without new development packages.
Compare all 64 queried values against the original library using the existing
builder and a copy of the exact installed candidate library. The package report
records that check and default RelaxNG include-limit verification.

Run Scout CVE/SBOM scans sequentially without exclusions and retain raw reports.
Upstream's fuzz corpus replay is not extended sanitizer-guided fuzzing. External
XML conformance and XSTC datasets are absent from the release archive, even when
the default aggregate target exits zero. `check_external_corpora.sh` can separately
compare the three checksummed W3C archives against the old and exact packaged
library, using existing verified builder tools and read-only input mounts. It
compares detailed logs as well as summaries and retains expected upstream errors;
see the package report for actual results. No extra software dependency is added.
The corpus downloads stay outside the runtime. Do not call missing external tests
a pass or describe expected-error results as zero-error conformance.

Local Git attributes preserve original upstream patch bytes and force LF for
generated patches, manifests and builder pins. This prevents Windows checkout
conversion from invalidating source checksums or APK version constraints.

Sources and evidence are under
`/usr/local/share/jobradar-postgres-prototype/libxml2/` in the candidate and
`.deploy/postgres-libxml-final-20261016/` when exported. Vulnerable bytes may still
exist in lower image layers; replacement/cleaner metadata does not prove a clean
distributable image. No Compose/CI/backup pin is changed by this prototype.
