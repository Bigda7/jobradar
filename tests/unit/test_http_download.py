import asyncio
import gzip
import zlib
from collections.abc import AsyncIterator
from time import monotonic

import httpx
import pytest

from jobradar.http_download import DownloadBudget, DownloadLimitError, bounded_get
from jobradar.sources.detail_cache import get_with_backoff


class Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes], delay: float = 0) -> None:
        self.chunks = chunks
        self.delay = delay
        self.delivered = 0
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            if self.delay:
                await asyncio.sleep(self.delay)
            self.delivered += 1
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


def encode(data: bytes, encoding: str) -> bytes:
    if encoding == "gzip":
        return gzip.compress(data)
    if encoding == "deflate":
        return zlib.compress(data)
    if encoding == "raw-deflate":
        compressor = zlib.compressobj(wbits=-15)
        return compressor.compress(data) + compressor.flush()
    return data


@pytest.mark.parametrize("encoding", ["identity", "gzip", "deflate", "raw-deflate"])
@pytest.mark.parametrize("chunk_size", [1, 17, 10000])
async def test_decodes_complete_documents_with_bounded_chunks(encoding, chunk_size):
    data = "Synthetic document with \u00e9. ".encode() * 20
    wire = encode(data, encoding)
    stream = Chunks([wire[i : i + chunk_size] for i in range(0, len(wire), chunk_size)])
    headers = {"content-encoding": "deflate" if encoding == "raw-deflate" else encoding}
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream, headers=headers))
    ) as client:
        result = await bounded_get(
            client, "https://source.test/feed", maximum_bytes=len(data), timeout_seconds=1
        )
    assert result.content == data
    assert result.headers.get("content-encoding") is None
    assert stream.closed


@pytest.mark.parametrize("length", [None, "1", "-1", "invalid"])
async def test_stops_before_reading_the_oversized_tail(length):
    stream = Chunks([b"a" * 100, b"x", b"not consumed"])
    headers = {} if length is None else {"Content-Length": length}
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream, headers=headers))
    ) as client:
        with pytest.raises(DownloadLimitError, match="decoded"):
            await bounded_get(client, "https://source.test", maximum_bytes=100, timeout_seconds=1)
    assert stream.delivered == 2 and stream.closed


async def test_old_buffered_get_control_consumes_the_entire_tail():
    stream = Chunks([b"a" * 100, b"x", b"old unbounded tail"])
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream))
    ) as client:
        response = await client.get("https://source.test")
    assert len(response.content) > 100 and stream.delivered == 3 and stream.closed


@pytest.mark.parametrize("encoding", ["gzip", "deflate", "raw-deflate"])
async def test_compressed_expansion_is_limited_before_an_unbounded_allocation(encoding):
    compressed = encode(b"x" * 1_000_000, encoding)
    assert len(compressed) < 3000
    stream = Chunks([compressed, b"unread tail"])
    budget = DownloadBudget(monotonic() + 1)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                stream=stream,
                headers={"Content-Encoding": "deflate" if encoding == "raw-deflate" else encoding},
            )
        )
    ) as client:
        with pytest.raises(DownloadLimitError):
            await bounded_get(
                client, "https://source.test", maximum_bytes=4096, timeout_seconds=1, budget=budget
            )
    assert stream.delivered == 1 and stream.closed
    assert budget.consumed_bytes == 4097


@pytest.mark.parametrize("encoding", ["gzip", "deflate"])
@pytest.mark.parametrize("invalid", ["corrupt", "truncated"])
async def test_rejects_incomplete_or_corrupt_compression(encoding, invalid):
    data = encode(b"document", encoding)
    data = data[:-2] if invalid == "truncated" else b"not compressed"
    stream = Chunks([data])
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, stream=stream, headers={"content-encoding": encoding})
        )
    ) as client:
        with pytest.raises(httpx.DecodingError):
            await bounded_get(client, "https://source.test", timeout_seconds=1)
    assert stream.closed


async def test_concatenated_gzip_members_share_one_output_limit():
    wire = gzip.compress(b"first") + gzip.compress(b"second")
    for maximum, accepted in [(11, True), (10, False)]:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200, stream=Chunks([wire]), headers={"content-encoding": "gzip"}
                )
            )
        ) as client:
            if accepted:
                result = await bounded_get(
                    client, "https://source.test", maximum_bytes=maximum, timeout_seconds=1
                )
                assert result.content == b"firstsecond"
            else:
                with pytest.raises(DownloadLimitError):
                    await bounded_get(
                        client, "https://source.test", maximum_bytes=maximum, timeout_seconds=1
                    )


@pytest.mark.parametrize("encoding", ["br", "zstd", "gzip, deflate", "unknown"])
async def test_unrequested_encoding_is_observable_without_body_read(encoding):
    stream = Chunks([b"unread"])
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, stream=stream, headers={"content-encoding": encoding})
        )
    ) as client:
        with pytest.raises(DownloadLimitError, match="encoding"):
            await bounded_get(client, "https://source.test", timeout_seconds=1)
    assert stream.delivered == 0 and stream.closed


@pytest.mark.parametrize("stage", ["headers", "body"])
async def test_total_deadline_includes_headers_and_slow_dripping_body(stage):
    stream = Chunks([b"a"] * 100, delay=0.005)

    async def handler(request):
        if stage == "headers":
            await asyncio.sleep(1)
        return httpx.Response(200, stream=stream)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.ReadTimeout, match="total deadline"):
            await asyncio.wait_for(
                bounded_get(client, "https://source.test", timeout_seconds=0.03), timeout=1
            )
    if stage == "body":
        assert stream.delivered < 100 and stream.closed


@pytest.mark.parametrize(
    "target",
    [
        "https://other.test/",
        "http://source.test/",
        "https://source.test:8443/",
        "https://user@source.test/",
    ],
)
async def test_redirect_cannot_change_origin_downgrade_or_add_credentials(target):
    stream = Chunks([b"redirect body must not be read"])
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(302, headers={"location": target}, stream=stream)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True
    ) as client:
        with pytest.raises(DownloadLimitError, match="redirect"):
            await bounded_get(client, "https://source.test/first", timeout_seconds=1)
    assert len(requests) == 1 and stream.delivered == 0 and stream.closed


async def test_same_origin_redirect_closes_unread_body_and_counts_each_request():
    stream = Chunks([b"unused redirect"])
    counts = []

    def handler(request):
        if request.url.path == "/first":
            return httpx.Response(302, headers={"location": "/last"}, stream=stream)
        return httpx.Response(200, text="complete")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await bounded_get(
            client,
            "https://source.test/first",
            timeout_seconds=1,
            before_request=lambda: counts.append(1),
        )
    assert result.text == "complete" and len(counts) == 2
    assert stream.delivered == 0 and stream.closed


async def test_redirect_loop_has_a_hop_limit():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(302, headers={"location": "/loop"}, stream=Chunks([]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(DownloadLimitError, match="hop limit"):
            await bounded_get(client, "https://source.test", timeout_seconds=1)
    assert len(calls) == 6


async def test_shared_run_budget_survives_failed_and_successful_requests():
    budget = DownloadBudget(monotonic() + 1, maximum_bytes=5)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, stream=Chunks([b"abcd"]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert (
            await bounded_get(client, "https://source.test", timeout_seconds=1, budget=budget)
        ).content == b"abcd"
        with pytest.raises(DownloadLimitError, match="run"):
            await bounded_get(client, "https://source.test", timeout_seconds=1, budget=budget)
        with pytest.raises(DownloadLimitError, match="run"):
            await bounded_get(client, "https://source.test", timeout_seconds=1, budget=budget)
    assert len(calls) == 2


@pytest.mark.parametrize("status", [302, 403, 429, 503])
async def test_status_errors_are_returned_without_loading_bodies(status):
    stream = Chunks([b"unused response"])
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(status, stream=stream, headers={"Retry-After": "2"})
        )
    ) as client:
        response = await bounded_get(
            client, "https://source.test", timeout_seconds=1, follow_redirects=False
        )
    assert response.status_code == status and response.headers["Retry-After"] == "2"
    assert stream.delivered == 0 and stream.closed


@pytest.mark.parametrize("length", ["1000000", "9" * 5000])
async def test_oversized_declared_length_is_rejected_without_reading_or_integer_overflow(length):
    stream = Chunks([b"not read"])
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, stream=stream, headers={"content-length": length})
        )
    ) as client:
        with pytest.raises(DownloadLimitError, match="wire"):
            await bounded_get(client, "https://source.test", maximum_bytes=100, timeout_seconds=1)
    assert stream.delivered == 0 and stream.closed


async def test_wire_limit_bounds_many_empty_compressed_members():
    data = gzip.compress(b"") * 4000
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, stream=Chunks([data]), headers={"content-encoding": "gzip"}
            )
        )
    ) as client:
        with pytest.raises(DownloadLimitError, match="wire"):
            await bounded_get(client, "https://source.test", maximum_bytes=1, timeout_seconds=1)


async def test_task_cancellation_closes_response_without_becoming_a_success():
    stream = Chunks([b"unused"], delay=10)
    started = asyncio.Event()

    def handler(request):
        started.set()
        return httpx.Response(200, stream=stream)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        task = asyncio.create_task(bounded_get(client, "https://source.test", timeout_seconds=1))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert stream.closed


async def test_expired_run_window_does_not_start_a_request():
    calls = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: calls.append(request))
    ) as client:
        with pytest.raises(httpx.ReadTimeout):
            await bounded_get(
                client,
                "https://source.test",
                timeout_seconds=1,
                budget=DownloadBudget(monotonic() - 1),
            )
    assert not calls


async def test_https_upgrade_cannot_be_followed_by_a_downgrade():
    calls = []

    def handler(request):
        calls.append(request)
        target = (
            "https://source.test/secure" if request.url.scheme == "http" else "http://source.test/"
        )
        return httpx.Response(302, headers={"location": target})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(DownloadLimitError, match="redirect"):
            await bounded_get(client, "http://source.test/", timeout_seconds=1)
    assert len(calls) == 2


async def test_redirect_without_location_is_not_a_key_error():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(302))
    ) as client:
        result = await bounded_get(client, "https://source.test", timeout_seconds=1)
    assert result.status_code == 302


@pytest.mark.parametrize("retry_after", ["nan", "inf", "9999", "-inf"])
async def test_nonfinite_or_excessive_retry_waits_do_not_extend_the_window(retry_after):
    calls = []
    stream = Chunks([b"unused rate limit body"])

    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": retry_after}, stream=stream)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await get_with_backoff(client, "https://source.test", timeout_seconds=0.1)
    assert result.status_code == 429 and len(calls) == 1
    assert stream.delivered == 0 and stream.closed


async def test_retry_wait_does_not_overrun_the_shared_run_deadline():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "1"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await get_with_backoff(
            client,
            "https://source.test",
            timeout_seconds=1,
            budget=DownloadBudget(monotonic() + 0.1),
        )
    assert result.status_code == 429 and len(calls) == 1
