import asyncio
import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from jobradar.db.models import Listing, NotificationDelivery, Opportunity, Source, SourceRun
from jobradar.ingestion.request_budget import DatabaseRequestBudget

pytestmark = pytest.mark.integration
MIGRATION_PATH = (
    Path(__file__).resolve().parents[2] / "alembic/versions/20261005_0021_request_budgets.py"
)


@pytest.mark.asyncio
async def test_request_budget_migration_seeds_recent_attempts_and_preserves_existing_data(
    postgres_engine: AsyncEngine,
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    async with postgres_session_factory() as session, session.begin():
        source = Source(name="djinni", display_name="Djinni")
        other = Source(name="workua", display_name="Work.ua")
        opportunity = Opportunity(title="Python Developer", canonical_key="unchanged")
        session.add_all([source, other, opportunity])
        await session.flush()
        recent = [(now - timedelta(seconds=1000 - index)).isoformat() for index in range(101)]
        dates = [*recent, "invalid", None, (now - timedelta(hours=2)).isoformat()]
        for index, date in enumerate(dates):
            session.add(
                Listing(
                    source_id=source.id,
                    opportunity_id=opportunity.id,
                    external_id=str(index),
                    source_url=f"https://djinni.co/jobs/{index}/",
                    canonical_url=f"https://djinni.co/jobs/{index}/",
                    content_hash="unchanged",
                    raw_data={"metadata_attempted_at": date},
                )
            )
        run = SourceRun(source_id=source.id, status="succeeded", discovered_count=len(dates))
        delivery = NotificationDelivery(
            opportunity_id=opportunity.id,
            profile_id="test",
            channel="telegram",
            event_key="sent",
            status="sent",
            sent_at=now,
        )
        session.add_all([run, delivery])
        await session.flush()
        source_id, other_id, run_id, delivery_id = source.id, other.id, run.id, delivery.id

    spec = importlib.util.spec_from_file_location("request_budget_migration", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    def migrate(connection):  # type: ignore[no-untyped-def]
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()

    async with postgres_engine.begin() as connection:
        await connection.execute(text("ALTER TABLE sources DROP COLUMN request_budget"))
        await connection.execute(
            text("ALTER TABLE source_runs DROP COLUMN metadata_deferred_count")
        )
        await connection.run_sync(migrate)
    async with postgres_session_factory() as session:
        source = await session.get(Source, source_id)
        other = await session.get(Source, other_id)
        run = await session.get(SourceRun, run_id)
        delivery = await session.get(NotificationDelivery, delivery_id)
        assert source is not None and len(source.request_budget["rss"]) == 100
        assert len(source.request_budget["metadata"]) == 100
        assert source.request_budget["metadata"] == [
            datetime.fromisoformat(stamp).timestamp() for stamp in recent[1:]
        ]
        assert other is not None and other.request_budget == {}
        assert run is not None and run.metadata_deferred_count == 0
        assert run.discovered_count == len(dates)
        assert delivery is not None and delivery.status == "sent" and delivery.sent_at == now
        listings = (await session.scalars(select(Listing).order_by(Listing.id))).all()
        assert [listing.raw_data["metadata_attempted_at"] for listing in listings] == dates
        assert all(listing.content_hash == "unchanged" for listing in listings)
    budget = DatabaseRequestBudget(postgres_session_factory, source_id)
    assert not await budget.reserve("rss")
    assert not await budget.reserve("metadata")
    async with postgres_engine.begin() as connection:
        # Older application inserts omit the new columns during a staged rollout.
        await connection.execute(
            text(
                "INSERT INTO sources (name, display_name, opportunity_kind, enabled, "
                "failure_alert_active, coverage_alert_active, created_at, updated_at) "
                "VALUES ('legacy-insert', 'Legacy', 'employment', true, false, false, now(), now())"
            )
        )
        legacy_state = await connection.scalar(
            text("SELECT request_budget FROM sources WHERE name='legacy-insert'")
        )
        assert legacy_state == {}
        defaults = dict(
            (
                await connection.execute(
                    text(
                        "SELECT column_name, column_default FROM information_schema.columns "
                        "WHERE table_schema='public' AND "
                        "((table_name='sources' AND column_name='request_budget') OR "
                        "(table_name='source_runs' AND column_name='metadata_deferred_count'))"
                    )
                )
            ).all()
        )
        assert defaults["request_budget"] is not None
        assert defaults["metadata_deferred_count"] is not None


@pytest.mark.asyncio
async def test_postgres_row_lock_prevents_concurrent_request_budget_overrun(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC).timestamp()
    async with postgres_session_factory() as session, session.begin():
        source = Source(name="djinni", display_name="Djinni", request_budget={"rss": [now] * 99})
        session.add(source)
        await session.flush()
        source_id = source.id
    budgets = [DatabaseRequestBudget(postgres_session_factory, source_id) for _ in range(8)]
    assert sum(await asyncio.gather(*(budget.reserve("rss") for budget in budgets))) == 1
    assert not await DatabaseRequestBudget(postgres_session_factory, source_id).reserve("rss")
    async with postgres_session_factory() as session:
        source = await session.get(Source, source_id)
        assert source is not None and len(source.request_budget["rss"]) == 100
