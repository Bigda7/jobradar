# Digest-pinned backend image rollout

This is a manual release procedure, not an automatic deployment. The normal
`compose.yaml` continues to build the local image. The registry override is used
only when explicitly selected. Publishing an image and changing production are
separate decisions.

## Publish a candidate

1. Confirm the backend release commit is on `main`, CI passed for that commit,
   and an existing `vX.Y.Z` tag matches `pyproject.toml`. The release must already be
   published (not a draft) in the public repository and contain the source-verification package.
2. Manually start the `Publish verified runtime image` GitHub Actions workflow
   with that tag. It builds the Linux ARM64 runtime candidate locally on the runner,
   verifies its attestations and reviewed corresponding-source coverage, and uploads a
   source ZIP and SHA256 file to that release. Anonymous source access is checked before
   publishing the same candidate to `ghcr.io/bigda7/jobradar`. It checks the registry copy for an attached SLSA
   provenance document and SPDX SBOM. It writes an `@sha256:...` reference to
   the workflow summary. Do not use the mutable `vX.Y.Z` tag for deployment.
3. Confirm the GHCR package visibility and access permissions before any server
   change. GHCR packages may be private. The production server needs a credential
   with `read:packages` if the package is private. Keep that credential in the
   server's secret store; do not add it to `.env`, this repository, logs, or chat.
4. Review the image's vulnerability and license-policy scan. The attestation
   checks do not resolve the existing copyleft-license policy finding and do not
   imply that every dependency is safe.

The workflow requires `packages: write` and `contents: write` for its GitHub token.
Source assets must remain available for their distributed image; do not delete them during
routine cleanup. An existing asset is not overwritten on a retry. Source/notice obligations
and evidence limits are documented in [runtime-source-bundle.md](runtime-source-bundle.md).
The local workflow changes still require a separately approved first publication. It does not
deploy, run migrations, send notifications, or change package visibility. A
failed post-publication verification can leave a published tag in GHCR; it must
not be deployed without a successful verification and explicit approval.

## Preflight before an approved production rollout

1. Recheck the exact server architecture and Docker Compose version. This
   override requires Docker Compose 2.24.4 or newer for `!reset`. The image is
   built for `linux/arm64` only.
2. Prepare a fresh PostgreSQL backup and prove it restores in an isolated
   container. Review migrations and rollback compatibility before proceeding:
   `api` runs `alembic upgrade head` on startup.
3. Verify the server can authenticate to GHCR without exposing a token on the
   command line. Prefer a scoped, read-only credential and protected Docker
   credential storage. Never print the token or resolved Compose configuration.
4. Set `JOBRADAR_IMAGE` to the verified digest reference from the successful
   workflow. Reject a tag-only reference. With `POSTGRES_PASSWORD` supplied by
   the existing secret mechanism, render and inspect the effective Compose
   configuration. Check that `api`, `worker`, and `bot` all use that exact digest
   and have no `build` section. Do not print the rendered configuration in a
   shared log because it can contain secrets.
5. Confirm the registry digest can be pulled. Keep the previous server Compose
   files, prior image, verified backup, and a recovery plan available.

Example commands to adapt for a separately approved rollout, run from the
backend directory on the ARM64 server:

```sh
export JOBRADAR_IMAGE='ghcr.io/bigda7/jobradar@sha256:REPLACE_WITH_VERIFIED_DIGEST'
docker compose -f compose.yaml -f compose.registry.yaml config --quiet
docker compose -f compose.yaml -f compose.registry.yaml pull api worker bot
docker compose -f compose.yaml -f compose.registry.yaml up -d --no-build api worker bot
docker compose -f compose.yaml -f compose.registry.yaml ps
```

The placeholder digest above is deliberately invalid; replace it with the
workflow's verified 64-character SHA-256 value. Do not run these commands as a
routine part of CI or this documentation change.

## Verify and recover

After an approved rollout, check healthy `api` and `db`, running `worker` and
`bot`, public `/health` and `/ready`, and authenticated jobs, matches, and source
APIs. Check a completed worker cycle and frontend proxy responses. Sending a
Telegram test message is a separate action requiring approval. If something
fails, diagnose database migration compatibility first; changing only the
image may not safely roll back the database. Recover with the retained image
and Compose configuration only when compatible, or restore the verified backup
under an approved recovery plan.
