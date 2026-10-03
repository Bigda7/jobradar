# Server Disk Maintenance Plan

Reviewed on 2026-10-03. This plan and fixes are local; production deletion, script replacement,
timer installation, image publication, and disk expansion have not been authorized or performed.

## Read-Only Server Snapshot

- Root EBS device: 25 GiB, ext4 root partition about 24 GiB; no significant unused partition
  headroom was identified. Filesystem: 24,883,167,232 bytes total, 19,536,232,448 used,
  5,330,157,568 available (79% used, about 5.0 GiB free).
- Physical containerd storage: 9,800,867,840 bytes; Docker volumes: 2,442,182,656 bytes.
- Docker accounting reports 192 build-cache records, 5.963 GB total. Of these, 126 unused,
  unshared records last used more than seven days ago total 3,981,093,840 bytes (3.7 GiB).
- Seven unreferenced anonymous volumes total 1,844,855,592 bytes according to individual usage
  records (approximately 1.7 GiB). Their creation timestamps align with restore checks.
- The active `jobradar_postgres_data` volume has one container reference and about 595 MB
  of data. It is never a cleanup target.
- Preserved backup dumps use about 1.05 GiB; deployment/rollback archives about 53 MiB.
  Host logs use about 106 MiB; container log storage about 5 MiB. Log cleanup is not a priority.
- The backup timer is active. Its next run is 2026-10-04 03:15 UTC; the latest service run
  at 2026-10-03 12:10 UTC completed successfully.
- Docker 29.1.3 uses containerd image storage. The server has no Buildx plugin; maintenance
  uses the available built-in `docker builder prune` command, not an added dependency.

Docker image and build-cache totals share layers and must not be added as independent physical
disk usage. Reclaimable bytes are estimates, not guaranteed savings. Do not remove containerd
directories or snapshot files manually.

## Verified Restore-Volume Leak

The running server's backup verifier matches the reviewed repository script. It starts an
isolated PostgreSQL container with `--rm`, then explicitly executes `docker rm --force`
without `--volumes`. A local Docker experiment reproduced a surviving anonymous volume after
that exact forced removal; repeating with `--volumes` removed the isolated test volume.
Both empty local test containers/volumes were cleaned up; no production object was deleted.
The local CI workflow now checks shell syntax and compares Docker volume inventory before
and after both successful and rejected corrupt-backup restore checks. A leak fails CI.
This remote CI change has not run yet; publication is still pending approval.

The local fix adds `--volumes` only to removal of the uniquely named
`jobradar-restore-check-*` temporary container. It does not enumerate or remove unrelated
volumes, and the production database/container name is not passed to this cleanup.
Deploying the fixed script should prevent this restore-check leak; it does not remove the
seven old volumes already left on the server. No fresh production restore was forced here.

## Prepared Automatic Cache Maintenance

`scripts/cleanup_build_cache.sh` defaults to a read-only dry run. The dry run and Bash syntax
were checked against the real server without storing or installing the script there.
`--apply` explicitly opts into cleanup of unused build cache older than 168 hours, retaining
2 GB of cache. The command fixes the local Unix Docker endpoint, skips while a backup is
running, and does nothing when usage is below 75% and at least 6 GiB is free. Referenced layers
remain protected by Docker. Recently used/in-use cache can exceed the retention target.

The prepared systemd service/timer proposes a daily capacity check at 04:00 UTC, after the
existing 03:15 backup schedule. The units are not installed or enabled, and their scheduled
execution has not been verified. Images, containers, volumes, backups, secret configuration,
deployment archives, and the current/rollback releases are not automatic deletion targets.

## Proposed Approval Package

Recommend safe retention before paid disk expansion:

1. Replace only `/opt/jobradar/scripts/verify_postgres_backup.sh` with the reviewed volume-cleanup
   fix, preserving ownership/mode and a rollback copy. No application restart or DB migration.
2. Execute one approved cache-only cleanup with the reviewed age/storage guards. The current
   unshared old cache estimate is 3.7 GiB; recheck actual reclaimed bytes afterward.
3. Install the reviewed cleanup script and systemd service/timer, validate the units, then enable
   the daily check. This needs explicit approval because subsequent runs delete cache entries.
4. Separately revalidate these seven exact anonymous volume IDs: zero container references,
   expected temporary PostgreSQL layout/provenance, and no reference to the live database.
   Remove only the approved IDs; do not use volume/system/image pruning. Their data removal
   is not recoverable as those same volumes; the retained verified dump remains the recovery
   source. Approval must name this historical-volume cleanup explicitly.

```text
46fa0b272bd45acbffbbc0eb1cbcd3a8942424f230a7ddab9a571d30e076b459
48a095ec889d21eced1812fc3aa635a45b51f38c681540d2210a32efcffe7230
989d3dc464531e845fd8ac5ce709b37bf89177c6498be702845f7e3c65ce9c54
82224d7f4eb1daeaf9074eabd59fc68350826b3ade19287ead1d2196cb898e0f
8801057607fc0bce33a3aeb084784c2bf69774179ae0afc3cc4faf915394b027
ac504953f2c8b8ea8e83545985d2bd31ca977648b819aef31cc0a430a1afa40f
f80f713495cefc0f068bf72ffd09d17b33bc015c94d6b0f34bd01ca4d33be2d1
```

Keep the running registry image, `jobradar-app:1.2.11`, PostgreSQL image, live volume,
`/opt/jobradar/.deploy/pre-registry-v1.2.12-20261003`, backup dumps, S3 retention policy, and
all secret/deployment configuration unchanged. Older release-image removal, archive cleanup,
backup-policy changes, and EBS expansion are outside this package and need separate decisions.
