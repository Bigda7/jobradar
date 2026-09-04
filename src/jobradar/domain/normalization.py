import hashlib
import json
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from jobradar.domain.models import NormalizedOpportunity

TRACKING_QUERY_PREFIXES = ("utm_",)
TRACKING_QUERY_KEYS = {"fbclid", "gclid", "ref", "source"}
COMPANY_LEGAL_SUFFIX_PATTERN = re.compile(
    r"(?:\s+(?:llc|incorporated|inc|ltd|limited|gmbh|plc|corp|corporation|"
    r"s\s*r\s*o|a\s*s|тов|тзов|пп|фоп))+$"
)
COMPANY_LEGAL_PREFIX_PATTERN = re.compile(
    r"^(?:(?:llc|ltd|gmbh|corp|corporation|s\s*r\s*o|тов|тзов|пп|фоп)\s+)+"
)
TITLE_CONTEXT_MARKERS = {
    "remote",
    "remote work",
    "віддалено",
    "віддалена робота",
    "europe",
    "eu",
    "worldwide",
    "anywhere",
    "kyiv",
    "kiev",
    "prague",
    "praha",
    "full time",
    "full-time",
}
BRACKETED_TITLE_TEXT_PATTERN = re.compile(r"[\[(]([^\])]+)[\])]")
TRAILING_TITLE_CONTEXT_PATTERN = re.compile(
    r"\s+(?:[-–—|·])\s*(?:remote|remote work|віддалено|віддалена робота|"
    r"europe|eu|worldwide|anywhere|kyiv|kiev|prague|praha|full[- ]time)\s*$"
)


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip().casefold()


def normalize_company_identity(value: str | None) -> str:
    normalized = normalize_text(value)
    normalized = re.sub(r"[^\w]+", " ", normalized, flags=re.UNICODE)
    normalized = normalized.replace("_", " ")
    normalized = re.sub(r"\s+", " ", normalized).strip()
    normalized = COMPANY_LEGAL_PREFIX_PATTERN.sub("", normalized).strip()
    return COMPANY_LEGAL_SUFFIX_PATTERN.sub("", normalized).strip()


def normalize_title_identity(value: str | None) -> str:
    normalized = normalize_text(value)

    def remove_context_group(match: re.Match[str]) -> str:
        group = normalize_text(match.group(1))
        parts = [part.strip() for part in re.split(r"[,/|·]+", group) if part.strip()]
        if parts and all(part in TITLE_CONTEXT_MARKERS for part in parts):
            return " "
        return match.group(0)

    normalized = BRACKETED_TITLE_TEXT_PATTERN.sub(remove_context_group, normalized)
    normalized = TRAILING_TITLE_CONTEXT_PATTERN.sub("", normalized)
    normalized = re.sub(r"[-–—_/.,:;]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def canonicalize_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    hostname = (parsed.hostname or "").casefold()
    if not hostname:
        raise ValueError("URL must contain a hostname.")

    default_port = (parsed.scheme.casefold() == "https" and parsed.port == 443) or (
        parsed.scheme.casefold() == "http" and parsed.port == 80
    )
    netloc = hostname if parsed.port is None or default_port else f"{hostname}:{parsed.port}"
    path = parsed.path.rstrip("/") or "/"
    query = urlencode(
        sorted(
            (key, item)
            for key, item in parse_qsl(parsed.query, keep_blank_values=True)
            if key.casefold() not in TRACKING_QUERY_KEYS
            and not key.casefold().startswith(TRACKING_QUERY_PREFIXES)
        )
    )
    return urlunsplit((parsed.scheme.casefold(), netloc, path, query, ""))


def build_canonical_key(opportunity: NormalizedOpportunity) -> str:
    components = [
        normalize_company_identity(opportunity.company),
        normalize_title_identity(opportunity.title),
        normalize_text(opportunity.location_text),
        opportunity.work_mode.value,
    ]
    return hashlib.sha256("|".join(components).encode("utf-8")).hexdigest()


def build_content_hash(
    opportunity: NormalizedOpportunity,
    raw_payload: dict[str, Any],
) -> str:
    content = {
        "normalized": opportunity.model_dump(mode="json"),
        "raw": raw_payload,
    }
    serialized = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
