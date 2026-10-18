import asyncio
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from time import monotonic

import httpx
import pytest
from pydantic import ValidationError
from structlog.testing import capture_logs
from tests.unit.test_workua_source import CHALLENGE_PAGE, DETAIL_PAGE, SEARCH_PAGE

from jobradar import http_download
from jobradar.config import Settings
from jobradar.http_download import DownloadBudget, DownloadLimitError, bounded_get
from jobradar.sources import robota_ua, workua
from jobradar.sources.base import CachedListing
from jobradar.sources.reader_pacing import ReaderRequestPacer
from jobradar.sources.registry import build_source_registry
from jobradar.sources.robota_ua import RobotaUaSource, RobotaUaSourceError
from jobradar.sources.workua import WorkUaSource, WorkUaSourceError

REAL_SLEEP = asyncio.sleep


class Clock:
    def __init__(self) -> None:
        self.now = monotonic()
        self.delays: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.delays.append(seconds)
        self.now += seconds
        await REAL_SLEEP(0)

    def pacer(self) -> ReaderRequestPacer:
        return ReaderRequestPacer(clock=lambda: self.now, sleep=self.sleep)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    value = Clock()
    for module in (http_download, workua, robota_ua):
        monkeypatch.setattr(module, "monotonic", lambda: value.now)
    monkeypatch.setattr(asyncio, "sleep", value.sleep)
    return value


@pytest.mark.parametrize("interval", [0, -1, 1.5, 3.0, float("inf"), float("nan")])
def test_reader_interval_cannot_exceed_the_anonymous_request_rate(interval: float) -> None:
    with pytest.raises(ValueError):
        ReaderRequestPacer(interval)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, reader_request_interval_seconds=interval)


def test_registry_shares_one_pacer_without_changing_source_budgets() -> None:
    settings = Settings(_env_file=None, reader_request_interval_seconds=4)
    sources = {source.name: source for source in build_source_registry(settings)}
    work = sources["workua"]
    robot = sources["robota_ua"]
    assert isinstance(work, WorkUaSource) and isinstance(robot, RobotaUaSource)
    assert work._reader_pacer is robot._reader_pacer
    assert work._reader_pacer is not None and work._reader_pacer._interval == 4
    assert work._max_searches_per_run == 5 and robot._max_searches_per_run == 7
    assert len(work._search_urls) == len(robot._search_urls) == 18
    assert (
        settings.workua_poll_interval_seconds == settings.robota_ua_poll_interval_seconds == 21600
    )
    assert work._max_items == 75 and robot._max_items == 100


async def test_all_reader_attempt_starts_share_spacing_and_rolling_minute_limit(
    clock: Clock,
) -> None:
    starts: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        starts.append(clock.now)
        return httpx.Response(200, text="synthetic")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        pacer = clock.pacer()
        work = WorkUaSource(client=client, reader_pacer=pacer)
        robot = RobotaUaSource(client=client, reader_pacer=pacer)
        for _ in range(10):
            await asyncio.gather(
                work._request(client, "https://reader.test/work", headers={}),
                robot._request(client, "https://reader.test/robot"),
                robot._request(client, "https://reader.test/robot-detail"),
            )
    assert len(starts) == 30
    assert all(right - left >= 3.2 - 1e-6 for left, right in zip(starts, starts[1:], strict=False))
    assert max(sum(start <= other < start + 60 for other in starts) for start in starts) <= 20


@pytest.mark.parametrize("status", [429, 502, 503])
async def test_workua_retries_are_paced_including_zero_retry_after(
    clock: Clock, status: int
) -> None:
    starts = []

    def handler(request: httpx.Request) -> httpx.Response:
        starts.append(clock.now)
        return httpx.Response(status if len(starts) == 1 else 200, headers={"Retry-After": "0"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = WorkUaSource(client=client, reader_pacer=clock.pacer())
        await source._request(client, "https://reader.test/search", headers={})
    assert len(starts) == 2 and starts[1] - starts[0] == pytest.approx(3.2)
    assert source._network_requests == 2


async def test_redirect_hops_are_paced_and_counted(clock: Clock) -> None:
    starts = []

    def handler(request: httpx.Request) -> httpx.Response:
        starts.append(clock.now)
        if len(starts) == 1:
            return httpx.Response(302, headers={"Location": "/final"})
        return httpx.Response(200, text="complete")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = WorkUaSource(client=client, reader_pacer=clock.pacer())
        assert await source._request(client, "https://reader.test/search", headers={}) == "complete"
    assert starts[1] - starts[0] == pytest.approx(3.2)
    assert source._network_requests == 2


async def test_robota_retry_and_api_page_paths_share_the_same_gate(clock: Clock) -> None:
    starts = []

    def handler(request: httpx.Request) -> httpx.Response:
        starts.append(clock.now)
        return httpx.Response(
            429 if len(starts) == 1 else 200, headers={"Retry-After": "0"}, text="synthetic"
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = RobotaUaSource(client=client, reader_pacer=clock.pacer())
        await source._fetch_api_detail("123")
        await source._fetch_page("https://robota.ua/company1/vacancy123")
    assert len(starts) == 3
    assert all(
        right - left == pytest.approx(3.2) for left, right in zip(starts, starts[1:], strict=False)
    )


async def test_exhausted_shared_cooldown_preserves_the_original_rate_limit_reason(
    clock: Clock,
) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(429)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = WorkUaSource(client=client, reader_pacer=clock.pacer())
        with pytest.raises(WorkUaSourceError) as error:
            await source._request(client, "https://reader.test/search", headers={})
    assert error.value.status_code == 429 and source._run_stop_error is error.value
    assert len(calls) == 1


async def test_format_fallback_and_detail_requests_do_not_bypass_spacing(clock: Clock) -> None:
    starts = []

    def handler(request: httpx.Request) -> httpx.Response:
        starts.append(clock.now)
        return httpx.Response(
            200, text=DETAIL_PAGE if "jobs/" in request.url.path else CHALLENGE_PAGE
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = WorkUaSource(client=client, reader_pacer=clock.pacer())
        with pytest.raises(WorkUaSourceError, match="challenge"):
            await source._fetch_search_cards("https://www.work.ua/en/jobs-remote-python/")
        assert await source._fetch_description("https://www.work.ua/en/jobs/123/")
    assert len(starts) == 3
    assert starts[1] - starts[0] == pytest.approx(3.2)
    assert starts[2] - starts[1] == pytest.approx(3.2)


@pytest.mark.parametrize("header", ["120", "nan", "inf", "invalid", None])
async def test_reader_cooldown_cannot_be_bypassed_by_another_source(
    clock: Clock, header: str | None
) -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        return httpx.Response(429, headers={} if header is None else {"Retry-After": header})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        pacer = clock.pacer()
        source = WorkUaSource(client=client, reader_pacer=pacer, retry_attempts=1)
        with pytest.raises(WorkUaSourceError):
            await source._request(client, "https://reader.test/work", headers={})
        robot = RobotaUaSource(client=client, reader_pacer=pacer)
        with pytest.raises(RobotaUaSourceError, match="remaining request window"):
            await robot._request(client, "https://reader.test/robot")
    assert requests == ["/work"] and clock.delays == []


@pytest.mark.parametrize("status", [429, 503])
async def test_http_date_cooldown_and_expiry_are_respected(clock: Clock, status: int) -> None:
    pacer = clock.pacer()
    header = format_datetime(datetime.now(UTC) + timedelta(seconds=12), usegmt=True)
    pacer.observe(httpx.Response(status, headers={"Retry-After": header}))
    start = clock.now
    await pacer.wait(clock.now + 30)
    assert 10 <= clock.now - start <= 12
    clock.now += 120
    before = len(clock.delays)
    await pacer.wait(clock.now + 30)
    assert len(clock.delays) == before


async def test_cooldown_extension_during_a_queued_wait_is_rechecked(clock: Clock) -> None:
    pacer = clock.pacer()
    await pacer.wait(clock.now + 30)
    original_sleep = pacer._sleep
    extended = False

    async def extend(seconds: float) -> None:
        nonlocal extended
        if not extended:
            extended = True
            pacer.observe(httpx.Response(503, headers={"Retry-After": "10"}))
        await original_sleep(seconds)

    pacer._sleep = extend
    start = clock.now
    await pacer.wait(clock.now + 30)
    assert clock.now - start == pytest.approx(10)


async def test_pacing_deadline_preserves_cached_description_and_discoveries(clock: Clock) -> None:
    requests = []
    fetched = datetime.now(UTC) - timedelta(days=2)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if len(requests) == 1:
            source._run_deadline = clock.now + 1
        return httpx.Response(
            200, text=SEARCH_PAGE if "jobs-remote" in request.url.path else DETAIL_PAGE
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = WorkUaSource(
            search_urls=("https://www.work.ua/en/jobs-remote-python/",),
            max_pages_per_search=1,
            client=client,
            reader_pacer=clock.pacer(),
        )
        source.prime_listing_cache(
            {
                "8441545": CachedListing(
                    {
                        "description": "Full cached description",
                        "detail_status": "complete",
                    },
                    fetched,
                )
            }
        )
        rows = [row async for row in source.fetch()]
        assert len(requests) == len(rows) == 1
        assert rows[0].payload["description"] == "Full cached description"
        assert rows[0].payload["detail_error"] == "deadline"
        assert rows[0].payload["detail_status"] == "cached" and rows[0].detail_fetched_at == fetched
        assert source.consume_run_metrics().detail_failure_count == 1
        assert source.consume_warnings() and clock.delays == []
        clock.now += 120
        source.begin_run()
        recovered = [row async for row in source.fetch()]
    assert len(recovered) == 1 and recovered[0].payload["detail_status"] == "complete"
    assert recovered[0].payload["detail_error"] is None
    assert source.consume_run_metrics().detail_failure_count == 0
    assert source.consume_warnings() == () and len(requests) == 3


async def test_expired_pacing_does_not_consume_a_network_reservation(clock: Clock) -> None:
    pacer = clock.pacer()
    await pacer.wait(clock.now + 10)
    calls = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: pytest.fail("Network used"))
    ) as client:
        with pytest.raises(DownloadLimitError, match="remaining request window"):
            await bounded_get(
                client,
                "https://reader.test/search",
                timeout_seconds=1,
                wait_before_request=pacer.wait,
                before_request=lambda: calls.append(True),
            )
    assert calls == [] and clock.delays == []


async def test_shared_byte_budget_is_rechecked_after_an_async_wait() -> None:
    budget = DownloadBudget(monotonic() + 30, maximum_bytes=1)
    reservations = []

    async def wait(deadline: float) -> None:
        budget.consume(1)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: pytest.fail("Network used after budget exhaustion"))
    ) as client:
        with pytest.raises(DownloadLimitError) as error:
            await bounded_get(
                client,
                "https://reader.test/page",
                timeout_seconds=1,
                budget=budget,
                wait_before_request=wait,
                before_request=lambda: reservations.append(True),
            )
    assert error.value.reason == "run_limit" and reservations == []


async def test_cancelling_a_waiter_releases_the_lock_without_spending_a_slot() -> None:
    entered = asyncio.Event()

    async def blocked_sleep(seconds: float) -> None:
        entered.set()
        await asyncio.Event().wait()

    pacer = ReaderRequestPacer(sleep=blocked_sleep)
    await pacer.wait(monotonic() + 30)
    reserved = pacer._next_request_at
    task = asyncio.create_task(pacer.wait(monotonic() + 30))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not pacer._lock.locked() and pacer._next_request_at == reserved


async def test_outer_download_deadline_bounds_waiting_for_a_busy_pacer() -> None:
    pacer = ReaderRequestPacer()
    await pacer._lock.acquire()
    try:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: pytest.fail("Network used"))
        ) as client:
            with pytest.raises(httpx.ReadTimeout):
                await bounded_get(
                    client,
                    "https://reader.test/search",
                    timeout_seconds=0.01,
                    wait_before_request=pacer.wait,
                )
    finally:
        pacer._lock.release()


@pytest.mark.parametrize("failure", ["transport", "timeout", "decoding"])
async def test_transport_diagnostics_are_sanitized(failure: str) -> None:
    secret = "synthetic-private-transport-marker"

    def handler(request: httpx.Request) -> httpx.Response:
        error_type = {
            "transport": httpx.ConnectError,
            "timeout": httpx.ReadTimeout,
            "decoding": httpx.DecodingError,
        }[failure]
        raise error_type(secret, request=request)

    with capture_logs() as logs:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            source = WorkUaSource(client=client, retry_attempts=1)
            with pytest.raises(WorkUaSourceError):
                await source._request(client, "https://reader.test/search", headers={})
    attempts = [row for row in logs if row["event"] == "workua_request_attempt"]
    assert len(attempts) == 1 and attempts[0]["status_code"] is None
    assert attempts[0]["outcome"] == ("invalid_response" if failure == "decoding" else failure)
    assert secret not in str(logs) and "reader.test" not in str(logs)


@pytest.mark.parametrize("status", [200, 403, 502])
async def test_attempt_diagnostics_do_not_log_urls_headers_bodies_or_exception_text(
    status: int,
) -> None:
    secret = "synthetic-private-marker"
    with capture_logs() as logs:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(status, text=secret, headers={"private": secret})
            )
        ) as client:
            source = WorkUaSource(client=client, retry_attempts=1)
            try:
                await source._request(
                    client,
                    "https://reader.test/en/jobs/123/?token=" + secret,
                    headers={"X-Return-Format": "html", "Authorization": secret},
                )
            except WorkUaSourceError:
                assert status != 200
    attempts = [row for row in logs if row["event"] == "workua_request_attempt"]
    assert len(attempts) == 1 and attempts[0]["status_code"] == status
    assert attempts[0]["page_kind"] == "detail" and attempts[0]["response_format"] == "html"
    assert attempts[0]["attempt"] == 1 and attempts[0]["network_requests"] == 1
    assert secret not in str(logs) and "reader.test" not in str(logs)


async def test_run_request_ceiling_uses_the_selected_batch_not_the_entire_pool() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(403))
    ) as client:
        source = WorkUaSource(max_searches_per_run=5, retry_attempts=1, client=client)
        with pytest.raises(WorkUaSourceError):
            _ = [row async for row in source.fetch()]
    assert source._network_request_limit == 2 * (5 * 2 + 75)
