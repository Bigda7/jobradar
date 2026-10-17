# Reviewed PostgreSQL fixtures

Prepared and transport archives published on 2026-10-17. This package changes the disposable restore checker,
GitHub CI database, and local Compose test database, not the working database image,
volume, credentials, schema, backup schedule, or collector/notification behavior.
PostgreSQL remains 17.11. No image is rebuilt and no dependency version is changed.
The owner approved archive/code publication, protected-branch merge after required
checks, and installation of only the restore checker on the server. This document
is a runbook, not blanket authorization for later image changes or deployments.

## Exact image transport

`deploy/postgres-fixture-images.json` binds two Docker-save archives to their byte
sizes, SHA256 hashes, previously reviewed OCI index IDs, config IDs, and one-layer
rootfs identities. The files include the existing images and their original OCI
metadata, not a new claim of build provenance or byte-reproducible upstream builds.
They contain no initialized database, production backup, environment file, or key.

| Platform | Archive bytes | Archive SHA256 |
| --- | ---: | --- |
| linux/amd64 | 120198144 | `687c034420f746e808f41fa15fe9b57cd735cf9967df7be0a993e29a20824b79` |
| linux/arm64 | 118014464 | `56336537ac47206c66d9d2ff3ba7ff4f0a9cc34191a38362a8886a2eef15341f` |

The exact transport archives and `SHA256SUMS` are published assets of backend release
v1.2.23. Their GitHub asset size/digests match this inventory. Public availability
must still be reverified when needed; a configured URL alone is not proof of delivery.
Existing corresponding sources are already separate public assets of that release:

- [AMD64 sources](https://github.com/Bigda7/jobradar/releases/download/v1.2.23/postgres-sources-amd64.zip),
  SHA256 `83e06f9426d07ceb712ca90b77cd5d748cb973f24c0edce81bcfa0b19c90b25a`.
- [ARM64 sources](https://github.com/Bigda7/jobradar/releases/download/v1.2.23/postgres-sources-arm64.zip),
  SHA256 `c677e87a45b5a4720ad28cd85a31c5384818758858e4f8326c36fcea2d9b0dda`.

Source delivery and its limitations are described in
[postgres-source-bundle.md](postgres-source-bundle.md). The project remains MIT;
this inventory is engineering evidence, not legal certification.

## Local verification and import

Use the existing Python environment, Docker, and the owned archive directory:

```powershell
.venv/Scripts/python.exe scripts/prepare_postgres_fixture.py verify --architecture amd64 --archive .deploy/postgres-fixture-20261017/jobradar-postgres-17.11-clean-linux-amd64.tar
.venv/Scripts/python.exe scripts/prepare_postgres_fixture.py load --architecture amd64 --archive .deploy/postgres-fixture-20261017/jobradar-postgres-17.11-clean-linux-amd64.tar
$env:JOBRADAR_TEST_POSTGRES_IMAGE = .venv/Scripts/python.exe scripts/prepare_postgres_fixture.py image-id --architecture amd64
.\scripts\compose.ps1 --profile test run --rm test
```

Use ARM64 arguments and its archive on an ARM64 host. The environment assignment
is required for classic Docker's config-ID addressing and for ARM64; the Compose
test default is the reviewed AMD64 OCI index. The ordinary `db` service is unchanged.
`db-test` has `pull_policy: never`, so a missing image is not downloaded implicitly.
Do not start the working stack to verify a test image.

`verify --archive` needs neither Docker nor networking. `load --archive` verifies
the size, full SHA256, OCI index, config checksum, Linux platform and rootfs before
Docker import. Import is the only Docker write performed by the loader: no tag,
container, volume, database, registry, or cloud setting is created or changed.
After import, inspection must match the same immutable ID/platform/rootfs.

Docker's containerd store addresses these exports by their OCI index ID; classic
Docker may use the config digest instead. Both identities are explicitly pinned,
not accepted from arbitrary Docker output. `scripts/postgres_fixture_image.sh`
selects the daemon's AMD64/ARM64 architecture, verifies the installed filesystem,
and prints only the accepted immutable ID. No mutable tag or old-image fallback
is used. `verify_postgres_backup.sh` invokes this selector and uses `--pull never`.
Keep both shell scripts together when installing the verifier.

The isolated restore container keeps its existing no-network, 256 MiB/no-extra-swap,
one-CPU, 128-PID limits, optional shared resource group, and owned-volume cleanup.
A missing or mismatched fixture fails the backup verification; it does not silently
use the old image or claim that the backup passed.

## CI and publication ordering

CI explicitly imports the AMD64 archive before creating the database. GitHub service
containers start too early to load a locally verified image, so the workflow uses
an explicit database-start step instead. The database is published only to loopback,
has memory/CPU/PID ceilings, and uses CI-only credentials. An `always()` cleanup step
checks the run-specific ownership label before removing its container and volume.
Migration, dependency audit, restore/corrupt-input checks, full tests, runtime image
verification, and redacted history scan remain required.

Approved publication and installation sequence (verify each completed stage):

1. Add both exact tar files and their checksum list to the existing public v1.2.23
   release, without overwriting source assets or moving its tag. Verify anonymous
   download size/SHA256 using a fresh download directory for each architecture.
2. Only then publish the fixture-code PR. Required CI will fail closed if either
   the selected archive is unavailable or its size/checksum/identity differs.
   Local tests do not prove a successful GitHub-hosted run.
3. After separate server-install authorization, verify the installed ARM64 image
   with `image-id` first. The reviewed production image may already satisfy the
   checker; do not import another copy unnecessarily. Only if absent, use the loader
   with an explicitly selected archive or download directory. While the backup
   service is idle, install the selector before replacing the verifier, retain the
   previous files for rollback, and run an isolated restore check of a suitable
   backup. Do not restart the working database, change its volume, trigger collectors,
   send notifications, or alter the timer.

The explicit download form is:

```sh
python3 scripts/prepare_postgres_fixture.py load --architecture arm64 --download-directory /path/to/owned/fixture-cache
```

Downloads stream in 1 MiB blocks with exact per-archive size bounds, a transfer
deadline/socket timeout, and HTTPS redirects restricted to GitHub's public asset
hosts. Failed partial files are removed; already cached archives are reverified.
Redirect signatures and exception payloads are not printed. The loader does not
need a GitHub token or production credentials. Preserve the reviewed original tar
bytes: regenerating an export is not permission to replace the pinned checksum.

Before removing old Docker images, retain the previous verifier and its image for
the agreed rollback period. This package performs no broad image/volume pruning.
