"""Space all Reader attempts across adapters without extending download deadlines."""

import asyncio
import math
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from time import monotonic

import httpx

from jobradar.http_download import DownloadLimitError

DEFAULT_READER_INTERVAL_SECONDS = 3.2


class ReaderRequestPacer:
    """A worker-local start gate shared by Reader searches, details and retries."""

    def __init__(
        self,
        interval_seconds: float = DEFAULT_READER_INTERVAL_SECONDS,
        *,
        clock: Callable[[], float] = monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if not math.isfinite(interval_seconds) or interval_seconds < 3.1:
            raise ValueError("Reader request spacing must be finite and at least 3.1 seconds.")
        self._interval = interval_seconds
        self._clock = clock
        self._sleep = sleep
        self._next_request_at = -math.inf
        self._lock = asyncio.Lock()

    async def wait(self, deadline: float) -> None:
        async with self._lock:
            while True:
                now = self._clock()
                delay = max(0.0, self._next_request_at - now)
                if now + delay >= deadline:
                    raise DownloadLimitError(
                        "Reader pacing exceeds the remaining request window.", reason="deadline"
                    )
                if delay == 0:
                    self._next_request_at = now + self._interval
                    return
                await self._sleep(delay)

    def observe(self, response: httpx.Response) -> None:
        """Honor a provider cooldown across concurrent and subsequent Reader calls."""
        if response.status_code != 429 and response.status_code < 500:
            return
        value = response.headers.get("Retry-After")
        delay: float | None = None
        if value is not None:
            try:
                delay = float(value)
            except ValueError:
                try:
                    retry_at = parsedate_to_datetime(value)
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=UTC)
                    delay = (retry_at - datetime.now(UTC)).total_seconds()
                except (ValueError, TypeError, OverflowError):
                    pass
        if delay is None or not math.isfinite(delay):
            delay = 60.0 if response.status_code == 429 else 0.0
        self._next_request_at = max(self._next_request_at, self._clock() + max(0.0, delay))
