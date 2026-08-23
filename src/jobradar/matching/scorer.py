import re
from typing import Any

from jobradar.domain.enums import OpportunityKind, WorkMode
from jobradar.domain.normalization import normalize_text
from jobradar.matching.freelance import score_freelance_candidate
from jobradar.matching.models import MatchCandidate as MatchCandidate
from jobradar.matching.models import ScoreResult as ScoreResult
from jobradar.matching.profile import SearchProfile
from jobradar.matching.rejections import hard_rejection_concern
from jobradar.matching.sanity import evaluate_sanity, monthly_salary_usd

SENIOR_TITLE_TERMS = ("senior", "sr.", "lead", "principal", "staff", "head", "architect")
JUNIOR_TITLE_TERMS = ("junior", "jr.", "trainee", "intern", "graduate", "entry level")
INCOMPATIBLE_TITLE_TERMS = (
    "designer",
    "qa engineer",
    "quality assurance",
    "devops",
    "seo",
    "recruiter",
    "sales",
    "marketing",
    "account manager",
    "project manager",
    "product manager",
    "data scientist",
    "machine learning",
    "embedded",
    "hardware",
    "vlsi",
    "blockchain",
)
ROLE_GROUPS = (
    ("Full-stack разработка", ("full stack", "fullstack", "full-stack"), 20),
    (
        "Front-end разработка",
        ("front end", "frontend", "front-end", "react developer", "javascript developer"),
        20,
    ),
    ("Back-end разработка на Python", ("python developer", "django developer"), 18),
    ("Веб-разработка", ("web developer", "software developer", "software engineer"), 10),
)


def score_candidate(candidate: MatchCandidate, profile: SearchProfile) -> ScoreResult:
    sanity = evaluate_sanity(candidate, profile)
    if sanity.rejection_concern is not None:
        return ScoreResult(score=0, reasons=(), concerns=(sanity.rejection_concern,))

    rejection_concern = hard_rejection_concern(candidate)
    if rejection_concern is not None:
        return ScoreResult(score=0, reasons=(), concerns=(rejection_concern,))

    if candidate.kind is OpportunityKind.FREELANCE_PROJECT:
        result = score_freelance_candidate(candidate, profile)
        if result.score == 0:
            return result
        return ScoreResult(
            score=max(0, min(result.score + sanity.score_adjustment, 100)),
            reasons=result.reasons,
            concerns=result.concerns + sanity.concerns,
        )

    if candidate.work_mode is not WorkMode.REMOTE:
        return ScoreResult(
            score=0,
            reasons=(),
            concerns=("Отклонено: вакансия не обозначена как удалённая.",),
        )

    score = 20
    reasons = ["Удалённый формат соответствует обязательному требованию."]
    concerns = list(sanity.concerns)
    title = normalize_text(candidate.title)
    searchable_text = normalize_text(
        " ".join(
            value
            for value in (
                candidate.title,
                candidate.company,
                candidate.description,
                _structured_search_text(candidate.raw_data),
            )
            if value
        )
    )

    if _contains_any(title, INCOMPATIBLE_TITLE_TERMS):
        score -= 60
        concerns.append("Специализация вакансии не соответствует профилю разработчика.")

    if _contains_any(title, SENIOR_TITLE_TERMS):
        score -= 40
        concerns.append("Вакансия рассчитана на уровень Senior, Lead или Architect.")
    elif _contains_any(title, JUNIOR_TITLE_TERMS):
        score += 12
        reasons.append("Вакансия явно рассчитана на Junior или начинающего специалиста.")
    else:
        score += _seniority_adjustment(candidate.raw_data, reasons, concerns)

    for label, aliases, weight in ROLE_GROUPS:
        if _contains_any(title, aliases):
            score += weight
            reasons.append(f"Направление «{label}» соответствует целевому профилю.")
            break

    matched_skills: list[str] = []
    skill_score = 0
    for skill in profile.skills:
        if _contains_any(searchable_text, skill.aliases):
            matched_skills.append(skill.name)
            skill_score += skill.weight
    if matched_skills:
        score += min(skill_score, 45)
        reasons.append(f"Совпавшие навыки: {', '.join(matched_skills)}.")
    else:
        concerns.append("Не найдены ключевые слова целевого технологического стека.")

    employment_type = normalize_text(candidate.employment_type)
    if employment_type == "full_time":
        score += 5
        reasons.append("Полная занятость соответствует предпочтительному формату работы.")
    elif employment_type in {"part_time", "contractor", "temporary"}:
        score += 2
        reasons.append("Формат занятости подходит как альтернативный вариант для старта.")

    score += sanity.score_adjustment
    score += _experience_adjustment(candidate.raw_data, reasons, concerns)
    score += _salary_adjustment(candidate, profile, reasons, concerns)
    score += _language_adjustment(searchable_text, concerns)

    return ScoreResult(
        score=max(0, min(score, 100)),
        reasons=tuple(reasons),
        concerns=tuple(concerns),
    )


def _experience_adjustment(
    raw_data: dict[str, Any],
    reasons: list[str],
    concerns: list[str],
) -> int:
    requirements = raw_data.get("experienceRequirements")
    if not isinstance(requirements, dict):
        return 0
    months = requirements.get("monthsOfExperience")
    if not isinstance(months, int | float):
        return 0
    if months <= 12:
        reasons.append("Требуемый опыт не превышает одного года.")
        return 10
    if months <= 24:
        reasons.append("Требуемый опыт находится в достижимом диапазоне для Junior.")
        return 5
    if months <= 36:
        concerns.append("Вакансия требует около трёх лет опыта.")
        return -10
    concerns.append("Требуемый опыт значительно превышает уровень Junior.")
    return -25


def _seniority_adjustment(
    raw_data: dict[str, Any],
    reasons: list[str],
    concerns: list[str],
) -> int:
    seniority = {
        normalize_text(value).replace("_", " ").replace("-", " ")
        for value in (
            *_string_values(raw_data.get("seniority")),
            *_string_values(raw_data.get("levels")),
        )
    }
    if seniority & {"senior", "senior level", "manager", "management", "director", "executive"}:
        concerns.append("Структурированный уровень вакансии рассчитан на Senior или выше.")
        return -40
    if seniority & {"mid", "mid level", "middle"}:
        concerns.append("Структурированный уровень вакансии указан как Middle.")
        return -10
    if seniority & {"entry", "entry level", "junior", "internship"}:
        reasons.append("Структурированный уровень вакансии подходит начинающему специалисту.")
        return 12
    return 0


def _structured_search_text(raw_data: dict[str, Any]) -> str:
    values: list[str] = []
    for key in ("categories", "parentCategories", "tags", "skills"):
        values.extend(_string_values(raw_data.get(key)))
    return " ".join(values)


def _string_values(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, dict):
        return tuple(
            item
            for key in ("name", "short_name", "label", "title", "slug")
            if isinstance((item := value.get(key)), str) and item.strip()
        )
    if not isinstance(value, list | tuple | set):
        return ()
    return tuple(item for child in value for item in _string_values(child))


def _salary_adjustment(
    candidate: MatchCandidate,
    profile: SearchProfile,
    reasons: list[str],
    concerns: list[str],
) -> int:
    minimum_monthly_usd, maximum_monthly_usd = monthly_salary_usd(candidate)
    upper_bound = maximum_monthly_usd or minimum_monthly_usd
    if upper_bound is None:
        return 0
    if upper_bound >= profile.minimum_monthly_salary_usd:
        reasons.append("Указанная зарплата достигает предпочтительного месячного уровня.")
        return 5
    concerns.append("Указанная зарплата ниже предпочтительного месячного уровня.")
    return -10


def _language_adjustment(searchable_text: str, concerns: list[str]) -> int:
    if re.search(r"english\s*(?:-|:)?\s*(?:c1|c2|advanced|fluent)", searchable_text):
        concerns.append("Требуемый уровень английского выше текущего уровня B1.")
        return -10
    if re.search(r"english\s*(?:-|:)?\s*b2", searchable_text):
        concerns.append("Вакансия требует английский B2, а в профиле указан уровень B1.")
        return -5
    return 0


def _contains_any(value: str, terms: tuple[str, ...]) -> bool:
    return any(
        re.search(rf"(?<!\w){re.escape(normalize_text(term))}(?!\w)", value) is not None
        for term in terms
    )
