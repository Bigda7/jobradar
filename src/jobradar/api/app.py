import asyncio
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Annotated

import structlog
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from sqlalchemy import exists, func, or_, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import ScalarSelect

from jobradar import __version__
from jobradar.api.middleware import SecurityHeadersMiddleware
from jobradar.api.schemas import (
    HealthResponse,
    JobListResponse,
    JobResponse,
    MatchListResponse,
    MatchResponse,
    SourceResponse,
)
from jobradar.config import Settings, get_settings
from jobradar.db.models import (
    Listing,
    MatchEvaluation,
    Opportunity,
    OpportunityUserState,
    Source,
)
from jobradar.db.session import engine, session_factory
from jobradar.domain.enums import OpportunityDisposition, WorkMode
from jobradar.ingestion.canonical import canonical_source_link_order
from jobradar.logging_config import configure_logging
from jobradar.matching.profile import BOHDAN_PROFILE
from jobradar.security import redact_sensitive_text

MAX_PAGE_SIZE = 200
MAX_OFFSET = 100_000
MAX_SALARY_FILTER = Decimal("1000000000")

settings = get_settings()
configure_logging(settings.log_level)
logger = structlog.get_logger(__name__)


def create_app(
    application_session_factory: async_sessionmaker[AsyncSession] | None = None,
    *,
    application_settings: Settings | None = None,
) -> FastAPI:
    selected_session_factory = application_session_factory or session_factory
    selected_settings = application_settings or settings

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        logger.info("api_started", environment=selected_settings.app_env)
        yield
        if application_session_factory is None:
            await engine.dispose()
        logger.info("api_stopped")

    application = FastAPI(
        title="JobRadar API",
        version=__version__,
        lifespan=lifespan,
        docs_url=None if selected_settings.app_env == "production" else "/docs",
        redoc_url=None if selected_settings.app_env == "production" else "/redoc",
        openapi_url=None if selected_settings.app_env == "production" else "/openapi.json",
    )
    application.state.session_factory = selected_session_factory
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(selected_settings.cors_origins),
        allow_credentials=False,
        allow_methods=["GET"],
        allow_headers=["Accept", "Authorization", "Content-Type"],
        max_age=600,
    )
    application.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=list(selected_settings.allowed_hosts),
    )
    application.add_middleware(
        SecurityHeadersMiddleware,
        enable_hsts=selected_settings.app_env.casefold() == "production",
    )

    async def request_session(request: Request) -> AsyncIterator[AsyncSession]:
        factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
        async with factory() as session:
            yield session

    async def require_api_access(
        authorization: Annotated[str | None, Header()] = None,
    ) -> None:
        configured_token = (
            selected_settings.api_bearer_token.get_secret_value().strip()
            if selected_settings.api_bearer_token is not None
            else ""
        )
        if not configured_token:
            return

        scheme, separator, supplied_token = (authorization or "").partition(" ")
        if (
            not separator
            or scheme.casefold() != "bearer"
            or not secrets.compare_digest(supplied_token, configured_token)
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication is required.",
                headers={"WWW-Authenticate": "Bearer"},
            )

    @application.get("/health", response_model=HealthResponse, tags=["system"])
    async def health() -> HealthResponse:
        return HealthResponse(status="ok")

    @application.get("/ready", response_model=HealthResponse, tags=["system"])
    async def ready(
        session: Annotated[AsyncSession, Depends(request_session)],
    ) -> HealthResponse:
        try:
            async with asyncio.timeout(selected_settings.readiness_timeout_seconds):
                await session.execute(text("SELECT 1"))
        except (TimeoutError, SQLAlchemyError) as error:
            logger.warning("readiness_check_failed", error_type=type(error).__name__)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Database is unavailable.",
            ) from None
        return HealthResponse(status="ready")

    @application.get("/jobs", response_model=JobListResponse, tags=["jobs"])
    async def list_jobs(
        session: Annotated[AsyncSession, Depends(request_session)],
        _: Annotated[None, Depends(require_api_access)],
        limit: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
        offset: int = Query(default=0, ge=0, le=MAX_OFFSET),
        work_mode: Annotated[WorkMode | None, Query()] = WorkMode.REMOTE,
        query: Annotated[
            str | None,
            Query(alias="q", min_length=2, max_length=100),
        ] = None,
        employment_type: Annotated[
            str | None,
            Query(min_length=2, max_length=100),
        ] = None,
        minimum_salary: Annotated[
            Decimal | None,
            Query(alias="min_salary", ge=0, le=MAX_SALARY_FILTER),
        ] = None,
    ) -> JobListResponse:
        query = _normalize_optional_filter(query, "q")
        employment_type = _normalize_optional_filter(employment_type, "employment_type")
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
            pattern = f"%{_escape_like(query)}%"
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
        listing_url = _canonical_listing_url()
        source_name = _canonical_source_name()
        source_display_name = _canonical_source_display_name()
        rows = (
            await session.execute(
                select(
                    Opportunity,
                    listing_url.label("source_url"),
                    source_name.label("source_name"),
                    source_display_name.label("source_display_name"),
                )
                .where(*filters)
                .order_by(Opportunity.published_at.desc().nullslast(), Opportunity.id.desc())
                .limit(limit)
                .offset(offset)
            )
        ).all()
        return JobListResponse(
            items=[
                _build_job_response(opportunity, source_url, source_name, source_display_name)
                for opportunity, source_url, source_name, source_display_name in rows
                if source_url is not None
                and source_name is not None
                and source_display_name is not None
            ],
            total=total or 0,
            limit=limit,
            offset=offset,
        )

    @application.get("/sources", response_model=list[SourceResponse], tags=["sources"])
    async def list_sources(
        session: Annotated[AsyncSession, Depends(request_session)],
        _: Annotated[None, Depends(require_api_access)],
    ) -> list[SourceResponse]:
        sources = (await session.scalars(select(Source).order_by(Source.name))).all()
        responses: list[SourceResponse] = []
        for item in sources:
            response = SourceResponse.model_validate(item)
            if response.last_error:
                response.last_error = redact_sensitive_text(response.last_error)
            responses.append(response)
        return responses

    @application.get("/matches", response_model=MatchListResponse, tags=["matches"])
    async def list_matches(
        session: Annotated[AsyncSession, Depends(request_session)],
        _: Annotated[None, Depends(require_api_access)],
        minimum_score: Annotated[int, Query(alias="min_score", ge=0, le=100)] = (
            selected_settings.matching_min_score
        ),
        source_filter: Annotated[
            str | None,
            Query(alias="source", min_length=1, max_length=100),
        ] = None,
        limit: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
        offset: int = Query(default=0, ge=0, le=MAX_OFFSET),
    ) -> MatchListResponse:
        selected_source_name = _normalize_optional_filter(source_filter, "source")
        if selected_source_name is not None:
            selected_source_name = selected_source_name.casefold()

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
                    *_active_listing_conditions(
                        selected_source_name,
                        opportunity_id=MatchEvaluation.opportunity_id,
                    ),
                )
            ),
        )
        total = await session.scalar(
            select(func.count()).select_from(MatchEvaluation).where(*filters)
        )
        listing_url = _canonical_listing_url(selected_source_name)
        source_name = _canonical_source_name(selected_source_name)
        source_display_name = _canonical_source_display_name(selected_source_name)
        rows = (
            await session.execute(
                select(
                    Opportunity,
                    MatchEvaluation,
                    listing_url.label("source_url"),
                    source_name.label("source_name"),
                    source_display_name.label("source_display_name"),
                )
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
        for opportunity, evaluation, source_url, source_name, source_display_name in rows:
            if source_url is None or source_name is None or source_display_name is None:
                continue
            job_data = _build_job_response(
                opportunity,
                source_url,
                source_name,
                source_display_name,
            ).model_dump()
            items.append(
                MatchResponse(
                    **job_data,
                    score=evaluation.score,
                    reasons=evaluation.reasons,
                    concerns=evaluation.concerns,
                    matched_skills=evaluation.matched_skills,
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


def _normalize_optional_filter(value: str | None, parameter_name: str) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if len(normalized) < 2:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Query parameter '{parameter_name}' must contain at least two characters.",
        )
    return normalized


def _active_listing_conditions(
    source_name: str | None,
    *,
    opportunity_id: object = Opportunity.id,
) -> tuple[ColumnElement[bool], ...]:
    conditions: list[ColumnElement[bool]] = [
        Listing.opportunity_id == opportunity_id,
        Listing.is_active.is_(True),
        Source.enabled.is_(True),
    ]
    if source_name is not None:
        conditions.append(Source.name == source_name)
    return tuple(conditions)


def _canonical_listing_url(source_name: str | None = None) -> ScalarSelect[str]:
    return (
        select(Listing.source_url)
        .join(Source, Source.id == Listing.source_id)
        .where(
            *_active_listing_conditions(source_name),
        )
        .order_by(*canonical_source_link_order())
        .limit(1)
        .scalar_subquery()
    )


def _canonical_source_name(source_name: str | None = None) -> ScalarSelect[str]:
    return (
        select(Source.name)
        .join(Listing, Listing.source_id == Source.id)
        .where(
            *_active_listing_conditions(source_name),
        )
        .order_by(*canonical_source_link_order())
        .limit(1)
        .scalar_subquery()
    )


def _canonical_source_display_name(
    source_name: str | None = None,
) -> ScalarSelect[str]:
    return (
        select(Source.display_name)
        .join(Listing, Listing.source_id == Source.id)
        .where(
            *_active_listing_conditions(source_name),
        )
        .order_by(*canonical_source_link_order())
        .limit(1)
        .scalar_subquery()
    )


def _build_job_response(
    opportunity: Opportunity,
    source_url: str,
    source_name: str,
    source_display_name: str,
) -> JobResponse:
    fields = {
        field_name: getattr(opportunity, field_name)
        for field_name in JobResponse.model_fields
        if field_name not in {"source_url", "source_name", "source_display_name"}
    }
    fields["source_url"] = source_url
    fields["source_name"] = source_name
    fields["source_display_name"] = source_display_name
    return JobResponse.model_validate(fields)
