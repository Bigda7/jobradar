from jobradar.config import Settings
from jobradar.sources.arbeitnow import ArbeitnowSource
from jobradar.sources.ashby import AshbySource
from jobradar.sources.ats_config import companies_for_provider, load_companies_config
from jobradar.sources.base import BaseSource
from jobradar.sources.djinni import DjinniSource
from jobradar.sources.dou_jobs import DouJobsSource
from jobradar.sources.freelance_cz import FreelanceCzSource
from jobradar.sources.freelancer import FreelancerApiClient, FreelancerSource
from jobradar.sources.greenhouse import GreenhouseSource
from jobradar.sources.himalayas import HimalayasSource
from jobradar.sources.jobicy import JobicySource
from jobradar.sources.jobs_cz import JobsCzSource
from jobradar.sources.lever import LeverSource
from jobradar.sources.mock import MockSource
from jobradar.sources.prace_cz import PraceCzSource
from jobradar.sources.remotive import RemotiveSource
from jobradar.sources.robota_ua import RobotaUaSource
from jobradar.sources.startup_jobs import StartupJobsSource
from jobradar.sources.startupjobs_cz import StartupJobsCzSource
from jobradar.sources.the_muse import TheMuseSource
from jobradar.sources.we_work_remotely import WeWorkRemotelySource
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
    if settings.freelancer_source_enabled:
        if settings.freelancer_oauth_token is None:
            raise RuntimeError("Freelancer is enabled without an OAuth token.")
        sources.append(
            FreelancerSource(
                api_client=FreelancerApiClient(
                    oauth_token=settings.freelancer_oauth_token.get_secret_value(),
                    api_base_url=settings.freelancer_api_base_url,
                    request_timeout_seconds=settings.freelancer_request_timeout_seconds,
                ),
                search_queries=settings.freelancer_queries,
                web_base_url=settings.freelancer_web_base_url,
                page_size=settings.freelancer_page_size,
                max_pages_per_query=settings.freelancer_max_pages_per_query,
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
                request_timeout_seconds=settings.robota_ua_request_timeout_seconds,
                max_pages_per_search=settings.robota_ua_max_pages_per_search,
                max_items=settings.robota_ua_max_items,
                remote_only=settings.robota_ua_remote_only,
                detail_cache_ttl_seconds=settings.robota_ua_detail_cache_ttl_seconds,
                detail_request_delay_seconds=(settings.robota_ua_detail_request_delay_seconds),
                retry_attempts=settings.robota_ua_retry_attempts,
            )
        )
    if settings.jobs_cz_source_enabled:
        sources.append(
            JobsCzSource(
                search_urls=settings.jobs_cz_urls,
                request_timeout_seconds=settings.jobs_cz_request_timeout_seconds,
                max_items=settings.jobs_cz_max_items,
                remote_only=settings.jobs_cz_remote_only,
                detail_cache_ttl_seconds=settings.jobs_cz_detail_cache_ttl_seconds,
                detail_request_delay_seconds=settings.jobs_cz_detail_request_delay_seconds,
                retry_attempts=settings.jobs_cz_retry_attempts,
            )
        )
    if settings.startupjobs_cz_source_enabled:
        sources.append(
            StartupJobsCzSource(
                api_base_url=settings.startupjobs_cz_api_base_url,
                web_base_url=settings.startupjobs_cz_web_base_url,
                search_queries=settings.startupjobs_cz_queries,
                request_timeout_seconds=settings.startupjobs_cz_request_timeout_seconds,
                page_size=settings.startupjobs_cz_page_size,
                max_pages_per_query=settings.startupjobs_cz_max_pages_per_query,
                max_items=settings.startupjobs_cz_max_items,
                remote_only=settings.startupjobs_cz_remote_only,
                detail_cache_ttl_seconds=settings.startupjobs_cz_detail_cache_ttl_seconds,
                detail_request_delay_seconds=(settings.startupjobs_cz_detail_request_delay_seconds),
            )
        )
    if settings.prace_cz_source_enabled:
        sources.append(
            PraceCzSource(
                search_urls=settings.prace_cz_urls,
                request_timeout_seconds=settings.prace_cz_request_timeout_seconds,
                max_items=settings.prace_cz_max_items,
                remote_only=settings.prace_cz_remote_only,
                detail_cache_ttl_seconds=settings.prace_cz_detail_cache_ttl_seconds,
                detail_request_delay_seconds=settings.prace_cz_detail_request_delay_seconds,
                retry_attempts=settings.prace_cz_retry_attempts,
            )
        )
    if settings.freelance_cz_source_enabled:
        sources.append(
            FreelanceCzSource(
                api_base_url=settings.freelance_cz_api_base_url,
                web_base_url=settings.freelance_cz_web_base_url,
                category=settings.freelance_cz_category,
                request_timeout_seconds=settings.freelance_cz_request_timeout_seconds,
                page_size=settings.freelance_cz_page_size,
                max_pages=settings.freelance_cz_max_pages,
                max_items=settings.freelance_cz_max_items,
                remote_only=settings.freelance_cz_remote_only,
                detail_cache_ttl_seconds=settings.freelance_cz_detail_cache_ttl_seconds,
                detail_request_delay_seconds=settings.freelance_cz_detail_request_delay_seconds,
            )
        )
    if settings.startup_jobs_source_enabled:
        if settings.startup_jobs_api_key is None:
            raise RuntimeError("Startup.jobs is enabled without an API key.")
        sources.append(
            StartupJobsSource(
                api_key=settings.startup_jobs_api_key.get_secret_value(),
                api_base_url=settings.startup_jobs_api_base_url,
                role=settings.startup_jobs_role,
                request_timeout_seconds=settings.startup_jobs_request_timeout_seconds,
                page_size=settings.startup_jobs_page_size,
                max_pages=settings.startup_jobs_max_pages,
                max_items=settings.startup_jobs_max_items,
            )
        )
    if settings.jobicy_source_enabled:
        sources.append(
            JobicySource(
                api_url=settings.jobicy_api_url,
                industry=settings.jobicy_industry,
                request_timeout_seconds=settings.jobicy_request_timeout_seconds,
                max_items=settings.jobicy_max_items,
            )
        )
    if settings.we_work_remotely_source_enabled:
        sources.append(
            WeWorkRemotelySource(
                feed_url=settings.we_work_remotely_feed_url,
                request_timeout_seconds=settings.we_work_remotely_request_timeout_seconds,
                max_items=settings.we_work_remotely_max_items,
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
    if settings.himalayas_source_enabled:
        sources.append(
            HimalayasSource(
                api_url=settings.himalayas_api_url,
                request_timeout_seconds=settings.himalayas_request_timeout_seconds,
                page_size=settings.himalayas_page_size,
                max_pages=settings.himalayas_max_pages,
                max_items=settings.himalayas_max_items,
            )
        )
    if settings.the_muse_source_enabled:
        sources.append(
            TheMuseSource(
                api_url=settings.the_muse_api_url,
                api_key=(
                    settings.the_muse_api_key.get_secret_value()
                    if settings.the_muse_api_key is not None
                    else None
                ),
                categories=settings.the_muse_category_values,
                levels=settings.the_muse_level_values,
                location=settings.the_muse_location,
                request_timeout_seconds=settings.the_muse_request_timeout_seconds,
                max_pages=settings.the_muse_max_pages,
                max_items=settings.the_muse_max_items,
            )
        )
    if settings.ats_source_enabled:
        ats_companies = load_companies_config(settings.ats_companies_file)
        greenhouse_companies = companies_for_provider(ats_companies, "greenhouse")
        lever_companies = companies_for_provider(ats_companies, "lever")
        ashby_companies = companies_for_provider(ats_companies, "ashby")
        if greenhouse_companies:
            sources.append(
                GreenhouseSource(
                    companies=greenhouse_companies,
                    request_timeout_seconds=settings.ats_request_timeout_seconds,
                    max_items_per_company=settings.ats_max_items_per_company,
                )
            )
        if lever_companies:
            sources.append(
                LeverSource(
                    companies=lever_companies,
                    request_timeout_seconds=settings.ats_request_timeout_seconds,
                    max_items_per_company=settings.ats_max_items_per_company,
                )
            )
        if ashby_companies:
            sources.append(
                AshbySource(
                    companies=ashby_companies,
                    request_timeout_seconds=settings.ats_request_timeout_seconds,
                    max_items_per_company=settings.ats_max_items_per_company,
                )
            )
    if settings.arbeitnow_source_enabled:
        sources.append(
            ArbeitnowSource(
                api_url=settings.arbeitnow_api_url,
                request_timeout_seconds=settings.arbeitnow_request_timeout_seconds,
                max_pages=settings.arbeitnow_max_pages,
                max_items=settings.arbeitnow_max_items,
            )
        )
    if settings.remotive_source_enabled:
        sources.append(
            RemotiveSource(
                api_url=settings.remotive_api_url,
                category=settings.remotive_category,
                request_timeout_seconds=settings.remotive_request_timeout_seconds,
                max_items=settings.remotive_max_items,
            )
        )
    if settings.mock_source_enabled:
        sources.append(MockSource())
    return tuple(sources)
