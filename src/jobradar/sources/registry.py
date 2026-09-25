from jobradar.config import Settings
from jobradar.sources.base import BaseSource
from jobradar.sources.djinni import DjinniSource
from jobradar.sources.dou_jobs import DouJobsSource
from jobradar.sources.mock import MockSource
from jobradar.sources.robota_ua import RobotaUaSource
from jobradar.sources.workua import WorkUaSource


def build_source_registry(settings: Settings) -> tuple[BaseSource, ...]:
    sources: list[BaseSource] = []
    if settings.djinni_source_enabled:
        sources.append(
            DjinniSource(
                jobs_url=settings.djinni_jobs_url,
                remote_only=settings.djinni_remote_only,
                request_timeout_seconds=settings.djinni_request_timeout_seconds,
                max_items=settings.djinni_max_items,
                max_pages=settings.djinni_max_pages,
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
            )
        )
    if settings.dou_jobs_source_enabled:
        sources.append(
            DouJobsSource(
                feed_url=settings.dou_jobs_feed_url,
                request_timeout_seconds=settings.dou_jobs_request_timeout_seconds,
                max_items=settings.dou_jobs_max_items,
            )
        )
    if settings.mock_source_enabled:
        sources.append(MockSource())
    return tuple(sources)
