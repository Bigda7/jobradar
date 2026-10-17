# New Explore notifications

The worker can include employment matches scoring35..54 alongside the normal main
recommendations. The website's main threshold, scoring rules and manual bot list
commands are unchanged. This is a notification selection change, not a scoring upgrade.

`TELEGRAM_EXPLORE_SINCE` is an opt-in timezone-aware timestamp, normalized to UTC.
An empty/unset value disables the addition. A naive or invalid timestamp is rejected.
Only the worker receives this setting in Compose; the API, bot and migration service
do not receive it. No database migration, new dependency or schema change is required.

When configured, notification selection is the union of:

- The existing main minimum score, with its existing history/cursor and retry rules.
- Explore scores35..54 whose opportunity `first_seen_at` is at or after the activation
  timestamp. They retain eligibility if delayed metadata raises the score after a
  later worker cursor: activation, not the main-range cursor, bounds this new range.

The activation bound applies to Explore even if a separately configured main threshold
is below55. Raising the main threshold does not broaden the main range just because
Explore is enabled. Existing trusted-link, source enablement, active-listing and hidden
record checks remain in place.

An older known vacancy does not become new because its description, score or source
update date changes. The timestamp must remain unchanged across later deployments;
moving it backward intentionally enables older Explore records and is outside the
agreed no-backfill rollout. Do not clear the notification cursor or delivery registry.

Already sent identities are never re-opened by a score/description update. An unseen
post-activation opportunity that only later reaches35 is eligible for its first
notification; a pre-activation opportunity remains excluded even after such updates.

Explore messages include a prominent `Explore: possible match` notice advising the
recipient to review requirements and concerns. Main-message formatting is unchanged.
Both ranges share the existing message ceiling, pause behavior, queue, retries and
deduplication. A retry keeps its stored message. The existing rare Telegram-accepted/
database-save interruption duplicate risk is not eliminated by this change.

## Authorized activation procedure

The owner approved publication/deployment and automatic delivery of ONLY newly seen
Explore after activation, not the accumulated historical list. Publish and verify the
new runtime through the existing attested ARM64 image/source-bundle workflow, prepare
and verify a fresh backup, preserve the same database, roles and resource limits, and
record a UTC activation timestamp immediately before starting the new worker.

Keep `MATCHING_MIN_SCORE=55`, `TELEGRAM_NOTIFY_EXISTING=false`, the existing cursor
and message limit. No forced collection or historical dispatch is part of activation.
Verify the live worker receives the exact cutoff and naturally completes a cycle.
No suitable newly collected Explore record means no Explore message is expected;
do not fabricate a production vacancy just to demonstrate delivery.

For rollback, restore the previous app image and reviewed Compose file. Do not delete
deliveries or alter database history. Existing queued/sent state remains authoritative.
New automatic deliveries after activation are authorized; a separate artificial test
message or historical batch was not requested.

## Local verification

1270 non-integration tests and119 PostgreSQL integration tests passed; the four new
PostgreSQL boundary/dedup cases were also repeated after the final window adjustment.
Focused notification/config/worker/isolation checks passed90 tests. All notification
clients in tests were recording/mocked clients, not the real Telegram transport.
Ruff format/lint, strict mypy, Bandit and frozen offline lock consistency passed.
The isolated PostgreSQL17.11 fixture used the reviewed immutable AMD64 image.

The first local PostgreSQL run omitted the preliminary Alembic upgrade and therefore
four existing permission checks encountered an absent migration table. After applying
the documented complete migration chain, all119 integration tests passed. No application
permission was relaxed to make the checks pass. A package-version consistency failure
during version preparation was corrected before publication by synchronizing the
application, project and locked project metadata at1.2.24.
