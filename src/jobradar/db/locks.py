from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


@asynccontextmanager
async def try_transaction_advisory_lock(
    engine: AsyncEngine,
    lock_key: int,
) -> AsyncIterator[bool]:
    if engine.dialect.name != "postgresql":
        yield True
        return

    async with engine.connect() as connection, connection.begin():
        acquired = bool(
            await connection.scalar(
                text("SELECT pg_try_advisory_xact_lock(:lock_key)"),
                {"lock_key": lock_key},
            )
        )
        yield acquired
