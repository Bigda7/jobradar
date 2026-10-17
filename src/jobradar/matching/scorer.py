import re
from typing import Any

from jobradar.domain.enums import OpportunityKind, WorkMode
from jobradar.domain.normalization import normalize_text
from jobradar.matching.freelance import score_freelance_candidate
from jobradar.matching.locations import evaluate_location_eligibility
from jobradar.matching.models import MatchCandidate as MatchCandidate
from jobradar.matching.models import ScoreResult as ScoreResult
from jobradar.matching.profile import SearchProfile
from jobradar.matching.rejections import (
    hard_rejection_concern,
    has_junior_title,
    required_experience_years,
)
from jobradar.matching.requirements import (
    Evidence,
    RequirementContext,
    mention_evidence,
    role_title,
)
from jobradar.matching.requirements import (
    string_values as _string_values,
)
from jobradar.matching.sanity import evaluate_sanity, monthly_salary_usd
from jobradar.notifications.currency import ExchangeRates

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
    "embedded",
    "hardware",
    "vlsi",
)
ROLE_GROUPS = (
    ("Full-stack разработка", ("full stack", "fullstack", "full-stack"), 20),
    (
        "Front-end разработка",
        (
            "front end",
            "frontend",
            "front-end",
            "react developer",
            "javascript developer",
            "next.js developer",
            "nextjs developer",
        ),
        20,
    ),
    (
        "Backend development",
        (
            "python developer",
            "django developer",
            "fastapi developer",
            "node.js developer",
            "nodejs developer",
            "backend",
            "back-end",
            "back end",
            "express developer",
        ),
        18,
    ),
    (
        "API integration and automation",
        (
            "api integration",
            "integration developer",
            "automation developer",
            "web scraping developer",
        ),
        16,
    ),
    ("Веб-разработка", ("web developer", "software developer", "software engineer"), 10),
)
CZECH_DESCRIPTION_MARKERS = (
    "nabízíme",
    "požadujeme",
    "pracovní pozice",
    "pracovní doba",
    "místo výkonu",
    "zkušenosti",
    "odpovědnosti",
    "výhodou",
)
SUSPICIOUS_APPLICATION_URL_PATTERN = re.compile(
    r"(?i)(?:job-seekers?/account/register|/(?:pricing|subscription|membership|"
    r"upgrade|checkout|payment)(?:[/?#]|$))"
)


def score_candidate(
    candidate: MatchCandidate,
    profile: SearchProfile,
    rates: ExchangeRates | None = None,
) -> ScoreResult:
    sanity = evaluate_sanity(candidate, profile, rates)
    if sanity.rejection_concern is not None:
        return ScoreResult(score=0, reasons=(), concerns=(sanity.rejection_concern,))

    rejection_concern = hard_rejection_concern(candidate)
    if rejection_concern is not None:
        return ScoreResult(score=0, reasons=(), concerns=(rejection_concern,))

    location = evaluate_location_eligibility(candidate, profile)
    if location.rejection_concern is not None:
        return ScoreResult(score=0, reasons=(), concerns=(location.rejection_concern,))

    if candidate.kind is OpportunityKind.FREELANCE_PROJECT:
        result = score_freelance_candidate(candidate, profile)
        if result.score == 0:
            return result
        return ScoreResult(
            score=max(0, min(result.score + sanity.score_adjustment, 100)),
            reasons=result.reasons,
            concerns=result.concerns + sanity.concerns + location.concerns,
            matched_skills=result.matched_skills,
        )

    score = 20
    reasons = [
        "Удалённый формат соответствует обязательному требованию."
        if candidate.work_mode is WorkMode.REMOTE
        else "Office or hybrid work in Prague matches your location preference."
    ]
    concerns = list(sanity.concerns + location.concerns)
    title = role_title(candidate.title)
    context = RequirementContext.from_text(
        candidate.title, candidate.description, candidate.raw_data
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
    supporting_score = 0
    core_skills = 0
    for skill in profile.skills:
        evidence = context.evidence(skill.aliases)
        if evidence < Evidence.OPTIONAL:
            continue
        matched_skills.append(skill.name)
        weight = min(skill.weight, 3) if evidence is Evidence.OPTIONAL else skill.weight
        if skill.supporting:
            supporting_score += weight
        else:
            skill_score += weight
            core_skills += evidence >= Evidence.UNSPECIFIED
    if matched_skills:
        score += min(skill_score + min(supporting_score, 8), 45)
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
    score += _experience_adjustment(candidate, reasons, concerns)
    score += _salary_adjustment(candidate, profile, reasons, concerns, rates)
    score += _language_adjustment(context, profile, concerns)
    score += _application_flow_adjustment(candidate.raw_data, concerns)
    negative_skills: list[str] = []
    has_core_negative_skill = False
    alternatives = tuple(
        alias for skill in profile.skills if not skill.supporting for alias in skill.aliases
    )
    for rule in profile.negative_skills:
        evidence = context.evidence(rule.aliases, alternatives=alternatives)
        if evidence is Evidence.OPTIONAL:
            concerns.append(f"Optional technology outside your demonstrated stack: {rule.name}.")
        elif evidence >= Evidence.UNSPECIFIED:
            negative_skills.append(rule.name)
            has_core_negative_skill |= evidence is Evidence.REQUIRED
    if negative_skills:
        score -= profile.negative_skill_penalty
        concerns.append(
            "Вакансия включает технологии вне профиля: "
            f"{', '.join(negative_skills)}. Применён штраф {profile.negative_skill_penalty} баллов."
        )

    final_score = max(0, min(score, 100))
    if not core_skills:
        final_score = min(final_score, max(0, profile.notification_threshold - 1))
        concerns.append(
            "No demonstrated core stack match; tools or optional skills alone are insufficient."
        )
    years = required_experience_years(candidate)
    mixed_level = has_junior_title(title) and re.search(r"\b(?:middle|mid[- ]level)\b", title)
    structured_middle = any(
        normalize_text(value).replace("_", " ").replace("-", " ") in {"mid", "middle", "mid level"}
        for key in ("seniority", "levels")
        for value in _string_values(candidate.raw_data.get(key))
    )
    if mixed_level or structured_middle or (years is not None and 2 <= years < 3):
        final_score = min(final_score, max(0, profile.notification_threshold - 1))
        concerns.append(
            "Stretch match: experience or mixed Junior/Middle level "
            "exceeds your demonstrated background."
        )
    if has_core_negative_skill:
        final_score = min(final_score, max(0, profile.notification_threshold - 1))
        concerns.append(
            "Технология вне профиля указана как основная или обязательная; "
            "итоговый балл ограничен ниже порога уведомления."
        )
    return ScoreResult(
        score=final_score,
        reasons=tuple(reasons),
        concerns=tuple(concerns),
        matched_skills=tuple(matched_skills),
    )


def _experience_adjustment(
    candidate: MatchCandidate,
    reasons: list[str],
    concerns: list[str],
) -> int:
    years = required_experience_years(candidate)
    if years is None:
        return 0
    if years <= 1:
        reasons.append("Требуемый опыт не превышает одного года.")
        return 10
    if years < 2:
        concerns.append("Вакансия требует от одного до двух лет опыта.")
        return -5
    if years < 3 and has_junior_title(candidate.title):
        concerns.append("Junior-вакансия требует около двух лет опыта.")
        return -5
    if years < 3:
        concerns.append("The role requests about two years of experience; treat it as a stretch.")
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


def _salary_adjustment(
    candidate: MatchCandidate,
    profile: SearchProfile,
    reasons: list[str],
    concerns: list[str],
    rates: ExchangeRates | None,
) -> int:
    minimum_monthly_usd, maximum_monthly_usd = monthly_salary_usd(candidate, rates)
    upper_bound = maximum_monthly_usd or minimum_monthly_usd
    if upper_bound is None:
        return 0
    if upper_bound >= profile.minimum_monthly_salary_usd:
        reasons.append("Указанная зарплата достигает предпочтительного месячного уровня.")
        return 5
    concerns.append("Указанная зарплата ниже предпочтительного месячного уровня.")
    return -10


def _language_adjustment(
    context: RequirementContext,
    profile: SearchProfile,
    concerns: list[str],
) -> int:
    if any(
        re.search(r"english\s*(?:-|:)?\s*(?:c1|c2|advanced|fluent)", clause.text)
        and mention_evidence(clause, "english") >= Evidence.UNSPECIFIED
        for clause in context.clauses
    ):
        concerns.append(
            f"Требуемый уровень английского выше текущего уровня {profile.english_level}."
        )
        return -10
    czech_markers = sum(marker in context.text for marker in CZECH_DESCRIPTION_MARKERS)
    if czech_markers >= 3 and not re.search(
        r"(?:czech|čeština|český jazyk).{0,24}(?:a1|a2|basic|základní)",
        context.text,
    ):
        concerns.append(
            f"Описание преимущественно на чешском языке; текущий уровень — {profile.czech_level}."
        )
        concerns.append(
            "The language of the advertisement alone does not establish a Czech requirement."
        )
    return 0


def _application_flow_adjustment(raw_data: dict[str, Any], concerns: list[str]) -> int:
    urls = tuple(_application_urls(raw_data))
    if any(SUSPICIOUS_APPLICATION_URL_PATTERN.search(url) for url in urls):
        concerns.append("Ссылка отклика может вести на платную регистрацию или подписку.")
        return -10
    return 0


def _application_urls(value: Any, key: str = "") -> tuple[str, ...]:
    if isinstance(value, dict):
        return tuple(
            url
            for child_key, child_value in value.items()
            for url in _application_urls(child_value, str(child_key))
        )
    if isinstance(value, list | tuple):
        return tuple(url for child in value for url in _application_urls(child, key))
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        normalized_key = normalize_text(key)
        if any(marker in normalized_key for marker in ("url", "link", "apply", "application")):
            return (value,)
    return ()


def _contains_any(value: str, terms: tuple[str, ...]) -> bool:
    return any(re.search(_term_pattern(term), value) is not None for term in terms)


def _term_pattern(term: str) -> str:
    return rf"(?<!\w){re.escape(normalize_text(term))}(?!\w)"
