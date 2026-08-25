from typing import Any

from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from jobradar.db.models import Listing, Opportunity, Source
from jobradar.domain.enums import OpportunityStatus
from jobradar.domain.models import NormalizedOpportunity
from jobradar.domain.normalization import build_canonical_key, normalize_text

DIRECT_ATS_SOURCE_NAMES = ("greenhouse", "lever", "ashby")


def normalized_snapshot(normalized: NormalizedOpportunity) -> dict[str, Any]:
    return normalized.model_dump(mode="json")


def listing_quality_score(normalized: NormalizedOpportunity) -> int:
    description_score = min(len(normalize_text(normalized.description)), 20_000)
    salary_score = (
        2_000 if normalized.salary_min is not None or normalized.salary_max is not None else 0
    )
    metadata_score = 0
    for value in (
        normalized.company,
        normalized.location_text,
        normalized.employment_type,
        normalized.contract_type,
        normalized.salary_currency,
        normalized.salary_period,
        normalized.published_at,
    ):
        if value is not None:
            metadata_score += 100
    return description_score + salary_score + metadata_score


def canonical_listing_order() -> tuple[Any, ...]:
    return Listing.quality_score.desc(), Listing.id.asc()


def canonical_source_link_order() -> tuple[Any, ...]:
    direct_ats_priority = case(
        (Source.name.in_(DIRECT_ATS_SOURCE_NAMES), 1),
        else_=0,
    )
    return direct_ats_priority.desc(), *canonical_listing_order()


async def refresh_opportunity_from_best_listing(
    session: AsyncSession,
    opportunity_id: int,
) -> Listing | None:
    opportunity = await session.get(Opportunity, opportunity_id)
    if opportunity is None:
        return None
    listing = await session.scalar(
        select(Listing)
        .join(Source, Source.id == Listing.source_id)
        .where(
            Listing.opportunity_id == opportunity_id,
            Listing.is_active.is_(True),
            Source.enabled.is_(True),
            Listing.normalized_data.is_not(None),
        )
        .order_by(*canonical_listing_order())
        .limit(1)
    )
    if listing is None or listing.normalized_data is None:
        opportunity.status = OpportunityStatus.STALE.value
        return None
    apply_normalized_opportunity(
        opportunity,
        NormalizedOpportunity.model_validate(listing.normalized_data),
    )
    return listing


def apply_normalized_opportunity(
    opportunity: Opportunity,
    normalized: NormalizedOpportunity,
) -> None:
    opportunity.kind = normalized.kind.value
    opportunity.status = OpportunityStatus.ACTIVE.value
    opportunity.canonical_key = build_canonical_key(normalized)
    opportunity.title = normalized.title
    opportunity.company = normalized.company
    opportunity.description = normalized.description
    opportunity.location_text = normalized.location_text
    opportunity.work_mode = normalized.work_mode.value
    opportunity.employment_type = normalized.employment_type
    opportunity.contract_type = normalized.contract_type
    opportunity.salary_min = normalized.salary_min
    opportunity.salary_max = normalized.salary_max
    opportunity.salary_currency = normalized.salary_currency
    opportunity.salary_period = normalized.salary_period
    opportunity.published_at = normalized.published_at
