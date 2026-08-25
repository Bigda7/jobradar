import re
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from jobradar.domain.enums import WorkMode
from jobradar.domain.normalization import normalize_text
from jobradar.matching.models import MatchCandidate, ScoreResult
from jobradar.matching.profile import SearchProfile

INCOMPATIBLE_PROJECT_TITLE_TERMS = (
    "accountant",
    "bookkeeping",
    "cold caller",
    "content writer",
    "copywriter",
    "data entry",
    "financial analyst",
    "graphic designer",
    "flutter",
    "illustrator",
    "lead generation",
    "logo design",
    "marketing specialist",
    "seo specialist",
    "social media manager",
    "solana",
    "telemarketing",
    "transcription",
    "translator",
    "video editor",
    "blockchain",
    "react native",
    "rust",
)

SUPPORTED_PROJECT_LANGUAGES = {"en", "uk", "ru"}

STRETCH_TECHNOLOGY_JOB_TERMS = {
    "android",
    "blockchain",
    "computer vision",
    "flutter",
    "iphone",
    "large language models (llms)",
    "machine learning (ml)",
    "mobile app development",
    "react native",
    "rust",
    "smart contracts",
    "solana",
}

SCAM_PATTERNS = (
    (
        "Отклонено: проект требует предоплату или депозит.",
        re.compile(
            r"\b(?:pay|send|transfer|provide).{0,40}"
            r"(?:security deposit|registration fee|application fee|joining fee|upfront fee)\b"
        ),
    ),
    (
        "Отклонено: проект требует доступ к финансовому аккаунту или аккаунту платформы.",
        re.compile(
            r"\b(?:"
            r"(?:buy|rent|lend|provide|share).{0,35}"
            r"(?:freelancer|upwork|paypal|bank|crypto|verified)\s+account|"
            r"rent(?:ing)?\s+(?:an?\s+|your\s+)?account|"
            r"upwork\s+agency|"
            r"bidding\s+on\s+your\s+behalf"
            r")\b"
        ),
    ),
    (
        "Отклонено: проект требует пароль, одноразовый код или приватный ключ.",
        re.compile(
            r"\b(?:share|send|provide).{0,30}"
            r"(?:otp|password|login credentials|seed phrase|private key)\b"
        ),
    ),
    (
        "Отклонено: проект предлагает перенести общение или оплату за пределы платформы.",
        re.compile(
            r"\b(?:(?:contact|message|reach)\s+(?:me|us).{0,25}"
            r"(?:telegram|whatsapp)|(?:payment|pay|paid).{0,35}"
            r"(?:outside|off)\s+(?:freelancer|the platform|platform))\b"
        ),
    ),
    (
        "Отклонено: проект предлагает неоплачиваемую или незащищённую работу.",
        re.compile(r"\b(?:no milestone|payment after completion|pay only after|unpaid trial)\b"),
    ),
)

MANAGEABLE_SCOPE_PATTERNS = (
    r"\bbug\s+fix\b",
    r"\bsmall\s+(?:task|project|change)\b",
    r"\bminor\s+(?:fix|change|update)\b",
    r"\bapi\s+integration\b",
    r"\bwebhook\b",
    r"\bdashboard\b",
    r"\blanding\s+page\b",
    r"\bsingle\s+page\b",
    r"\bshopify\s+theme\b",
    r"\bliquid\b",
    r"\bautomation\b",
    r"\bscript\b",
    r"\bmvp\b",
)

LARGE_SCOPE_PATTERNS = (
    r"\b(?:complete|entire|full)\s+"
    r"(?:application|marketplace|platform|saas|site|store|system|website)\b",
    r"\bfully[- ](?:featured|functional|functioning)\b",
    r"\bproduction[- ]ready\b",
    r"\bfrom\s+scratch\b",
    r"\bmulti[- ]vendor\b",
    r"\bend[- ]to[- ]end\b",
    r"\bboth\s+ios\s+and\s+android\b",
    r"\bglobal.{0,80}\bplatform\b",
    r"\b(?:app|application).{0,30}(?:and|&).{0,30}\bwebsite\b",
    r"\bwebsite.{0,30}(?:and|&).{0,30}\b(?:app|application)\b",
)

EXCESSIVE_EXPERIENCE_PATTERNS = (
    r"\bexpert\s+only\b",
    r"\b(?:minimum|at\s+least)\s+(?:4|5|6|7|8|9|10)\+?\s+years\b",
    r"\b(?:4|5|6|7|8|9|10)\+\s+years\b",
)


def score_freelance_candidate(
    candidate: MatchCandidate,
    profile: SearchProfile,
) -> ScoreResult:
    if candidate.work_mode is not WorkMode.REMOTE:
        return ScoreResult(
            score=0,
            reasons=(),
            concerns=("Отклонено: проект не обозначен как удалённый.",),
        )

    title = normalize_text(candidate.title)
    description = normalize_text(candidate.description)
    project_text = normalize_text(" ".join(value for value in (title, description) if value))
    project_language = normalize_text(_string(candidate.raw_data.get("language")))
    if project_language and project_language not in SUPPORTED_PROJECT_LANGUAGES:
        return ScoreResult(
            score=0,
            reasons=(),
            concerns=("Отклонено: основной язык проекта не поддерживается.",),
        )
    scam_concern = _scam_concern(project_text)
    if scam_concern is not None:
        return ScoreResult(score=0, reasons=(), concerns=(scam_concern,))

    score = 20
    reasons = ["Удалённый онлайн-проект соответствует обязательному требованию."]
    concerns: list[str] = []

    if candidate.raw_data.get("description_truncated") is True:
        score -= 8
        concerns.append(
            "Источник показывает незалогиненному пользователю только публичный фрагмент описания."
        )

    if _contains_any(title, INCOMPATIBLE_PROJECT_TITLE_TERMS):
        score -= 60
        concerns.append("Специализация проекта не соответствует профилю разработчика.")

    searchable_text = _searchable_project_text(candidate)
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

    stretch_technologies = sorted(_job_names(candidate.raw_data) & STRETCH_TECHNOLOGY_JOB_TERMS)
    if stretch_technologies:
        score -= 15
        concerns.append(
            f"Проект включает технологии вне основного профиля: {', '.join(stretch_technologies)}."
        )

    if _matches_any(project_text, MANAGEABLE_SCOPE_PATTERNS):
        score += 8
        reasons.append("Проект выглядит сфокусированным и выполнимым по объёму.")
    if _matches_any(project_text, LARGE_SCOPE_PATTERNS):
        score -= 18
        concerns.append("Запрошенный объём слишком велик для первого фриланс-проекта.")
    if re.search(r"\b(?:within|in)\s+(?:24|48)\s+hours?\b|\bwithin\s+one\s+day\b", project_text):
        score -= 10
        concerns.append("Срок выполнения выглядит необычно коротким.")
    elif re.search(r"\b(?:urgent|asap)\b", project_text):
        score -= 4
        concerns.append("Проект отмечен как срочный.")

    if re.search(r"\b(?:senior|lead|principal|architect)\b", title):
        score -= 25
        concerns.append("Проект рассчитан на специалиста уровня Senior или Lead.")
    elif _matches_any(project_text, EXCESSIVE_EXPERIENCE_PATTERNS):
        score -= 20
        concerns.append("Проект явно требует значительно большего опыта.")

    score += _budget_adjustment(candidate, profile, reasons, concerns)
    score += _competition_adjustment(candidate.raw_data, reasons, concerns)
    score += _employer_adjustment(candidate.raw_data, reasons, concerns)
    score += _language_adjustment(project_text, concerns)

    return ScoreResult(
        score=max(0, min(score, 100)),
        reasons=tuple(reasons),
        concerns=tuple(concerns),
    )


def _scam_concern(project_text: str) -> str | None:
    for concern, pattern in SCAM_PATTERNS:
        if pattern.search(project_text):
            return concern
    return None


def _searchable_project_text(candidate: MatchCandidate) -> str:
    values = [candidate.title, candidate.company, candidate.description]
    jobs = candidate.raw_data.get("jobs")
    if isinstance(jobs, list):
        values.extend(
            str(job.get("name")) for job in jobs if isinstance(job, Mapping) and job.get("name")
        )
    return normalize_text(" ".join(value for value in values if value))


def _job_names(raw_data: Mapping[str, Any]) -> set[str]:
    jobs = raw_data.get("jobs")
    if not isinstance(jobs, list):
        return set()
    return {
        normalize_text(str(job.get("name")))
        for job in jobs
        if isinstance(job, Mapping) and job.get("name")
    }


def _budget_adjustment(
    candidate: MatchCandidate,
    profile: SearchProfile,
    reasons: list[str],
    concerns: list[str],
) -> int:
    minimum_usd, maximum_usd = _budget_usd(candidate)
    upper_bound = maximum_usd or minimum_usd
    if upper_bound is None:
        concerns.append("Бюджет проекта не удалось конвертировать в USD.")
        return 0

    contract_type = normalize_text(candidate.contract_type)
    formatted_budget = _format_usd_range(minimum_usd, maximum_usd)
    if contract_type == "hourly":
        if minimum_usd is not None and minimum_usd >= profile.preferred_freelance_hourly_usd:
            reasons.append(
                f"Почасовой бюджет достигает предпочтительного диапазона: {formatted_budget}."
            )
            return 12
        if upper_bound >= profile.preferred_freelance_hourly_usd:
            reasons.append(
                f"Почасовой бюджет достигает предпочтительного диапазона: {formatted_budget}."
            )
            return 10
        if upper_bound >= profile.minimum_freelance_hourly_usd:
            reasons.append(f"Почасовой бюджет находится в рабочем диапазоне: {formatted_budget}.")
            return 5
        reasons.append(f"Малый почасовой бюджет принят для набора репутации: {formatted_budget}.")
        return 0

    lower_bound = minimum_usd or maximum_usd
    if (
        lower_bound is not None
        and Decimal("5") <= lower_bound
        and upper_bound <= Decimal("100")
        and _payment_verified(candidate.raw_data)
    ):
        reasons.append(
            "Малый фиксированный проект подходит для набора репутации: "
            f"{formatted_budget}, платёж заказчика подтверждён."
        )
        return 15
    if minimum_usd is not None and minimum_usd >= profile.preferred_freelance_fixed_usd:
        reasons.append(
            f"Фиксированный бюджет достигает предпочтительного диапазона: {formatted_budget}."
        )
        return 10
    if upper_bound >= profile.preferred_freelance_fixed_usd:
        reasons.append(
            f"Фиксированный бюджет достигает предпочтительного диапазона: {formatted_budget}."
        )
        return 8
    if upper_bound >= profile.minimum_freelance_fixed_usd:
        reasons.append(f"Фиксированный бюджет находится в рабочем диапазоне: {formatted_budget}.")
        return 5
    reasons.append(f"Малый фиксированный бюджет принят для набора репутации: {formatted_budget}.")
    return 0


def _budget_usd(candidate: MatchCandidate) -> tuple[Decimal | None, Decimal | None]:
    exchange_rate = Decimal("1") if candidate.salary_currency == "USD" else None
    currency = candidate.raw_data.get("currency")
    if isinstance(currency, Mapping):
        raw_exchange_rate = _decimal(currency.get("exchange_rate"))
        if raw_exchange_rate is not None and raw_exchange_rate > 0:
            exchange_rate = raw_exchange_rate
    if exchange_rate is None:
        return None, None
    minimum = candidate.salary_min * exchange_rate if candidate.salary_min is not None else None
    maximum = candidate.salary_max * exchange_rate if candidate.salary_max is not None else None
    return minimum, maximum


def _competition_adjustment(
    raw_data: Mapping[str, Any],
    reasons: list[str],
    concerns: list[str],
) -> int:
    bid_stats = raw_data.get("bid_stats")
    if not isinstance(bid_stats, Mapping):
        return 0
    bid_count = _integer(bid_stats.get("bid_count"))
    if bid_count is None or bid_count < 0:
        return 0
    if bid_count <= 10:
        reasons.append(f"Низкая конкуренция: {bid_count} ставок.")
        return 10
    if bid_count <= 25:
        reasons.append(f"Приемлемая конкуренция: {bid_count} ставок.")
        return 7
    if bid_count <= 50:
        reasons.append(f"Умеренная конкуренция: {bid_count} ставок.")
        return 3
    if bid_count <= 100:
        concerns.append(f"Высокая конкуренция: {bid_count} ставок.")
        return -3
    if bid_count <= 200:
        concerns.append(f"Очень высокая конкуренция: {bid_count} ставок.")
        return -8
    concerns.append(f"Экстремально высокая конкуренция: {bid_count} ставок.")
    return -15


def _employer_adjustment(
    raw_data: Mapping[str, Any],
    reasons: list[str],
    concerns: list[str],
) -> int:
    owner = _owner_data(raw_data)
    if owner is None:
        return 0
    adjustment = 0
    status = owner.get("status")
    if isinstance(status, Mapping):
        payment_verified = status.get("payment_verified")
        if payment_verified is True:
            reasons.append("Заказчик подтвердил платёжные данные.")
            adjustment += 5
        elif payment_verified is False:
            concerns.append("Заказчик не подтвердил платёжные данные.")
            adjustment -= 3

    reputation = owner.get("employer_reputation")
    history = reputation.get("entire_history") if isinstance(reputation, Mapping) else None
    if not isinstance(history, Mapping):
        return adjustment
    rating = _decimal(history.get("overall"))
    reviews = _integer(history.get("reviews"))
    if rating is None or reviews is None or reviews < 3:
        return adjustment
    if rating >= Decimal("4.5"):
        reasons.append(f"Рейтинг заказчика {rating:g}/5 на основе {reviews} отзывов.")
        return adjustment + 5
    if rating < Decimal("3.5"):
        concerns.append(f"Низкий рейтинг заказчика: {rating:g}/5.")
        return adjustment - 8
    return adjustment


def _owner_data(raw_data: Mapping[str, Any]) -> Mapping[str, Any] | None:
    for key in ("_owner", "owner_info"):
        owner = raw_data.get(key)
        if isinstance(owner, Mapping):
            return owner
    return None


def _payment_verified(raw_data: Mapping[str, Any]) -> bool:
    owner = _owner_data(raw_data)
    if owner is None:
        return False
    status = owner.get("status")
    return isinstance(status, Mapping) and status.get("payment_verified") is True


def _language_adjustment(project_text: str, concerns: list[str]) -> int:
    if re.search(r"english\s*(?:-|:)?\s*(?:c1|c2|advanced|fluent)", project_text):
        concerns.append("Требуемый уровень английского выше текущего уровня B1.")
        return -10
    if re.search(r"english\s*(?:-|:)?\s*b2", project_text):
        concerns.append("Проект требует английский B2, а в профиле указан уровень B1.")
        return -5
    return 0


def _format_usd_range(minimum: Decimal | None, maximum: Decimal | None) -> str:
    if minimum is not None and maximum is not None:
        return f"USD {minimum.quantize(Decimal('0.01')):g}-{maximum.quantize(Decimal('0.01')):g}"
    value = minimum or maximum
    if value is None:
        return "неизвестная сумма"
    return f"USD {value.quantize(Decimal('0.01')):g}"


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _integer(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _string(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _matches_any(value: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, value) is not None for pattern in patterns)


def _contains_any(value: str, terms: tuple[str, ...]) -> bool:
    return any(
        re.search(rf"(?<!\w){re.escape(normalize_text(term))}(?!\w)", value) is not None
        for term in terms
    )
