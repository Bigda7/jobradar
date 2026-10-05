import re
from dataclasses import dataclass
from typing import Any

from jobradar.domain.enums import OpportunityKind, WorkMode
from jobradar.domain.normalization import normalize_text
from jobradar.matching.models import MatchCandidate
from jobradar.matching.profile import SearchProfile


@dataclass(frozen=True, slots=True)
class LocationEligibility:
    rejection_concern: str | None = None
    concerns: tuple[str, ...] = ()


def evaluate_location_eligibility(
    candidate: MatchCandidate, profile: SearchProfile
) -> LocationEligibility:
    requirements = candidate.raw_data.get("applicantLocationRequirements")
    concerns: list[str] = []
    if requirements and profile.residence_country_aliases:
        decisions = [_residence_allowed(item, profile) for item in _items(requirements)]
        if decisions and all(value is False for value in decisions):
            return LocationEligibility(
                rejection_concern=(
                    "Rejected: candidate residence requirements exclude your location in Czechia."
                )
            )
        if True not in decisions:
            concerns.append(
                "Candidate residence requirements could not be verified "
                "from the available metadata."
            )
    if isinstance(candidate.raw_data.get("rss"), dict):
        if candidate.raw_data.get("metadata_origin") != "job_page":
            concerns.append(
                "Candidate residence restrictions are not verified yet; page metadata is pending."
            )
        elif candidate.raw_data.get("metadata_rss_updated_at") != candidate.raw_data.get(
            "sourceUpdatedAt"
        ):
            concerns.append("Candidate residence metadata may be stale after a vacancy update.")
        elif not requirements:
            concerns.append(
                "The source does not specify permitted candidate residence; "
                "eligibility is uncertain."
            )

    if candidate.kind is OpportunityKind.EMPLOYMENT:
        if candidate.work_mode in {WorkMode.ONSITE, WorkMode.HYBRID}:
            if not _local_office(candidate, profile):
                return LocationEligibility(
                    rejection_concern=(
                        "Rejected: office or hybrid work requires a confirmed workplace in Prague."
                    )
                )
        elif candidate.work_mode is not WorkMode.REMOTE:
            return LocationEligibility(
                rejection_concern="Rejected: work must be remote, or office/hybrid in Prague."
            )
    return LocationEligibility(concerns=tuple(concerns))


def _residence_allowed(item: Any, profile: SearchProfile) -> bool | None:
    if not isinstance(item, dict):
        return None
    address = item.get("address")
    address = address if isinstance(address, dict) else {}
    countries = _names(address.get("addressCountry"))
    if not countries and item.get("@type") == "Country":
        countries = _names(item.get("name"))
    regions = _names(address.get("addressRegion"))
    if not countries and not regions:
        regions = _names(item.get("name"))
    cities = _names(address.get("addressLocality"))
    if countries and not _allows_any(
        countries, profile.residence_country_aliases + profile.residence_region_aliases
    ):
        return False
    if cities and not any(_is_local_city(city, profile) for city in cities):
        return False
    if regions and not _allows_any(
        regions,
        profile.residence_region_aliases
        + profile.residence_country_aliases
        + profile.local_city_aliases,
    ):
        return None
    return True if countries or regions or cities else None


def _local_office(candidate: MatchCandidate, profile: SearchProfile) -> bool:
    if not profile.local_city_aliases:
        return False
    locations = candidate.raw_data.get("jobLocation")
    if locations:
        for item in _items(locations):
            if not isinstance(item, dict):
                continue
            address = item.get("address")
            address = address if isinstance(address, dict) else {}
            countries = _names(address.get("addressCountry"))
            if countries and not _allows_any(countries, profile.residence_country_aliases):
                continue
            cities = _names(address.get("addressLocality"))
            if cities:
                if any(_is_local_city(city, profile) for city in cities):
                    return True
                continue
            if _mentions_city(str(item.get("name") or ""), profile):
                return True
            # Djinni often supplies only the country; require explicit office-location text.
            if countries and _explicit_local_office(candidate, profile):
                return True
        return False
    return _mentions_city(candidate.location_text or "", profile) or _explicit_local_office(
        candidate, profile
    )


def _explicit_local_office(candidate: MatchCandidate, profile: SearchProfile) -> bool:
    city = (
        "(?:"
        + "|".join(re.escape(normalize_text(value)) for value in profile.local_city_aliases)
        + ")"
    )
    patterns = (
        rf"^(?:office(?: location)?|workplace|job location|work location|location)"
        rf"\s*(?:is\s+|in\s+|:\s*){city}(?!\w)",
        rf"^{city}(?!\w)\s+(?:office|workplace)\b",
    )
    for line in (candidate.description or "").splitlines():
        text = normalize_text(line).lstrip(" -*•")
        if re.search(r"\b(?:no|not|without|closed|unavailable|headquarters|hq)\b", text):
            continue
        if any(re.search(pattern, text) is not None for pattern in patterns):
            return True
    return False


def _mentions_city(text: str, profile: SearchProfile) -> bool:
    normalized = normalize_text(text)
    return any(_is_local_city(part.strip(), profile) for part in re.split(r"[,;/|]", normalized))


def _is_local_city(text: str, profile: SearchProfile) -> bool:
    normalized = normalize_text(text)
    return any(
        re.fullmatch(rf"{re.escape(normalize_text(city))}(?:\s+\d+)?", normalized) is not None
        for city in profile.local_city_aliases
    )


def _allows_any(values: tuple[str, ...], allowed: tuple[str, ...]) -> bool:
    aliases = {normalize_text(value) for value in allowed}
    return any(normalize_text(value) in aliases for value in values)


def _items(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


def _names(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if isinstance(value, dict):
        return _names(value.get("name"))
    if isinstance(value, list):
        return tuple(name for item in value for name in _names(item))
    return ()
