"""Request reservations shared across adapters and network stages."""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

REQUEST_WINDOWS = {"rss": (100, 60.0), "metadata": (100, 3600.0)}


def reserve_timestamp(
    state: dict[str, list[float]], category: str, now: float
) -> tuple[dict[str, list[float]], bool]:
    limit, window = REQUEST_WINDOWS[category]
    updated = {
        key: [timestamp for timestamp in state.get(key, []) if timestamp > now - seconds]
        for key, (_, seconds) in REQUEST_WINDOWS.items()
    }
    if len(updated[category]) >= limit:
        return updated, False
    if category == "metadata" and updated[category] and max(updated[category]) > now - 2:
        return updated, False
    updated[category].append(now)
    return updated, True


class RequestBudget(Protocol):
    async def reserve(self, category: str) -> bool: ...


class MemoryRequestBudget:
    """Standalone probes share a budget while their adapter instance is alive."""

    def __init__(self, clock: Callable[[], float] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC).timestamp())
        self._state: dict[str, list[float]] = {}
        self._lock = asyncio.Lock()

    async def reserve(self, category: str) -> bool:
        async with self._lock:
            self._state, allowed = reserve_timestamp(self._state, category, self._clock())
            return allowed
