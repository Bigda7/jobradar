from functools import lru_cache

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    log_level: str = "INFO"
    database_url: str = "postgresql+psycopg://jobradar:jobradar@localhost:5432/jobradar"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    mock_source_enabled: bool = False
    djinni_source_enabled: bool = True
    djinni_jobs_url: str = "https://djinni.co/jobs/l-nonhr/remote/"
    djinni_remote_only: bool = True
    djinni_request_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    djinni_max_items: int = Field(default=50, ge=1, le=100)
    djinni_poll_interval_seconds: int = Field(default=3600, ge=1800)
    freelancer_source_enabled: bool = False
    freelancer_api_base_url: str = "https://www.freelancer.com/api/projects/0.1"
    freelancer_web_base_url: str = "https://www.freelancer.com"
    freelancer_oauth_token: SecretStr | None = None
    freelancer_search_queries: str = (
        "python django;react javascript typescript;shopify liquid;rest api postgresql"
    )
    freelancer_request_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    freelancer_page_size: int = Field(default=50, ge=1, le=100)
    freelancer_max_pages_per_query: int = Field(default=2, ge=1, le=10)
    freelancer_poll_interval_seconds: int = Field(default=3600, ge=1800)
    workua_source_enabled: bool = True
    workua_reader_base_url: str = "https://r.jina.ai/http://www.work.ua"
    workua_search_urls: str = (
        "https://www.work.ua/en/jobs-remote-python/;"
        "https://www.work.ua/en/jobs-remote-django/;"
        "https://www.work.ua/en/jobs-remote-react/;"
        "https://www.work.ua/en/jobs-remote-javascript/;"
        "https://www.work.ua/en/jobs-remote-shopify/"
    )
    workua_request_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    workua_max_items: int = Field(default=50, ge=1, le=100)
    workua_remote_only: bool = True
    workua_detail_cache_ttl_seconds: int = Field(default=86400, ge=3600)
    workua_detail_request_delay_seconds: float = Field(default=1.5, ge=0, le=10)
    workua_retry_attempts: int = Field(default=2, ge=1, le=3)
    workua_poll_interval_seconds: int = Field(default=21600, ge=3600)
    jobs_cz_source_enabled: bool = True
    jobs_cz_search_urls: str = (
        "https://www.jobs.cz/prace/?q%5B0%5D=Python&arrangement=work-mostly-from-home;"
        "https://www.jobs.cz/prace/?q%5B0%5D=React&arrangement=work-mostly-from-home;"
        "https://www.jobs.cz/prace/?q%5B0%5D=JavaScript&arrangement=work-mostly-from-home;"
        "https://www.jobs.cz/prace/?q%5B0%5D=Shopify&arrangement=work-mostly-from-home"
    )
    jobs_cz_request_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    jobs_cz_max_items: int = Field(default=20, ge=1, le=50)
    jobs_cz_remote_only: bool = True
    jobs_cz_detail_cache_ttl_seconds: int = Field(default=86400, ge=3600)
    jobs_cz_detail_request_delay_seconds: float = Field(default=1.0, ge=0, le=10)
    jobs_cz_retry_attempts: int = Field(default=2, ge=1, le=3)
    jobs_cz_poll_interval_seconds: int = Field(default=21600, ge=3600)
    startupjobs_cz_source_enabled: bool = True
    startupjobs_cz_api_base_url: str = "https://back.startupjobs.cz"
    startupjobs_cz_web_base_url: str = "https://www.startupjobs.cz"
    startupjobs_cz_search_queries: str = (
        "python django;react javascript typescript;fullstack frontend backend;shopify liquid api"
    )
    startupjobs_cz_request_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    startupjobs_cz_page_size: int = Field(default=20, ge=1, le=50)
    startupjobs_cz_max_pages_per_query: int = Field(default=2, ge=1, le=5)
    startupjobs_cz_max_items: int = Field(default=20, ge=1, le=50)
    startupjobs_cz_remote_only: bool = True
    startupjobs_cz_detail_cache_ttl_seconds: int = Field(default=86400, ge=3600)
    startupjobs_cz_detail_request_delay_seconds: float = Field(default=0.5, ge=0, le=10)
    startupjobs_cz_poll_interval_seconds: int = Field(default=21600, ge=3600)
    prace_cz_source_enabled: bool = True
    prace_cz_search_urls: str = (
        "https://www.prace.cz/nabidky/programator/;"
        "https://www.prace.cz/nabidky/?q=python;"
        "https://www.prace.cz/nabidky/?q=react;"
        "https://www.prace.cz/nabidky/?q=javascript;"
        "https://www.prace.cz/nabidky/?q=django;"
        "https://www.prace.cz/nabidky/?q=shopify"
    )
    prace_cz_request_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    prace_cz_max_items: int = Field(default=20, ge=1, le=50)
    prace_cz_remote_only: bool = True
    prace_cz_detail_cache_ttl_seconds: int = Field(default=86400, ge=3600)
    prace_cz_detail_request_delay_seconds: float = Field(default=1.0, ge=0, le=10)
    prace_cz_retry_attempts: int = Field(default=2, ge=1, le=3)
    prace_cz_poll_interval_seconds: int = Field(default=21600, ge=3600)
    freelance_cz_source_enabled: bool = True
    freelance_cz_api_base_url: str = "https://www.freelance.cz/api/ui"
    freelance_cz_web_base_url: str = "https://www.freelance.cz"
    freelance_cz_category: str = "programovani-it"
    freelance_cz_request_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    freelance_cz_page_size: int = Field(default=25, ge=1, le=100)
    freelance_cz_max_pages: int = Field(default=2, ge=1, le=5)
    freelance_cz_max_items: int = Field(default=25, ge=1, le=100)
    freelance_cz_remote_only: bool = True
    freelance_cz_detail_cache_ttl_seconds: int = Field(default=86400, ge=3600)
    freelance_cz_detail_request_delay_seconds: float = Field(default=0.5, ge=0, le=10)
    freelance_cz_poll_interval_seconds: int = Field(default=21600, ge=3600)
    startup_jobs_source_enabled: bool = False
    startup_jobs_api_base_url: str = "https://api.startup.jobs"
    startup_jobs_api_key: SecretStr | None = None
    startup_jobs_role: str = "engineering"
    startup_jobs_request_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    startup_jobs_page_size: int = Field(default=50, ge=1, le=50)
    startup_jobs_max_pages: int = Field(default=2, ge=1, le=10)
    startup_jobs_max_items: int = Field(default=100, ge=1, le=500)
    startup_jobs_poll_interval_seconds: int = Field(default=21600, ge=3600)
    jobicy_source_enabled: bool = True
    jobicy_api_url: str = "https://jobicy.com/api/v2/remote-jobs"
    jobicy_industry: str = "engineering"
    jobicy_request_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    jobicy_max_items: int = Field(default=100, ge=1, le=100)
    jobicy_poll_interval_seconds: int = Field(default=21600, ge=3600)
    we_work_remotely_source_enabled: bool = True
    we_work_remotely_feed_url: str = (
        "https://weworkremotely.com/categories/remote-programming-jobs.rss"
    )
    we_work_remotely_request_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    we_work_remotely_max_items: int = Field(default=100, ge=1, le=200)
    we_work_remotely_poll_interval_seconds: int = Field(default=3600, ge=3600)
    dou_jobs_source_enabled: bool = True
    dou_jobs_feed_url: str = "https://jobs.dou.ua/vacancies/feeds/?remote"
    dou_jobs_request_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    dou_jobs_max_items: int = Field(default=100, ge=1, le=200)
    dou_jobs_poll_interval_seconds: int = Field(default=1800, ge=900)
    himalayas_source_enabled: bool = True
    himalayas_api_url: str = "https://himalayas.app/jobs/api"
    himalayas_request_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    himalayas_page_size: int = Field(default=20, ge=1, le=20)
    himalayas_max_pages: int = Field(default=5, ge=1, le=25)
    himalayas_max_items: int = Field(default=100, ge=1, le=500)
    himalayas_poll_interval_seconds: int = Field(default=86400, ge=86400)
    the_muse_source_enabled: bool = False
    the_muse_api_url: str = "https://www.themuse.com/api/public/jobs"
    the_muse_api_key: SecretStr | None = None
    the_muse_categories: str = "Software Engineering"
    the_muse_levels: str = "Entry Level;Mid Level"
    the_muse_location: str = "Flexible / Remote"
    the_muse_request_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    the_muse_max_pages: int = Field(default=5, ge=1, le=25)
    the_muse_max_items: int = Field(default=100, ge=1, le=500)
    the_muse_poll_interval_seconds: int = Field(default=21600, ge=21600)
    ats_source_enabled: bool = False
    ats_companies_file: str = "companies.yaml"
    ats_request_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    ats_max_items_per_company: int = Field(default=500, ge=1, le=2000)
    ats_poll_interval_seconds: int = Field(default=86400, ge=21600)
    arbeitnow_source_enabled: bool = True
    arbeitnow_api_url: str = "https://www.arbeitnow.com/api/job-board-api"
    arbeitnow_request_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    arbeitnow_max_pages: int = Field(default=3, ge=1, le=10)
    arbeitnow_max_items: int = Field(default=100, ge=1, le=500)
    arbeitnow_poll_interval_seconds: int = Field(default=21600, ge=3600)
    remotive_source_enabled: bool = True
    remotive_api_url: str = "https://remotive.com/api/remote-jobs"
    remotive_category: str = "software-dev"
    remotive_request_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    remotive_max_items: int = Field(default=100, ge=1, le=200)
    remotive_poll_interval_seconds: int = Field(default=21600, ge=21600)
    matching_enabled: bool = True
    matching_min_score: int = Field(default=55, ge=0, le=100)
    telegram_enabled: bool = False
    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: int | None = None
    telegram_notify_existing: bool = False
    telegram_max_messages_per_cycle: int = Field(default=3, ge=1, le=20)
    telegram_request_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    telegram_polling_enabled: bool = False
    telegram_poll_timeout_seconds: int = Field(default=15, ge=1, le=50)
    telegram_latest_limit: int = Field(default=5, ge=1, le=20)
    nbu_rates_url: str = "https://bank.gov.ua/NBUStatService/v1/statdirectory/exchange?json"
    nbu_request_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    employment_stale_after_days: int = Field(default=30, ge=1, le=365)
    freelance_stale_after_days: int = Field(default=7, ge=1, le=365)
    source_poll_jitter_ratio: float = Field(default=0.15, ge=0, le=0.5)
    worker_interval_seconds: int = Field(default=300, ge=10)

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

    @model_validator(mode="after")
    def validate_freelancer_configuration(self) -> "Settings":
        if self.freelancer_source_enabled and (
            self.freelancer_oauth_token is None
            or not self.freelancer_oauth_token.get_secret_value().strip()
        ):
            raise ValueError(
                "FREELANCER_OAUTH_TOKEN is required when FREELANCER_SOURCE_ENABLED is true."
            )
        if self.freelancer_source_enabled and not self.freelancer_queries:
            raise ValueError(
                "FREELANCER_SEARCH_QUERIES must contain at least one query "
                "when the source is enabled."
            )
        return self

    @property
    def freelancer_queries(self) -> tuple[str, ...]:
        return tuple(
            query.strip() for query in self.freelancer_search_queries.split(";") if query.strip()
        )

    @property
    def workua_urls(self) -> tuple[str, ...]:
        return tuple(url.strip() for url in self.workua_search_urls.split(";") if url.strip())

    @property
    def jobs_cz_urls(self) -> tuple[str, ...]:
        return tuple(url.strip() for url in self.jobs_cz_search_urls.split(";") if url.strip())

    @property
    def startupjobs_cz_queries(self) -> tuple[str, ...]:
        return tuple(
            query.strip()
            for query in self.startupjobs_cz_search_queries.split(";")
            if query.strip()
        )

    @property
    def prace_cz_urls(self) -> tuple[str, ...]:
        return tuple(url.strip() for url in self.prace_cz_search_urls.split(";") if url.strip())

    @property
    def the_muse_category_values(self) -> tuple[str, ...]:
        return tuple(
            value.strip() for value in self.the_muse_categories.split(";") if value.strip()
        )

    @property
    def the_muse_level_values(self) -> tuple[str, ...]:
        return tuple(value.strip() for value in self.the_muse_levels.split(";") if value.strip())

    @model_validator(mode="after")
    def validate_workua_configuration(self) -> "Settings":
        if self.workua_source_enabled and not self.workua_urls:
            raise ValueError(
                "WORKUA_SEARCH_URLS must contain at least one URL when the source is enabled."
            )
        return self

    @model_validator(mode="after")
    def validate_jobs_cz_configuration(self) -> "Settings":
        if self.jobs_cz_source_enabled and not self.jobs_cz_urls:
            raise ValueError(
                "JOBS_CZ_SEARCH_URLS must contain at least one URL when the source is enabled."
            )
        return self

    @model_validator(mode="after")
    def validate_startupjobs_cz_configuration(self) -> "Settings":
        if self.startupjobs_cz_source_enabled and not self.startupjobs_cz_queries:
            raise ValueError(
                "STARTUPJOBS_CZ_SEARCH_QUERIES must contain at least one query "
                "when the source is enabled."
            )
        return self

    @model_validator(mode="after")
    def validate_prace_cz_configuration(self) -> "Settings":
        if self.prace_cz_source_enabled and not self.prace_cz_urls:
            raise ValueError(
                "PRACE_CZ_SEARCH_URLS must contain at least one URL when the source is enabled."
            )
        return self

    @model_validator(mode="after")
    def validate_startup_jobs_configuration(self) -> "Settings":
        if self.startup_jobs_source_enabled and (
            self.startup_jobs_api_key is None
            or not self.startup_jobs_api_key.get_secret_value().strip()
        ):
            raise ValueError(
                "STARTUP_JOBS_API_KEY is required when STARTUP_JOBS_SOURCE_ENABLED is true."
            )
        if self.startup_jobs_source_enabled and not self.startup_jobs_role.strip():
            raise ValueError(
                "STARTUP_JOBS_ROLE is required when STARTUP_JOBS_SOURCE_ENABLED is true."
            )
        return self

    @model_validator(mode="after")
    def validate_the_muse_configuration(self) -> "Settings":
        if self.the_muse_source_enabled and not self.the_muse_category_values:
            raise ValueError(
                "THE_MUSE_CATEGORIES must contain at least one category when the source is enabled."
            )
        if self.the_muse_source_enabled and not self.the_muse_level_values:
            raise ValueError(
                "THE_MUSE_LEVELS must contain at least one level when the source is enabled."
            )
        if self.the_muse_source_enabled and not self.the_muse_location.strip():
            raise ValueError(
                "THE_MUSE_LOCATION is required when THE_MUSE_SOURCE_ENABLED is true."
            )
        return self

    def source_poll_interval_seconds(self, source_name: str) -> int:
        intervals = {
            "djinni": self.djinni_poll_interval_seconds,
            "freelancer": self.freelancer_poll_interval_seconds,
            "workua": self.workua_poll_interval_seconds,
            "jobs_cz": self.jobs_cz_poll_interval_seconds,
            "startupjobs_cz": self.startupjobs_cz_poll_interval_seconds,
            "prace_cz": self.prace_cz_poll_interval_seconds,
            "freelance_cz": self.freelance_cz_poll_interval_seconds,
            "startup_jobs": self.startup_jobs_poll_interval_seconds,
            "jobicy": self.jobicy_poll_interval_seconds,
            "we_work_remotely": self.we_work_remotely_poll_interval_seconds,
            "dou_jobs": self.dou_jobs_poll_interval_seconds,
            "himalayas": self.himalayas_poll_interval_seconds,
            "the_muse": self.the_muse_poll_interval_seconds,
            "greenhouse": self.ats_poll_interval_seconds,
            "lever": self.ats_poll_interval_seconds,
            "ashby": self.ats_poll_interval_seconds,
            "arbeitnow": self.arbeitnow_poll_interval_seconds,
            "remotive": self.remotive_poll_interval_seconds,
            "mock": self.worker_interval_seconds,
        }
        return intervals.get(source_name, self.worker_interval_seconds)


@lru_cache
def get_settings() -> Settings:
    return Settings()
