# Database roles and service secrets

Local package 5, 2026-10-17. **Not applied to production.** No dependency, image pin,
schema/data model, source schedule, matching, or notification delivery policy changes.

## Policy

| Login | Allowed | Deliberately denied |
| --- | --- | --- |
| `jobradar_api` | SELECT on the six API tables | All writes, sequences, private notification tables, DDL, admin |
| `jobradar_worker` | SELECT on application tables; INSERT/UPDATE except notification preferences; DELETE only opportunities, evaluations, deliveries and user states for existing ingestion/deduplication/expiration paths | Schema changes, migration version writes, TRUNCATE, role/database administration |
| `jobradar_bot` | SELECT on the six API tables, preferences and message registry; INSERT/UPDATE preferences, user states, message registry; message sequence USAGE | Vacancy changes, delivery-state access/changes, schema/admin operations |
| `jobradar_migration` | Owns application tables and attached sequences; CREATE in public schema | Superuser, role/database creation, replication, bypass RLS |
| `jobradar_backup` | SELECT all existing application tables/version/sequences; future migration-owned tables/sequences | Writes, nextval/setval, schema/admin operations |

All five logins have distinct passwords and no role memberships. Database ownership
stays with the existing administrator. PUBLIC loses CONNECT/CREATE/TEMP on this
dedicated DB and USAGE/CREATE on public; managed roles receive explicit CONNECT and
USAGE, only migration receives schema CREATE. The administrator installs pg_trgm.
The legacy administrator is preserved for recovery, not injected into application
containers. Existing system/extension objects are not reassigned with REASSIGN OWNED.

API and backup default to read-only transactions, but tests turn that setting off
and confirm that ACLs still reject writes. Runtime roles do not own objects. No
runtime role can SET ROLE to migration/admin. PostgreSQL's built-in catalog and
function visibility is not an OS/container/network sandbox.

New migration-owned tables/sequences default to backup SELECT only. New functions
lose PUBLIC EXECUTE and enum/domain types lose PUBLIC USAGE. Runtime access is
fail-closed: review new objects and update the explicit policy with each schema
change. Current models use strings, not PostgreSQL enums; a synthetic future enum
tests the default boundary. Custom public functions/enums/domains, unexpected
relations and unmanaged/elevated/member roles cause provisioning to refuse the DB
instead of silently adopting it. This provisioning tool is for this dedicated
database, not an arbitrary shared cluster.
Column-level ACLs and unattached public sequences also require explicit review;
table-level REVOKE alone would not remove a preexisting column grant.

## Service environment

Compose no longer injects the entire `.env`. Explicit environment allowlists use
Compose interpolation from the operator's existing configuration:

- API receives only API/auth/pool configuration and `API_DATABASE_URL` as DATABASE_URL.
  It receives no Telegram token, migration URL or admin password.
- Worker receives its own URL, collector/matching/pool/delivery settings and Telegram
  credentials. Bot receives its own URL and bot/delivery/pool settings. Neither gets
  the API bearer token, migration URL or administrator password.
- The maintenance-profile `migrate` service gets `MIGRATION_DATABASE_URL` and no
  notification/API credentials. It is never an automatic API startup dependency.
- DB receives its existing initialization administrator password only. Backup
  credentials belong to the backup runner, not API/worker/bot/db environment.

`SERVICE_ROLE` defaults to api for backward-compatible host use. Production worker,
bot and migration Settings no longer require irrelevant API bearer/hostname secrets;
the HTTP app refuses to start under any non-api role. API security validation stays
strict. Enabled Telegram still requires a token and nonzero chat ID. Compose keeps
explicitly empty URL-list overrides (notably optional Djinni feeds) rather than
replacing them with defaults. `.env` remains excluded from the runtime image.
Compose also forces `DB_ENFORCE_SERVICE_ROLE=true`: Settings refuses administrator
or another service's username in DATABASE_URL before opening a connection. Host
tools retain their existing opt-in compatibility default for this flag.

Host tools still read `.env` through Settings. This is not a host secret manager;
operators must not mount/copy the shared `.env` into application containers, print
resolved `docker compose config`, or enable credential-bearing SQL/driver logging.
An administrator with Docker/root access can still inspect container environments.

## Controlled preparation and rollout (requires separate live approval)

This is an operator runbook, **not authorization to execute it on production**.

1. Retain the prior application image/Compose/configuration and administrator access.
   Create and restore-verify a fresh backup before any live change. At transition,
   the old backup runner still works with its existing administrator; do not replace
   its configuration before the new backup login is verified.
2. Generate five independent printable ASCII passwords, at least 32 characters each,
   using a secure generator. Load them as `DB_MIGRATION_PASSWORD`, `DB_API_PASSWORD`,
   `DB_WORKER_PASSWORD`, `DB_BOT_PASSWORD`, `DB_BACKUP_PASSWORD` in a private operator
   environment. `DB_ADMIN_URL` is a native `postgresql://` administrator URL. Do not
   put credentials in shell history/CLI arguments or source control. URL-encode
   passwords in URLs; raw passwords belong in the password variables.
3. In an approved maintenance window, stop API/worker/bot to avoid in-flight DDL and
   permission transitions. Run the new application module in a controlled operator
   environment (it is not an application startup hook):

   ```text
   python -m jobradar.db.access --database jobradar
   ```

   It validates the target, dedicated schema inventory, existing role markers and
   memberships, then performs role changes, ownership, grants and defaults in one
   transaction with bounded lock/statement waits. Failure rolls policy changes back.
   Re-running with the same passwords is supported. Plaintext passwords are not
   interpolated into SQL: it uses salted SCRAM verifiers; these are still sensitive.
4. Configure the four service URLs in the deployment interpolation store; each must
   use the matching login and same existing DB. Keep POSTGRES_PASSWORD for the DB
   initialization/admin recovery path. Do not change/delete the DB volume.
5. Run the separate migration step, then reapply the access policy to grant reviewed
   tables and transfer legacy ownership when necessary:

   ```text
   docker compose --profile maintenance run --rm migrate
   ```

   New databases require provision -> migrate -> provision. Existing databases use
   the same order. No migration runs during ordinary API restarts. The separate
   migration login's default statement timeout is 30 seconds; measure index/DDL time
   on the restored production-size fixture before choosing the live window or a
   reviewed session timeout override. No live duration has been measured here.
6. Configure the existing backup runner's private environment with
   `JOBRADAR_BACKUP_DB_PASSWORD` equal to DB_BACKUP_PASSWORD. The script passes
   PGPASSWORD by environment and uses TCP/password authentication as jobradar_backup;
   it fails closed if the secret is missing. Verify a dump and isolated restore.
   Existing archive verifier uses --no-owner/--no-privileges: on actual recovery,
   provision -> restore as administrator -> provision restores the role boundary.
   A pg_dump is not a backup of role passwords; retain these in the deployment store.
7. Start new API/worker/bot and check protected endpoints, readiness, natural worker
   execution and bot commands. A real notification or forced collection needs its
   own explicit approval. Review logs without printing credentials.

## Rollback

The old administrator remains unchanged and retains access to application objects.
If deployment fails, stop new services, restore the exact prior Compose/image/private
configuration and prior backup runner configuration, and restart against the existing
data. This temporarily restores the previous broad privilege model; it is not the
desired final state. No DROP ROLE, destructive downgrade or data restore is needed
for a configuration-only rollback. Leave managed roles/default ACLs in place until
the issue is understood. If schema/data restoration is necessary, use the verified
pre-change archive in an explicit approved recovery procedure, not an automatic
destructive rollback.

## Verification

Unit checks render Compose with a synthetic explicit env file, never the real .env,
assert per-service secret exclusion, and instantiate every service's Settings.
PostgreSQL integration tests authenticate each role over TCP, exercise API queries,
mock ingestion/matching, stub delivery/idempotency, pause/resume, favorites/hide/
restore, lists/stats and message clearing, plus denied DDL/admin/write attempts.
No real Telegram/provider requests are made.

`deploy/postgres-prototype/check_access.py` uses only labeled loopback Docker fixtures:
fresh migrations as non-superuser; legacy ownership adoption; all PG tests; read-only
backup login; compressed restore into another fixture; reapplication of grants;
sent-record/timestamp/content integrity; corrupt archive rejection; preserved admin
backup fallback. Fixtures are ownership-checked and removed in finally blocks.
CI's existing integration run includes these role tests; real published CI and live
service restart/backup scheduler remain for the separately approved rollout.

PostgreSQL references: [default privileges](https://www.postgresql.org/docs/17/sql-alterdefaultprivileges.html)
and [schema privileges](https://www.postgresql.org/docs/17/ddl-schemas.html).
