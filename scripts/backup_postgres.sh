#!/usr/bin/env bash

set -Eeuo pipefail

umask 077

project_dir="${JOBRADAR_PROJECT_DIR:-/opt/jobradar}"
backup_dir="${JOBRADAR_BACKUP_DIR:-${project_dir}/backups}"
retention_days="${JOBRADAR_BACKUP_RETENTION_DAYS:-14}"
lock_file="${JOBRADAR_BACKUP_LOCK_FILE:-/tmp/jobradar-postgres-backup.lock}"
s3_bucket="${JOBRADAR_BACKUP_S3_BUCKET:-}"
s3_prefix="${JOBRADAR_BACKUP_S3_PREFIX:-postgres}"

if [[ ! "${retention_days}" =~ ^[0-9]+$ ]]; then
  printf 'JOBRADAR_BACKUP_RETENTION_DAYS must be a non-negative integer.\n' >&2
  exit 1
fi

required_commands=(docker flock)
if [[ -n "${s3_bucket}" ]]; then
  required_commands+=(aws stat)
fi

for required_command in "${required_commands[@]}"; do
  if ! command -v "${required_command}" >/dev/null 2>&1; then
    printf 'Required command is not available: %s\n' "${required_command}" >&2
    exit 1
  fi
done

mkdir -p "${backup_dir}"

exec 9>"${lock_file}"
if ! flock -n 9; then
  printf 'Another PostgreSQL backup is already running.\n'
  exit 0
fi

cd "${project_dir}"

if ! docker compose ps --status running --services | grep -Fxq 'db'; then
  printf 'The PostgreSQL container is not running.\n' >&2
  exit 1
fi

timestamp="$(date -u '+%Y%m%dT%H%M%SZ')"
final_path="${backup_dir}/jobradar-${timestamp}.dump"
temporary_path="${final_path}.partial"

cleanup() {
  rm -f "${temporary_path}"
}
trap cleanup EXIT

docker compose exec -T db pg_dump \
  --username=jobradar \
  --dbname=jobradar \
  --format=custom \
  --compress=6 \
  >"${temporary_path}"

if [[ ! -s "${temporary_path}" ]]; then
  printf 'PostgreSQL backup is empty.\n' >&2
  exit 1
fi

docker compose exec -T db pg_restore --list <"${temporary_path}" >/dev/null
mv "${temporary_path}" "${final_path}"
trap - EXIT

if [[ -n "${s3_bucket}" ]]; then
  normalized_s3_prefix="${s3_prefix#/}"
  normalized_s3_prefix="${normalized_s3_prefix%/}"
  s3_key="$(basename "${final_path}")"
  if [[ -n "${normalized_s3_prefix}" ]]; then
    s3_key="${normalized_s3_prefix}/${s3_key}"
  fi

  aws s3 cp \
    --only-show-errors \
    "${final_path}" \
    "s3://${s3_bucket}/${s3_key}"

  local_size="$(stat --format='%s' "${final_path}")"
  remote_size="$(
    aws s3api head-object \
      --bucket "${s3_bucket}" \
      --key "${s3_key}" \
      --query 'ContentLength' \
      --output text
  )"

  if [[ "${remote_size}" != "${local_size}" ]]; then
    printf 'S3 backup size mismatch: local=%s remote=%s.\n' \
      "${local_size}" \
      "${remote_size}" >&2
    exit 1
  fi

  printf 'PostgreSQL backup uploaded: s3://%s/%s\n' \
    "${s3_bucket}" \
    "${s3_key}"
fi

find "${backup_dir}" \
  -type f \
  -name 'jobradar-*.dump' \
  -mtime "+${retention_days}" \
  -delete

printf 'PostgreSQL backup created: %s\n' "${final_path}"
