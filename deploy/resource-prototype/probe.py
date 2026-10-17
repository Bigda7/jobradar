"""Synthetic container workloads; no provider collection or Telegram transport."""

import asyncio
import errno
import json
import os
import signal
import time
from pathlib import Path

from sqlalchemy import text

from jobradar.db.session import engine, session_factory
from jobradar.ingestion.service import IngestionService
from jobradar.matching.profile import BOHDAN_PROFILE
from jobradar.matching.service import MatchingService
from jobradar.notifications.messages import TelegramMessageRegistry
from jobradar.notifications.preferences import NotificationPreferenceService
from jobradar.notifications.service import NotificationService
from jobradar.notifications.telegram import TelegramClient
from jobradar.opportunities.service import OpportunityStateService
from jobradar.sources.mock import MockSource


def metrics(stage):
    values = {}
    for name in (
        "memory.current",
        "memory.peak",
        "memory.max",
        "memory.events",
        "pids.current",
        "pids.max",
        "cpu.max",
        "cpu.stat",
    ):
        values[name] = Path("/sys/fs/cgroup", name).read_text().strip()
    print(json.dumps({"stage": stage, "metrics": values}), flush=True)


class FixtureSource(MockSource):
    name = "djinni"
    display_name = "Synthetic Djinni"

    async def fetch(self):
        hold = min(20, max(0, int(os.environ.get("RESOURCE_CACHE_HOLD_SECONDS", "0"))))
        if hold:
            metrics("worker_cache_loaded")
            await asyncio.sleep(hold)
        async for item in super().fetch():
            yield item


class RecordingClient(TelegramClient):
    def __init__(self):
        self.messages = []

    async def send_message(self, text, reply_markup=None, chat_id=None):
        self.messages.append(text)
        return 800000 + len(self.messages)


async def worker():
    # Refresh an existing large source cache, then exercise matching separately.
    async with session_factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT l.external_id, l.source_url, o.title, o.company, o.description "
                    "FROM listings l JOIN opportunities o ON o.id=l.opportunity_id "
                    "WHERE l.source_id=(SELECT id FROM sources WHERE name='djinni') "
                    "AND l.is_active ORDER BY l.id"
                )
            )
        ).all()
    values = [
        {
            "id": row[0],
            "url": row[1],
            "title": row[2],
            "company": row[3],
            "description": row[4],
            "location": "Remote Worldwide",
            "work_mode": "remote",
            "employment_type": "full_time",
            "published_at": None,
        }
        for row in rows
    ]
    summary = await IngestionService(session_factory).run_source(FixtureSource(values))
    assert summary.discovered == len(values)
    metrics("worker_cache_refresh")
    evaluation = await MatchingService(session_factory).evaluate(BOHDAN_PROFILE)
    print(
        json.dumps(
            {
                "stage": "worker_completed",
                "records": len(values),
                "evaluations": evaluation.evaluated,
                "unchanged": evaluation.unchanged,
            }
        ),
        flush=True,
    )
    metrics("worker_matching")
    assert evaluation.evaluated + evaluation.unchanged == len(values)


async def cache_only():
    async with session_factory() as session:
        source_id = await session.scalar(text("SELECT id FROM sources WHERE name='djinni'"))
    cache = await IngestionService(session_factory)._load_listing_cache(source_id)
    assert len(cache) == 14000
    assert all(len(item.payload["description"]) == 7983 for item in cache.values())
    metrics("cache_only")


async def resume_worker():
    async with session_factory() as session:
        active = await session.scalar(text("SELECT count(*) FROM listings WHERE is_active"))
    assert active == 10000
    evaluation = await MatchingService(session_factory).evaluate(BOHDAN_PROFILE)
    assert evaluation.evaluated + evaluation.unchanged == active
    print(
        json.dumps(
            {
                "stage": "matching_resumed",
                "evaluated": evaluation.evaluated,
                "unchanged": evaluation.unchanged,
            }
        ),
        flush=True,
    )
    metrics("worker_matching_resumed")


async def bot():
    preferences = NotificationPreferenceService(session_factory)
    states = OpportunityStateService(session_factory)
    messages = TelegramMessageRegistry(session_factory)
    async with session_factory() as session:
        identifier = await session.scalar(text("SELECT min(id) FROM opportunities"))
    for _ in range(10):
        await preferences.set_paused("resource-profile", "telegram", True)
        await preferences.set_paused("resource-profile", "telegram", False)
        await states.toggle_favorite(identifier)
        await states.hide(identifier)
        await states.restore(identifier)
        await states.stats(BOHDAN_PROFILE, 55)
        await messages.record(identifier, 900000)
        await messages.mark_deleted([900000])
    metrics("bot_commands")


async def crash_worker():
    async with session_factory() as session, session.begin():
        marker = await session.scalar(
            text(
                "SELECT error_message FROM source_runs WHERE source_id="
                "(SELECT id FROM sources WHERE name='resource-fixture') ORDER BY id DESC LIMIT 1"
            )
        )
        if marker == "synthetic-oom-armed":
            await session.execute(
                text(
                    "UPDATE source_runs SET error_message='synthetic-oom-injected', "
                    "status='failed' "
                    "WHERE source_id=(SELECT id FROM sources WHERE name='resource-fixture')"
                )
            )
    if marker == "synthetic-oom-armed":
        metrics("worker_before_oom")
        # Bounded by the owned container's hard cgroup limit, never the host.
        blocks = []
        while True:
            blocks.append(bytearray(16 * 1024 * 1024))
    client = RecordingClient()
    service = NotificationService(session_factory, client)
    first = await service.retry_due("resource-profile", 10)
    assert first.sent == 1
    async with session_factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT event_key, status, attempts, next_attempt_at IS NOT NULL "
                    "FROM notification_deliveries WHERE profile_id='resource-profile' "
                    "ORDER BY event_key"
                )
            )
        ).all()
    assert {row[0]: row[1] for row in rows} == {
        "already-sent": "sent",
        "linked-pending": "sent",
        "unconfirmed-pending": "failed",
        "queued": "sent",
    }
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "UPDATE notification_deliveries SET next_attempt_at=now()-interval '1 second' "
                "WHERE profile_id='resource-profile' AND event_key='unconfirmed-pending'"
            )
        )
    assert (await service.retry_due("resource-profile", 10)).sent == 1
    assert (await service.retry_due("resource-profile", 10)).sent == 0
    assert len(client.messages) == 2
    print(
        json.dumps(
            {
                "stage": "worker_recovered_after_oom",
                "stub_sends": len(client.messages),
                "preserved_already_sent": True,
            }
        ),
        flush=True,
    )
    metrics("worker_after_oom")


def pid_boundary():
    children = []
    denied = False
    try:
        for _ in range(80):
            try:
                child = os.fork()
            except OSError as error:
                if error.errno != errno.EAGAIN:
                    raise
                denied = True
                break
            if child == 0:
                time.sleep(8)
                os._exit(0)
            children.append(child)
        assert denied
        print(json.dumps({"stage": "pid_limit_enforced", "children": len(children)}), flush=True)
        metrics("pid_pressure")
    finally:
        for child in children:
            try:
                os.kill(child, signal.SIGTERM)
            except ProcessLookupError:
                pass
        for child in children:
            os.waitpid(child, 0)


async def main():
    mode = os.environ["RESOURCE_PROBE_MODE"]
    try:
        if mode == "worker":
            await worker()
        elif mode == "cache-only":
            await cache_only()
        elif mode == "resume-worker":
            await resume_worker()
        elif mode == "bot":
            await bot()
        elif mode == "crash-worker":
            await crash_worker()
        elif mode == "pid":
            pid_boundary()
        else:
            raise ValueError("Unsupported synthetic probe mode")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
