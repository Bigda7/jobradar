import re
from dataclasses import dataclass
from decimal import Decimal

from jobradar.domain.enums import OpportunityKind
from jobradar.domain.normalization import normalize_text
from jobradar.matching.models import MatchCandidate
from jobradar.matching.profile import SearchProfile

LOCATION_REJECTION_PATTERN = re.compile(
    r"(?<!\w)(?:"
    r"u\.?s\.?[- ]+only|"
    r"united\s+states[- ]+only|"
    r"must\s+(?:reside|live|be\s+based|be\s+located)\s+in\s+(?:the\s+)?u\.?s\.?|"
    r"(?:position|role|job)?\s*(?:is\s+)?only\s+available\s+"
    r"(?:within|in|to\s+candidates\s+in)\s+(?:the\s+)?u\.?s\.?"
    r")(?!\w)"
)
EQUITY_ONLY_PATTERN = re.compile(
    r"(?<!\w)(?:equity[- ]+only|unpaid\s+startup|profit[- ]+share)(?!\w)"
)
BASE_SALARY_PATTERN = re.compile(
    r"(?<!\w)(?:base\s+salary|базов\w*\s+зарплат\w*|базов\w*\s+заробітн\w*)(?!\w)"
)
TOXIC_MARKER_PATTERN = re.compile(
    r"(?<!\w)(?:rockstar\s+developer|code\s+ninja|10x\s+engineer)(?!\w)"
)

REFERENCE_USD_PER_CURRENCY = {
    "USD": Decimal("1"),
    "EUR": Decimal("1.17"),
    "GBP": Decimal("1.35"),
    "CZK": Decimal("0.048"),
    "UAH": Decimal("0.024"),
    "PLN": Decimal("0.275"),
    "CAD": Decimal("0.72"),
    "AUD": Decimal("0.65"),
    "INR": Decimal("0.0114"),
}


@dataclass(frozen=True, slots=True)
class SanityResult:
    rejection_concern: str | None = None
    score_adjustment: int = 0
    concerns: tuple[str, ...] = ()


def evaluate_sanity(candidate: MatchCandidate, profile: SearchProfile) -> SanityResult:
    searchable_text = normalize_text(
        " ".join(
            value
            for value in (
                candidate.title,
                candidate.company,
                candidate.location_text,
                candidate.description,
            )
            if value
        )
    )
    if LOCATION_REJECTION_PATTERN.search(searchable_text):
        return SanityResult(
            rejection_concern=("Отклонено: удалённая работа доступна только кандидатам из США.")
        )
    if EQUITY_ONLY_PATTERN.search(searchable_text) and not _has_base_salary(
        candidate, searchable_text
    ):
        return SanityResult(
            rejection_concern=(
                "Отклонено: предлагается работа за долю или идею без базовой зарплаты."
            )
        )

    adjustment = 0
    concerns: list[str] = []
    if candidate.kind is OpportunityKind.EMPLOYMENT:
        salary_adjustment, salary_concerns = _salary_sanity(candidate, profile)
        adjustment += salary_adjustment
        concerns.extend(salary_concerns)
    if TOXIC_MARKER_PATTERN.search(searchable_text):
        adjustment -= profile.toxic_language_penalty
        concerns.append(
            "В описании найден токсичный маркер ожиданий: rockstar, ninja или 10x engineer."
        )
    return SanityResult(score_adjustment=adjustment, concerns=tuple(concerns))


def monthly_salary_usd(
    candidate: MatchCandidate,
) -> tuple[Decimal | None, Decimal | None]:
    currency = (candidate.salary_currency or "").upper()
    usd_rate = REFERENCE_USD_PER_CURRENCY.get(currency)
    period_factor = _monthly_period_factor(candidate.salary_period)
    if usd_rate is None or period_factor is None:
        return None, None
    minimum = (
        candidate.salary_min * usd_rate * period_factor
        if candidate.salary_min is not None
        else None
    )
    maximum = (
        candidate.salary_max * usd_rate * period_factor
        if candidate.salary_max is not None
        else None
    )
    return minimum, maximum


def _salary_sanity(
    candidate: MatchCandidate,
    profile: SearchProfile,
) -> tuple[int, tuple[str, ...]]:
    minimum, maximum = monthly_salary_usd(candidate)
    base_salary = minimum if minimum is not None else maximum
    upper_salary = maximum if maximum is not None else minimum
    if base_salary is not None and base_salary > profile.maximum_junior_monthly_salary_usd:
        formatted_limit = f"{profile.maximum_junior_monthly_salary_usd:,.0f}"
        return (
            -profile.excessive_salary_penalty,
            (
                f"Базовая зарплата выше USD {formatted_limit} в месяц и может указывать "
                "на скрытый уровень Middle или Senior.",
            ),
        )
    if (
        upper_salary is not None
        and upper_salary < profile.minimum_full_time_monthly_salary_usd
        and _is_full_time(candidate.employment_type)
    ):
        return (
            -profile.very_low_salary_penalty,
            ("Зарплата за полную занятость ниже USD 400 в месяц.",),
        )
    return 0, ()


def _has_base_salary(candidate: MatchCandidate, searchable_text: str) -> bool:
    structured_salary = candidate.salary_currency is not None and (
        candidate.salary_min is not None or candidate.salary_max is not None
    )
    return structured_salary or BASE_SALARY_PATTERN.search(searchable_text) is not None


def _monthly_period_factor(value: str | None) -> Decimal | None:
    period = normalize_text(value).replace("_", " ")
    factors = {
        "hour": Decimal("160"),
        "hourly": Decimal("160"),
        "day": Decimal("22"),
        "daily": Decimal("22"),
        "week": Decimal("52") / Decimal("12"),
        "weekly": Decimal("52") / Decimal("12"),
        "fortnight": Decimal("26") / Decimal("12"),
        "fortnightly": Decimal("26") / Decimal("12"),
        "month": Decimal("1"),
        "monthly": Decimal("1"),
        "year": Decimal("1") / Decimal("12"),
        "yearly": Decimal("1") / Decimal("12"),
        "annual": Decimal("1") / Decimal("12"),
        "annually": Decimal("1") / Decimal("12"),
    }
    return factors.get(period)


def _is_full_time(value: str | None) -> bool:
    normalized = normalize_text(value).replace("_", " ").replace("-", " ")
    return re.search(r"(?<!\w)full\s*time(?!\w)", normalized) is not None
