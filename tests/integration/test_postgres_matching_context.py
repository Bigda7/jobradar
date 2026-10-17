import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.unit.test_api import (
    test_matches_exploration_range_filters_before_count_and_pagination as _verify_range,
)
from tests.unit.test_telegram import (
    test_context_rules_recalculation_does_not_resend_historical_matches as _verify_sent,
)
from tests.unit.test_telegram import (
    test_profile_upgrade_preserves_retries_without_creating_historical_backlog as _verify_retry,
)

from jobradar.domain.enums import DeliveryStatus

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(("score", "expected"), ((34, 0), (35, 1), (54, 1), (55, 0)))
async def test_postgres_explore_range(
    postgres_session_factory: async_sessionmaker[AsyncSession],
    score: int,
    expected: int,
) -> None:
    await _verify_range(postgres_session_factory, score, expected)


@pytest.mark.parametrize(
    "status", (DeliveryStatus.SENT, DeliveryStatus.QUEUED, DeliveryStatus.FAILED)
)
async def test_postgres_profile_upgrade_retry(
    postgres_session_factory: async_sessionmaker[AsyncSession],
    status: DeliveryStatus,
) -> None:
    await _verify_retry(postgres_session_factory, status)


async def test_postgres_profile_upgrade_sent(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _verify_sent(postgres_session_factory)
