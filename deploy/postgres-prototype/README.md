# Local PostgreSQL zlib prototype

Latest follow-up: [clean/](clean/README.md) reassembles the approved final libxml2
runtime into one fresh layer without the superseded lower-layer files. Earlier
zlib/gosu/APK/libxml2 prototypes and their historical evidence remain unchanged.
See workspace `POSTGRES_CLEAN_IMAGE_LOCAL_PACKAGE_2026-10-16.md` for current results
and limitations; no prototype command authorizes deployment or publication.

This directory builds and checks an isolated local candidate. It is not a deployment
tool and does not change the application's Docker Compose, CI service or backup image
pins. Do not use this candidate as a production security fix without resolving the
remaining findings and separately approving rollout.

## Scope

The separately approved follow-up in [gosu/](gosu/README.md) adds a local gosu rebuild
while preserving this original zlib-only Dockerfile and its baseline evidence.
The shared checker accepts `--candidate-image` and never implicitly pulls an image.
Neither prototype is a complete PostgreSQL security remediation or a deployment.

The owner approved zlib 1.3.2-r0 to 1.3.2-r1 only. The base is the currently pinned
PostgreSQL 17 Alpine image, index digest
`sha256:18cfe3ef5e6815560c98237d6216d1e5119702fb0f3894c8785dd58b8bbe5d73`.
PostgreSQL remains 17.11. The Dockerfile fails if any other installed package version
changes. It also verifies unchanged PostgreSQL, gosu, OpenSSL and libxml2 binaries.
The upstream zlib license is included with a pinned download checksum.

## Build and check

Use the existing Docker Desktop, Buildx builder, uv and locked Python environment.
No new host compiler or dependency is installed. From the backend root:

```powershell
docker buildx build --builder jobradar-release-check --platform linux/amd64 --tag jobradar-postgres-zlib:amd64-20261013 --load --progress plain deploy/postgres-prototype
docker buildx build --builder jobradar-release-check --platform linux/arm64 --tag jobradar-postgres-zlib:arm64-20261013 --load --progress plain deploy/postgres-prototype
uv run python deploy/postgres-prototype/check_candidate.py --architecture amd64
uv run python deploy/postgres-prototype/check_candidate.py --architecture arm64
uv run python -m pytest tests/unit/test_postgres_prototype.py -q
```

ARM64 execution is emulated on this AMD64 Windows workstation. Each run creates unique
labeled containers and volumes containing synthetic data only. PostgreSQL is published
on a random loopback-only port. There is no HTTP server, worker, bot, real notification
or production database connection. The synthetic password in the checker is not a
production credential.

The bootstrap requires both database URLs to point to loopback `/jobradar_test` and
selects the Windows-compatible asyncio policy. Repository helpers remain importable
as with `python -m`. API test settings match CI; inherited API credentials and hostnames
are overridden for in-process ASGI tests, not by changing production authentication.

Checks cover fresh Alembic migration and drift detection, all PostgreSQL integration
tests, existing-volume restart, compressed custom dumps restored in both directions
between the original and candidate images, corrupt archive rejection, and pg_trgm
index rebuild. Snapshots compare schema revision, record counts, a large description's
hash and length, sent-notification fields, foreign key/index validity, encoding,
locale and basic trusted XML/trigram behavior. This is not a hostile XML exploit test.

Ownership and mount checks precede fixture cleanup. Successful and failed runs remove
their own test containers and volumes; no general Docker prune is performed. Images,
scanner reports and build caches remain local. Review cleanup errors rather than
deleting unrelated resources.

## Security boundary

Functional success is not a vulnerability clearance. Preserve all Scout SARIF findings;
do not use severity filters or exceptions to describe the image as clean. The zlib-only
change leaves gosu/Go, libxml2, OpenSSL and util-linux findings open. Known image hashes,
test results and limitations are recorded in the workspace PostgreSQL package report.
This package does not verify a real production backup, S3, native ARM64 performance,
independent signatures or a production rollout/rollback.
