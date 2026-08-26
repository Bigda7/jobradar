from collections.abc import AsyncIterator

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from jobradar.config import get_settings


def create_engine(
    database_url: str,
    *,
    pool_size: int = 5,
    max_overflow: int = 5,
    pool_timeout_seconds: float = 10.0,
    pool_recycle_seconds: int = 1800,
    connect_timeout_seconds: int = 10,
    statement_timeout_milliseconds: int = 30000,
) -> AsyncEngine:
    engine_options: dict[str, object] = {"pool_pre_ping": True}
    if make_url(database_url).get_backend_name() == "postgresql":
        engine_options.update(
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_timeout=pool_timeout_seconds,
            pool_recycle=pool_recycle_seconds,
            pool_use_lifo=True,
            connect_args={
                "connect_timeout": connect_timeout_seconds,
                "options": f"-c statement_timeout={statement_timeout_milliseconds}",
            },
        )
    return create_async_engine(database_url, **engine_options)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


settings = get_settings()
engine = create_engine(
    settings.database_url,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_timeout_seconds=settings.db_pool_timeout_seconds,
    pool_recycle_seconds=settings.db_pool_recycle_seconds,
    connect_timeout_seconds=settings.db_connect_timeout_seconds,
    statement_timeout_milliseconds=settings.db_statement_timeout_milliseconds,
)
session_factory = create_session_factory(engine)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session
