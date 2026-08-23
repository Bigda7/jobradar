from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Annotated

import structlog
from fastapi import Depends, FastAPI, Query, Request
from sqlalchemy import exists, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobradar.api.schemas import (
    HealthResponse,
    JobListResponse,
    JobResponse,
    MatchListResponse,
    MatchResponse,
    SourceResponse,
)
from jobradar.config import get_settings
from jobradar.db.models import (
    Listing,
    MatchEvaluation,
    Opportunity,
    OpportunityUserState,
    Source,
)
from jobradar.db.session import engine, session_factory
from jobradar.domain.enums import OpportunityDisposition, WorkMode
from jobradar.ingestion.canonical import canonical_listing_order
from jobradar.logging_config import configure_logging
from jobradar.matching.profile import BOHDAN_PROFILE

settings = get_settings()
configure_logging(settings.log_level)
logger = structlog.get_logger(__name__)


def create_app(
    application_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> FastAPI:
    selected_session_factory = application_session_factory or session_factory

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        logger.info("api_started", environment=settings.app_env)
        yield
        if application_session_factory is None:
            await engine.dispose()
        logger.info("api_stopped")

    application = FastAPI(
        title="JobRadar API",
        version="0.6.0",
        lifespan=lifespan,
    )
    application.state.session_factory = selected_session_factory

    async def request_session(request: Request) -> AsyncIterator[AsyncSession]:
        factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
        async with factory() as session:
            yield session

    @application.get("/health", response_model=HealthResponse, tags=["system"])
    async def health() -> HealthResponse:
        return HealthResponse(status="ok")

    @application.get("/ready", response_model=HealthResponse, tags=["system"])
    async def ready(
        session: Annotated[AsyncSession, Depends(request_session)],
    ) -> HealthResponse:
        await session.execute(text("SELECT 1"))
        return HealthResponse(status="ready")

    @application.get("/jobs", response_model=JobListResponse, tags=["jobs"])
    async def list_jobs(
        session: Annotated[AsyncSession, Depends(request_session)],
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
        work_mode: Annotated[WorkMode | None, Query()] = WorkMode.REMOTE,
        query: Annotated[
            str | None,
            Query(alias="q", min_length=2, max_length=100),
        ] = None,
        employment_type: Annotated[
            str | None,
            Query(min_length=2, max_length=100),
        ] = None,
        minimum_salary: Annotated[Decimal | None, Query(alias="min_salary", ge=0)] = None,
    ) -> JobListResponse:
        filters = [
            exists(
                select(Listing.id)
                .join(Source, Source.id == Listing.source_id)
                .where(
                    Listing.opportunity_id == Opportunity.id,
                    Listing.is_active.is_(True),
                    Source.enabled.is_(True),
                )
            ),
            ~exists(
                select(OpportunityUserState.opportunity_id).where(
                    OpportunityUserState.opportunity_id == Opportunity.id,
                    OpportunityUserState.disposition == OpportunityDisposition.HIDDEN.value,
                )
            ),
        ]
        if work_mode is not None:
            filters.append(Opportunity.work_mode == work_mode.value)
        if query is not None:
            pattern = f"%{_escape_like(query.strip())}%"
            filters.append(
                or_(
                    Opportunity.title.ilike(pattern, escape="\\"),
                    Opportunity.company.ilike(pattern, escape="\\"),
                    Opportunity.description.ilike(pattern, escape="\\"),
                )
            )
        if employment_type is not None:
            filters.append(Opportunity.employment_type == employment_type.casefold())
        if minimum_salary is not None:
            filters.append(Opportunity.salary_max >= minimum_salary)

        total = await session.scalar(select(func.count()).select_from(Opportunity).where(*filters))
        opportunities = (
            await session.scalars(
                select(Opportunity)
                .where(*filters)
                .order_by(Opportunity.published_at.desc().nullslast(), Opportunity.id.desc())
                .limit(limit)
                .offset(offset)
            )
        ).all()
        return JobListResponse(
            items=[JobResponse.model_validate(item) for item in opportunities],
            total=total or 0,
            limit=limit,
            offset=offset,
        )

    @application.get("/sources", response_model=list[SourceResponse], tags=["sources"])
    async def list_sources(
        session: Annotated[AsyncSession, Depends(request_session)],
    ) -> list[SourceResponse]:
        sources = (await session.scalars(select(Source).order_by(Source.name))).all()
        return [SourceResponse.model_validate(item) for item in sources]

    @application.get("/matches", response_model=MatchListResponse, tags=["matches"])
    async def list_matches(
        session: Annotated[AsyncSession, Depends(request_session)],
        minimum_score: Annotated[int, Query(alias="min_score", ge=0, le=100)] = (
            settings.matching_min_score
        ),
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> MatchListResponse:
        filters = (
            MatchEvaluation.profile_id == BOHDAN_PROFILE.profile_id,
            MatchEvaluation.rules_version == BOHDAN_PROFILE.rules_version,
            MatchEvaluation.score >= minimum_score,
            ~exists(
                select(OpportunityUserState.opportunity_id).where(
                    OpportunityUserState.opportunity_id == MatchEvaluation.opportunity_id,
                    OpportunityUserState.disposition == OpportunityDisposition.HIDDEN.value,
                )
            ),
            exists(
                select(Listing.id)
                .join(Source, Source.id == Listing.source_id)
                .where(
                    Listing.opportunity_id == MatchEvaluation.opportunity_id,
                    Listing.is_active.is_(True),
                    Source.enabled.is_(True),
                )
            ),
        )
        total = await session.scalar(
            select(func.count()).select_from(MatchEvaluation).where(*filters)
        )
        listing_url = (
            select(Listing.source_url)
            .join(Source, Source.id == Listing.source_id)
            .where(
                Listing.opportunity_id == Opportunity.id,
                Listing.is_active.is_(True),
                Source.enabled.is_(True),
            )
            .order_by(*canonical_listing_order())
            .limit(1)
            .scalar_subquery()
        )
        rows = (
            await session.execute(
                select(Opportunity, MatchEvaluation, listing_url.label("source_url"))
                .join(
                    MatchEvaluation,
                    MatchEvaluation.opportunity_id == Opportunity.id,
                )
                .where(*filters)
                .order_by(
                    MatchEvaluation.score.desc(),
                    Opportunity.published_at.desc().nullslast(),
                    Opportunity.id.desc(),
                )
                .limit(limit)
                .offset(offset)
            )
        ).all()
        items: list[MatchResponse] = []
        for opportunity, evaluation, source_url in rows:
            if source_url is None:
                continue
            job_data = JobResponse.model_validate(opportunity).model_dump()
            items.append(
                MatchResponse(
                    **job_data,
                    source_url=source_url,
                    score=evaluation.score,
                    reasons=evaluation.reasons,
                    concerns=evaluation.concerns,
                    rules_version=evaluation.rules_version,
                )
            )
        return MatchListResponse(
            items=items,
            total=total or 0,
            minimum_score=minimum_score,
            limit=limit,
            offset=offset,
        )

    return application


app = create_app()


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
