import re
from collections.abc import Mapping, MutableMapping
from typing import Any

_SENSITIVE_KEY_PATTERN = re.compile(
    r"(?:authorization|api[_-]?key|access[_-]?token|oauth[_-]?token|"
    r"bot[_-]?token|password|passwd|secret)",
    re.IGNORECASE,
)
_URL_PASSWORD_PATTERN = re.compile(r"(://[^:/\s]+:)([^@\s]+)(@)")
_QUERY_SECRET_PATTERN = re.compile(
    r"([?&](?:api[_-]?key|access[_-]?token|oauth[_-]?token|token|"
    r"password|secret)=)([^&\s]+)",
    re.IGNORECASE,
)
_ASSIGNMENT_SECRET_PATTERN = re.compile(
    r"((?:authorization|api[_-]?key|access[_-]?token|oauth[_-]?token|"
    r"bot[_-]?token|password|passwd|secret)\s*[=:]\s*)([^\s,;]+)",
    re.IGNORECASE,
)
_BEARER_PATTERN = re.compile(r"(Bearer\s+)([A-Za-z0-9._~+/=-]+)", re.IGNORECASE)
_TELEGRAM_TOKEN_PATTERN = re.compile(r"(?<!\d)(\d{6,12}):([A-Za-z0-9_-]{20,})(?!\w)")
_REDACTED = "[REDACTED]"


def redact_sensitive_text(value: str) -> str:
    redacted = _URL_PASSWORD_PATTERN.sub(rf"\1{_REDACTED}\3", value)
    redacted = _QUERY_SECRET_PATTERN.sub(rf"\1{_REDACTED}", redacted)
    redacted = _BEARER_PATTERN.sub(rf"\1{_REDACTED}", redacted)
    redacted = _ASSIGNMENT_SECRET_PATTERN.sub(rf"\1{_REDACTED}", redacted)
    return _TELEGRAM_TOKEN_PATTERN.sub(_REDACTED, redacted)


def redact_sensitive_value(key: str, value: Any) -> Any:
    if _SENSITIVE_KEY_PATTERN.search(key):
        return _REDACTED
    if isinstance(value, str):
        return redact_sensitive_text(value)
    if isinstance(value, Mapping):
        return {
            nested_key: redact_sensitive_value(str(nested_key), nested_value)
            for nested_key, nested_value in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive_value("", item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_sensitive_value("", item) for item in value)
    return value


def redact_structlog_event(
    _: Any,
    __: str,
    event_dict: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    for key, value in tuple(event_dict.items()):
        event_dict[key] = redact_sensitive_value(key, value)
    return event_dict
