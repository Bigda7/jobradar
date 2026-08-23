from decimal import Decimal

import httpx
import pytest

from jobradar.notifications.currency import (
    CurrencyConversionError,
    ExchangeRates,
    NbuExchangeRateClient,
    format_converted_range,
)


def test_converted_range_always_returns_usd_uah_and_czk() -> None:
    rates = ExchangeRates(
        {
            "USD": Decimal("40"),
            "UAH": Decimal("1"),
            "CZK": Decimal("2"),
            "EUR": Decimal("50"),
        }
    )

    result = format_converted_range(
        Decimal("1000"),
        Decimal("1500"),
        "EUR",
        "month",
        rates,
    )

    assert result == (
        "USD: 1,250-1,875 / месяц",
        "UAH: 50,000-75,000 / месяц",
        "CZK: 25,000-37,500 / месяц",
    )


@pytest.mark.asyncio
async def test_nbu_client_parses_official_rate_contract() -> None:
    payload = [
        {"cc": "CZK", "rate": 2.1, "exchangedate": "22.08.2026"},
        {"cc": "USD", "rate": 42.0, "exchangedate": "22.08.2026"},
        {"cc": "EUR", "rate": 49.0, "exchangedate": "22.08.2026"},
    ]
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    ) as client:
        rates = await NbuExchangeRateClient(
            rates_url="https://bank.test/exchange?json",
            client=client,
        ).fetch_rates()

    assert rates.effective_date == "22.08.2026"
    assert rates.convert(Decimal("100"), "EUR", "USD") == Decimal("116.6666666666666666666666667")


def test_unknown_source_currency_is_rejected() -> None:
    rates = ExchangeRates({"USD": Decimal("40"), "CZK": Decimal("2")})

    with pytest.raises(CurrencyConversionError, match="GBP"):
        rates.convert(Decimal("100"), "GBP", "USD")
