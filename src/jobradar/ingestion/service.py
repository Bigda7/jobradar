import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobradar.db.models import Listing, Opportunity, Source, SourceRun
from jobradar.domain.enums import OpportunityStatus, RunStatus
from jobradar.domain.models import NormalizedOpportunity, RawListing
from jobradar.domain.normalization import (
    build_canonical_key,
    build_content_hash,
    canonicalize_url,
    normalize_text,
)
from jobradar.ingestion.canonical import (
    listing_quality_score,
    normalized_snapshot,
    refresh_opportunity_from_best_listing,
)
from jobradar.sources.base import BaseSource, CachedListing

logger = structlog.get_logger(__name__)


class SuspiciousSourceInventoryError(RuntimeError):
    pass


@dataclass(slots=True)
class IngestionResult:
    source_name: str
    run_id: int
    status: RunStatus
    discovered: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    duplicates: int = 0
    deactivated: int = 0
    errors: int = 0


class IngestionService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        reconciliation_max_missing_ratio: float = 0.8,
    ) -> None:
        if not 0 <= reconciliation_max_missing_ratio < 1:
            raise ValueError("reconciliation_max_missing_ratio must be between 0 and 1.")
        self._session_factory = session_factory
        self._reconciliation_max_missing_ratio = reconciliation_max_missing_ratio

    async def synchronize_enabled_sources(self, adapters: Iterable[BaseSource]) -> None:
        enabled_names = {adapter.name for adapter in adapters}
        async with self._session_factory() as session, session.begin():
            await session.execute(update(Source).values(enabled=False))
            if enabled_names:
                await session.execute(
                    update(Source).where(Source.name.in_(enabled_names)).values(enabled=True)
                )

    async def is_source_due(
        self,
        source_name: str,
        poll_interval_seconds: int,
        *,
        jitter_ratio: float = 0.15,
        now: datetime | None = None,
    ) -> bool:
        async with self._session_factory() as session:
            last_run_at = await session.scalar(
                select(Source.last_run_at).where(Source.name == source_name)
            )
        if last_run_at is None:
            return True
        current_time = now or datetime.now(UTC)
        if last_run_at.tzinfo is None:
            last_run_at = last_run_at.replace(tzinfo=UTC)
        effective_interval_seconds = jittered_poll_interval_seconds(
            source_name,
            poll_interval_seconds,
            last_run_at,
            jitter_ratio=jitter_ratio,
        )
        return current_time >= last_run_at.astimezone(UTC) + timedelta(
            seconds=effective_interval_seconds
        )

    async def run_source(self, adapter: BaseSource) -> IngestionResult:
        source_id, run_id = await self._start_run(adapter)
        adapter.prime_listing_cache(await self._load_listing_cache(source_id))
        result = IngestionResult(
            source_name=adapter.name,
            run_id=run_id,
            status=RunStatus.RUNNING,
        )
        terminal_error: Exception | None = None
        seen_external_ids: set[str] = set()

        try:
            async for raw_listing in adapter.fetch():
                result.discovered += 1
                seen_external_ids.add(raw_listing.external_id)
                try:
                    normalized = adapter.normalize(raw_listing)
                    outcome = await self._ingest_listing(source_id, raw_listing, normalized)
                    if outcome == "created":
                        result.created += 1
                    elif outcome == "updated":
                        result.updated += 1
                    elif outcome == "duplicate":
                        result.duplicates += 1
                    else:
                        result.unchanged += 1
                except Exception as error:
                    result.errors += 1
                    logger.exception(
                        "listing_ingestion_failed",
                        source=adapter.name,
                        external_id=raw_listing.external_id,
                        error=str(error),
                    )
        except Exception as error:
            terminal_error = error
            result.errors += 1
            logger.exception("source_fetch_failed", source=adapter.name, error=str(error))

        if terminal_error is None and adapter.deactivate_missing_listings:
            try:
                result.deactivated = await self._deactivate_missing_listings(
                    source_id,
                    seen_external_ids,
                )
            except Exception as error:
                terminal_error = error
                result.errors += 1
                logger.exception(
                    "source_deactivation_failed",
                    source=adapter.name,
                    error=str(error),
                )

        if terminal_error is not None:
            result.status = RunStatus.FAILED
        elif result.errors:
            result.status = RunStatus.PARTIAL
        else:
            result.status = RunStatus.SUCCEEDED

        await self._finish_run(result, terminal_error)
        logger.info(
            "source_run_finished",
            source=adapter.name,
            run_id=run_id,
            status=result.status.value,
            discovered=result.discovered,
            created=result.created,
            updated=result.updated,
            unchanged=result.unchanged,
            duplicates=result.duplicates,
            deactivated=result.deactivated,
            errors=result.errors,
        )
        return result

    async def _start_run(self, adapter: BaseSource) -> tuple[int, int]:
        async with self._session_factory() as session, session.begin():
            source_id = await self._upsert_source(session, adapter)
            source = await session.get(Source, source_id)
            if source is None:
                raise RuntimeError("Source upsert did not return a stored source.")
            source.last_run_at = datetime.now(UTC)
            run = SourceRun(source_id=source.id)
            session.add(run)
            await session.flush()
            return source.id, run.id

    async def _load_listing_cache(self, source_id: int) -> dict[str, CachedListing]:
        async with self._session_factory() as session:
            listings = (
                await session.scalars(select(Listing).where(Listing.source_id == source_id))
            ).all()
        return {
            listing.external_id: CachedListing(
                payload=listing.raw_data,
                detail_fetched_at=listing.detail_fetched_at,
            )
            for listing in listings
        }

    async def _ingest_listing(
        self,
        source_id: int,
        raw_listing: RawListing,
        normalized: NormalizedOpportunity,
    ) -> str:
        now = datetime.now(UTC)
        canonical_url = canonicalize_url(str(raw_listing.source_url))
        content_hash = build_content_hash(normalized, raw_listing.payload)

        async with self._session_factory() as session, session.begin():
            listing = await session.scalar(
                select(Listing).where(
                    Listing.source_id == source_id,
                    Listing.external_id == raw_listing.external_id,
                )
            )

            if listing is None:
                opportunity = await self._find_cross_source_duplicate(session, normalized)
                is_cross_source_duplicate = opportunity is not None
                if opportunity is None:
                    opportunity = self._new_opportunity(normalized, now)
                    session.add(opportunity)
                    await session.flush()
                inserted_id = await self._insert_listing_if_absent(
                    session=session,
                    source_id=source_id,
                    opportunity_id=opportunity.id,
                    raw_listing=raw_listing,
                    canonical_url=canonical_url,
                    content_hash=content_hash,
                    normalized=normalized,
                    now=now,
                )
                if inserted_id is not None:
                    opportunity.last_seen_at = now
                    await refresh_opportunity_from_best_listing(session, opportunity.id)
                    return "duplicate" if is_cross_source_duplicate else "created"

                if not is_cross_source_duplicate:
                    await session.delete(opportunity)
                    await session.flush()
                listing = await session.scalar(
                    select(Listing).where(
                        Listing.source_id == source_id,
                        Listing.external_id == raw_listing.external_id,
                    )
                )
                if listing is None:
                    raise RuntimeError("Concurrent listing upsert did not return a listing.")

            stored_opportunity = await session.get(Opportunity, listing.opportunity_id)
            if stored_opportunity is None:
                raise RuntimeError("Listing references a missing opportunity.")
            opportunity = stored_opportunity

            listing.last_seen_at = now
            listing.is_active = True
            if raw_listing.detail_fetched_at is not None:
                listing.detail_fetched_at = raw_listing.detail_fetched_at
            listing.normalized_data = normalized_snapshot(normalized)
            listing.quality_score = listing_quality_score(normalized)
            opportunity.last_seen_at = now

            if listing.content_hash == content_hash:
                await refresh_opportunity_from_best_listing(session, opportunity.id)
                return "unchanged"

            listing.source_url = str(raw_listing.source_url)
            listing.canonical_url = canonical_url
            listing.content_hash = content_hash
            listing.raw_data = raw_listing.payload
            listing.published_at = normalized.published_at
            await refresh_opportunity_from_best_listing(session, opportunity.id)
            return "updated"

    async def _deactivate_missing_listings(
        self,
        source_id: int,
        seen_external_ids: set[str],
    ) -> int:
        async with self._session_factory() as session, session.begin():
            statement = select(Listing).where(
                Listing.source_id == source_id,
                Listing.is_active.is_(True),
            )
            active_listings = list((await session.scalars(statement)).all())
            if not active_listings:
                return 0
            listings = [
                listing
                for listing in active_listings
                if listing.external_id not in seen_external_ids
            ]
            if not listings:
                return 0
            missing_count = len(listings)
            missing_ratio = missing_count / len(active_listings)
            if missing_count and (
                not seen_external_ids or missing_ratio > self._reconciliation_max_missing_ratio
            ):
                raise SuspiciousSourceInventoryError(
                    "Source inventory reconciliation was blocked: "
                    f"{missing_count} of {len(active_listings)} active listings disappeared "
                    f"({missing_ratio:.1%})."
                )
            opportunity_ids = {listing.opportunity_id for listing in listings}
            for listing in listings:
                listing.is_active = False
            await session.flush()
            for opportunity_id in opportunity_ids:
                await refresh_opportunity_from_best_listing(session, opportunity_id)
            return len(listings)

    @staticmethod
    async def _find_cross_source_duplicate(
        session: AsyncSession,
        normalized: NormalizedOpportunity,
    ) -> Opportunity | None:
        if normalized.kind.value != "employment" or not normalize_text(normalized.company):
            return None
        title_key = normalize_text(normalized.title)
        company_key = normalize_text(normalized.company)
        candidates = (
            await session.scalars(
                select(Opportunity)
                .where(Opportunity.kind == normalized.kind.value)
                .order_by(Opportunity.id.asc())
            )
        ).all()
        return next(
            (
                opportunity
                for opportunity in candidates
                if normalize_text(opportunity.title) == title_key
                and normalize_text(opportunity.company) == company_key
            ),
            None,
        )

    @staticmethod
    async def _upsert_source(session: AsyncSession, adapter: BaseSource) -> int:
        values = {
            "name": adapter.name,
            "display_name": adapter.display_name,
            "opportunity_kind": adapter.opportunity_kind.value,
            "enabled": True,
        }
        dialect_name = session.bind.dialect.name
        dialect_insert: Any
        if dialect_name == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as dialect_insert
        elif dialect_name == "sqlite":
            from sqlalchemy.dialects.sqlite import insert as dialect_insert
        else:
            source = await session.scalar(select(Source).where(Source.name == adapter.name))
            if source is None:
                source = Source(**values)
                session.add(source)
                await session.flush()
            else:
                source.display_name = adapter.display_name
                source.opportunity_kind = adapter.opportunity_kind.value
            return source.id

        statement = (
            dialect_insert(Source)
            .values(**values)
            .on_conflict_do_update(
                index_elements=[Source.name],
                set_={
                    "display_name": adapter.display_name,
                    "opportunity_kind": adapter.opportunity_kind.value,
                    "enabled": True,
                },
            )
            .returning(Source.id)
        )
        source_id = cast(int | None, await session.scalar(statement))
        if source_id is None:
            raise RuntimeError("Source upsert returned no identifier.")
        return source_id

    @staticmethod
    async def _insert_listing_if_absent(
        session: AsyncSession,
        source_id: int,
        opportunity_id: int,
        raw_listing: RawListing,
        canonical_url: str,
        content_hash: str,
        normalized: NormalizedOpportunity,
        now: datetime,
    ) -> int | None:
        values = {
            "source_id": source_id,
            "opportunity_id": opportunity_id,
            "external_id": raw_listing.external_id,
            "source_url": str(raw_listing.source_url),
            "canonical_url": canonical_url,
            "content_hash": content_hash,
            "raw_data": raw_listing.payload,
            "normalized_data": normalized_snapshot(normalized),
            "quality_score": listing_quality_score(normalized),
            "published_at": normalized.published_at,
            "detail_fetched_at": raw_listing.detail_fetched_at,
            "first_seen_at": now,
            "last_seen_at": now,
            "is_active": True,
        }
        dialect_name = session.bind.dialect.name
        dialect_insert: Any
        if dialect_name == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as dialect_insert
        elif dialect_name == "sqlite":
            from sqlalchemy.dialects.sqlite import insert as dialect_insert
        else:
            listing = Listing(**values)
            session.add(listing)
            await session.flush()
            return listing.id

        statement = (
            dialect_insert(Listing)
            .values(**values)
            .on_conflict_do_nothing(index_elements=[Listing.source_id, Listing.external_id])
            .returning(Listing.id)
        )
        return cast(int | None, await session.scalar(statement))

    @staticmethod
    def _new_opportunity(
        normalized: NormalizedOpportunity,
        now: datetime,
    ) -> Opportunity:
        return Opportunity(
            kind=normalized.kind.value,
            status=OpportunityStatus.ACTIVE.value,
            canonical_key=build_canonical_key(normalized),
            title=normalized.title,
            company=normalized.company,
            description=normalized.description,
            location_text=normalized.location_text,
            work_mode=normalized.work_mode.value,
            employment_type=normalized.employment_type,
            contract_type=normalized.contract_type,
            salary_min=normalized.salary_min,
            salary_max=normalized.salary_max,
            salary_currency=normalized.salary_currency,
            salary_period=normalized.salary_period,
            published_at=normalized.published_at,
            first_seen_at=now,
            last_seen_at=now,
        )

    async def _finish_run(
        self,
        result: IngestionResult,
        terminal_error: Exception | None,
    ) -> None:
        now = datetime.now(UTC)
        async with self._session_factory() as session, session.begin():
            run = await session.get(SourceRun, result.run_id)
            if run is None:
                raise RuntimeError("Source run disappeared before completion.")
            source = await session.get(Source, run.source_id)
            if source is None:
                raise RuntimeError("Source disappeared before run completion.")

            run.status = result.status.value
            run.finished_at = now
            run.discovered_count = result.discovered
            run.created_count = result.created
            run.updated_count = result.updated
            run.unchanged_count = result.unchanged
            run.deactivated_count = result.deactivated
            run.error_count = result.errors
            run.error_message = str(terminal_error)[:2000] if terminal_error else None

            source.last_run_at = now
            if result.status in {RunStatus.SUCCEEDED, RunStatus.PARTIAL}:
                source.last_success_at = now
                source.last_error = None
            else:
                source.last_error = (
                    str(terminal_error)[:2000] if terminal_error else "Unknown error"
                )


def jittered_poll_interval_seconds(
    source_name: str,
    poll_interval_seconds: int,
    last_run_at: datetime,
    *,
    jitter_ratio: float = 0.15,
) -> int:
    if poll_interval_seconds <= 0:
        raise ValueError("poll_interval_seconds must be positive.")
    if not 0 <= jitter_ratio <= 0.5:
        raise ValueError("jitter_ratio must be between 0 and 0.5.")
    if jitter_ratio == 0:
        return poll_interval_seconds

    normalized_time = last_run_at
    if normalized_time.tzinfo is None:
        normalized_time = normalized_time.replace(tzinfo=UTC)
    normalized_time = normalized_time.astimezone(UTC)
    seed = f"{source_name}:{normalized_time.isoformat(timespec='microseconds')}".encode()
    digest = hashlib.sha256(seed).digest()
    unit_interval = int.from_bytes(digest[:8], byteorder="big") / ((1 << 64) - 1)
    factor = (1 - jitter_ratio) + (2 * jitter_ratio * unit_interval)
    return max(1, round(poll_interval_seconds * factor))
