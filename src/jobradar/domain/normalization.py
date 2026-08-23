import hashlib
import json
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from jobradar.domain.models import NormalizedOpportunity

TRACKING_QUERY_PREFIXES = ("utm_",)
TRACKING_QUERY_KEYS = {"fbclid", "gclid", "ref", "source"}


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip().casefold()


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
        normalize_text(opportunity.company),
        normalize_text(opportunity.title),
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
