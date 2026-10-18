import re
from dataclasses import replace
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobradar.api.app import create_app
from jobradar.api.explanations import english_explanation
from jobradar.db.models import MatchEvaluation
from jobradar.domain.enums import OpportunityKind, WorkMode
from jobradar.ingestion.service import IngestionService
from jobradar.matching.models import MatchCandidate
from jobradar.matching.profile import BOHDAN_PROFILE
from jobradar.matching.scorer import score_candidate
from jobradar.matching.service import MatchingService
from jobradar.sources.mock import MockSource


@pytest.mark.parametrize(
    ("stored", "expected"),
    (
        (
            "Вакансия включает технологии вне профиля: NestJS, Java. Применён штраф 15 баллов.",
            "The role includes technologies outside your profile: NestJS, Java. "
            "A 15-point penalty applies.",
        ),
        (
            "Направление «Full-stack разработка» соответствует целевому профилю.",
            "The Full-stack development role matches your target profile.",
        ),
        (
            "Отклонено: вакансия требует рабочее владение языком: немецкий.",
            "Rejected: the role requires working proficiency in German.",
        ),
        ("Совпавшие навыки: React, Python.", "Matched skills: React, Python."),
        ("Приемлемая конкуренция: 12 ставок.", "Acceptable competition: 12 bids."),
        ("Рейтинг заказчика 4.8/5 на основе 24 отзывов.", "Client rating: 4.8/5 from 24 reviews."),
        (
            "Already English; preserve this explanation.",
            "Already English; preserve this explanation.",
        ),
        ("An unknown <script>message</script>", "An unknown <script>message</script>"),
        ("Незнакомое пояснение", "Незнакомое пояснение"),
    ),
)
def test_explanations_preserve_details_and_unknown_messages(stored: str, expected: str) -> None:
    assert english_explanation(stored) == expected
    assert english_explanation(expected) == expected


@pytest.mark.parametrize(
    ("title", "description"),
    (
        ("Junior Full-stack Developer", "React and Python. Requirements: 1 year of experience."),
        ("Software Developer", "React and Python. Requirements: 2 years of experience and NestJS."),
        ("Junior Frontend Developer", "React required. Nice to have: PHP."),
        ("Senior Developer", "Python required."),
        ("Software Developer", "Requirements: German fluency. React required."),
        ("Developer", "Remote work restricted to candidates based in Canada only."),
    ),
)
def test_current_score_explanations_have_english_presentation(title: str, description: str) -> None:
    candidate = MatchCandidate(
        kind=OpportunityKind.EMPLOYMENT,
        title=title,
        company="Example",
        description=description,
        location_text="Europe",
        work_mode=WorkMode.REMOTE,
        employment_type="full_time",
        contract_type=None,
        salary_min=Decimal("1200"),
        salary_max=Decimal("1800"),
        salary_currency="USD",
        salary_period="month",
        raw_data={},
    )
    result = score_candidate(candidate, BOHDAN_PROFILE)
    for text in (*result.reasons, *result.concerns):
        assert not re.search(r"[А-Яа-яЁё]", english_explanation(text))
    assert score_candidate(replace(candidate), BOHDAN_PROFILE) == result


async def test_matches_translate_on_read_without_mutating_evaluations_or_rescoring(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await IngestionService(sqlite_session_factory).run_source(MockSource())
    await MatchingService(sqlite_session_factory).evaluate(BOHDAN_PROFILE)
    original_reasons = ["Совпавшие навыки: React."]
    original_concerns = [
        "Вакансия включает технологии вне профиля: NestJS. Применён штраф 15 баллов.",
        "The role requests about two years of experience; treat it as a stretch.",
    ]
    async with sqlite_session_factory() as session, session.begin():
        evaluation = (await session.scalars(select(MatchEvaluation))).first()
        assert evaluation is not None
        evaluation.reasons = original_reasons.copy()
        evaluation.concerns = original_concerns.copy()
        evaluation.score = 54
        evaluation_id = evaluation.id
        opportunity_id = evaluation.opportunity_id
        rules_version = evaluation.rules_version
    async with AsyncClient(
        transport=ASGITransport(app=create_app(sqlite_session_factory)), base_url="http://test"
    ) as client:
        response = await client.get("/matches", params={"min_score": 0})
    assert response.status_code == 200
    item = next(item for item in response.json()["items"] if item["id"] == opportunity_id)
    assert item["reasons"] == ["Matched skills: React."]
    assert item["concerns"][0].endswith("A 15-point penalty applies.")
    assert item["concerns"][1] == original_concerns[1]
    assert item["score"] == 54
    assert item["rules_version"] == rules_version
    async with sqlite_session_factory() as session:
        persisted = await session.get(MatchEvaluation, evaluation_id)
        assert persisted is not None
        assert persisted.reasons == original_reasons
        assert persisted.concerns == original_concerns
        assert persisted.score == 54
