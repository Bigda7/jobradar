import hashlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit

import httpx
from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

from jobradar.domain.enums import OpportunityKind, WorkMode
from jobradar.domain.models import NormalizedOpportunity, RawListing
from jobradar.sources.base import BaseSource
from jobradar.sources.structured_data import html_to_text

DEFAULT_FEED_URL = "https://weworkremotely.com/categories/remote-programming-jobs.rss"
USER_AGENT = "JobRadar/1.1 (personal job aggregator)"
MAX_FEED_BYTES = 5_000_000


class WeWorkRemotelySourceError(RuntimeError):
    pass


class WeWorkRemotelySource(BaseSource):
    name = "we_work_remotely"
    display_name = "We Work Remotely"
    opportunity_kind = OpportunityKind.EMPLOYMENT

    def __init__(
        self,
        feed_url: str = DEFAULT_FEED_URL,
        request_timeout_seconds: float = 30.0,
        max_items: int = 100,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._feed_url = feed_url
        self._request_timeout_seconds = request_timeout_seconds
        self._max_items = max_items
        self._client = client

    async def fetch(self) -> AsyncIterator[RawListing]:
        if self._client is not None:
            async for listing in self._fetch_with_client(self._client):
                yield listing
            return

        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(self._request_timeout_seconds),
            headers={"Accept": "application/rss+xml, application/xml", "User-Agent": USER_AGENT},
        ) as client:
            async for listing in self._fetch_with_client(client):
                yield listing

    def normalize(self, raw_listing: RawListing) -> NormalizedOpportunity:
        payload = raw_listing.payload
        title, company = _title_and_company(_required_string(payload.get("title"), "title"))
        description_html = _required_string(payload.get("description"), "description")
        return NormalizedOpportunity(
            kind=self.opportunity_kind,
            title=title,
            company=company,
            description=html_to_text(description_html),
            location_text=_optional_string(payload.get("region")) or "Remote",
            work_mode=WorkMode.REMOTE,
            published_at=_rss_datetime(payload.get("pubDate")),
        )

    async def _fetch_with_client(self, client: httpx.AsyncClient) -> AsyncIterator[RawListing]:
        self.record_page()
        try:
            response = await client.get(self._feed_url)
            response.raise_for_status()
            if len(response.content) > MAX_FEED_BYTES:
                raise WeWorkRemotelySourceError(
                    "We Work Remotely RSS response exceeded the size limit."
                )
            root = ElementTree.fromstring(response.content)
        except (httpx.HTTPError, ElementTree.ParseError, DefusedXmlException) as error:
            raise WeWorkRemotelySourceError(
                f"We Work Remotely RSS request failed: {error}"
            ) from error

        items = root.findall("./channel/item")
        self.record_candidates(len(items))
        seen: set[str] = set()
        yielded = 0
        for item in items:
            payload = {child.tag: child.text or "" for child in item}
            source_url = _optional_string(payload.get("link")) or _optional_string(
                payload.get("guid")
            )
            if source_url is None:
                self.record_filtered()
                continue
            external_id = _external_id(source_url)
            if external_id in seen:
                self.record_filtered()
                continue
            seen.add(external_id)
            yield RawListing(
                external_id=external_id,
                source_url=source_url,
                payload=payload,
            )
            yielded += 1
            if yielded >= self._max_items:
                self.mark_limit_reached()
                return


def _title_and_company(value: str) -> tuple[str, str | None]:
    company, separator, title = value.partition(":")
    if not separator or not company.strip() or not title.strip():
        return value.strip(), None
    return title.strip(), company.strip()


def _external_id(source_url: str) -> str:
    slug = urlsplit(source_url).path.rstrip("/").rsplit("/", maxsplit=1)[-1]
    if slug and len(slug) <= 255:
        return slug
    return hashlib.sha256(source_url.encode("utf-8")).hexdigest()


def _rss_datetime(value: Any) -> datetime | None:
    text = _optional_string(value)
    if text is None:
        return None
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _required_string(value: Any, field_name: str) -> str:
    result = _optional_string(value)
    if result is None:
        raise WeWorkRemotelySourceError(f"We Work Remotely listing is missing {field_name}.")
    return result


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None
