import json
import os
import runpy
import shutil
import subprocess
from contextlib import nullcontext
from pathlib import Path

import pytest
from alembic import context
from alembic.config import Config
from pydantic import ValidationError
from tests import conftest

from jobradar.api.app import create_app
from jobradar.config import Settings
from jobradar.db.access import ROLES, validate_passwords

ROOT = Path(__file__).resolve().parents[2]


def test_migration_credentials_support_percent_encoded_passwords(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configuration = Config()
    url = "postgresql+psycopg://jobradar_migration:p%40ss%25%2Fword@db/jobradar"
    settings = Settings(_env_file=None, service_role="migration", database_url=url)
    monkeypatch.setattr("jobradar.config.get_settings", lambda: settings)
    monkeypatch.setattr(context, "config", configuration, raising=False)
    monkeypatch.setattr(context, "is_offline_mode", lambda: True)
    configured = {}
    monkeypatch.setattr(context, "configure", lambda **kwargs: configured.update(kwargs))
    monkeypatch.setattr(context, "begin_transaction", nullcontext)
    monkeypatch.setattr(context, "run_migrations", lambda: None)
    runpy.run_path(str(ROOT / "alembic/env.py"))
    assert configuration.get_main_option("sqlalchemy.url") == url
    assert configured["url"] == url


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://synthetic@production.invalid/jobradar",
        "postgresql+psycopg://synthetic@production.invalid/jobradar_test",
        "postgresql+psycopg://synthetic@127.0.0.1/jobradar",
        "invalid-synthetic-url",
    ],
)
async def test_pg_fixture_refuses_unsafe_target_before_connecting(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    monkeypatch.setenv("TEST_DATABASE_URL", url)

    def forbidden_connect(*args, **kwargs):
        pytest.fail("Unsafe database was contacted before validation")

    monkeypatch.setattr(conftest, "create_engine", forbidden_connect)
    generator = conftest.postgres_engine.__wrapped__()
    with pytest.raises(pytest.fail.Exception, match="isolated"):
        await anext(generator)


def test_backup_fails_closed_without_its_own_password() -> None:
    bash = Path("C:/Program Files/Git/bin/bash.exe") if os.name == "nt" else shutil.which("bash")
    if not bash or not Path(bash).is_file():
        pytest.skip("Bash is required to execute the backup runner guard.")
    environment = {
        name: value
        for name, value in os.environ.items()
        if name.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"}
    }
    result = subprocess.run(
        [str(bash), str(ROOT / "scripts/backup_postgres.sh")],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 1
    assert "JOBRADAR_BACKUP_DB_PASSWORD is required" in result.stderr
    script = (ROOT / "scripts/backup_postgres.sh").read_text()
    assert "--username=jobradar_backup" in script
    assert "--host=127.0.0.1" in script
    assert "exec -T --env PGPASSWORD db pg_dump" in script


@pytest.mark.parametrize("role", ["api", "worker", "bot", "migration"])
def test_compose_role_guard_rejects_admin_or_wrong_service_login(role: str) -> None:
    for username in ("jobradar", "jobradar_backup", "unexpected"):
        with pytest.raises(ValidationError, match="matching restricted service login"):
            Settings(
                _env_file=None,
                service_role=role,
                db_enforce_service_role=True,
                database_url=f"postgresql+psycopg://{username}:synthetic@db/jobradar",
            )
    assert Settings(
        _env_file=None,
        service_role=role,
        db_enforce_service_role=True,
        database_url=f"postgresql+psycopg://jobradar_{role}:synthetic@db/jobradar",
    ).db_enforce_service_role


@pytest.mark.parametrize("role", ["worker", "bot", "migration"])
def test_non_http_services_do_not_require_api_secret(role: str) -> None:
    settings = Settings(
        _env_file=None,
        service_role=role,
        app_env="production",
        database_url=f"postgresql+psycopg://jobradar_{role}:test-password@db/jobradar",
    )
    assert settings.api_bearer_token is None
    with pytest.raises(ValueError, match="SERVICE_ROLE=api"):
        create_app(application_settings=settings)
    with pytest.raises(ValidationError, match="placeholder production password"):
        Settings(
            _env_file=None,
            service_role=role,
            app_env="production",
            database_url="postgresql+psycopg://jobradar:your_secure_password@localhost/jobradar",
        )


@pytest.mark.parametrize("role", ["worker", "bot"])
def test_telegram_still_requires_delivery_credentials(role: str) -> None:
    with pytest.raises(ValidationError, match="TELEGRAM_BOT_TOKEN"):
        Settings(_env_file=None, service_role=role, telegram_enabled=True)
    with pytest.raises(ValidationError, match="TELEGRAM_CHAT_ID"):
        Settings(
            _env_file=None,
            service_role=role,
            telegram_enabled=True,
            telegram_bot_token="synthetic",
            telegram_chat_id=0,
        )
    assert Settings(
        _env_file=None,
        service_role=role,
        telegram_enabled=True,
        telegram_bot_token="synthetic",
        telegram_chat_id=123,
    ).telegram_enabled


@pytest.mark.parametrize(
    "bad_password", ["short", "a" * 31, "é" * 32, "\n" * 32, "your_secure_password" * 2]
)
def test_provisioning_rejects_bad_passwords_without_echo(bad_password: str) -> None:
    passwords = {role: f"synthetic-{role}-{'x' * 32}" for role in ROLES}
    passwords["api"] = bad_password
    with pytest.raises(ValueError) as error:
        validate_passwords(passwords)
    assert bad_password not in str(error.value)


def test_provisioning_requires_all_distinct_passwords() -> None:
    with pytest.raises(ValueError):
        validate_passwords({})
    with pytest.raises(ValueError, match="distinct"):
        validate_passwords(dict.fromkeys(ROLES, "synthetic" * 8))


def test_compose_environment_is_an_explicit_service_allowlist() -> None:
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("Docker CLI is required to render Compose, not a running daemon.")
    # Never resolve the developer's .env or inherit ambient credentials into output.
    environment = {
        name: value
        for name, value in os.environ.items()
        if name.upper()
        in {
            "PATH",
            "SYSTEMROOT",
            "WINDIR",
            "COMSPEC",
            "TEMP",
            "TMP",
            "USERPROFILE",
            "PROGRAMDATA",
            "APPDATA",
            "LOCALAPPDATA",
        }
    }
    environment.update(
        {
            "POSTGRES_PASSWORD": "synthetic-admin-password",
            "API_BEARER_TOKEN": "synthetic-api-token-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            "TELEGRAM_BOT_TOKEN": "synthetic-telegram-token",
            "TELEGRAM_CHAT_ID": "123",
            "TELEGRAM_ENABLED": "true",
            "APP_ENV": "production",
            "API_ALLOWED_HOSTS": "api.example.invalid",
            "UNRELATED_PRIVATE_SECRET": "must-not-be-injected",
        }
    )
    for role in ("api", "worker", "bot", "migration"):
        environment[f"{role.upper()}_DATABASE_URL"] = (
            f"postgresql+psycopg://jobradar_{role}:synthetic-{role}@db/jobradar"
        )
    result = subprocess.run(
        [
            docker,
            "compose",
            "--env-file",
            str(ROOT / "tests/fixtures/compose.env"),
            "-f",
            str(ROOT / "compose.yaml"),
            "--profile",
            "maintenance",
            "config",
            "--format",
            "json",
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    services = json.loads(result.stdout)["services"]
    for service, role in (
        ("api", "api"),
        ("worker", "worker"),
        ("bot", "bot"),
        ("migrate", "migration"),
    ):
        config = services[service]
        values = config["environment"]
        assert "env_file" not in config
        assert values["SERVICE_ROLE"] == role
        assert values["DB_ENFORCE_SERVICE_ROLE"] == "true"
        assert values["DATABASE_URL"] == environment[f"{role.upper()}_DATABASE_URL"]
        assert "POSTGRES_PASSWORD" not in values
        assert "UNRELATED_PRIVATE_SECRET" not in values
        assert "DB_ADMIN_URL" not in values
        assert "MIGRATION_DATABASE_URL" not in values
        if service in {"api", "migrate"}:
            assert "TELEGRAM_BOT_TOKEN" not in values
            assert values["TELEGRAM_ENABLED"] == "false"
        else:
            assert "API_BEARER_TOKEN" not in values
            assert values["TELEGRAM_BOT_TOKEN"] == "synthetic-telegram-token"
            assert values["TELEGRAM_ENABLED"] == "true"
        if service != "api":
            assert "API_BEARER_TOKEN" not in values
        Settings(_env_file=None, **{name.lower(): value for name, value in values.items()})
    assert "alembic" not in " ".join(services["api"]["command"])
    assert services["migrate"]["command"] == ["alembic", "upgrade", "head"]
    assert services["migrate"]["profiles"] == ["maintenance"]
    assert "migrate" not in services["api"]["depends_on"]
    assert set(services["db"]["environment"]) == {
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
    }
