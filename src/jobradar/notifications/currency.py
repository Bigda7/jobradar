from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Protocol

import httpx

TARGET_CURRENCIES = ("USD", "UAH", "CZK")
DEFAULT_NBU_RATES_URL = "https://bank.gov.ua/NBUStatService/v1/statdirectory/exchange?json"
USER_AGENT = "JobRadar/0.5 (personal job aggregator)"


class CurrencyConversionError(RuntimeError):
    pass


class ExchangeRateProvider(Protocol):
    async def fetch_rates(self) -> "ExchangeRates": ...


@dataclass(frozen=True, slots=True)
class ExchangeRates:
    uah_per_unit: Mapping[str, Decimal]
    effective_date: str | None = None

    def __post_init__(self) -> None:
        normalized = {code.upper(): Decimal(str(rate)) for code, rate in self.uah_per_unit.items()}
        normalized["UAH"] = Decimal("1")
        for code in TARGET_CURRENCIES:
            rate = normalized.get(code)
            if rate is None or rate <= 0:
                raise CurrencyConversionError(f"Exchange rate for {code} is missing or invalid.")
        object.__setattr__(self, "uah_per_unit", normalized)

    def convert(self, amount: Decimal, source_currency: str, target_currency: str) -> Decimal:
        source = source_currency.upper()
        target = target_currency.upper()
        source_rate = self.uah_per_unit.get(source)
        target_rate = self.uah_per_unit.get(target)
        if source_rate is None or source_rate <= 0:
            raise CurrencyConversionError(f"Exchange rate for {source} is unavailable.")
        if target_rate is None or target_rate <= 0:
            raise CurrencyConversionError(f"Exchange rate for {target} is unavailable.")
        return amount * source_rate / target_rate


class NbuExchangeRateClient:
    def __init__(
        self,
        rates_url: str = DEFAULT_NBU_RATES_URL,
        request_timeout_seconds: float = 20.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._rates_url = rates_url
        self._request_timeout_seconds = request_timeout_seconds
        self._client = client

    async def fetch_rates(self) -> ExchangeRates:
        if self._client is not None:
            return await self._request(self._client)

        timeout = httpx.Timeout(self._request_timeout_seconds)
        async with httpx.AsyncClient(
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=timeout,
        ) as client:
            return await self._request(client)

    async def _request(self, client: httpx.AsyncClient) -> ExchangeRates:
        try:
            response = await client.get(self._rates_url)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise CurrencyConversionError(f"NBU exchange-rate request failed: {error}") from error
        return parse_nbu_exchange_rates(payload)


def parse_nbu_exchange_rates(payload: Any) -> ExchangeRates:
    if not isinstance(payload, list):
        raise CurrencyConversionError("NBU exchange-rate response must be a list.")

    rates: dict[str, Decimal] = {"UAH": Decimal("1")}
    effective_date: str | None = None
    for item in payload:
        if not isinstance(item, dict):
            continue
        code = item.get("cc")
        value = item.get("rate")
        if not isinstance(code, str) or value is None:
            continue
        try:
            rate = Decimal(str(value))
        except (InvalidOperation, ValueError):
            continue
        if rate <= 0:
            continue
        rates[code.upper()] = rate
        date_value = item.get("exchangedate")
        if effective_date is None and isinstance(date_value, str):
            effective_date = date_value
    return ExchangeRates(rates, effective_date=effective_date)


def format_converted_range(
    minimum: Decimal | None,
    maximum: Decimal | None,
    source_currency: str | None,
    period: str | None,
    rates: ExchangeRates,
) -> tuple[str, ...]:
    if minimum is None and maximum is None:
        return ()
    if not source_currency:
        raise CurrencyConversionError("A currency is required for a published amount.")

    source_minimum = minimum if minimum is not None else maximum
    source_maximum = maximum if maximum is not None else minimum
    if source_minimum is None or source_maximum is None:
        return ()

    period_suffix = f" / {_translate_period(period)}" if period else ""
    result: list[str] = []
    for code in TARGET_CURRENCIES:
        converted_minimum = rates.convert(source_minimum, source_currency, code)
        converted_maximum = rates.convert(source_maximum, source_currency, code)
        value = _format_range(converted_minimum, converted_maximum)
        result.append(f"{code}: {value}{period_suffix}")
    return tuple(result)


def _format_range(minimum: Decimal, maximum: Decimal) -> str:
    formatted_minimum = _format_amount(minimum)
    formatted_maximum = _format_amount(maximum)
    if formatted_minimum == formatted_maximum:
        return formatted_minimum
    return f"{formatted_minimum}-{formatted_maximum}"


def _format_amount(value: Decimal) -> str:
    rounded = value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return f"{rounded:,.0f}"


def _translate_period(value: str) -> str:
    periods = {
        "hour": "час",
        "day": "день",
        "week": "неделя",
        "month": "месяц",
        "year": "год",
        "project": "проект",
    }
    normalized = value.casefold().replace("_", " ")
    return periods.get(normalized, normalized)
