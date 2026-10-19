from datetime import UTC, datetime

import pytest

from jobradar.domain.enums import OpportunityKind
from jobradar.notifications.localization import MATCH_EXPLANATIONS_RU, localize_match_explanation
from jobradar.notifications.service import NotificationCandidate, format_match_message


def notification_candidate(score: int, kind: OpportunityKind) -> NotificationCandidate:
    return NotificationCandidate(
        opportunity_id=1,
        kind=kind,
        title="Scraping Engineer (Internship)",
        company=None,
        location_text="Remote",
        employment_type=None,
        contract_type=None,
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        salary_period=None,
        first_seen_at=datetime.now(UTC),
        source_display_name="Djinni",
        source_url="https://djinni.co/jobs/854040-scraping-engineer-internship/",
        content_hash="a" * 64,
        score=score,
        reasons=("Office or hybrid work in Prague matches your location preference.",),
        concerns=(
            "Candidate residence restrictions are not verified yet; page metadata is pending.",
            "Optional technology outside your demonstrated stack: NestJS <script>.",
        ),
        raw_data={},
    )


@pytest.mark.parametrize("score", (34, 35, 36, 54, 55, 85))
@pytest.mark.parametrize("kind", (OpportunityKind.EMPLOYMENT, OpportunityKind.FREELANCE_PROJECT))
def test_explore_label_follows_score_without_an_extra_preamble(
    score: int, kind: OpportunityKind
) -> None:
    message = format_match_message(notification_candidate(score, kind))
    suffix = " (Explore)" if 35 <= score <= 54 else ""
    heading = "Вакансия" if kind is OpportunityKind.EMPLOYMENT else "Фриланс-проект"
    assert message.splitlines()[0] == f"<b>[Djinni] {heading}: {score}/100{suffix}</b>"
    assert "Explore: possible match" not in message
    assert "Candidate residence restrictions" not in message
    assert "Ограничения по стране проживания ещё не проверены" in message
    assert "ожидаем дополнительные данные со страницы вакансии." in message
    assert "Офисный или гибридный формат в Праге" in message
    assert (
        "Необязательная технология вне вашего подтверждённого стека: NestJS &lt;script&gt;."
        in message
    )
    assert "<script>" not in message
    assert "Scraping Engineer (Internship)" in message


@pytest.mark.parametrize("original,translated", MATCH_EXPLANATIONS_RU.items())
def test_known_service_explanations_are_localized(original: str, translated: str) -> None:
    assert localize_match_explanation(original) == translated
    assert original != translated


@pytest.mark.parametrize("value", ("Совпавшие навыки: Python, PostgreSQL.", "Unknown warning <b>"))
def test_existing_russian_and_unknown_explanations_are_preserved(value: str) -> None:
    assert localize_match_explanation(value) == value
