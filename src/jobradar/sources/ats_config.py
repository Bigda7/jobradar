from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

import yaml

AtsProvider = Literal["greenhouse", "lever", "ashby"]
SUPPORTED_ATS_PROVIDERS = frozenset({"greenhouse", "lever", "ashby"})


class CompaniesConfigError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AtsCompany:
    name: str
    provider: AtsProvider
    identifier: str


def load_companies_config(path: str | Path) -> tuple[AtsCompany, ...]:
    config_path = Path(path)
    try:
        document: Any = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except OSError as error:
        raise CompaniesConfigError(
            f"Cannot read ATS companies file {config_path}: {error}"
        ) from error
    except yaml.YAMLError as error:
        raise CompaniesConfigError(
            f"Invalid YAML in ATS companies file {config_path}: {error}"
        ) from error

    if not isinstance(document, dict) or not isinstance(document.get("companies"), list):
        raise CompaniesConfigError("ATS companies file must contain a companies list.")

    companies: list[AtsCompany] = []
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(document["companies"], start=1):
        if not isinstance(item, dict):
            raise CompaniesConfigError(f"ATS company entry {index} must be an object.")
        enabled = item.get("enabled", True)
        if not isinstance(enabled, bool):
            raise CompaniesConfigError(
                f"ATS company entry {index} has a non-boolean enabled value."
            )
        if not enabled:
            continue
        name = _required_string(item.get("name"), index, "name")
        provider_value = _required_string(item.get("provider"), index, "provider").casefold()
        if provider_value not in SUPPORTED_ATS_PROVIDERS:
            raise CompaniesConfigError(
                f"ATS company entry {index} uses unsupported provider {provider_value!r}."
            )
        identifier = _required_string(item.get("identifier"), index, "identifier")
        identity = (provider_value, identifier.casefold())
        if identity in seen:
            raise CompaniesConfigError(
                f"ATS company entry {index} duplicates {provider_value}:{identifier}."
            )
        seen.add(identity)
        companies.append(
            AtsCompany(
                name=name,
                provider=cast(AtsProvider, provider_value),
                identifier=identifier,
            )
        )
    return tuple(companies)


def companies_for_provider(
    companies: tuple[AtsCompany, ...],
    provider: AtsProvider,
) -> tuple[AtsCompany, ...]:
    return tuple(company for company in companies if company.provider == provider)


def _required_string(value: Any, index: int, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CompaniesConfigError(f"ATS company entry {index} is missing {field_name}.")
    return value.strip()
