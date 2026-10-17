# Local PostgreSQL gosu candidate

Owner-approved scope on 2026-10-16: retain PostgreSQL 17.11 and the existing pinned
base; rebuild upstream gosu 1.19 with Go 1.26.9, moby/sys/user 0.4.1 and x/sys 0.49.0.
The earlier approved zlib 1.3.2-r1 fix is retained. No other APK version changes are
allowed by the build guard. Application dependency files, Compose, CI and backup
image pins are unchanged by this package. This is not a production deployment tool.

## Reproducible inputs and local builds

From the backend root, download the pristine archive if it is absent:

```powershell
Invoke-WebRequest -Uri 'https://codeload.github.com/tianon/gosu/tar.gz/6456aaa0f3c854d199d0f037f068eb97515b7513' -OutFile deploy/postgres-prototype/gosu/source.tar.gz
Get-FileHash deploy/postgres-prototype/gosu/source.tar.gz -Algorithm SHA256
docker buildx build --builder jobradar-release-check --platform linux/amd64 --target artifacts --output type=local,dest=.deploy/postgres-gosu-final-20261016 deploy/postgres-prototype/gosu
docker buildx build --builder jobradar-release-check --platform linux/amd64 --target candidate --tag jobradar-postgres-gosu:amd64-20261016 --load deploy/postgres-prototype/gosu
docker buildx build --builder jobradar-release-check --platform linux/arm64 --target candidate --tag jobradar-postgres-gosu:arm64-20261016 --load deploy/postgres-prototype/gosu
```

Archive SHA256 is pinned to
`33d7537d588ea49458b9509bcf4554bdf5ceacc66da71e5caa1058ea3b689c3b`.
The existing Go builder is pinned by digest; nothing is installed on the host.
Checksums and Go module verification are not independent author-signature verification.

The build preserves upstream `.go` file checksums, verifies exactly two module
version changes with no added/replaced modules, checks rejection of unauthorized
graphs, and compiles each binary twice with byte-for-byte comparison. Upstream
symbols are retained for later function-level analysis. Oversized group input is
accepted by the original dependency and rejected by the updated one in a red/green
test. Three updated parser tests also cover ordinary supplementary groups and
invalid user specifications. Updated source module files and licenses are included
in the candidate; the unmodified archive remains separately identifiable.

`gosu --version` retains upstream 1.19 while reporting the new compiler. Its Go main
module is `(devel)` because no fake upstream release tag is fabricated. Use the image
source labels, archive hash, module graph and build metadata to identify this custom
build; scanners cannot infer a newly published upstream gosu release from it.

## Isolated checks

```powershell
uv run --frozen python deploy/postgres-prototype/check_candidate.py --architecture amd64 --candidate-image jobradar-postgres-gosu:amd64-20261016
uv run --frozen python deploy/postgres-prototype/check_candidate.py --architecture arm64 --candidate-image jobradar-postgres-gosu:arm64-20261016
uv run --frozen python deploy/postgres-prototype/gosu/check_runtime.py --architecture amd64 --candidate-image jobradar-postgres-gosu:amd64-20261016
uv run --frozen python deploy/postgres-prototype/gosu/check_runtime.py --architecture arm64 --candidate-image jobradar-postgres-gosu:arm64-20261016 --emulated-arm64
```

The existing checker uses labeled isolated synthetic databases and validates migrations,
PostgreSQL integration tests, existing-volume restart and compressed restores in both
directions, corrupt archive rejection, indexes and notification fixture identity.
Candidate images must already exist locally: no implicit pull is allowed.

Runtime checks use network-disabled read-only containers and temporary scratch storage,
not a database, source collector or Telegram client. They cover named/numeric identities,
explicit groups, HOME reset, invalid inputs/commands and process replacement. Ten checks
passed on AMD64. Eight passed on emulated ARM64. The two setid-binary rejection checks
are explicitly UNVERIFIED on native ARM64: the setuid case failed under local emulation
for both the original and rebuilt gosu. This does not prove a native ARM64 regression
or successful native protection. The actual installed candidate binary remains mode
0755, never setuid/setgid. Do not weaken the upstream guard to make emulation pass.

The native limitation above describes the original local package. On 2026-10-17,
both exact-message setid guards passed on real aarch64 hardware for the final clean
PostgreSQL image `sha256:51958852a6c4b54ffd5f768cf4e6e6a7786ffe1b78a30dbd638b835381de0e41`.
The approved temporary containers did not start PostgreSQL or access working data;
production container identities/restart counts/health were unchanged and owned
remote artifacts were removed. This does not retroactively test every historical
candidate or validate the complete production rollout. See workspace
`FINAL_VERIFICATION_2026-10-17.md`.

## Scan evidence and limitations

Retain unfiltered full-image SARIF and SBOM. On this workstation Scout 1.24.0 inventories
both the old gosu from base layer ordinal 4 and the replacement at ordinal 26. The
full-image report still has 85 findings (6 Critical, 42 High, 30 Medium, 7 Low). Do not
claim they disappeared or suppress base-image findings. Old bytes remain recoverable
from the lower image layer even though the normal runtime path is overwritten.

Both independently scanned replacement binaries contain Go 1.26.9/user 0.4.1/sys 0.49.0
and have zero known scanner findings. This is narrower evidence than a clean full image
or an exhaustive source audit. The 25 APK findings remain: libxml2 11, OpenSSL 10,
util-linux 4; component inventory is not proof of exploitability. Native ARM64 setid
verification, libxml2/other APK remediation, full-image supply-chain acceptance and
production backup/rollout/rollback remain open. Do not publish this candidate as a
complete PostgreSQL security remediation.

See workspace `POSTGRES_GOSU_LOCAL_PACKAGE_2026-10-16.md` for final results and hashes.

## Separately approved APK follow-up

The owner subsequently approved exactly libcrypto3/libssl3 3.5.7-r0 to 3.5.9-r0
and libuuid 2.42.1-r0 to 2.42.3-r1. The new `apk-candidate` target extends `candidate`
without changing the gosu-only target. Full installed-package comparison rejects
additional changes; PostgreSQL/gosu/libxml2/zlib binary hashes must remain equal.

```powershell
docker buildx build --builder jobradar-release-check --platform linux/amd64 --target apk-candidate --tag jobradar-postgres-apk:amd64-20261016 --load deploy/postgres-prototype/gosu
docker buildx build --builder jobradar-release-check --platform linux/arm64 --target apk-candidate --tag jobradar-postgres-apk:arm64-20261016 --load deploy/postgres-prototype/gosu
uv run --frozen python deploy/postgres-prototype/check_candidate.py --architecture amd64 --candidate-image jobradar-postgres-apk:amd64-20261016
uv run --frozen python deploy/postgres-prototype/check_candidate.py --architecture arm64 --candidate-image jobradar-postgres-apk:arm64-20261016
uv run --frozen python deploy/postgres-prototype/check_tls.py --architecture amd64 --candidate-image jobradar-postgres-apk:amd64-20261016
uv run --frozen python deploy/postgres-prototype/check_tls.py --architecture arm64 --candidate-image jobradar-postgres-apk:arm64-20261016
```

TLS checks generate a disposable self-signed certificate using an existing host
OpenSSL command, copy it into a labeled synthetic PostgreSQL fixture and test
TLS1.2/TLS1.3 with certificate verification, authentication failures and non-TLS
compatibility. They never enable TLS on production or install a host dependency.
UUID v1/v4/v5 tests load the real PostgreSQL extension; standalone `ldd` on a plugin
would incorrectly complain about symbols that its PostgreSQL host provides.

All 28 PostgreSQL tests and eight restore/integrity groups passed per architecture;
six additional TLS/UUID groups passed per architecture; 877 ordinary tests and static
checks passed. Full unfiltered scans are now 71 (4C/31H/29M/7L), down from85 by14 IDs,
no new IDs. libxml2 and old lower-layer gosu findings remain; this is not a complete
security clearance. See workspace `POSTGRES_APK_LOCAL_PACKAGE_2026-10-16.md`.
