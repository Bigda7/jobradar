from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobradar.notifications.scan_cursor import NotificationScanCursorService


@pytest.mark.asyncio
async def test_scan_cursor_persists_failed_cycle_and_advances_after_completion(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    first_start = datetime.now(UTC)
    second_start = first_start + timedelta(minutes=5)
    service = NotificationScanCursorService(sqlite_session_factory)

    assert await service.begin_cycle("bohdan", "telegram", first_start) == first_start
    assert (
        await NotificationScanCursorService(sqlite_session_factory).begin_cycle(
            "bohdan", "telegram", second_start
        )
        == first_start
    )

    await service.complete_cycle("bohdan", "telegram", second_start)

    assert await service.begin_cycle("bohdan", "telegram", second_start) == second_start
    assert await service.begin_cycle("other", "telegram", second_start) == second_start
