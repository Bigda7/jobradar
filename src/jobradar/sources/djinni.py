from __future__ import annotations

import asyncio
import math
import re
from collections import deque
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from time import monotonic
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
import structlog
from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

from jobradar.domain.enums import OpportunityKind, WorkMode
from jobradar.domain.models import NormalizedOpportunity, RawListing
from jobradar.sources.base import BaseSource
from jobradar.sources.djinni_rss import (
    FEED_SATURATION,
    FeedPartition,
    category_filter_is_ignored,
    description_text,
    split_partition,
)
from jobradar.sources.djinni_rss_diagnostics import (
    MAX_DIAGNOSTIC_PARENTS,
    FeedReadDiagnostic,
    consistency_diagnostic,
)
from jobradar.sources.link_policy import SOURCE_LISTING_HOSTS, is_trusted_source_link
from jobradar.sources.request_budget import MemoryRequestBudget
from jobradar.sources.structured_data import html_to_text, parse_job_postings

if TYPE_CHECKING:
    # Element is a type annotation only; defusedxml exclusively parses response data.
    from xml.etree.ElementTree import Element  # nosec B405

DEFAULT_JOBS_URL = "https://djinni.co/jobs/rss/?editorial=nonhr&employment=remote"
USER_AGENT = "JobRadar/0.2 (personal job aggregator)"
MAX_FEED_BYTES = 5_000_000
MAX_RUN_BYTES = 100_000_000
RSS_REQUEST_ATTEMPTS = 3
RSS_RETRY_STATUSES = frozenset({408, 500, 502, 503, 504})
MAX_RSS_RETRY_WAIT = 30.0
MAX_CONSISTENCY_RECHECKS = 12
MAX_RECHECK_MISSING_ITEMS = 3
logger = structlog.get_logger(__name__)
CONTENT_ENCODED_TAG = "{http://purl.org/rss/1.0/modules/content/}encoded"
LEGACY_METADATA_FIELDS = (
    "hiringOrganization",
    "estimatedSalary",
    "employmentType",
    "applicantLocationRequirements",
    "jobLocation",
)


class DjinniSourceError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        stop_traversal: bool = False,
        retryable: bool = False,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.stop_traversal = stop_traversal
        self.retryable = retryable
        self.retry_after = retry_after


class MetadataBudgetExhausted(RuntimeError):
    pass


class _RequestLimiter:
    def __init__(self, delay_seconds: float) -> None:
        self._delay = max(60 / 95, delay_seconds)
        self._next_request_at = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            wait_seconds = self._next_request_at - monotonic()
            if wait_seconds > 0:
                await asyncio.sleep(wait_seconds)
            self._next_request_at = monotonic() + self._delay


class DjinniSource(BaseSource):
    name = "djinni"
    poll_from_start = True
    display_name = "Djinni"
    opportunity_kind = OpportunityKind.EMPLOYMENT
    allowed_listing_hosts = SOURCE_LISTING_HOSTS["djinni"]

    def __init__(
        self,
        jobs_url: str = DEFAULT_JOBS_URL,
        remote_only: bool = True,
        request_timeout_seconds: float = 20.0,
        max_items: int = 10000,
        max_pages: int = 10,
        client: httpx.AsyncClient | None = None,
        *,
        max_feed_requests: int = 512,
        request_delay_seconds: float = 60 / 95,
        run_timeout_seconds: float = 600.0,
        metadata_enabled: bool = False,
        max_metadata_requests: int = 100,
        metadata_request_delay_seconds: float = 2.0,
        metadata_cache_seconds: int = 86400,
        additional_feed_urls: tuple[str, ...] = (),
    ) -> None:
        self._feed_url = _feed_url(jobs_url, remote_only)
        self._additional_feed_urls = tuple(
            dict.fromkeys(_feed_url(url, False) for url in additional_feed_urls)
        )
        if len(self._additional_feed_urls) > 8:
            raise ValueError("At most eight additional Djinni feeds may be configured.")
        self._request_timeout_seconds = request_timeout_seconds
        self._max_items = max_items
        # Retain the legacy constructor argument; RSS does not support page traversal.
        self._client = client
        self._max_feed_requests = max_feed_requests
        self._run_timeout_seconds = run_timeout_seconds
        self._limiter = _RequestLimiter(request_delay_seconds)
        self._metadata_enabled = metadata_enabled
        self._max_metadata_requests = min(100, max(1, max_metadata_requests))
        self._metadata_cache_seconds = metadata_cache_seconds
        self._metadata_limiter = _RequestLimiter(max(2.0, metadata_request_delay_seconds))
        self._fetching = False
        self._request_budget = MemoryRequestBudget()

    async def fetch(self) -> AsyncIterator[RawListing]:
        if self._fetching:
            raise DjinniSourceError("A Djinni RSS fetch is already running on this adapter.")
        self._fetching = True
        self._run_deadline = monotonic() + self._run_timeout_seconds
        self._run_bytes = 0
        self._feed_request_attempts = 0
        self._metadata_blocked = False
        try:
            if self._client is not None:
                async for listing in self._fetch_listings(self._client):
                    yield listing
            else:
                async with httpx.AsyncClient(
                    headers={"User-Agent": USER_AGENT},
                    timeout=httpx.Timeout(self._request_timeout_seconds),
                ) as client:
                    async for listing in self._fetch_listings(client):
                        yield listing
        finally:
            self._fetching = False

    async def _fetch_listings(self, client: httpx.AsyncClient) -> AsyncIterator[RawListing]:
        if not self._metadata_enabled:
            async for listing in self._fetch_feeds(client):
                yield listing
            return
        listings = [listing async for listing in self._fetch_feeds(client)]
        now = datetime.now(UTC)
        pending = [
            index for index, listing in enumerate(listings) if self._metadata_due(listing, now)
        ]
        pending.sort(key=lambda index: _metadata_priority(listings[index]))
        requests = 0
        failures = 0
        for index in pending:
            if (
                self._metadata_blocked
                or requests >= self._max_metadata_requests
                or monotonic() >= self._run_deadline
            ):
                break
            requests += 1
            try:
                listings[index] = await self._enrich_listing(client, listings[index])
                failures = 0
            except MetadataBudgetExhausted:
                requests -= 1
                break
            except (DjinniSourceError, ValueError) as error:
                payload = dict(listings[index].payload)
                payload["metadata_attempted_at"] = datetime.now(UTC).isoformat()
                listings[index] = listings[index].model_copy(update={"payload": payload})
                self.record_detail_failure()
                self.report_warning(f"Djinni metadata could not be refreshed: {error}")
                failures += 1
                if (
                    isinstance(error, DjinniSourceError)
                    and (error.stop_traversal or error.status_code in {403, 429})
                ) or failures >= 3:
                    break
        if requests < len(pending):
            self.record_metadata_deferred(len(pending) - requests)
        for listing in listings:
            yield listing

    def _metadata_due(self, listing: RawListing, now: datetime) -> bool:
        checked = listing.detail_fetched_at
        if checked is None:
            return True
        if checked.tzinfo is None:
            checked = checked.replace(tzinfo=UTC)
        return now - checked >= timedelta(
            seconds=self._metadata_cache_seconds
        ) or listing.payload.get("metadata_rss_updated_at") != listing.payload.get(
            "sourceUpdatedAt"
        )

    async def _enrich_listing(self, client: httpx.AsyncClient, listing: RawListing) -> RawListing:
        content = await self._request_bytes(client, str(listing.source_url), metadata=True)
        matches: list[dict[str, Any]] = []
        for posting in parse_job_postings(content.decode("utf-8", errors="replace")):
            try:
                candidate = _to_raw_listing(posting)
            except (DjinniSourceError, ValueError):
                continue
            if (
                candidate.external_id == listing.external_id
                and is_trusted_source_link(str(candidate.source_url), self.allowed_listing_hosts)
                and _vacancy_id(str(candidate.source_url)) == listing.external_id
            ):
                matches.append(posting)
        if len(matches) != 1:
            raise DjinniSourceError("Djinni detail page has no unique matching JobPosting.")
        payload = dict(listing.payload)
        # A successful page refresh replaces old fields, including fields removed by the provider.
        for key in LEGACY_METADATA_FIELDS:
            payload.pop(key, None)
            if key in matches[0]:
                payload[key] = matches[0][key]
        payload["metadata_origin"] = "job_page"
        payload["metadata_rss_updated_at"] = payload.get("sourceUpdatedAt")
        payload["metadata_attempted_at"] = datetime.now(UTC).isoformat()
        enriched = listing.model_copy(
            update={"payload": payload, "detail_fetched_at": datetime.now(UTC)}
        )
        self.normalize(enriched)
        return enriched

    async def _fetch_feeds(self, client: httpx.AsyncClient) -> AsyncIterator[RawListing]:
        configured_categories = [
            value
            for key, value in parse_qsl(urlsplit(self._feed_url).query)
            if key == "primary_keyword" and value.strip()
        ]
        initial = FeedPartition(
            self._feed_url,
            configured_categories[0] if len(configured_categories) == 1 else None,
        )
        queue = deque([(initial, False)])
        partitions = {initial.url: initial}
        scheduled = {initial.url}
        for url in self._additional_feed_urls:
            if url not in scheduled:
                categories = [
                    value
                    for key, value in parse_qsl(urlsplit(url).query)
                    if key == "primary_keyword" and value.strip()
                ]
                partition = FeedPartition(url, categories[0] if len(categories) == 1 else None)
                queue.append((partition, False))
                partitions[url] = partition
                scheduled.add(url)
        configured_roots = set(scheduled)
        requests = 0
        malformed = 0
        cached_descriptions = 0
        consecutive_failures = 0
        seen_ids: set[str] = set()
        observed_ids: dict[str, set[str]] = {}
        subdivisions: dict[str, tuple[str, ...]] = {}
        root_ids: set[str] = set()
        catalog_heading_count = 0
        limited = False
        consistency_checked = False
        recheck_missing: dict[str, set[str]] = {}
        first_reads: dict[str, FeedReadDiagnostic] = {}
        rechecks: dict[str, FeedReadDiagnostic] = {}
        scheduled_rechecks: set[str] = set()
        stop_reason = "completed"
        while True:
            if not queue:
                if consistency_checked or self._metadata_blocked:
                    break
                consistency_checked = True
                recheck_urls: set[str] = set()
                for parent, children_urls in subdivisions.items():
                    covered = set().union(*(observed_ids.get(url, set()) for url in children_urls))
                    missing = observed_ids.get(parent, set()) - covered
                    if (
                        not missing
                        or len(missing) > MAX_RECHECK_MISSING_ITEMS
                        or not all(url in observed_ids for url in children_urls)
                    ):
                        continue
                    recheck_missing[parent] = missing
                    for url in children_urls:
                        if len(recheck_urls) >= MAX_CONSISTENCY_RECHECKS:
                            break
                        if url not in recheck_urls:
                            queue.append((partitions[url], True))
                            recheck_urls.add(url)
                            scheduled_rechecks.add(url)
                if not queue:
                    break
            if (
                self._feed_request_attempts >= self._max_feed_requests
                or monotonic() >= self._run_deadline
            ):
                self.report_warning("Djinni RSS traversal stopped at its request or time budget.")
                limited = True
                stop_reason = (
                    "request_budget"
                    if self._feed_request_attempts >= self._max_feed_requests
                    else "time_budget"
                )
                break
            partition, is_recheck = queue.popleft()
            requests += 1
            read = FeedReadDiagnostic(sequence=requests)
            (rechecks if is_recheck else first_reads)[partition.url] = read
            attempts_before = self._feed_request_attempts
            started = monotonic()
            try:
                root = await self._request(client, partition.url)
            except DjinniSourceError as error:
                read.outcome = "failed"
                read.http_status = error.status_code
                if requests == 1:
                    raise
                consecutive_failures += 1
                limited = True
                self.report_warning(f"Djinni RSS partition failed: {error}")
                if (
                    error.stop_traversal
                    or error.status_code in {403, 429}
                    or consecutive_failures >= 3
                ):
                    self._metadata_blocked = True
                    stop_reason = "source_stop"
                    break
                continue
            finally:
                read.attempts = self._feed_request_attempts - attempts_before
                read.elapsed_seconds = round(monotonic() - started, 3)
            consecutive_failures = 0
            items = root.findall("./channel/item")
            read.response_items = len(items)
            self.record_candidates(len(items))
            if category_filter_is_ignored(partition, items):
                read.outcome = "category_ignored"
                if requests == 1:
                    raise DjinniSourceError(
                        "Djinni RSS did not apply the configured category filter."
                    )
                if partition.url in configured_roots or is_recheck:
                    limited = True
                    self.report_warning(
                        "Djinni RSS did not apply an additional feed's configured category filter."
                        if partition.url in configured_roots
                        else "Djinni RSS did not apply a rechecked feed's category filter."
                    )
                # RSS channel categories include headings that are not accepted query values.
                catalog_heading_count += 1
                self.record_filtered(len(items))
                continue
            read.outcome = "processed"
            partition_ids: set[str] = set()
            for item in items:
                if monotonic() >= self._run_deadline:
                    limited = True
                    self.report_warning("Djinni RSS traversal stopped at its time budget.")
                    read.outcome = "time_budget"
                    stop_reason = "time_budget"
                    break
                try:
                    raw_listing = self._rss_listing(item, partition.url)
                    self.normalize(raw_listing)
                except (DjinniSourceError, ValueError):
                    malformed += 1
                    self.record_filtered()
                    continue
                partition_ids.add(raw_listing.external_id)
                if raw_listing.external_id in seen_ids:
                    self.record_filtered()
                    continue
                if len(seen_ids) >= self._max_items:
                    limited = True
                    read.outcome = "item_budget"
                    stop_reason = "item_budget"
                    break
                seen_ids.add(raw_listing.external_id)
                if raw_listing.payload.get("description_origin") == "previously_stored_description":
                    cached_descriptions += 1
                yield raw_listing
            read.ids = partition_ids
            observed_ids.setdefault(partition.url, set()).update(partition_ids)
            if requests == 1:
                root_ids = partition_ids
            if len(seen_ids) >= self._max_items:
                limited = True
                stop_reason = "item_budget"
                break
            if len(items) >= FEED_SATURATION:
                children = split_partition(partition, root)
                if not children:
                    limited = True
                elif is_recheck:
                    # A newly saturated recheck cannot certify coverage of its unseen tail.
                    if partition.url not in subdivisions:
                        limited = True
                        self.report_warning("Djinni RSS recheck reached a newly saturated feed.")
                else:
                    subdivisions[partition.url] = tuple(child.url for child in children)
                    for child in children:
                        if child.url not in scheduled:
                            queue.append((child, False))
                            scheduled.add(child.url)
                            partitions[child.url] = child
        diagnostics_count = 0
        for parent, children_urls in subdivisions.items():
            covered = set().union(*(observed_ids.get(url, set()) for url in children_urls))
            missing = observed_ids.get(parent, set()) - covered
            if missing or parent in recheck_missing:
                diagnostics_count += 1
                if diagnostics_count <= MAX_DIAGNOSTIC_PARENTS:
                    if parent in recheck_missing:
                        recheck_selection = "eligible"
                    elif not consistency_checked:
                        recheck_selection = "not_reached"
                    elif not all(url in observed_ids for url in children_urls):
                        recheck_selection = "unobserved_children"
                    elif len(missing) > MAX_RECHECK_MISSING_ITEMS:
                        recheck_selection = "missing_count_above_ceiling"
                    else:
                        recheck_selection = "ineligible"
                    logger.info(
                        "djinni_rss_consistency_diagnostic",
                        **consistency_diagnostic(
                            parent,
                            children_urls,
                            missing | recheck_missing.get(parent, set()),
                            first_reads,
                            rechecks,
                            scheduled_rechecks,
                            remaining_missing=missing,
                            stop_reason=stop_reason,
                            recheck_selection=recheck_selection,
                        ),
                    )
            if missing:
                limited = True
                self.report_warning(
                    f"Djinni RSS subdivisions did not reproduce {len(missing)} parent items; "
                    "their already-fetched descriptions were retained."
                )
            elif parent in recheck_missing:
                logger.info(
                    "djinni_rss_consistency_recheck_resolved",
                    retained_items=len(recheck_missing[parent]),
                )
        if diagnostics_count > MAX_DIAGNOSTIC_PARENTS:
            logger.info(
                "djinni_rss_consistency_diagnostics_truncated",
                omitted_parents=diagnostics_count - MAX_DIAGNOSTIC_PARENTS,
            )
        if catalog_heading_count and not root_ids.issubset(
            set().union(*(values for url, values in observed_ids.items() if url != initial.url))
        ):
            limited = True
        if limited:
            self.mark_limit_reached()
        if malformed:
            self.report_warning(f"Djinni skipped {malformed} malformed RSS items.")
        if cached_descriptions:
            self.report_warning(
                f"Djinni RSS omitted {cached_descriptions} descriptions; stored text was retained."
            )

    def normalize(self, raw_listing: RawListing) -> NormalizedOpportunity:
        posting = raw_listing.payload
        salary_min, salary_max, salary_currency, salary_period = _salary(posting)
        return NormalizedOpportunity(
            kind=self.opportunity_kind,
            title=_required_string(posting.get("title"), "title"),
            company=_company(posting),
            description=_optional_string(posting.get("description")),
            location_text=_location(posting),
            work_mode=_work_mode(posting),
            employment_type=_employment_type(posting),
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency=salary_currency,
            salary_period=salary_period,
            # Djinni confirmed that RSS pubDate and page datePosted describe updates/bumps.
            published_at=None,
            source_updated_at=_datetime(posting.get("sourceUpdatedAt")),
        )

    async def _request(self, client: httpx.AsyncClient, feed_url: str) -> Element:
        deadline = min(self._run_deadline, monotonic() + 120.0)
        for attempt in range(RSS_REQUEST_ATTEMPTS):
            try:
                content = await self._request_bytes(client, feed_url, deadline=deadline)
                break
            except DjinniSourceError as error:
                if (
                    not error.retryable
                    or error.stop_traversal
                    or attempt + 1 == RSS_REQUEST_ATTEMPTS
                ):
                    raise
                wait = max(2.0**attempt, error.retry_after or 0.0)
                if (
                    not math.isfinite(wait)
                    or wait > MAX_RSS_RETRY_WAIT
                    or wait >= deadline - monotonic()
                ):
                    raise DjinniSourceError(
                        "Djinni RSS retry wait exceeds its bounded request window.",
                        status_code=error.status_code,
                        stop_traversal=True,
                    ) from error
                await self._wait_for_rss_retry(wait)
        try:
            root = ElementTree.fromstring(content, forbid_dtd=True)
            if root.tag != "rss" or root.find("channel") is None:
                raise DjinniSourceError("Djinni response is not an RSS channel.")
            if attempt:
                logger.info("djinni_rss_request_recovered", attempts=attempt + 1)
            return root
        except (ElementTree.ParseError, DefusedXmlException) as error:
            raise DjinniSourceError(
                "Djinni response contains invalid or unsafe RSS XML."
            ) from error

    async def _wait_for_rss_retry(self, seconds: float) -> None:
        await asyncio.sleep(seconds)

    async def _request_bytes(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        metadata: bool = False,
        deadline: float | None = None,
    ) -> bytes:
        label = "metadata" if metadata else "RSS"
        effective_deadline = min(self._run_deadline, deadline or self._run_deadline)
        try:
            remaining = effective_deadline - monotonic()
            if remaining <= 0:
                raise DjinniSourceError(
                    "Djinni request exceeded its time budget.", stop_traversal=True
                )
            async with asyncio.timeout(remaining):
                await (self._metadata_limiter if metadata else self._limiter).wait()
                if metadata:
                    if not await self._request_budget.reserve("metadata"):
                        raise MetadataBudgetExhausted
                else:
                    if self._feed_request_attempts >= self._max_feed_requests:
                        raise DjinniSourceError(
                            "Djinni RSS traversal exceeded its request budget.", stop_traversal=True
                        )
                    for _ in range(100):
                        if await self._request_budget.reserve("rss"):
                            break
                        await asyncio.sleep(60 / 95)
                    else:
                        raise DjinniSourceError("Djinni RSS request window is unavailable.")
                if monotonic() >= effective_deadline:
                    raise DjinniSourceError("Djinni request deadline reached.", stop_traversal=True)
                if not metadata:
                    self._feed_request_attempts += 1
                self.record_page()
                async with (
                    asyncio.timeout(
                        min(self._request_timeout_seconds, effective_deadline - monotonic())
                    ),
                    client.stream(
                        "GET",
                        url,
                        follow_redirects=False,
                        headers={
                            "Accept": "text/html"
                            if metadata
                            else "application/rss+xml, application/xml"
                        },
                        timeout=self._request_timeout_seconds,
                    ) as response,
                ):
                    response.raise_for_status()
                    content = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        if len(content) + len(chunk) > (1_000_000 if metadata else MAX_FEED_BYTES):
                            raise DjinniSourceError(
                                f"Djinni {label} exceeds the response byte limit."
                            )
                        self._run_bytes += len(chunk)
                        if self._run_bytes > MAX_RUN_BYTES:
                            raise DjinniSourceError(
                                "Djinni RSS traversal exceeds its byte budget.", stop_traversal=True
                            )
                        content.extend(chunk)
            return bytes(content)
        except httpx.HTTPStatusError as error:
            status = error.response.status_code
            raise DjinniSourceError(
                f"Djinni {label} request returned HTTP {status}.",
                status_code=status,
                retryable=not metadata and status in RSS_RETRY_STATUSES,
                retry_after=_retry_after_seconds(error.response.headers.get("Retry-After")),
            ) from error
        except (httpx.HTTPError, TimeoutError) as error:
            raise DjinniSourceError(
                f"Djinni {label} request failed or timed out.",
                retryable=not metadata and isinstance(error, (httpx.TransportError, TimeoutError)),
                stop_traversal=monotonic() >= effective_deadline,
            ) from error

    def _rss_listing(self, item: Element, feed_url: str) -> RawListing:
        source_url = _required_string(item.findtext("link") or item.findtext("guid"), "link")
        if not is_trusted_source_link(source_url, self.allowed_listing_hosts):
            raise DjinniSourceError("Djinni RSS item has an untrusted link.")
        external_id = _vacancy_id(source_url)
        if external_id is None:
            raise DjinniSourceError("Djinni RSS item has no numeric vacancy identifier.")
        posting: dict[str, Any] = {}
        cached = self.cached_listing(external_id)
        if cached is not None:
            posting.update(
                {
                    key: cached.payload[key]
                    for key in LEGACY_METADATA_FIELDS
                    if key in cached.payload
                }
            )
            if posting:
                posting["metadata_origin"] = cached.payload.get(
                    "metadata_origin", "previously_stored_metadata"
                )
            for key in ("metadata_rss_updated_at", "metadata_origin", "metadata_attempted_at"):
                if key in cached.payload:
                    posting[key] = cached.payload[key]
        encoded_description = item.findtext(CONTENT_ENCODED_TAG)
        description = description_text(encoded_description or item.findtext("description") or "")
        if not description and cached is not None:
            description = description_text(str(cached.payload.get("description") or ""))
            if description:
                posting["description_origin"] = "previously_stored_description"
        if not description:
            raise DjinniSourceError("Djinni RSS item is missing its description.")
        employment = dict(parse_qsl(urlsplit(feed_url).query)).get("employment")
        if employment == "remote":
            posting["jobLocationType"] = "TELECOMMUTE"
        elif employment == "office":
            posting.pop("jobLocationType", None)
            posting["jobLocation"] = posting.get("jobLocation") or {"@type": "Place"}
        posting.update(
            {
                "identifier": external_id,
                "url": source_url,
                "title": _required_string(html_to_text(item.findtext("title") or ""), "title"),
                "description": description,
                "sourceUpdatedAt": _rss_datetime(item.findtext("pubDate")),
                "rss": {
                    **({"employment": "office"} if employment == "office" else {}),
                    "title": item.findtext("title"),
                    "link": item.findtext("link"),
                    "guid": item.findtext("guid"),
                    "description": item.findtext("description"),
                    "pubDate": item.findtext("pubDate"),
                    "categories": [value.text for value in item.findall("category") if value.text],
                    **({"content_encoded": encoded_description} if encoded_description else {}),
                },
            }
        )
        listing = _to_raw_listing(posting)
        return listing.model_copy(
            update={"detail_fetched_at": cached.detail_fetched_at if cached else None}
        )


def _retry_after_seconds(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            target = parsedate_to_datetime(value)
            if target.tzinfo is None:
                target = target.replace(tzinfo=UTC)
            seconds = (target - datetime.now(UTC)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return None
    return max(0.0, seconds) if math.isfinite(seconds) else math.inf


def _metadata_priority(listing: RawListing) -> tuple[int, datetime, int]:
    attempted = _datetime(listing.payload.get("metadata_attempted_at"))
    checked = listing.detail_fetched_at
    if checked is not None and checked.tzinfo is None:
        checked = checked.replace(tzinfo=UTC)
    if checked is None and attempted is None:
        priority = 0
    elif (
        checked is not None
        and (attempted is None or checked >= attempted)
        and listing.payload.get("metadata_rss_updated_at") != listing.payload.get("sourceUpdatedAt")
    ):
        priority = 1
    else:
        priority = 2
    rss = listing.payload.get("rss")
    office_priority = 0 if isinstance(rss, dict) and rss.get("employment") == "office" else 1
    return priority, attempted or datetime.min.replace(tzinfo=UTC), office_priority


def _vacancy_id(url: str) -> str | None:
    match = re.fullmatch(r"/jobs/(\d+)(?:-[^/]*)?/?", urlsplit(url).path)
    return match.group(1) if match else None


def _feed_url(base_url: str, remote_only: bool) -> str:
    if not is_trusted_source_link(base_url, SOURCE_LISTING_HOSTS["djinni"]):
        raise ValueError("Djinni feed URL must use a trusted HTTPS Djinni host.")
    parsed = urlsplit(base_url)
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key != "page"
    ]
    if parsed.path in {"/jobs/l-nonhr/remote/", "/jobs/l-nonhr/"}:
        if not any(key == "editorial" for key, _ in query):
            query.append(("editorial", "nonhr"))
    elif parsed.path not in {"/jobs/", "/jobs/remote/", "/jobs/rss/"}:
        raise ValueError("Unsupported legacy Djinni path; configure the filtered RSS URL.")
    if remote_only or parsed.path.endswith("/remote/"):
        query = [(key, value) for key, value in query if key != "employment"]
        query.append(("employment", "remote"))
    return urlunsplit((parsed.scheme, parsed.netloc, "/jobs/rss/", urlencode(query), ""))


def _rss_datetime(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC).isoformat()
    except (TypeError, ValueError, OverflowError):
        return None


def _to_raw_listing(posting: dict[str, Any]) -> RawListing:
    external_id = posting.get("identifier")
    if isinstance(external_id, dict):
        external_id = external_id.get("value") or external_id.get("name")
    return RawListing(
        external_id=_required_string(external_id, "identifier"),
        source_url=_required_string(posting.get("url"), "url"),
        payload=posting,
    )


def _work_mode(posting: dict[str, Any]) -> WorkMode:
    location_type = posting.get("jobLocationType")
    values = location_type if isinstance(location_type, list) else [location_type]
    if any(str(value).casefold() == "telecommute" for value in values if value is not None):
        return WorkMode.REMOTE
    if posting.get("jobLocation"):
        return WorkMode.ONSITE
    return WorkMode.UNKNOWN


def _company(posting: dict[str, Any]) -> str | None:
    organization = posting.get("hiringOrganization")
    if isinstance(organization, dict):
        return _optional_string(organization.get("name"))
    return None


def _employment_type(posting: dict[str, Any]) -> str | None:
    value = posting.get("employmentType")
    if isinstance(value, list):
        return ",".join(str(item).casefold() for item in value)
    return str(value).casefold() if value is not None else None


def _salary(
    posting: dict[str, Any],
) -> tuple[Decimal | None, Decimal | None, str | None, str | None]:
    salary = posting.get("estimatedSalary")
    if not isinstance(salary, dict):
        return None, None, None, None

    value = salary.get("value")
    amount = value if isinstance(value, dict) else salary
    minimum = _decimal(amount.get("minValue") or amount.get("value"))
    maximum = _decimal(amount.get("maxValue") or amount.get("value"))
    currency = _optional_string(salary.get("currency"))
    if currency is not None:
        currency = currency.upper()
    period = _optional_string(amount.get("unitText"))
    if period is not None:
        period = period.casefold()
    return minimum, maximum, currency, period


def _location(posting: dict[str, Any]) -> str | None:
    values = _location_values(posting.get("applicantLocationRequirements"))
    if not values:
        values = _location_values(posting.get("jobLocation"))
    if values:
        return ", ".join(dict.fromkeys(values))
    if _work_mode(posting) is WorkMode.REMOTE:
        return "Remote"
    return None


def _location_values(value: Any) -> list[str]:
    items = value if isinstance(value, list) else [value]
    values: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        address = item.get("address")
        if not isinstance(address, dict):
            if item.get("@type") == "Country":
                values.extend(_location_names(item.get("name")))
            continue
        for key in ("addressCountry", "addressRegion", "addressLocality"):
            values.extend(_location_names(address.get(key)))
    return values


def _location_names(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, dict):
        return _location_names(value.get("name"))
    if isinstance(value, list):
        return [name for item in value for name in _location_names(item)]
    return []


def _datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _required_string(value: Any, field_name: str) -> str:
    result = _optional_string(value)
    if result is None:
        raise DjinniSourceError(f"Djinni JobPosting is missing {field_name}.")
    return result


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None
