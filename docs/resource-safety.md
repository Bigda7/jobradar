# Resource safety and maintenance review

Reviewed on 2026-10-17. This package is local only; it does not authorize deployment,
instance resizing, host updates, reboots, cloud policy changes, or real notifications.

Later October17 API follow-up supersedes the locally OPEN stress-latency and split-
benchmark status below: page-scoped batch link ranking passed one fresh uninterrupted
final-policy run, including 10k unchanged/reused, concurrent API/bot/backup and all
failure/recovery groups. Initial 200-item/8-concurrent max5.051s, 50-item6.108s;
parent peak858,406,912 bytes/no parent OOM/no swap. See api-query-performance.md and
workspace API_PERFORMANCE_PACKAGE_2026-10-17.md. Native host/Internet acceptance and
explicit production authorization are still required; nothing was deployed.

## Opt-in candidate, not a production default

The ordinary `compose.yaml` remains without newly enabled resource ceilings.
`compose.resources.yaml` is an opt-in 2 GiB candidate, not an installed default.
The owner explicitly ruled out paid capacity upgrades. The previous 3712 MiB
calculation summed individual ceilings and extra reserves; it was not a measured
minimum product requirement. The new design uses a shared enforced budget.

| Service | RAM ceiling, MiB | CPU ceiling | PID ceiling |
| --- | ---: | ---: | ---: |
| API | 384 | 1 | 64 |
| Worker | 448 | 1 | 64 |
| Bot | 128 | 0.25 | 32 |
| PostgreSQL | 640 | 1 | 128 |
| Offline migration | 256 | 1 | 64 |
| Temporary backup verifier | 256 | 1 | 128 |

Each memory/swap ceiling is equal, so these containers cannot additionally consume
swap. OOM killing remains enabled. CPU ceilings are not reserved CPU shares and
can slow work when multiple services are busy. These limits are containment, not
proof against all denial-of-service or all future workloads.

All services and the verifier must share `jobradar.slice`: 1408 MiB total RAM,
no swap, two CPUs, and 512 tasks. Individual limits are ceilings, not reservations;
their sum intentionally exceeds the aggregate limit. The operator preflight uses
1408 MiB for the group plus 384 MiB for host services, totaling 1792 MiB. The
reviewed host's 1,924,182,016 bytes pass this RAM check, leaving about 427 MiB
outside the group. Its swap is not counted or needed for the local acceptance.

Install neither the Compose override nor the slice automatically. A separately
authorized rollout must install `deploy/systemd/jobradar.slice` and the opt-in
`deploy/systemd/jobradar-backup.service.d/resources.conf` drop-in, reload systemd,
start/verify the slice, and recreate the reviewed containers under it. The backup
drop-in puts the runner/S3 client in the same slice and supplies the container
parent; the verifier rejects a mismatched actual parent. Keep the base backup
service/timer and its schedule unchanged.

The explicit preflight on the actual target, after authorized policy installation:

```sh
python3 scripts/check_resource_capacity.py --check-slice
```

This is a manually invoked fail-closed check, not an automatically installed
deployment hook or recurring monitor. Passing it is necessary, not sufficient:
the actual ARM64 host, systemd scopes, real workload, backups, and recovery behavior
still need acceptance. A memory-only offline check is available with
`--memory-bytes`; it does not confirm an installed resource policy. Missing,
inactive, unlimited, swap-enabled, or incorrectly sized slices fail `--check-slice`.

The reserve does not hard-limit the OS/Docker daemon, image extraction/builds,
other administrative commands, or unrelated programs. Do not build images on the
production host or overlap migrations, verification and host/image maintenance.
Monitor headroom during the approved rollout rather than treating the local
application-group test as a complete emulation of the production operating system.

Only after capacity acceptance and explicit rollout authorization would the
additional Compose file be used. Keep existing deployment image/secret overrides
and the database-role migration procedure; this file changes only resources.
Removing this override removes the ceilings, but does not undo database roles or
an image/schema transition. Never remove a production data volume for rollback.

## Local evidence and reproducible checks

`deploy/resource-prototype/check_2gb.py` enforces a real parent cgroup around local
PostgreSQL/API/worker/bot/verifier fixtures. On the existing Windows setup it uses
WSL's Docker VM for narrowly scoped cgroup control; owners must be exact random
12-character hex IDs. Only its own fixed-prefix group is created/removed, never
recursively. No privileged Docker container or new software is needed. Production
uses a systemd slice instead (the server's existing driver is systemd/cgroup v2).

`deploy/resource-prototype/check_resources.py` supplies shared workload checks and
owned, labeled synthetic fixtures. It requires the existing locked Python
environment, Docker, and Bash (Git Bash on Windows). It does not read production
secrets, run actual providers, or contact Telegram. The local runtime image is
`jobradar-local:memory-20261017`; the PostgreSQL image is
`jobradar-postgres-clean:amd64-20261016`. It does not publish these images.

The fixture holds 14,000 opportunities/listings with 7,983-byte descriptions,
10,000 active listings, separate service logins, and four stored delivery states.
API traffic is published only on loopback; its container uses the default bridge
plus the fixture's internal network because Docker Desktop does not expose ports
from an internal-only bridge. The collector and notification stub use the internal
network. This is not a claim of a complete hostile-network sandbox.

Checks cover concurrent API requests, cached ingestion, matching, bot database
commands, a bounded real worker OOM with one automatic restart, PID exhaustion,
abrupt fixture database/API restarts, data/description preservation, and stored
delivery recovery using a stub. Already-sent or registry-confirmed pending messages
are not sent again; an unconfirmed pending send is retried under existing policy.
Real Telegram exactly-once delivery is not guaranteed by this local stub.

The backup script is exercised against a synthetic compressed dump and corrupt
input. Task fixtures are removed after verifying ownership labels; pre-existing
images, builders, volumes, and user resources must remain untouched.

The memory optimization selects only external ID, raw payload and detail timestamp,
streams 200 rows at a time, and closes the cursor before fetching provider pages.
The required raw cache is preserved in full, including inactive records and nested
metadata; adapters release their cache in a `finally` block even on failure or
cancellation. Rules, request budgets, schedules and notification policy do not change.
On the same 14,000-record fixture, cache loading peaked at 670,175,232 bytes before
and 198,782,976 bytes after optimization. These are whole-process cgroup peaks for
that operation, not an assertion that every workload improves by the same amount.

The fresh cold 10k ingestion/initial-scoring run passed with parent peak903,233,536
bytes, no parent OOM and no swap. A separately repeated backup confirmed its actual
parent during active collection; the first cold orchestration had started before
that assertion was added. The final warm snapshot run passed10k unchanged/reused,
strict backup-parent/corrupt checks and all failure/recovery groups; peak830,595,072
bytes, no parent OOM/swap. API's0.75 CPU ceiling produced a13.427s extreme200-item/
8-concurrent request during parallel checks. It was raised to1 CPU in the warm run,
without increasing aggregate2 CPUs; later max-page checks took5.330/4.122/3.464s,
and the actual UI50-item page test took4.786s. Workload phases differed, so this is
not a controlled CPU-only speed comparison. A subsequent run started with the final
1 CPU ceiling: the50-item UI check passed8.191s, but200-item/8-concurrent stress
during collection/backup reached19.434s, above the proxy10s deadline. This stress
latency is OPEN; CPU refinement alone did not resolve it. API query/serialization
profiling and native API/proxy acceptance remain before rollout. Do not raise the
proxy timeout or call all future response times guaranteed by these checks.

That final-policy heavy run preserved10k unchanged/reused results and had parent
peak828,076,032 bytes/no swap/no parent OOM, but stopped at the narrow Docker OOM
event-window assertion after worker recovery. The event was independently found
by owned ID in a wider interval; do not report the stopped run as fully passed.
Event bounds now use and record the daemon's own clock, rather than mixing Windows
wall time with daemon events. Final-policy recovery-only acceptance passed OOM/
one automatic restart/stub delivery,PID64,DB/API crash/data preservation from the
checked synthetic archive, without another crawl or score calculation. Parent
peak731,967,488 bytes/no parent OOM/no swap. Keep failed evidence and distinguish
these separate groups from one uninterrupted successful final-policy benchmark.

The memory harness also supports a hash-checked snapshot of its own synthetic
fixture for a second warm/unchanged run; arbitrary or production dumps are refused.
Cold rescoring and warm reuse are reported separately, not conflated. Backup cleanup
verification checks the exact generated anonymous volume, not all Docker volumes:
unrelated concurrent tests must not be mistaken for leaked restore data. Container
creation failure must not delete a pre-existing same-name container.

An interrupted fixture can be continued with `--resume` and its exact 12-character
owner ID. This requires matching container/volume/network labels, the same runtime
image and the expected source checkpoint/counts; it never adopts arbitrary user
containers or a production database. Completed ingestion is retained, and matching
reuses persisted evaluations. Report a resumed run as resumed, not as an uninterrupted
fresh benchmark. RAM high-water readings from before a host reboot are separate
evidence because cgroup counters reset when containers are restarted.

`--recovery-only` runs failure/restart groups on a new 14,000-record fixture without
repeating ingestion or scoring; it is not a substitute for the heavy phase. Docker
can reassign an ephemeral published API port after a restart. The runner rereads
the owned container's loopback mapping before readiness/load checks. A stale-port
failure in the original harness was reproduced independently, fixed, and the
recovery group then passed. Those October17 package6 results were split/resumed;
the later 2 GiB optimization checks are recorded separately in the memory report.

Resumed scoring of 2,916 previously unevaluated long-description fixtures used
about 256 CPU-seconds at the one-core ceiling; 7,084 persisted evaluations were
reused. This is not a throughput SLA or a measured full production rescore. A
fresh 10,000-record worst-description rescore can take many minutes, so the test
worker has a bounded 30-minute deadline, separate from source HTTP deadlines.
Account for slower initial rescoring during rollout; do not mistake ordinary
incremental reuse for the cost of a complete rules-version recalculation.

The load fixture exposed a real recovery bug: with autoflush disabled, the
canonical-listing query could miss a freshly reopened listing or one whose old
normalized snapshot was SQL NULL. The explicit flush now makes those changes
visible within the same transaction before querying; no matching rules, content
hash semantics, schedules, or notification policy change. SQLite and PostgreSQL
regressions cover both cases.

## Production maintenance proposal, not executed

Read-only inspection found no current restart/OOM evidence, but live containers
have no memory/CPU/PID ceilings. The installed newer AWS kernel is not running and
the OS requests a reboot. Package candidates came from cached apt metadata, not
from a refreshed repository snapshot.

Prepare one separately authorized window:

1. Recheck health, disk/RAM, source-run state, current kernel, package candidates,
   backup timer results and available rollback artifacts. Refresh package metadata
   only once the maintenance action itself is authorized.
2. Create and verify a fresh backup, including isolated restore and S3 copy. This
   is a new production action and is not covered by the read-only audit. Preserve
   administrative rollback access while switching database roles.
3. Retain the existing 2 GiB instance; paid resizing is explicitly out of scope.
   Verify the installed shared resource policy and actual host headroom. Take a
   recoverable volume snapshot only if separately approved. Do not add paid
   CloudWatch features, S3 Object Lock, replication or WAF products.
4. Apply only the reviewed host package transaction, then perform a controlled
   reboot. This interrupts the host and needs explicit authorization. Keep the
   prior kernel and exact application/DB images; no image or system prune.
5. Verify SSH, kernel, systemd, database readiness, API/proxy, ordinary worker/bot
   recovery, backups, disk/RAM and restart counts. Do not force a source collection
   or send a notification without that authority. Stop rollout on failed gates.
6. Apply separately approved product/image/role/resource changes only with their
   own rollback steps. For host rollback, prefer the known-good kernel where
   appropriate; disk-snapshot restoration is a last resort and can lose data
   written since the snapshot. Do not describe reboot as universally reversible.

No OS updates, reboots, resizing, cloud edits, backup generation, production data
changes, real sends, or scheduled checks were performed in this package.

## Cloud review and boundaries

AWS console review was scoped to the existing instance, attached security group,
backup instance role and configured backup bucket. It confirmed public HTTP/HTTPS,
SSH restricted to one IPv4 /32, no public database/API rule, required IMDSv2 with
hop limit 1, termination protection, and a backup role restricted to its bucket
and `postgres/` object prefix without delete permission. No role was broadened
to make the audit easier.

The bucket blocks public access, disables ACLs, has versioning and SSE-S3 default
encryption. Enabled lifecycle rules expire current backups after 90 days,
noncurrent versions after 7 days, incomplete uploads after 1 day, and expired
delete markers within `postgres/`. Versioning is not immutable/WORM protection:
Object Lock and MFA Delete are not enabled; account administrators remain trusted.
No cross-region replication or S3 access logging was enabled by this work.

Vercel Hobby review confirmed an active fixed-window rule for paths starting
`/api/`: 60 requests/60 seconds per IP, returning 429. System DDoS mitigations are
active; bot protection is off and paid OWASP rules are not enabled. These are not
automatically vulnerabilities or authorization to enable paid features. Shared-IP
users can share this limit; it does not guarantee origin protection outside the
Vercel path. Production stays deliberately public.

Vercel Authentication is enabled with Standard Protection; protected sourcemaps,
build-log/source protection and Git-fork protection are enabled. No bypass secrets
were copied. Functions use Fluid Compute, standard 1 vCPU/2 GB and region `iad1`,
while the backend is in Frankfurt. Aligning regions is an optional latency change,
not a change made here. Node major 24 is selected and allowed by package engines;
CI's Node 22 remains a supported separate baseline. The exact deployed Node patch
and an explicit numeric function duration were not verified.

Authenticated AWS/Vercel tabs were retained for subsequent work using browser
handoff, without exporting cookies, passwords, tokens or browser profiles. The
providers still control session expiry; retained tabs do not promise permanent
authentication.

This is a scoped point-in-time configuration review, not an exhaustive AWS account
audit, penetration test, historical resource trace, native ARM64 acceptance, fresh
production restore, or image vulnerability rescan.
