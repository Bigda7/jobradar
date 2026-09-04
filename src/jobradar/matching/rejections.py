import re
from decimal import Decimal

from jobradar.domain.enums import OpportunityKind
from jobradar.domain.normalization import normalize_text
from jobradar.matching.models import MatchCandidate

VOLUNTEER_PATTERN = re.compile(r"(?<!\w)(?:волонтер\w*|волонтёр\w*|volunteer\w*)(?!\w)")
FREE_WORK_PATTERN = re.compile(r"(?<!\w)(?:бесплатн\w*|безкоштовн\w*|unpaid)(?!\w)")
VOLUNTEERING_BENEFIT_PATTERN = re.compile(
    r"(?<!\w)(?:"
    r"(?:paid\s+)?volunteer(?:ing)?\s+days?|"
    r"days?\s+off\s+for\s+volunteer(?:ing)?|"
    r"волонт[её]рск\w*\s+д(?:ень|ня)|"
    r"день\s+(?:для\s+)?волонт[её]рств\w*"
    r")(?!\w)"
)
MILITARY_EQUAL_OPPORTUNITY_PATTERN = re.compile(
    r"(?<!\w)(?:"
    r"military\s+or\s+veteran\s+status|military\s+status|protected\s+veteran|"
    r"veteran\s+of\s+the\s+armed\s+forces(?:\s+of\s+the\s+united\s+states)?"
    r")(?!\w)"
)
MILITARY_PATTERN = re.compile(
    r"(?<!\w)(?:"
    r"зсу|мобилизац\w*|мобілізац\w*|тцк|"
    r"сили\s+оборони|збройн\w*\s+сил\w*|військов\w*|"
    r"defen[cs]e\s+forces|armed\s+forces|military|miltech|deftech|"
    r"defen[cs]e[-\s]+(?:tech(?:nology|nologies)?|industry|sector|systems?)|"
    r"оборонн\w*|армі\w*|арми\w*"
    r")(?!\w)"
)
JUNIOR_TITLE_PATTERN = re.compile(
    r"(?<!\w)(?:junior\+?|jr\.?|trainee|intern(?:ship)?|graduate|entry[- ]level)(?!\w)"
)
SENIORITY_TITLE_PATTERN = re.compile(
    r"(?<!\w)(?:"
    r"senior|sr\.?|lead|team\s+lead|tech\s+lead|principal|staff|architect|"
    r"head\s+of|director|middle\+?|mid[- ]level|mid[- /]+senior"
    r")(?!\w)"
)
NON_DEVELOPMENT_TITLE_PATTERNS = (
    re.compile(r"(?<!\w)(?:designer|ui[/ -]?ux)(?!\w)"),
    re.compile(r"(?<!\w)(?:ppc|media\s+buyer|seo|smm|affiliate|marketing|marketer)(?!\w)"),
    re.compile(
        r"(?<!\w)(?:recruiter|recruitment|human\s+resources|hr|sales|"
        r"account\s+manager|customer\s+(?:service|support)|support)(?!\w)"
    ),
    re.compile(
        r"(?<!\w)(?:soc\s+analyst|security\s+operations|cybersecurity|"
        r"information\s+security)(?!\w)"
    ),
    re.compile(r"(?<!\w)(?:qa|quality\s+assurance|manual\s+tester|tester)(?!\w)"),
    re.compile(
        r"(?<!\w)(?:odoo|1c|1с|sharepoint|power\s+(?:platform|apps?)|"
        r"no[- ]code|low[- ]code)(?!\w)"
    ),
    re.compile(r"(?<!\w)(?:system\s+administrator|sysadmin)(?!\w)"),
)
ANALYST_TITLE_PATTERN = re.compile(r"(?<!\w)(?:product|business|bi)\s+analyst(?!\w)")
ENGINEERING_TITLE_PATTERN = re.compile(r"(?<!\w)(?:developer|engineer)(?!\w)")
EXPERIENCE_VALUE_PATTERN = re.compile(
    r"(?<!\w)(?P<years>\d{1,2})(?:\s*\+|\s*(?:-|–|—|to)\s*\d{1,2})?\s*"
    r"(?:years?|yrs?|рок(?:и|ів)?|года?|лет|roky?|roků|let)(?!\w)"
)
EXPERIENCE_CONTEXT_PATTERN = re.compile(
    r"(?<!\w)(?:experience|requirements?|commercial|professional|hands[- ]on|"
    r"досвід|опыт|комерційн\w*|коммерческ\w*|praxe|zkušenost\w*|"
    r"at\s+least|minimum|minimálně|alespoň|мінімум|минимум|від|от)(?!\w)"
)
EXPERIENCE_HISTORY_PATTERN = re.compile(r"^\s*(?:ago|old|in\s+business)(?!\w)")
FOREIGN_LANGUAGE_ALIASES = {
    "немецкий": ("german", "deutsch", "deutschkenntnisse"),
    "чешский": ("czech", "čeština", "český jazyk", "znalost češtiny"),
    "французский": ("french", "français", "francais"),
    "испанский": ("spanish", "español", "espanol"),
    "польский": ("polish", "język polski", "polski język"),
}
LANGUAGE_REQUIREMENT_MARKERS = (
    "required",
    "mandatory",
    "must have",
    "fluent",
    "native",
    "professional",
    "working proficiency",
    "b1",
    "b2",
    "c1",
    "c2",
    "požadujeme",
    "požadována",
    "nutná",
    "plynulá",
    "rodilý",
)
BASIC_CZECH_PATTERN = re.compile(
    r"(?:(?:czech|čeština|český\s+jazyk).{0,24}(?:a1|a2|basic|základní)|"
    r"(?:a1|a2|basic|základní).{0,24}(?:czech|čeština|český\s+jazyk))"
)
OPTIONAL_LANGUAGE_PATTERN = re.compile(
    r"^.{0,24}(?:not\s+(?:required|mandatory)|optional|nice\s+to\s+have)"
)


def hard_rejection_concern(candidate: MatchCandidate) -> str | None:
    title_and_company = normalize_text(
        " ".join(value for value in (candidate.title, candidate.company) if value)
    )
    description = normalize_text(candidate.description or "")
    description_without_benefits = VOLUNTEERING_BENEFIT_PATTERN.sub(" ", description)
    description_without_equal_opportunity = MILITARY_EQUAL_OPPORTUNITY_PATTERN.sub(" ", description)
    searchable_text = f"{title_and_company} {description}"
    if FREE_WORK_PATTERN.search(searchable_text) or VOLUNTEER_PATTERN.search(
        f"{title_and_company} {description_without_benefits}"
    ):
        return "Отклонено: волонтёрская или неоплачиваемая работа."
    if MILITARY_PATTERN.search(f"{title_and_company} {description_without_equal_opportunity}"):
        return "Отклонено: военный рекрутинг, мобилизация или служба в армии."
    if candidate.kind is OpportunityKind.EMPLOYMENT:
        employment_rejection = _employment_rejection_concern(candidate, description)
        if employment_rejection is not None:
            return employment_rejection
    return None


def required_experience_years(candidate: MatchCandidate) -> float | None:
    values: list[float] = []
    requirements = candidate.raw_data.get("experienceRequirements")
    if isinstance(requirements, dict):
        months = requirements.get("monthsOfExperience")
        if isinstance(months, int | float) and not isinstance(months, bool) and months >= 0:
            values.append(float(months) / 12)

    text = normalize_text(" ".join((candidate.title, candidate.description or "")))
    for match in EXPERIENCE_VALUE_PATTERN.finditer(text):
        if EXPERIENCE_HISTORY_PATTERN.search(text[match.end() : match.end() + 24]):
            continue
        context = text[max(0, match.start() - 70) : min(len(text), match.end() + 70)]
        if EXPERIENCE_CONTEXT_PATTERN.search(context):
            values.append(float(match.group("years")))
    return max(values, default=None)


def has_junior_title(title: str) -> bool:
    return JUNIOR_TITLE_PATTERN.search(normalize_text(title)) is not None


def _employment_rejection_concern(
    candidate: MatchCandidate,
    description: str,
) -> str | None:
    title = normalize_text(candidate.title)
    if SENIORITY_TITLE_PATTERN.search(title):
        return "Отклонено: вакансия рассчитана на уровень Middle, Senior или выше."
    if _is_non_development_title(title):
        return "Отклонено: специализация вакансии не соответствует профилю разработчика."
    if _has_zero_salary(candidate):
        return "Отклонено: в вакансии явно указана нулевая оплата."

    years = required_experience_years(candidate)
    if years is not None and years >= 3:
        return "Отклонено: вакансия требует не менее трёх лет опыта."
    if years is not None and years >= 2 and not has_junior_title(candidate.title):
        return "Отклонено: вакансия требует не менее двух лет опыта без уровня Junior."

    language = _required_unsupported_language(f"{title} {description}")
    if language is not None:
        return f"Отклонено: вакансия требует рабочее владение языком: {language}."
    return None


def _is_non_development_title(title: str) -> bool:
    if ANALYST_TITLE_PATTERN.search(title) and not ENGINEERING_TITLE_PATTERN.search(title):
        return True
    return any(pattern.search(title) is not None for pattern in NON_DEVELOPMENT_TITLE_PATTERNS)


def _has_zero_salary(candidate: MatchCandidate) -> bool:
    amounts = tuple(
        amount for amount in (candidate.salary_min, candidate.salary_max) if amount is not None
    )
    return bool(amounts) and all(amount == Decimal(0) for amount in amounts)


def _required_unsupported_language(text: str) -> str | None:
    normalized = normalize_text(text)
    for label, aliases in FOREIGN_LANGUAGE_ALIASES.items():
        if label == "чешский" and BASIC_CZECH_PATTERN.search(normalized):
            continue
        for alias in aliases:
            language_position = normalized.find(alias)
            if language_position < 0:
                continue
            language_tail = normalized[
                language_position + len(alias) : language_position + len(alias) + 60
            ]
            if OPTIONAL_LANGUAGE_PATTERN.search(language_tail):
                continue
            context = normalized[
                max(0, language_position - 45) : language_position + len(alias) + 45
            ]
            if any(marker in context for marker in LANGUAGE_REQUIREMENT_MARKERS):
                return label
    return None
