import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from jobradar.sources.base import CachedListing


def discovery_fingerprint(payload: dict[str, Any]) -> str:
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def can_reuse_detail(
    cached: CachedListing | None,
    *,
    fingerprint: str,
    cached_fingerprint: str | None,
    required_fields: tuple[str, ...],
    ttl_seconds: int,
    now: datetime,
) -> bool:
    if cached is None or cached.detail_fetched_at is None:
        return False
    fetched_at = cached.detail_fetched_at
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=UTC)
    if now > fetched_at.astimezone(UTC) + timedelta(seconds=ttl_seconds):
        return False
    if cached_fingerprint != fingerprint:
        return False
    return all(cached.payload.get(field) not in (None, "") for field in required_fields)


async def polite_delay(seconds: float) -> None:
    if seconds > 0:
        await asyncio.sleep(seconds)


async def get_with_backoff(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    attempts: int = 2,
    maximum_delay_seconds: float = 30.0,
) -> httpx.Response:
    for attempt in range(attempts):
        response = await client.get(url, headers=headers)
        if response.status_code != httpx.codes.TOO_MANY_REQUESTS or attempt + 1 >= attempts:
            return response
        retry_after = response.headers.get("Retry-After")
        try:
            delay = float(retry_after) if retry_after is not None else float(2**attempt)
        except ValueError:
            delay = float(2**attempt)
        if delay > maximum_delay_seconds:
            return response
        await asyncio.sleep(max(delay, 0))
    raise RuntimeError("Unreachable rate-limit retry state.")
