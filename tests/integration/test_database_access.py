import asyncio
import os
from collections.abc import AsyncIterator

import httpx
import psycopg
import pytest
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine
from tests.unit.test_bot import FixedExchangeRateProvider, RecordingBotClient

from jobradar.api.app import create_app
from jobradar.bot import TelegramBotService
from jobradar.config import Settings
from jobradar.db.access import ROLES, provision_access
from jobradar.db.models import NotificationDelivery, Opportunity
from jobradar.db.session import create_engine, create_session_factory
from jobradar.ingestion.service import IngestionService
from jobradar.matching.profile import BOHDAN_PROFILE
from jobradar.matching.service import MatchingService
from jobradar.notifications.messages import TelegramMessageRegistry
from jobradar.notifications.preferences import NotificationPreferenceService
from jobradar.notifications.service import NotificationService
from jobradar.opportunities.service import OpportunityStateService
from jobradar.sources.mock import MockSource

pytestmark = pytest.mark.integration
PASSWORDS = {role: f"synthetic-isolated-{role}-{'x' * 32}-p@ss%/word:#?$" for role in ROLES}


@pytest.fixture
async def restricted_engines(postgres_engine: AsyncEngine) -> AsyncIterator[dict[str, AsyncEngine]]:
    url = make_url(os.environ["TEST_DATABASE_URL"])
    if url.database != "jobradar_test" or url.host not in {"127.0.0.1", "localhost", "db-test"}:
        pytest.fail("Role tests require the isolated jobradar_test database.")

    def provision() -> None:
        with psycopg.connect(
            url.set(drivername="postgresql").render_as_string(hide_password=False)
        ) as c:
            provision_access(c, "jobradar_test", PASSWORDS)
            # A rerun must remove stale global and schema-local default grants too.
            c.execute(
                "ALTER DEFAULT PRIVILEGES FOR ROLE jobradar_migration IN SCHEMA public "
                "GRANT INSERT, UPDATE ON TABLES TO jobradar_api"
            )
            c.execute(
                "ALTER DEFAULT PRIVILEGES FOR ROLE jobradar_migration "
                "GRANT USAGE ON SEQUENCES TO jobradar_api"
            )
            provision_access(c, "jobradar_test", PASSWORDS)

    await asyncio.to_thread(provision)
    engines = {
        role: create_engine(
            url.set(username=f"jobradar_{role}", password=PASSWORDS[role]).render_as_string(
                hide_password=False
            )
        )
        for role in ROLES
    }
    try:
        yield engines
    finally:
        for engine in engines.values():
            await engine.dispose()


@pytest.mark.parametrize("role", ["api", "worker", "bot", "backup"])
@pytest.mark.parametrize(
    "statement",
    [
        "CREATE TABLE public.forbidden_table (id int)",
        "CREATE TEMP TABLE forbidden_temp (id int)",
        "CREATE SCHEMA forbidden_schema",
        "ALTER TABLE public.opportunities ADD COLUMN forbidden int",
        "DROP TABLE public.notification_scan_cursors",
        "TRUNCATE public.sources CASCADE",
        "CREATE ROLE forbidden_role",
        "CREATE DATABASE forbidden_db",
        "SET ROLE jobradar_migration",
        "SET ROLE jobradar",
        "UPDATE public.alembic_version SET version_num='forbidden'",
    ],
)
async def test_runtime_roles_deny_ddl_and_admin(
    restricted_engines: dict[str, AsyncEngine], role: str, statement: str
) -> None:
    # Disable the convenience read-only default: ACLs, not a mutable GUC, must enforce access.
    import sqlalchemy.exc

    async with restricted_engines[role].connect() as connection:
        connection = await connection.execution_options(isolation_level="AUTOCOMMIT")
        await connection.execute(text("SET default_transaction_read_only = off"))
        await connection.commit()
        with pytest.raises(sqlalchemy.exc.ProgrammingError) as error:
            await connection.execute(text(statement))
        assert error.value.orig.sqlstate == "42501"
        await connection.rollback()


@pytest.mark.parametrize("role", ["api", "backup", "bot"])
@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO sources (name) VALUES ('forbidden')",
        "UPDATE opportunities SET title='forbidden'",
        "DELETE FROM listings",
        "SELECT nextval('public.opportunities_id_seq')",
    ],
)
async def test_non_collectors_cannot_change_vacancies(
    restricted_engines: dict[str, AsyncEngine], role: str, statement: str
) -> None:
    import sqlalchemy.exc

    async with restricted_engines[role].connect() as connection:
        await connection.execute(text("SET default_transaction_read_only = off"))
        await connection.commit()
        with pytest.raises(sqlalchemy.exc.ProgrammingError) as error:
            await connection.execute(text(statement))
        assert error.value.orig.sqlstate == "42501"
        await connection.rollback()


async def test_restricted_services_end_to_end(restricted_engines: dict[str, AsyncEngine]) -> None:
    worker = create_session_factory(restricted_engines["worker"])
    first = await IngestionService(worker).run_source(MockSource())
    assert first.created == 2
    assert (await IngestionService(worker).run_source(MockSource())).unchanged == 2
    await MatchingService(worker).evaluate(BOHDAN_PROFILE)
    client = RecordingBotClient()
    rates = FixedExchangeRateProvider()
    notifications = NotificationService(worker, client, rates)
    summary = await notifications.dispatch(BOHDAN_PROFILE, 0, 10, None)
    assert summary.sent == 2
    assert (await notifications.dispatch(BOHDAN_PROFILE, 0, 10, None)).sent == 0
    assert len(client.messages) == 2
    async with worker() as session:
        opportunity_id = await session.scalar(select(Opportunity.id).order_by(Opportunity.id))
        before = [
            (d.id, d.status, d.attempts, d.sent_at)
            for d in (await session.scalars(select(NotificationDelivery))).all()
        ]
    assert opportunity_id is not None

    bot_factory = create_session_factory(restricted_engines["bot"])
    preferences = NotificationPreferenceService(bot_factory)
    bot = TelegramBotService(
        client,
        NotificationService(bot_factory, client, rates),
        OpportunityStateService(bot_factory),
        TelegramMessageRegistry(bot_factory),
        preferences,
        rates,
        123,
        0,
        5,
        1,
        0,
    )
    for command in ("/pause", "/resume", "/latest", "/all", "/favorites", "/stats"):
        await bot.handle_update({"message": {"chat": {"id": 123}, "text": command}})
    for action in ("favorite", "hide", "restore"):
        await bot.handle_update(
            {
                "callback_query": {
                    "id": f"synthetic-{action}",
                    "data": f"{action}:{opportunity_id}",
                    "message": {"message_id": 1, "chat": {"id": 123}},
                }
            }
        )
    await bot.handle_update({"message": {"chat": {"id": 123}, "text": "/clear"}})
    assert not await preferences.is_paused(BOHDAN_PROFILE.profile_id, "telegram")
    assert client.deleted_message_ids
    async with worker() as session:
        after = [
            (d.id, d.status, d.attempts, d.sent_at)
            for d in (await session.scalars(select(NotificationDelivery))).all()
        ]
    assert after == before

    application = create_app(
        create_session_factory(restricted_engines["api"]),
        application_settings=Settings(_env_file=None, app_env="test"),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as api:
        for path in ("/health", "/ready", "/jobs", "/jobs?q=Python", "/matches", "/sources"):
            assert (await api.get(path)).status_code == 200


async def test_default_privileges_are_fail_closed(
    restricted_engines: dict[str, AsyncEngine],
) -> None:
    async with restricted_engines["migration"].begin() as connection:
        await connection.execute(text("CREATE TYPE public.future_enum AS ENUM ('active')"))
        await connection.execute(
            text(
                "CREATE TABLE public.future_table "
                "(id bigserial PRIMARY KEY, status public.future_enum)"
            )
        )
        await connection.execute(
            text("CREATE FUNCTION public.future_function() RETURNS int LANGUAGE sql AS 'SELECT 1'")
        )
    try:
        async with restricted_engines["backup"].connect() as connection:
            assert (await connection.execute(text("SELECT * FROM future_table"))).all() == []
        async with restricted_engines["migration"].connect() as connection:
            for role in ("api", "bot", "worker"):
                row = (
                    await connection.execute(
                        text(
                            "SELECT has_table_privilege(:role, 'future_table', 'SELECT'), "
                            "has_table_privilege(:role, 'future_table', 'INSERT'), "
                            "has_table_privilege(:role, 'future_table', 'UPDATE'), "
                            "has_sequence_privilege(:role, 'future_table_id_seq', 'USAGE'), "
                            "has_type_privilege(:role, 'future_enum', 'USAGE'), "
                            "has_function_privilege(:role, 'future_function()', 'EXECUTE')"
                        ),
                        {"role": f"jobradar_{role}"},
                    )
                ).one()
                assert tuple(row) == (False, False, False, False, False, False)
    finally:
        async with restricted_engines["migration"].begin() as connection:
            await connection.execute(text("DROP TABLE future_table"))
            await connection.execute(text("DROP TYPE future_enum"))
            await connection.execute(text("DROP FUNCTION future_function()"))


@pytest.mark.parametrize(
    "role,statement",
    [
        ("api", "SELECT * FROM notification_deliveries"),
        ("api", "SELECT * FROM notification_preferences"),
        ("api", "SELECT * FROM notification_scan_cursors"),
        ("api", "SELECT * FROM telegram_opportunity_messages"),
        ("worker", "UPDATE notification_preferences SET is_paused=true"),
        ("bot", "UPDATE notification_deliveries SET status='sent'"),
        ("bot", "DELETE FROM opportunity_user_states"),
    ],
)
async def test_service_private_boundaries(
    restricted_engines: dict[str, AsyncEngine], role: str, statement: str
) -> None:
    import sqlalchemy.exc

    async with restricted_engines[role].connect() as connection:
        connection = await connection.execution_options(isolation_level="AUTOCOMMIT")
        await connection.execute(text("SET default_transaction_read_only=off"))
        with pytest.raises(sqlalchemy.exc.ProgrammingError) as error:
            await connection.execute(text(statement))
        assert error.value.orig.sqlstate == "42501"


@pytest.mark.parametrize(
    "unexpected",
    [
        "CREATE TABLE unexpected_table (id int)",
        "CREATE FUNCTION unexpected_function() RETURNS int LANGUAGE sql AS 'SELECT 1'",
        "CREATE TYPE unexpected_enum AS ENUM ('active')",
        "CREATE SEQUENCE unexpected_sequence",
        "GRANT UPDATE(title) ON opportunities TO jobradar_api",
        "GRANT jobradar_worker TO jobradar_api",
        "ALTER ROLE jobradar_bot SUPERUSER",
        "COMMENT ON ROLE jobradar_api IS 'unmanaged-fixture'",
        "ALTER DATABASE jobradar_test OWNER TO jobradar_api",
        "ALTER SCHEMA public OWNER TO jobradar_api",
    ],
)
async def test_provisioning_refuses_unknown_state_atomically(
    restricted_engines: dict[str, AsyncEngine], unexpected: str
) -> None:
    url = make_url(os.environ["TEST_DATABASE_URL"]).set(drivername="postgresql")

    def check() -> None:
        with psycopg.connect(url.render_as_string(hide_password=False)) as connection:
            # Keep the unexpected object/grant inside an outer transaction, rolled back below.
            connection.execute(unexpected)
            before = connection.execute(
                "SELECT rolname, rolpassword FROM pg_authid "
                "WHERE rolname LIKE 'jobradar_%' ORDER BY rolname"
            ).fetchall()
            with pytest.raises(ValueError):
                provision_access(connection, "jobradar_test", PASSWORDS)
            after = connection.execute(
                "SELECT rolname, rolpassword FROM pg_authid "
                "WHERE rolname LIKE 'jobradar_%' ORDER BY rolname"
            ).fetchall()
            # Do not include verifier material in pytest assertion diagnostics.
            if before != after:
                pytest.fail("Rejected provisioning changed role credentials")
            connection.rollback()

    await asyncio.to_thread(check)


async def test_role_attributes_ownership_and_password_authentication(
    restricted_engines: dict[str, AsyncEngine],
) -> None:
    async with restricted_engines["migration"].connect() as connection:
        roles = (
            await connection.execute(
                text(
                    "SELECT rolname, rolsuper, rolcreatedb, rolcreaterole, "
                    "rolreplication, rolbypassrls "
                    "FROM pg_roles WHERE rolname IN "
                    "('jobradar_api','jobradar_worker','jobradar_bot','jobradar_backup','jobradar_migration')"
                )
            )
        ).all()
        assert len(roles) == 5
        assert all(not any(row[1:]) for row in roles)
        assert (
            await connection.execute(
                text(
                    "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname='public' AND c.relkind IN ('r','S') "
                    "AND pg_get_userbyid(c.relowner) <> 'jobradar_migration'"
                )
            )
        ).scalar_one() == 0
        assert (
            await connection.execute(
                text(
                    "SELECT has_sequence_privilege('jobradar_backup', "
                    "'opportunities_id_seq', 'SELECT'), "
                    "has_sequence_privilege('jobradar_backup', 'opportunities_id_seq', 'UPDATE'), "
                    "has_sequence_privilege('jobradar_bot', "
                    "'telegram_opportunity_messages_id_seq', 'USAGE')"
                )
            )
        ).one() == (True, False, True)
    url = restricted_engines["api"].url.set(drivername="postgresql", password="wrong-synthetic")
    with pytest.raises(psycopg.OperationalError):
        await psycopg.AsyncConnection.connect(
            url.render_as_string(hide_password=False), connect_timeout=5
        )
