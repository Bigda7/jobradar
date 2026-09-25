import pytest
from pydantic import ValidationError

from jobradar.config import Settings
from jobradar.sources.registry import build_source_registry


def test_cors_origins_are_normalized_and_deduplicated() -> None:
    settings = Settings(
        cors_allowed_origins=(
            " http://localhost:5173/;https://jobradar.vercel.app;https://jobradar.vercel.app "
        )
    )

    assert settings.cors_origins == (
        "http://localhost:5173",
        "https://jobradar.vercel.app",
    )


@pytest.mark.parametrize(
    "origin",
    (
        "*",
        "https://jobradar.vercel.app/path",
        "https://user:password@jobradar.vercel.app",
        "ftp://jobradar.vercel.app",
    ),
)
def test_cors_origins_reject_unsafe_values(origin: str) -> None:
    with pytest.raises(ValidationError, match="CORS_ALLOWED_ORIGINS"):
        Settings(cors_allowed_origins=origin)


def test_allowed_hosts_are_normalized_and_deduplicated() -> None:
    settings = Settings(api_allowed_hosts=" LOCALHOST.;api.example.com;api.example.com ")

    assert settings.allowed_hosts == ("localhost", "api.example.com")


@pytest.mark.parametrize(
    "host",
    (
        "*",
        "*.example.com",
        "https://api.example.com",
        "api.example.com:8000",
        "api.example.com/path",
        "",
    ),
)
def test_allowed_hosts_reject_unsafe_values(host: str) -> None:
    with pytest.raises(ValidationError, match="API_ALLOWED_HOSTS"):
        Settings(api_allowed_hosts=host)


def test_production_requires_secure_api_configuration() -> None:
    with pytest.raises(ValidationError, match="API_BEARER_TOKEN"):
        Settings(app_env="production")

    with pytest.raises(ValidationError, match="placeholder production password"):
        Settings(
            app_env="production",
            api_allowed_hosts="api.example.com",
            api_bearer_token="a" * 32,
            database_url=("postgresql+psycopg://jobradar:your_secure_password@db:5432/jobradar"),
        )

    with pytest.raises(ValidationError, match="public API hostname"):
        Settings(
            app_env="production",
            api_bearer_token="a" * 32,
            database_url="postgresql+psycopg://jobradar:secret@db:5432/jobradar",
        )


def test_production_accepts_complete_secure_configuration() -> None:
    settings = Settings(
        app_env="production",
        api_allowed_hosts="api.example.com",
        api_bearer_token="a" * 32,
        database_url="postgresql+psycopg://jobradar:secret@db:5432/jobradar",
    )

    assert settings.app_env == "production"


def test_source_coverage_defaults_use_audited_balanced_limits() -> None:
    settings = Settings(_env_file=None)

    assert settings.djinni_max_items == 200
    assert settings.djinni_max_pages == 20
    assert settings.freelancer_max_pages_per_query == 3
    assert settings.workua_max_items == 75
    assert settings.startup_jobs_max_pages == 10
    assert settings.startup_jobs_max_items == 500
    assert settings.jobicy_max_items == 200


def test_default_registry_contains_only_retained_sources() -> None:
    settings = Settings(_env_file=None)

    assert {source.name for source in build_source_registry(settings)} == {
        "djinni",
        "dou_jobs",
        "robota_ua",
        "workua",
    }
