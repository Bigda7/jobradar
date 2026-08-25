import hashlib
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit

from jobradar.domain.normalization import normalize_text
from jobradar.sources.ats_config import AtsCompany

USER_AGENT = "JobRadar/1.6 (personal job aggregator)"
REMOTE_PATTERN = re.compile(
    r"(?<!\w)(?:remote|fully[- ]remote|home[- ]based|work[- ]from[- ]home|"
    r"work[- ]from[- ]anywhere|anywhere[- ]in[- ]the[- ]world|distributed)(?!\w)",
    re.IGNORECASE,
)
NON_REMOTE_PATTERN = re.compile(
    r"(?<!\w)(?:hybrid|on[- ]site|onsite|office[- ]based|in[- ]office|"
    r"part(?:ial|ially)[- ]remote|occasional(?:ly)?[- ]remote)(?!\w)",
    re.IGNORECASE,
)
TEXT_SALARY_PATTERN = re.compile(
    r"(?:(?P<label>base\s+(?:pay|salary)|salary|compensation)\s*(?:range)?\s*:?\s*)?"
    r"(?P<currency>USD|EUR|GBP|CAD|AUD|\$|€|£)\s*"
    r"(?P<minimum>\d[\d,]*(?:\.\d+)?)\s*(?P<minimum_k>[kK])?"
    r"(?:\s*(?:-|–|—|to)\s*"
    r"(?:(?P<maximum_currency>USD|EUR|GBP|CAD|AUD|\$|€|£)\s*)?"
    r"(?P<maximum>\d[\d,]*(?:\.\d+)?)\s*(?P<maximum_k>[kK])?)?"
    r"\s*(?P<period>/\s*(?:h(?:ou)?r|day|week|month|mo|year|yr)|"
    r"per\s+(?:hour|day|week|month|year)|hourly|daily|weekly|monthly|annually|annual)",
    re.IGNORECASE,
)


class AtsSourceError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class AtsSalary:
    minimum: Decimal
    maximum: Decimal
    currency: str
    period: str


def company_payload(job: Mapping[str, Any], company: AtsCompany) -> dict[str, Any]:
    payload = dict(job)
    payload["_jobradar_company"] = {
        "name": company.name,
        "provider": company.provider,
        "identifier": company.identifier,
    }
    return payload


def company_name(payload: Mapping[str, Any]) -> str:
    metadata = payload.get("_jobradar_company")
    if not isinstance(metadata, Mapping):
        raise AtsSourceError("ATS listing is missing company metadata.")
    return required_string(metadata.get("name"), "_jobradar_company.name")


def strict_remote(*values: Any) -> bool:
    text = normalize_text(" ".join(_string_values(values)))
    return bool(text and REMOTE_PATTERN.search(text) and not NON_REMOTE_PATTERN.search(text))


def parse_text_salary(value: str | None) -> AtsSalary | None:
    if not value:
        return None
    matches = [match for match in TEXT_SALARY_PATTERN.finditer(value) if match.group("label")]
    if not matches:
        return None
    match = next(
        (
            item
            for item in matches
            if normalize_text(item.group("label")) in {"base pay", "base salary"}
        ),
        matches[0],
    )
    minimum = decimal_amount(match.group("minimum"), match.group("minimum_k"))
    maximum = decimal_amount(match.group("maximum"), match.group("maximum_k"))
    if minimum is None:
        return None
    maximum = maximum if maximum is not None else minimum
    if minimum > maximum:
        minimum, maximum = maximum, minimum
    return AtsSalary(
        minimum=minimum,
        maximum=maximum,
        currency=currency_code(match.group("currency")),
        period=salary_period(match.group("period")),
    )


def decimal_amount(value: Any, thousands_marker: str | None = None) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None
    if thousands_marker:
        amount *= Decimal("1000")
    return amount


def currency_code(value: Any) -> str:
    normalized = required_string(value, "salary currency").upper()
    return {"$": "USD", "€": "EUR", "£": "GBP"}.get(normalized, normalized)[:3]


def salary_period(value: Any) -> str:
    normalized = re.sub(r"[\s/_-]", "", normalize_text(optional_string(value)))
    if normalized in {"hr", "hour", "perhour", "hourly", "1hour"}:
        return "hour"
    if normalized in {"day", "perday", "daily", "1day"}:
        return "day"
    if normalized in {"week", "perweek", "weekly", "1week"}:
        return "week"
    if normalized in {"month", "mo", "permonth", "monthly", "1month"}:
        return "month"
    return "year"


def employment_type(value: Any) -> str | None:
    normalized = re.sub(r"[\s_-]", "", normalize_text(optional_string(value)))
    values = {
        "fulltime": "full_time",
        "parttime": "part_time",
        "contract": "contractor",
        "contractor": "contractor",
        "temporary": "temporary",
        "temp": "temporary",
        "intern": "internship",
        "internship": "internship",
    }
    return values.get(normalized)


def infer_employment_type(description: str) -> str | None:
    patterns = (
        ("full_time", r"(?<!\w)full[- ]time(?!\w)"),
        ("part_time", r"(?<!\w)part[- ]time(?!\w)"),
        ("contractor", r"(?<!\w)(?:contractor|contract position)(?!\w)"),
        ("temporary", r"(?<!\w)temporary(?!\w)"),
        ("internship", r"(?<!\w)internship(?!\w)"),
    )
    normalized = normalize_text(description)
    return next((name for name, pattern in patterns if re.search(pattern, normalized)), None)


def parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        try:
            return datetime.fromtimestamp(timestamp, tz=UTC)
        except (OSError, OverflowError, ValueError):
            return None
    text = optional_string(value)
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def external_id(company: AtsCompany, value: Any, source_url: str) -> str:
    identifier = optional_string(value)
    if identifier is None:
        identifier = urlsplit(source_url).path.rstrip("/").rsplit("/", maxsplit=1)[-1]
    candidate = f"{company.identifier}:{identifier}" if identifier else ""
    if candidate and len(candidate) <= 255:
        return candidate
    return hashlib.sha256(f"{company.identifier}:{source_url}".encode()).hexdigest()


def required_string(value: Any, field_name: str) -> str:
    result = optional_string(value)
    if result is None:
        raise AtsSourceError(f"ATS listing is missing {field_name}.")
    return result


def optional_string(value: Any) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def _string_values(values: Iterable[Any]) -> Iterable[str]:
    for value in values:
        if isinstance(value, str):
            yield value
        elif isinstance(value, Mapping):
            yield from _string_values(value.values())
        elif isinstance(value, Iterable) and not isinstance(value, bytes):
            yield from _string_values(value)
