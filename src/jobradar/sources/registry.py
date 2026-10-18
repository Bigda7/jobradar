import structlog

from jobradar.config import Settings
from jobradar.matching.profile import BOHDAN_PROFILE
from jobradar.sources.base import BaseSource
from jobradar.sources.djinni import DjinniSource
from jobradar.sources.dou_jobs import DouJobsSource
from jobradar.sources.mock import MockSource
from jobradar.sources.reader_pacing import ReaderRequestPacer
from jobradar.sources.robota_ua import RobotaUaSource
from jobradar.sources.workua import WorkUaSource


def build_source_registry(settings: Settings) -> tuple[BaseSource, ...]:
    structlog.get_logger(__name__).info(
        "source_discovery_profile",
        profile=settings.source_search_profile,
        workua_queries=len(settings.workua_urls),
        robota_queries=len(settings.robota_ua_urls),
        dou_feeds=len(settings.dou_urls),
        djinni_category_allowlist=False,
    )
    sources: list[BaseSource] = []
    reader_pacer = ReaderRequestPacer(settings.reader_request_interval_seconds)
    if settings.djinni_source_enabled:
        sources.append(
            DjinniSource(
                jobs_url=settings.djinni_jobs_url,
                additional_feed_urls=settings.djinni_additional_urls,
                remote_only=settings.djinni_remote_only,
                request_timeout_seconds=settings.djinni_request_timeout_seconds,
                max_items=settings.djinni_max_items,
                max_pages=settings.djinni_max_pages,
                max_feed_requests=settings.djinni_max_feed_requests,
                request_delay_seconds=settings.djinni_request_delay_seconds,
                run_timeout_seconds=settings.djinni_run_timeout_seconds,
                metadata_enabled=settings.djinni_metadata_enabled,
                max_metadata_requests=settings.djinni_max_metadata_requests,
                metadata_request_delay_seconds=settings.djinni_metadata_request_delay_seconds,
                metadata_cache_seconds=settings.djinni_metadata_cache_seconds,
            )
        )
    if settings.workua_source_enabled:
        sources.append(
            WorkUaSource(
                search_urls=settings.workua_urls,
                reader_base_url=settings.workua_reader_base_url,
                request_timeout_seconds=settings.workua_request_timeout_seconds,
                max_pages_per_search=settings.workua_max_pages_per_search,
                max_items=settings.workua_max_items,
                remote_only=settings.workua_remote_only,
                detail_cache_ttl_seconds=settings.workua_detail_cache_ttl_seconds,
                detail_request_delay_seconds=settings.workua_detail_request_delay_seconds,
                retry_attempts=settings.workua_retry_attempts,
                max_searches_per_run=settings.workua_max_searches_per_run,
                reader_pacer=reader_pacer,
            )
        )
    if settings.robota_ua_source_enabled:
        sources.append(
            RobotaUaSource(
                search_urls=settings.robota_ua_urls,
                reader_base_url=settings.robota_ua_reader_base_url,
                api_reader_base_url=settings.robota_ua_api_reader_base_url,
                request_timeout_seconds=settings.robota_ua_request_timeout_seconds,
                max_pages_per_search=settings.robota_ua_max_pages_per_search,
                max_items=settings.robota_ua_max_items,
                remote_only=settings.robota_ua_remote_only,
                detail_cache_ttl_seconds=settings.robota_ua_detail_cache_ttl_seconds,
                detail_request_delay_seconds=(settings.robota_ua_detail_request_delay_seconds),
                retry_attempts=settings.robota_ua_retry_attempts,
                max_searches_per_run=settings.robota_ua_max_searches_per_run,
                reader_pacer=reader_pacer,
            )
        )
    if settings.dou_jobs_source_enabled:
        sources.append(
            DouJobsSource(
                feed_url=settings.dou_urls[0],
                request_timeout_seconds=settings.dou_jobs_request_timeout_seconds,
                max_items=settings.dou_jobs_max_items,
                additional_feed_urls=settings.dou_additional_urls,
                max_feeds_per_run=settings.dou_jobs_max_feeds_per_run,
                feed_delay_seconds=settings.dou_jobs_feed_delay_seconds,
                local_city_aliases=(
                    BOHDAN_PROFILE.local_city_aliases
                    if settings.source_search_profile == "portfolio"
                    else ()
                ),
            )
        )
    if settings.mock_source_enabled:
        sources.append(MockSource())
    return tuple(sources)
