import asyncio
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest

from jobradar.sources.workua import WorkUaSource, WorkUaSourceError


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "transport", 500, 502, 503, 504, 429])
async def test_transient_failure_is_retried_within_attempt_cap(
    failure: str | int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    delays = []

    async def pause(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", pause)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            if failure == "timeout":
                raise httpx.ReadTimeout("Synthetic timeout", request=request)
            if failure == "transport":
                raise httpx.ConnectError("Synthetic connection error", request=request)
            return httpx.Response(int(failure), headers={"Retry-After": "0"})
        return httpx.Response(200, text="recovered")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = WorkUaSource(client=client)
        assert await source._request(client, "https://reader.test/page", headers={}) == "recovered"
    assert calls == 2 and len(delays) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 403, 404, 410])
async def test_non_transient_status_is_not_retried(status: int) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = WorkUaSource(client=client)
        with pytest.raises(WorkUaSourceError) as error:
            await source._request(client, "https://reader.test/page", headers={})
    assert calls == 1 and error.value.status_code == status


@pytest.mark.asyncio
@pytest.mark.parametrize("retry_after", ["60", "nan", "inf"])
async def test_excessive_or_nonfinite_retry_after_does_not_trigger_early_retry(
    retry_after: str,
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={"Retry-After": retry_after})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = WorkUaSource(client=client)
        with pytest.raises(WorkUaSourceError):
            await source._request(client, "https://reader.test/page", headers={})
    assert calls == 1


@pytest.mark.asyncio
async def test_http_date_retry_after_is_respected(monkeypatch: pytest.MonkeyPatch) -> None:
    delays = []

    async def pause(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", pause)
    header = format_datetime(datetime.now(UTC) + timedelta(seconds=12), usegmt=True)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(503, headers={"Retry-After": header})
        )
    ) as client:
        source = WorkUaSource(client=client)
        with pytest.raises(WorkUaSourceError):
            await source._request(client, "https://reader.test/page", headers={})
    assert len(delays) == 1 and 10 <= delays[0] <= 12


@pytest.mark.asyncio
async def test_invalid_response_is_recorded_without_retrying_or_leaking_transport_text() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.DecodingError(
            "Synthetic credential text must not be persisted", request=request
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = WorkUaSource(client=client)
        with pytest.raises(WorkUaSourceError) as error:
            await source._request(client, "https://reader.test/page", headers={})
    assert calls == 1
    assert str(error.value) == "Work.ua reader response is unavailable."


@pytest.mark.asyncio
async def test_outer_timeout_bounds_a_slow_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    from time import monotonic

    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        await asyncio.Event().wait()
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = WorkUaSource(client=client, request_timeout_seconds=0.02)
        source._run_deadline = monotonic() + 0.04
        with pytest.raises(WorkUaSourceError) as error:
            await source._request(client, "https://reader.test/page", headers={})
    assert calls == 1 and error.value.reason == "timeout"


@pytest.mark.asyncio
@pytest.mark.parametrize("constraint", ["request_budget", "deadline"])
async def test_run_deadline_and_request_budget_block_network_access(constraint: str) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = WorkUaSource(client=client)
        if constraint == "request_budget":
            source._network_request_limit = 0
        else:
            source._run_deadline = 0
        with pytest.raises(WorkUaSourceError) as error:
            await source._request(client, "https://reader.test/page", headers={})
        assert error.value.reason == constraint
    assert calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("header", ["5", "60", "nan", "inf"])
async def test_retry_after_on_last_attempt_stops_other_logical_requests(
    monkeypatch: pytest.MonkeyPatch, header: str
) -> None:
    requests: list[str] = []

    async def pause(seconds: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", pause)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        return httpx.Response(503, headers={"Retry-After": "0" if len(requests) == 1 else header})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = WorkUaSource(client=client)
        with pytest.raises(WorkUaSourceError) as failure:
            await source._request(client, "https://reader.test/first", headers={})
        assert failure.value.status_code == 503
        with pytest.raises(WorkUaSourceError) as failure:
            await source._request(client, "https://reader.test/second", headers={})
        assert failure.value.status_code == 503
    assert requests == ["/first", "/first"]
