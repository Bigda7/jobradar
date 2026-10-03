#!/usr/bin/env bash

set -Eeuo pipefail

if [[ "$#" -gt 1 || ( "$#" -eq 1 && "$1" != '--apply' ) ]]; then
  printf 'Usage: %s [--apply]\n' "$0" >&2
  exit 1
fi

if [[ "$(uname -s)" != Linux || ! -S /var/run/docker.sock ]]; then
  printf 'A local Linux Docker daemon is required.\n' >&2
  exit 1
fi

# Fix the endpoint; this maintenance command must never target a remote context.
docker_local=(docker --host unix:///var/run/docker.sock)
"${docker_local[@]}" system df

if [[ "$#" -eq 0 ]]; then
  printf 'Dry run: no data was deleted.\n'
  printf 'Apply mode prunes unused build cache older than 168h, retaining 2GB of cache.\n'
  printf 'Images, containers, volumes, backups, and deployment archives are not cleanup targets.\n'
  exit 0
fi

if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet jobradar-backup.service; then
  printf 'A backup is running; cache cleanup is deferred.\n'
  exit 0
fi

used_percent="$(df --output=pcent / | tail -n 1 | tr -dc '0-9')"
available_bytes="$(df --output=avail -B1 / | tail -n 1 | tr -d '[:space:]')"
if [[ ! "${used_percent}" =~ ^[0-9]+$ || ! "${available_bytes}" =~ ^[0-9]+$ ]]; then
  printf 'Could not determine filesystem capacity; nothing was deleted.\n' >&2
  exit 1
fi
if (( used_percent < 75 && available_bytes >= 6442450944 )); then
  printf 'Filesystem has sufficient headroom; cache cleanup is skipped.\n'
  exit 0
fi

# Docker protects referenced layers; only expendable build-cache entries are targeted.
"${docker_local[@]}" builder prune --all --filter until=168h --keep-storage 2GB --force
df -P -B1 /
"${docker_local[@]}" system df
