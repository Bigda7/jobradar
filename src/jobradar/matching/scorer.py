import re
from typing import Any

from jobradar.domain.enums import OpportunityKind, WorkMode
from jobradar.domain.normalization import normalize_text
from jobradar.matching.freelance import score_freelance_candidate
from jobradar.matching.models import MatchCandidate as MatchCandidate
from jobradar.matching.models import ScoreResult as ScoreResult
from jobradar.matching.profile import NegativeSkillRule, SearchProfile
from jobradar.matching.rejections import (
    hard_rejection_concern,
    has_junior_title,
    required_experience_years,
)
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
AMBIGUOUS_NEGATIVE_SKILL_ALIASES = frozenset({"go", "spring", "vue"})
NEGATIVE_SKILL_CORE_MARKERS = (
    "backend",
    "backend stack",
    "core stack",
    "main stack",
    "mandatory",
    "must",
    "primary stack",
    "proficient",
    "required",
    "requirements",
    "solid knowledge",
    "strong knowledge",
    "tech stack",
    "technology stack",
    "необходимо",
    "обов'язково",
    "обязательно",
    "обязательный",
    "основний стек",
    "основной стек",
    "требуется",
    "вимога",
)
NEGATIVE_SKILL_OPTIONAL_MARKERS = (
    "advantage",
    "bonus",
    "good to have",
    "nice to have",
    "optional",
    "plus",
    "preferred",
    "will be a plus",
    "будет плюсом",
    "желательно",
    "необязательно",
    "перевагою",
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
NODE_BACKEND_PATTERN = re.compile(
    r"(?<!\w)(?:node(?:\.js|js)?|nestjs|express(?:\.js|js)?|fastify)(?!\w)"
)
PYTHON_BACKEND_PATTERN = re.compile(r"(?<!\w)(?:python|django|fastapi|flask|sqlalchemy)(?!\w)")
BACKEND_ROLE_PATTERN = re.compile(r"(?<!\w)(?:full[- ]?stack|backend|back[- ]end)(?!\w)")
SUSPICIOUS_APPLICATION_URL_PATTERN = re.compile(
    r"(?i)(?:job-seekers?/account/register|/(?:pricing|subscription|membership|"
    r"upgrade|checkout|payment)(?:[/?#]|$))"
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
            matched_skills=result.matched_skills,
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
    score += _experience_adjustment(candidate, reasons, concerns)
    score += _salary_adjustment(candidate, profile, reasons, concerns)
    score += _language_adjustment(searchable_text, profile, concerns)
    score += _backend_stack_adjustment(title, searchable_text, concerns)
    score += _application_flow_adjustment(candidate.raw_data, concerns)
    negative_skills, has_core_negative_skill = _negative_skill_matches(
        candidate,
        profile.negative_skills,
    )
    if negative_skills:
        score -= profile.negative_skill_penalty
        concerns.append(
            "Вакансия включает технологии вне профиля: "
            f"{', '.join(negative_skills)}. Применён штраф {profile.negative_skill_penalty} баллов."
        )

    final_score = max(0, min(score, 100))
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


def _language_adjustment(
    searchable_text: str,
    profile: SearchProfile,
    concerns: list[str],
) -> int:
    if re.search(r"english\s*(?:-|:)?\s*(?:c1|c2|advanced|fluent)", searchable_text):
        concerns.append(
            f"Требуемый уровень английского выше текущего уровня {profile.english_level}."
        )
        return -10
    czech_markers = sum(marker in searchable_text for marker in CZECH_DESCRIPTION_MARKERS)
    if czech_markers >= 3 and not re.search(
        r"(?:czech|čeština|český jazyk).{0,24}(?:a1|a2|basic|základní)",
        searchable_text,
    ):
        concerns.append(
            f"Описание преимущественно на чешском языке; текущий уровень — {profile.czech_level}."
        )
        return -20
    return 0


def _backend_stack_adjustment(
    title: str,
    searchable_text: str,
    concerns: list[str],
) -> int:
    if (
        BACKEND_ROLE_PATTERN.search(title)
        and NODE_BACKEND_PATTERN.search(searchable_text)
        and not PYTHON_BACKEND_PATTERN.search(searchable_text)
    ):
        concerns.append("Backend вакансии основан на Node.js без Python в основном стеке.")
        return -15
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


def _negative_skill_matches(
    candidate: MatchCandidate,
    rules: tuple[NegativeSkillRule, ...],
) -> tuple[tuple[str, ...], bool]:
    title = normalize_text(candidate.title)
    description = normalize_text(candidate.description)
    structured_values = tuple(
        normalize_text(value)
        for key in ("categories", "parentCategories", "tags", "skills")
        for value in _string_values(candidate.raw_data.get(key))
    )
    matched_names: list[str] = []
    has_core_match = False
    for rule in rules:
        title_match = _contains_any(title, rule.aliases)
        structured_match = any(_contains_any(value, rule.aliases) for value in structured_values)
        description_aliases = tuple(
            alias
            for alias in rule.aliases
            if normalize_text(alias) not in AMBIGUOUS_NEGATIVE_SKILL_ALIASES
        )
        description_match = _contains_any(description, description_aliases) or any(
            _ambiguous_alias_has_technology_context(description, alias)
            for alias in rule.aliases
            if normalize_text(alias) in AMBIGUOUS_NEGATIVE_SKILL_ALIASES
        )
        if not (title_match or structured_match or description_match):
            continue
        matched_names.append(rule.name)
        explicitly_optional = any(
            _negative_skill_is_explicitly_optional(description, alias)
            for alias in rule.aliases
            if _contains_any(description, (alias,))
        )
        if (
            title_match
            or (structured_match and not explicitly_optional)
            or _negative_skill_is_core(description, rule.aliases)
        ):
            has_core_match = True
    return tuple(matched_names), has_core_match


def _negative_skill_is_core(description: str, aliases: tuple[str, ...]) -> bool:
    for alias in aliases:
        if not _contains_any(description, (alias,)):
            continue
        core_distance = _alias_marker_distance(description, alias, NEGATIVE_SKILL_CORE_MARKERS)
        optional_distance = _alias_marker_distance(
            description,
            alias,
            NEGATIVE_SKILL_OPTIONAL_MARKERS,
        )
        if core_distance is not None and (
            optional_distance is None or core_distance < optional_distance
        ):
            return True
    return False


def _negative_skill_is_explicitly_optional(description: str, alias: str) -> bool:
    optional_distance = _alias_marker_distance(
        description,
        alias,
        NEGATIVE_SKILL_OPTIONAL_MARKERS,
    )
    if optional_distance is None:
        return False
    core_distance = _alias_marker_distance(description, alias, NEGATIVE_SKILL_CORE_MARKERS)
    return core_distance is None or optional_distance <= core_distance


def _alias_marker_distance(text: str, alias: str, markers: tuple[str, ...]) -> int | None:
    alias_pattern = _term_pattern(alias)
    marker_pattern = "(?:" + "|".join(re.escape(normalize_text(marker)) for marker in markers) + ")"
    alias_matches = tuple(re.finditer(alias_pattern, text))
    marker_matches = tuple(re.finditer(marker_pattern, text))
    distances = (
        max(alias_match.start() - marker_match.end(), marker_match.start() - alias_match.end(), 0)
        for alias_match in alias_matches
        for marker_match in marker_matches
    )
    nearby_distances = tuple(distance for distance in distances if distance <= 60)
    return min(nearby_distances, default=None)


def _ambiguous_alias_has_technology_context(text: str, alias: str) -> bool:
    alias_pattern = _term_pattern(alias)
    prefix_pattern = (
        r"(?:experience|knowledge|proficiency|proficient|required|skills?)"
        r"(?:\s+(?:in|of|with))?\s+"
    )
    suffix_pattern = (
        r"(?:backend|developer|engineer|experience|framework|knowledge|language|"
        r"nice to have|optional|preferred|required|skills?|stack|will be a plus|"
        r"будет плюсом|желательно|перевагою)"
    )
    return (
        re.search(rf"{prefix_pattern}{alias_pattern}", text) is not None
        or re.search(rf"{alias_pattern}\s+(?:is\s+)?{suffix_pattern}", text) is not None
    )


def _contains_any(value: str, terms: tuple[str, ...]) -> bool:
    return any(re.search(_term_pattern(term), value) is not None for term in terms)


def _term_pattern(term: str) -> str:
    return rf"(?<!\w){re.escape(normalize_text(term))}(?!\w)"
