from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobradar.db.models import Source
from jobradar.ingestion.request_budget import DatabaseRequestBudget
from jobradar.sources.djinni import DjinniSource, DjinniSourceError

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_rss_retry_commits_each_attempt_and_a_new_adapter_cannot_reset_budget(
    postgres_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC).timestamp()
    async with postgres_session_factory() as session, session.begin():
        source_row = Source(
            name="djinni",
            display_name="Djinni",
            request_budget={"rss": [now - 5] * 98},
        )
        session.add(source_row)
        await session.flush()
        source_id = source_row.id
    calls = []

    async def no_wait(self, seconds):  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr(DjinniSource, "_wait_for_rss_retry", no_wait)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url)
        return (
            httpx.Response(502)
            if len(calls) == 1
            else httpx.Response(
                200,
                text="<rss><channel><item><title>Python Developer</title>"
                "<link>https://djinni.co/jobs/123-python/</link>"
                "<description>Build Python APIs remotely.</description></item></channel></rss>",
            )
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = DjinniSource(client=client)
        source.configure_request_budget(DatabaseRequestBudget(postgres_session_factory, source_id))
        rows = [row async for row in source.fetch()]
        assert len(rows) == 1 and rows[0].external_id == "123"
        assert source.consume_run_metrics().page_count == 2 and source.consume_warnings() == ()
        replacement = DjinniSource(client=client, run_timeout_seconds=0.02)
        replacement.configure_request_budget(
            DatabaseRequestBudget(postgres_session_factory, source_id)
        )
        with pytest.raises(DjinniSourceError, match="timed out"):
            await anext(replacement.fetch())
    assert len(calls) == 2
    async with postgres_session_factory() as session:
        stored = await session.get(Source, source_id)
        assert stored is not None and len(stored.request_budget["rss"]) == 100
        assert all(stamp >= now for stamp in stored.request_budget["rss"][-2:])
