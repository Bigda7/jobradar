import re
from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    database_url: str = "postgresql+psycopg://jobradar:your_secure_password@localhost:5432/jobradar"
    db_pool_size: int = Field(default=5, ge=1, le=50)
    db_max_overflow: int = Field(default=5, ge=0, le=100)
    db_pool_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    db_pool_recycle_seconds: int = Field(default=1800, ge=60, le=86400)
    db_connect_timeout_seconds: int = Field(default=10, ge=1, le=60)
    db_statement_timeout_milliseconds: int = Field(default=30000, ge=1000, le=300000)
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    api_allowed_hosts: str = "localhost;127.0.0.1;test"
    api_bearer_token: SecretStr | None = None
    readiness_timeout_seconds: float = Field(default=3.0, gt=0, le=30)
    cors_allowed_origins: str = "http://localhost:5173"
    mock_source_enabled: bool = False
    djinni_source_enabled: bool = True
    djinni_jobs_url: str = "https://djinni.co/jobs/l-nonhr/remote/"
    djinni_remote_only: bool = True
    djinni_request_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    djinni_max_items: int = Field(default=200, ge=1, le=200)
    djinni_max_pages: int = Field(default=20, ge=1, le=20)
    djinni_poll_interval_seconds: int = Field(default=3600, ge=1800)
    workua_source_enabled: bool = True
    workua_reader_base_url: str = "https://r.jina.ai/http://www.work.ua"
    workua_search_urls: str = (
        "https://www.work.ua/en/jobs-remote-programmer/;"
        "https://www.work.ua/en/jobs-remote-developer/;"
        "https://www.work.ua/en/jobs-remote-junior+developer/;"
        "https://www.work.ua/en/jobs-remote-front-end+developer/;"
        "https://www.work.ua/en/jobs-remote-back-end+developer/;"
        "https://www.work.ua/en/jobs-remote-full-stack+developer/;"
        "https://www.work.ua/en/jobs-remote-python/;"
        "https://www.work.ua/en/jobs-remote-django/;"
        "https://www.work.ua/en/jobs-remote-fastapi/;"
        "https://www.work.ua/en/jobs-remote-react/;"
        "https://www.work.ua/en/jobs-remote-javascript/;"
        "https://www.work.ua/en/jobs-remote-typescript/;"
        "https://www.work.ua/en/jobs-remote-node.js/;"
        "https://www.work.ua/en/jobs-remote-shopify/"
    )
    workua_request_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    workua_max_pages_per_search: int = Field(default=2, ge=1, le=5)
    workua_max_items: int = Field(default=75, ge=1, le=200)
    workua_remote_only: bool = True
    workua_detail_cache_ttl_seconds: int = Field(default=86400, ge=3600)
    workua_detail_request_delay_seconds: float = Field(default=1.5, ge=0, le=10)
    workua_retry_attempts: int = Field(default=2, ge=1, le=3)
    workua_poll_interval_seconds: int = Field(default=21600, ge=3600)
    robota_ua_source_enabled: bool = True
    robota_ua_reader_base_url: str = "https://r.jina.ai/http://robota.ua"
    robota_ua_api_reader_base_url: str = "https://r.jina.ai/http://api.robota.ua"
    robota_ua_search_urls: str = (
        "https://robota.ua/zapros/developer-remote/ukraine;"
        "https://robota.ua/zapros/junior-developer-remote/ukraine;"
        "https://robota.ua/zapros/frontend-developer-remote/ukraine;"
        "https://robota.ua/zapros/backend-developer-remote/ukraine;"
        "https://robota.ua/zapros/full-stack-developer-remote/ukraine;"
        "https://robota.ua/zapros/python-remote/ukraine;"
        "https://robota.ua/zapros/react-remote/ukraine"
    )
    robota_ua_request_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    robota_ua_max_pages_per_search: int = Field(default=2, ge=1, le=5)
    robota_ua_max_items: int = Field(default=100, ge=1, le=200)
    robota_ua_remote_only: bool = True
    robota_ua_detail_cache_ttl_seconds: int = Field(default=86400, ge=3600)
    robota_ua_detail_request_delay_seconds: float = Field(default=1.5, ge=0, le=10)
    robota_ua_retry_attempts: int = Field(default=2, ge=1, le=3)
    robota_ua_poll_interval_seconds: int = Field(default=21600, ge=3600)
    dou_jobs_source_enabled: bool = True
    dou_jobs_feed_url: str = "https://jobs.dou.ua/vacancies/feeds/?remote"
    dou_jobs_request_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    dou_jobs_max_items: int = Field(default=100, ge=1, le=200)
    dou_jobs_poll_interval_seconds: int = Field(default=1800, ge=900)
    matching_enabled: bool = True
    matching_min_score: int = Field(default=55, ge=0, le=100)
    telegram_enabled: bool = False
    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: int | None = None
    telegram_notify_existing: bool = False
    telegram_source_health_alerts_enabled: bool = True
    telegram_max_messages_per_cycle: int = Field(default=3, ge=1, le=20)
    telegram_request_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    telegram_polling_enabled: bool = False
    telegram_poll_timeout_seconds: int = Field(default=15, ge=1, le=50)
    telegram_latest_limit: int = Field(default=5, ge=1, le=20)
    telegram_all_message_delay_seconds: float = Field(default=1.0, ge=0, le=5)
    nbu_rates_url: str = "https://bank.gov.ua/NBUStatService/v1/statdirectory/exchange?json"
    nbu_request_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    employment_stale_after_days: int = Field(default=30, ge=1, le=365)
    freelance_stale_after_days: int = Field(default=30, ge=1, le=365)
    source_reconciliation_max_missing_ratio: float = Field(default=0.8, ge=0, lt=1)
    source_poll_jitter_ratio: float = Field(default=0.15, ge=0, le=0.5)
    worker_interval_seconds: int = Field(default=300, ge=10)
    worker_failure_retry_seconds: int = Field(default=30, ge=1, le=600)

    @property
    def cors_origins(self) -> tuple[str, ...]:
        origins: list[str] = []
        for raw_origin in self.cors_allowed_origins.split(";"):
            origin = raw_origin.strip().rstrip("/")
            if not origin:
                continue
            parsed = urlsplit(origin)
            if (
                origin == "*"
                or parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError(
                    "CORS_ALLOWED_ORIGINS must contain semicolon-separated HTTP origins "
                    "without paths, queries, fragments, credentials, or wildcards."
                )
            if origin not in origins:
                origins.append(origin)
        return tuple(origins)

    @property
    def allowed_hosts(self) -> tuple[str, ...]:
        hosts: list[str] = []
        for raw_host in self.api_allowed_hosts.split(";"):
            host = raw_host.strip().casefold().rstrip(".")
            if not host:
                continue
            if (
                host == "*"
                or not re.fullmatch(r"[a-z0-9.-]+", host)
                or host.startswith(".")
                or ".." in host
            ):
                raise ValueError(
                    "API_ALLOWED_HOSTS must contain semicolon-separated exact hostnames "
                    "or IPv4 addresses without schemes, ports, paths, or wildcards."
                )
            if host not in hosts:
                hosts.append(host)
        if not hosts:
            raise ValueError("API_ALLOWED_HOSTS must contain at least one host.")
        return tuple(hosts)

    @model_validator(mode="after")
    def validate_cors_configuration(self) -> "Settings":
        _ = self.cors_origins
        return self

    @model_validator(mode="after")
    def validate_allowed_hosts_configuration(self) -> "Settings":
        _ = self.allowed_hosts
        return self

    @model_validator(mode="after")
    def validate_production_security(self) -> "Settings":
        if self.app_env != "production":
            return self

        token = (
            self.api_bearer_token.get_secret_value().strip()
            if self.api_bearer_token is not None
            else ""
        )
        if len(token) < 32:
            raise ValueError("API_BEARER_TOKEN must contain at least 32 characters in production.")
        if "your_secure_password" in self.database_url:
            raise ValueError("DATABASE_URL must not use the placeholder production password.")

        local_hosts = {"localhost", "127.0.0.1", "test"}
        if set(self.allowed_hosts).issubset(local_hosts):
            raise ValueError(
                "API_ALLOWED_HOSTS must include the public API hostname in production."
            )
        return self

    @model_validator(mode="after")
    def validate_telegram_configuration(self) -> "Settings":
        if self.telegram_enabled and (
            self.telegram_bot_token is None
            or not self.telegram_bot_token.get_secret_value().strip()
            or self.telegram_chat_id is None
        ):
            raise ValueError(
                "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required when Telegram is enabled."
            )
        return self

    @property
    def workua_urls(self) -> tuple[str, ...]:
        return tuple(url.strip() for url in self.workua_search_urls.split(";") if url.strip())

    @property
    def robota_ua_urls(self) -> tuple[str, ...]:
        return tuple(url.strip() for url in self.robota_ua_search_urls.split(";") if url.strip())

    @model_validator(mode="after")
    def validate_workua_configuration(self) -> "Settings":
        if self.workua_source_enabled and not self.workua_urls:
            raise ValueError(
                "WORKUA_SEARCH_URLS must contain at least one URL when the source is enabled."
            )
        return self

    @model_validator(mode="after")
    def validate_robota_ua_configuration(self) -> "Settings":
        if self.robota_ua_source_enabled and not self.robota_ua_urls:
            raise ValueError(
                "ROBOTA_UA_SEARCH_URLS must contain at least one URL when the source is enabled."
            )
        return self

    def source_poll_interval_seconds(self, source_name: str) -> int:
        intervals = {
            "djinni": self.djinni_poll_interval_seconds,
            "workua": self.workua_poll_interval_seconds,
            "robota_ua": self.robota_ua_poll_interval_seconds,
            "dou_jobs": self.dou_jobs_poll_interval_seconds,
            "mock": self.worker_interval_seconds,
        }
        return intervals.get(source_name, self.worker_interval_seconds)


@lru_cache
def get_settings() -> Settings:
    return Settings()
