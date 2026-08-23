from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from jobradar.db.base import Base, TimestampMixin, utc_now
from jobradar.db.types import JSON_TYPE, PRIMARY_KEY_TYPE
from jobradar.domain.enums import (
    DeliveryStatus,
    OpportunityDisposition,
    OpportunityKind,
    OpportunityStatus,
    RunStatus,
    WorkMode,
)


class Source(TimestampMixin, Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(PRIMARY_KEY_TYPE, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    opportunity_kind: Mapped[str] = mapped_column(
        String(50),
        default=OpportunityKind.EMPLOYMENT.value,
        nullable=False,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)

    runs: Mapped[list["SourceRun"]] = relationship(back_populates="source")
    listings: Mapped[list["Listing"]] = relationship(back_populates="source")


class SourceRun(Base):
    __tablename__ = "source_runs"

    id: Mapped[int] = mapped_column(PRIMARY_KEY_TYPE, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(30),
        default=RunStatus.RUNNING.value,
        nullable=False,
        index=True,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    discovered_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    unchanged_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    deactivated_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)

    source: Mapped[Source] = relationship(back_populates="runs")


class Opportunity(TimestampMixin, Base):
    __tablename__ = "opportunities"

    id: Mapped[int] = mapped_column(PRIMARY_KEY_TYPE, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(
        String(50),
        default=OpportunityKind.EMPLOYMENT.value,
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(30),
        default=OpportunityStatus.ACTIVE.value,
        nullable=False,
        index=True,
    )
    canonical_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    company: Mapped[str | None] = mapped_column(String(500), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    location_text: Mapped[str | None] = mapped_column(String(500))
    work_mode: Mapped[str] = mapped_column(
        String(30),
        default=WorkMode.UNKNOWN.value,
        nullable=False,
        index=True,
    )
    employment_type: Mapped[str | None] = mapped_column(String(100))
    contract_type: Mapped[str | None] = mapped_column(String(100))
    salary_min: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    salary_max: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    salary_currency: Mapped[str | None] = mapped_column(String(3))
    salary_period: Mapped[str | None] = mapped_column(String(50))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        index=True,
    )

    listings: Mapped[list["Listing"]] = relationship(back_populates="opportunity")
    evaluations: Mapped[list["MatchEvaluation"]] = relationship(back_populates="opportunity")
    deliveries: Mapped[list["NotificationDelivery"]] = relationship(back_populates="opportunity")
    telegram_messages: Mapped[list["TelegramOpportunityMessage"]] = relationship(
        back_populates="opportunity",
        cascade="all, delete-orphan",
    )
    user_state: Mapped["OpportunityUserState | None"] = relationship(
        back_populates="opportunity",
        cascade="all, delete-orphan",
        uselist=False,
    )


class Listing(TimestampMixin, Base):
    __tablename__ = "listings"
    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_listings_source_external_id"),
        Index("ix_listings_source_canonical_url", "source_id", "canonical_url"),
    )

    id: Mapped[int] = mapped_column(PRIMARY_KEY_TYPE, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    opportunity_id: Mapped[int] = mapped_column(
        ForeignKey("opportunities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, nullable=False)
    normalized_data: Mapped[dict[str, Any] | None] = mapped_column(JSON_TYPE)
    quality_score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    detail_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        index=True,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    source: Mapped[Source] = relationship(back_populates="listings")
    opportunity: Mapped[Opportunity] = relationship(back_populates="listings")


class MatchEvaluation(Base):
    __tablename__ = "match_evaluations"
    __table_args__ = (
        UniqueConstraint(
            "opportunity_id",
            "profile_id",
            "rules_version",
            name="uq_match_evaluations_opportunity_profile_rules",
        ),
    )

    id: Mapped[int] = mapped_column(PRIMARY_KEY_TYPE, primary_key=True, autoincrement=True)
    opportunity_id: Mapped[int] = mapped_column(
        ForeignKey("opportunities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    profile_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    rules_version: Mapped[str] = mapped_column(String(64), nullable=False)
    listing_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    reasons: Mapped[list[str]] = mapped_column(JSON_TYPE, default=list, nullable=False)
    concerns: Mapped[list[str]] = mapped_column(JSON_TYPE, default=list, nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    opportunity: Mapped[Opportunity] = relationship(back_populates="evaluations")


class NotificationDelivery(Base):
    __tablename__ = "notification_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "opportunity_id",
            "profile_id",
            "channel",
            "event_key",
            name="uq_notification_deliveries_event",
        ),
    )

    id: Mapped[int] = mapped_column(PRIMARY_KEY_TYPE, primary_key=True, autoincrement=True)
    opportunity_id: Mapped[int] = mapped_column(
        ForeignKey("opportunities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    profile_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(50), nullable=False)
    event_key: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30),
        default=DeliveryStatus.PENDING.value,
        nullable=False,
        index=True,
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    opportunity: Mapped[Opportunity] = relationship(back_populates="deliveries")


class NotificationPreference(TimestampMixin, Base):
    __tablename__ = "notification_preferences"

    profile_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    channel: Mapped[str] = mapped_column(String(50), primary_key=True)
    is_paused: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OpportunityUserState(TimestampMixin, Base):
    __tablename__ = "opportunity_user_states"

    opportunity_id: Mapped[int] = mapped_column(
        ForeignKey("opportunities.id", ondelete="CASCADE"),
        primary_key=True,
    )
    disposition: Mapped[str] = mapped_column(
        String(30),
        default=OpportunityDisposition.NEW.value,
        nullable=False,
        index=True,
    )

    opportunity: Mapped[Opportunity] = relationship(back_populates="user_state")


class TelegramOpportunityMessage(TimestampMixin, Base):
    __tablename__ = "telegram_opportunity_messages"

    id: Mapped[int] = mapped_column(PRIMARY_KEY_TYPE, primary_key=True, autoincrement=True)
    opportunity_id: Mapped[int] = mapped_column(
        ForeignKey("opportunities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    telegram_message_id: Mapped[int] = mapped_column(
        PRIMARY_KEY_TYPE,
        nullable=False,
        unique=True,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    opportunity: Mapped[Opportunity] = relationship(back_populates="telegram_messages")
