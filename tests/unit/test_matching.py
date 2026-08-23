from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobradar.db.models import MatchEvaluation
from jobradar.domain.enums import OpportunityKind, WorkMode
from jobradar.ingestion.service import IngestionService
from jobradar.matching.profile import BOHDAN_PROFILE
from jobradar.matching.sanity import evaluate_sanity, monthly_salary_usd
from jobradar.matching.scorer import MatchCandidate, score_candidate
from jobradar.matching.service import MatchingService
from jobradar.sources.mock import MockSource


def _candidate(**changes: object) -> MatchCandidate:
    values: dict[str, object] = {
        "kind": OpportunityKind.EMPLOYMENT,
        "title": "Junior Full-Stack Developer",
        "company": "Example Labs",
        "description": "Build React interfaces and Django REST APIs with PostgreSQL.",
        "location_text": "Europe",
        "work_mode": WorkMode.REMOTE,
        "employment_type": "full_time",
        "contract_type": None,
        "salary_min": Decimal("1200"),
        "salary_max": Decimal("1800"),
        "salary_currency": "USD",
        "salary_period": "month",
        "raw_data": {
            "experienceRequirements": {
                "monthsOfExperience": 12,
            }
        },
    }
    values.update(changes)
    return MatchCandidate(**values)  # type: ignore[arg-type]


def _freelance_candidate(**changes: object) -> MatchCandidate:
    values: dict[str, object] = {
        "kind": OpportunityKind.FREELANCE_PROJECT,
        "title": "Django REST API and React dashboard integration",
        "company": "Verified Employer",
        "description": "Build a small dashboard and webhook integration with PostgreSQL.",
        "location_text": "Remote",
        "work_mode": WorkMode.REMOTE,
        "employment_type": None,
        "contract_type": "fixed",
        "salary_min": Decimal("300"),
        "salary_max": Decimal("600"),
        "salary_currency": "USD",
        "salary_period": "project",
        "raw_data": {
            "currency": {"code": "USD", "exchange_rate": 1.0},
            "language": "en",
            "jobs": [
                {"name": "Django"},
                {"name": "React.js"},
                {"name": "PostgreSQL"},
            ],
            "bid_stats": {"bid_count": 12},
            "_owner": {
                "status": {"payment_verified": True},
                "employer_reputation": {"entire_history": {"overall": 4.8, "reviews": 24}},
            },
        },
    }
    values.update(changes)
    return MatchCandidate(**values)  # type: ignore[arg-type]


def test_matching_profile_scores_relevant_junior_role_highly() -> None:
    result = score_candidate(_candidate(), BOHDAN_PROFILE)

    assert result.score == 100
    assert any("Совпавшие навыки" in reason for reason in result.reasons)
    assert result.concerns == ()


def test_matching_profile_rejects_non_remote_role() -> None:
    result = score_candidate(_candidate(work_mode=WorkMode.ONSITE), BOHDAN_PROFILE)

    assert result.score == 0
    assert result.reasons == ()
    assert "не обозначена как удалённая" in result.concerns[0]


@pytest.mark.parametrize(
    "trigger",
    (
        "волонтер",
        "volunteer",
        "бесплатно",
        "безкоштовно",
        "unpaid",
    ),
)
@pytest.mark.parametrize("kind", (OpportunityKind.EMPLOYMENT, OpportunityKind.FREELANCE_PROJECT))
def test_matching_profile_hard_rejects_unpaid_work(
    trigger: str,
    kind: OpportunityKind,
) -> None:
    candidate = (
        _candidate(description=f"React and Django role. {trigger} project.")
        if kind is OpportunityKind.EMPLOYMENT
        else _freelance_candidate(description=f"React and Django task. {trigger} project.")
    )

    result = score_candidate(candidate, BOHDAN_PROFILE)

    assert result.score == 0
    assert result.reasons == ()
    assert "неоплачиваемая" in result.concerns[0]


@pytest.mark.parametrize(
    "benefit",
    (
        "One paid volunteering day each year.",
        "One day off for volunteering.",
        "Волонтерский день как дополнительный выходной.",
        "День для волонтерства как часть социального пакета.",
    ),
)
def test_matching_profile_does_not_reject_volunteering_leave_benefit(benefit: str) -> None:
    result = score_candidate(
        _candidate(description=f"Full-time React and Django role. Benefits: {benefit}"),
        BOHDAN_PROFILE,
    )

    assert result.score > 0
    assert not any("неоплачиваемая" in concern for concern in result.concerns)


@pytest.mark.parametrize(
    "trigger",
    (
        "ЗСУ",
        "мобилизация",
        "мобілізація",
        "ТЦК",
        "сили оборони",
        "defense forces",
        "military",
        "армія",
        "miltech",
        "defence technology",
        "збройні сили",
        "військове програмне забезпечення",
        "оборонний сектор",
    ),
)
@pytest.mark.parametrize("kind", (OpportunityKind.EMPLOYMENT, OpportunityKind.FREELANCE_PROJECT))
def test_matching_profile_hard_rejects_military_recruiting(
    trigger: str,
    kind: OpportunityKind,
) -> None:
    candidate = (
        _candidate(company=trigger)
        if kind is OpportunityKind.EMPLOYMENT
        else _freelance_candidate(company=trigger)
    )

    result = score_candidate(candidate, BOHDAN_PROFILE)

    assert result.score == 0
    assert result.reasons == ()
    assert "военный рекрутинг" in result.concerns[0]


def test_matching_profile_penalizes_senior_experience() -> None:
    result = score_candidate(
        _candidate(
            title="Senior React Developer",
            raw_data={"experienceRequirements": {"monthsOfExperience": 60}},
        ),
        BOHDAN_PROFILE,
    )

    assert result.score < BOHDAN_PROFILE.notification_threshold
    assert any("Senior" in concern for concern in result.concerns)
    assert any("значительно превышает" in concern for concern in result.concerns)


def test_matching_profile_penalizes_unrelated_specialization() -> None:
    result = score_candidate(
        _candidate(title="UI/UX Designer", description="Design interfaces in Figma."),
        BOHDAN_PROFILE,
    )

    assert result.score < BOHDAN_PROFILE.notification_threshold
    assert any("Специализация" in concern for concern in result.concerns)


@pytest.mark.parametrize(
    ("field", "marker"),
    (
        ("location_text", "US Only"),
        ("description", "This remote role is United States Only."),
        ("description", "Candidates must reside in US."),
    ),
)
def test_sanity_check_rejects_us_only_remote_roles(field: str, marker: str) -> None:
    result = score_candidate(_candidate(**{field: marker}), BOHDAN_PROFILE)

    assert result.score == 0
    assert result.reasons == ()
    assert "только кандидатам из США" in result.concerns[0]


@pytest.mark.parametrize("marker", ("equity only", "unpaid startup", "profit share"))
def test_sanity_check_rejects_equity_work_without_base_salary(marker: str) -> None:
    result = score_candidate(
        _candidate(
            description=f"Build a React product for {marker}.",
            salary_min=None,
            salary_max=None,
            salary_currency=None,
            salary_period=None,
        ),
        BOHDAN_PROFILE,
    )

    assert result.score == 0
    assert "без базовой зарплаты" in result.concerns[0]


def test_sanity_check_allows_profit_share_with_base_salary() -> None:
    result = score_candidate(
        _candidate(description="Base salary plus profit share for a React developer."),
        BOHDAN_PROFILE,
    )

    assert result.score > 0
    assert not any("без базовой зарплаты" in concern for concern in result.concerns)


@pytest.mark.parametrize(
    ("minimum", "maximum", "currency", "period"),
    (
        ("4001", "5000", "USD", "month"),
        ("48012", "60000", "USD", "year"),
        ("85001", "100000", "CZK", "month"),
    ),
)
def test_sanity_check_penalizes_excessive_base_salary(
    minimum: str,
    maximum: str,
    currency: str,
    period: str,
) -> None:
    sanity = evaluate_sanity(
        _candidate(
            salary_min=Decimal(minimum),
            salary_max=Decimal(maximum),
            salary_currency=currency,
            salary_period=period,
        ),
        BOHDAN_PROFILE,
    )

    assert sanity.score_adjustment == -20
    assert "Middle или Senior" in sanity.concerns[0]


def test_sanity_check_keeps_exact_salary_boundaries_unpenalized() -> None:
    ceiling = evaluate_sanity(
        _candidate(
            salary_min=Decimal("4000"),
            salary_max=Decimal("5000"),
        ),
        BOHDAN_PROFILE,
    )
    floor = evaluate_sanity(
        _candidate(
            salary_min=Decimal("300"),
            salary_max=Decimal("400"),
        ),
        BOHDAN_PROFILE,
    )

    assert ceiling.score_adjustment == 0
    assert floor.score_adjustment == 0


@pytest.mark.parametrize(
    ("maximum", "period"),
    (("399", "month"), ("4788", "year")),
)
def test_sanity_check_penalizes_very_low_full_time_salary(
    maximum: str,
    period: str,
) -> None:
    sanity = evaluate_sanity(
        _candidate(
            salary_min=Decimal("300" if period == "month" else "3600"),
            salary_max=Decimal(maximum),
            salary_period=period,
        ),
        BOHDAN_PROFILE,
    )

    assert sanity.score_adjustment == -15
    assert "ниже USD 400" in sanity.concerns[0]


def test_sanity_check_does_not_apply_low_salary_penalty_to_part_time_role() -> None:
    sanity = evaluate_sanity(
        _candidate(
            employment_type="part_time",
            salary_min=Decimal("200"),
            salary_max=Decimal("300"),
        ),
        BOHDAN_PROFILE,
    )

    assert sanity.score_adjustment == 0


@pytest.mark.parametrize("marker", ("rockstar developer", "code ninja", "10x engineer"))
def test_sanity_check_applies_one_toxic_language_penalty(marker: str) -> None:
    sanity = evaluate_sanity(
        _candidate(description=f"We need a {marker} for our React team."),
        BOHDAN_PROFILE,
    )

    assert sanity.score_adjustment == -5
    assert "токсичный маркер" in sanity.concerns[0]


def test_monthly_salary_normalization_uses_period_and_currency() -> None:
    minimum, maximum = monthly_salary_usd(
        _candidate(
            salary_min=Decimal("48000"),
            salary_max=Decimal("60000"),
            salary_currency="USD",
            salary_period="year",
        )
    )

    assert minimum == Decimal("4000")
    assert maximum == Decimal("5000")


def test_freelance_profile_scores_relevant_project_highly() -> None:
    result = score_candidate(_freelance_candidate(), BOHDAN_PROFILE)

    assert result.score >= 80
    assert any("Совпавшие навыки" in reason for reason in result.reasons)
    assert any("Фиксированный бюджет" in reason for reason in result.reasons)
    assert any("Приемлемая конкуренция" in reason for reason in result.reasons)
    assert any("платёжные данные" in reason for reason in result.reasons)


@pytest.mark.parametrize(
    "description",
    (
        "We want to rent account for client bidding.",
        "Join our Upwork agency to receive this project.",
        "We will be bidding on your behalf using your profile.",
    ),
)
def test_freelance_profile_rejects_account_rental_variants(description: str) -> None:
    result = score_candidate(_freelance_candidate(description=description), BOHDAN_PROFILE)

    assert result.score == 0
    assert "аккаунту платформы" in result.concerns[0]


def test_freelance_profile_does_not_penalize_normal_agency_word() -> None:
    result = score_candidate(
        _freelance_candidate(
            company="Normal Web Agency",
            description="A web agency needs a Django and React dashboard integration.",
        ),
        BOHDAN_PROFILE,
    )

    assert result.score > 0
    assert not any("аккаунту платформы" in concern for concern in result.concerns)


@pytest.mark.parametrize(
    "description",
    (
        "Contact me on Telegram before bidding so we can arrange payment.",
        "You must pay a registration fee before the project starts.",
        "Provide your Upwork account and password for this task.",
        "Complete an unpaid trial before a milestone is created.",
    ),
)
def test_freelance_profile_rejects_scam_patterns(description: str) -> None:
    result = score_candidate(_freelance_candidate(description=description), BOHDAN_PROFILE)

    assert result.score == 0
    assert result.reasons == ()
    assert result.concerns[0].startswith("Отклонено:")


def test_freelance_profile_penalizes_low_budget_and_extreme_competition() -> None:
    raw_data = {
        "currency": {"code": "USD", "exchange_rate": 1.0},
        "jobs": [{"name": "React.js"}],
        "bid_stats": {"bid_count": 250},
    }
    result = score_candidate(
        _freelance_candidate(
            title="Small React task",
            description="Fix a React component.",
            salary_min=Decimal("5"),
            salary_max=Decimal("20"),
            raw_data=raw_data,
        ),
        BOHDAN_PROFILE,
    )

    assert result.score < BOHDAN_PROFILE.notification_threshold
    assert any("очень низкий" in concern for concern in result.concerns)
    assert any("Экстремально" in concern for concern in result.concerns)


def test_freelance_profile_converts_api_currency_rate_to_usd() -> None:
    raw_data = {
        "currency": {"code": "INR", "exchange_rate": 0.010449},
        "jobs": [{"name": "Django"}],
        "bid_stats": {"bid_count": 30},
    }
    result = score_candidate(
        _freelance_candidate(
            salary_min=Decimal("1500"),
            salary_max=Decimal("12500"),
            salary_currency="INR",
            raw_data=raw_data,
        ),
        BOHDAN_PROFILE,
    )

    assert any("USD 15.67-130.61" in concern for concern in result.concerns)


def test_freelance_profile_rejects_unsupported_project_language() -> None:
    raw_data = {
        "language": "fr",
        "currency": {"code": "USD", "exchange_rate": 1.0},
        "jobs": [{"name": "Django"}],
        "bid_stats": {"bid_count": 5},
    }
    result = score_candidate(_freelance_candidate(raw_data=raw_data), BOHDAN_PROFILE)

    assert result.score == 0
    assert "язык проекта не поддерживается" in result.concerns[0]


def test_freelance_profile_penalizes_broad_mobile_scope() -> None:
    raw_data = {
        "language": "en",
        "currency": {"code": "USD", "exchange_rate": 1.0},
        "jobs": [{"name": "React Native"}, {"name": "Mobile App Development"}],
        "bid_stats": {"bid_count": 40},
    }
    result = score_candidate(
        _freelance_candidate(
            title="React Native marketplace from scratch",
            description="Build a production-ready mobile application and website.",
            raw_data=raw_data,
        ),
        BOHDAN_PROFILE,
    )

    assert result.score < BOHDAN_PROFILE.notification_threshold
    assert any("технологии вне основного профиля" in concern for concern in result.concerns)
    assert any("объём слишком велик" in concern for concern in result.concerns)


@pytest.mark.asyncio
async def test_matching_service_is_idempotent_for_unchanged_content(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await IngestionService(sqlite_session_factory).run_source(MockSource())
    service = MatchingService(sqlite_session_factory)

    first = await service.evaluate(BOHDAN_PROFILE)
    second = await service.evaluate(BOHDAN_PROFILE)

    assert first.evaluated == 2
    assert first.unchanged == 0
    assert second.evaluated == 0
    assert second.unchanged == 2
    async with sqlite_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(MatchEvaluation)) == 2
