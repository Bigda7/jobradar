import asyncio
import os
from urllib.parse import urlsplit

from sqlalchemy import text

from jobradar.db.session import create_engine


async def main() -> None:
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        raise RuntimeError("TEST_DATABASE_URL is required.")
    database_name = urlsplit(database_url.replace("postgresql+psycopg", "postgresql")).path.lstrip(
        "/"
    )
    if database_name != "jobradar_test":
        raise RuntimeError("Refusing to reset a database other than jobradar_test.")

    engine = create_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("DROP SCHEMA public CASCADE"))
            await connection.execute(text("CREATE SCHEMA public"))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
