#!/usr/bin/env bash

set -Eeuo pipefail

if [[ "$#" -ne 1 || ! -f "$1" || ! -s "$1" ]]; then
  printf 'Usage: %s non-empty-backup.dump\n' "$0" >&2
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  printf 'Required command is not available: docker\n' >&2
  exit 1
fi

backup_path="$1"
postgres_image='postgres:17-alpine@sha256:18cfe3ef5e6815560c98237d6216d1e5119702fb0f3894c8785dd58b8bbe5d73'
container_name="jobradar-restore-check-$$-${RANDOM}"
export POSTGRES_PASSWORD
POSTGRES_PASSWORD="$(od -An -N24 -tx1 /dev/urandom | tr -d '[:space:]')"

cleanup() {
  # Remove only this isolated container and its anonymous restore volume.
  docker rm --force --volumes "${container_name}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker run \
  --detach \
  --rm \
  --name "${container_name}" \
  --network none \
  --label jobradar.restore-check=true \
  --env POSTGRES_DB=jobradar \
  --env POSTGRES_USER=jobradar \
  --env POSTGRES_PASSWORD \
  "${postgres_image}" >/dev/null

ready=0
for _ in {1..30}; do
  if docker exec "${container_name}" pg_isready --host=127.0.0.1 \
    --username=jobradar --dbname=jobradar \
    >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done

if [[ "${ready}" -ne 1 ]]; then
  printf 'Temporary PostgreSQL instance did not become ready.\n' >&2
  exit 1
fi

if ! docker exec -i "${container_name}" pg_restore \
  --exit-on-error \
  --single-transaction \
  --no-owner \
  --no-privileges \
  --username=jobradar \
  --dbname=jobradar \
  <"${backup_path}" 2>/dev/null; then
  printf 'The backup could not be restored into the temporary database.\n' >&2
  exit 1
fi

if ! verification_output="$(
  docker exec -i "${container_name}" psql \
    --username=jobradar \
    --dbname=jobradar \
    --no-psqlrc \
    --quiet \
    --tuples-only \
    --no-align \
    --set ON_ERROR_STOP=1 2>/dev/null <<'SQL'
DO $$
BEGIN
  IF to_regclass('public.alembic_version') IS NULL
    OR to_regclass('public.sources') IS NULL
    OR to_regclass('public.opportunities') IS NULL
    OR to_regclass('public.listings') IS NULL
    OR to_regclass('public.notification_deliveries') IS NULL THEN
    RAISE EXCEPTION 'Required JobRadar tables are missing';
  END IF;

  IF (SELECT count(*) FROM alembic_version) <> 1 THEN
    RAISE EXCEPTION 'Expected exactly one Alembic version';
  END IF;

  IF (SELECT count(*) FROM sources) < 1 THEN
    RAISE EXCEPTION 'Expected at least one source';
  END IF;

  IF EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE contype = 'f' AND NOT convalidated
  ) THEN
    RAISE EXCEPTION 'Unvalidated foreign keys exist';
  END IF;
END $$;

SELECT 'migration=' || version_num FROM alembic_version;
SELECT 'sources=' || count(*) FROM sources;
SELECT 'opportunities=' || count(*) FROM opportunities;
SELECT 'listings=' || count(*) FROM listings;
SELECT 'notification_deliveries=' || count(*) FROM notification_deliveries;
SQL
)"; then
  printf 'Restored database failed JobRadar integrity checks.\n' >&2
  exit 1
fi

printf 'Backup restore verified in an isolated PostgreSQL container.\n%s\n' \
  "${verification_output}"
