import asyncio
import gzip
from collections.abc import AsyncIterator

import httpx
import pytest

from jobradar.notifications.currency import CurrencyConversionError, NbuExchangeRateClient
from jobradar.sources.djinni import DjinniSource, DjinniSourceError
from jobradar.sources.dou_jobs import DouJobsSource, DouJobsSourceError
from jobradar.sources.robota_ua import RobotaUaSource, RobotaUaSourceError
from jobradar.sources.workua import WorkUaSource, WorkUaSourceError


class ResponseStream(httpx.AsyncByteStream):
    def __init__(self, failure: str):
        self.failure = failure
        self.closed = False
        self.reads = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        if self.failure == "slow_body":
            await asyncio.sleep(1)
        self.reads += 1
        yield gzip.compress(b"x" * 5_000_001) if self.failure == "compressed" else b"ignored"

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.parametrize("adapter", ["djinni", "workua", "robota_ua", "dou_jobs", "nbu"])
@pytest.mark.parametrize("failure", ["declared", "compressed", "slow_body"])
async def test_each_external_document_path_enforces_bounded_downloads(
    adapter, failure, monkeypatch
):
    monkeypatch.setattr("jobradar.sources.djinni.RSS_REQUEST_ATTEMPTS", 1)
    stream = ResponseStream(failure)
    headers = {"Content-Length": "100000000"} if failure == "declared" else {}
    if failure == "compressed":
        headers["Content-Encoding"] = "gzip"
    timeout = 0.02 if failure == "slow_body" else 1
    errors = {
        "djinni": DjinniSourceError,
        "workua": WorkUaSourceError,
        "robota_ua": RobotaUaSourceError,
        "dou_jobs": DouJobsSourceError,
        "nbu": CurrencyConversionError,
    }
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, headers=headers, stream=stream))
    ) as client:
        with pytest.raises(errors[adapter]):
            if adapter == "djinni":
                _ = [
                    row
                    async for row in DjinniSource(
                        client=client, request_timeout_seconds=timeout
                    ).fetch()
                ]
            elif adapter == "workua":
                await WorkUaSource(
                    client=client, request_timeout_seconds=timeout, retry_attempts=1
                )._request(client, "https://reader.test/page", headers={})
            elif adapter == "robota_ua":
                await RobotaUaSource(
                    client=client, request_timeout_seconds=timeout, retry_attempts=1
                )._request(client, "https://reader.test/page")
            elif adapter == "dou_jobs":
                _ = [
                    row
                    async for row in DouJobsSource(
                        client=client, request_timeout_seconds=timeout
                    ).fetch()
                ]
            else:
                await NbuExchangeRateClient(
                    client=client, request_timeout_seconds=timeout
                ).fetch_rates()
    assert stream.closed
    assert stream.reads == (1 if failure == "compressed" else 0)
