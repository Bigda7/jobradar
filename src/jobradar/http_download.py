"""Bounded read-only downloads for source documents and supporting public data."""

import asyncio
import math
import zlib
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from time import monotonic

import httpx

MAX_DOCUMENT_BYTES = 5_000_000
MAX_RUN_BYTES = 100_000_000
MAX_RUN_SECONDS = 600.0
MAX_REDIRECTS = 5


class DownloadLimitError(httpx.HTTPError):
    def __init__(self, message: str, *, reason: str = "response_limit") -> None:
        super().__init__(message)
        self.reason = reason


@dataclass
class DownloadBudget:
    deadline: float
    maximum_bytes: int = MAX_RUN_BYTES
    consumed_bytes: int = 0

    def consume(self, count: int) -> None:
        self.consumed_bytes += count
        if self.consumed_bytes > self.maximum_bytes:
            raise DownloadLimitError(
                "Source run exceeded its decoded byte budget.", reason="run_limit"
            )


class _Decoder:
    def __init__(self, encoding: str, maximum_bytes: int) -> None:
        if encoding not in {"", "identity", "gzip", "deflate"}:
            raise DownloadLimitError("Unsupported response content encoding.", reason="encoding")
        self.encoding = encoding
        self.maximum_bytes = maximum_bytes
        self.produced = 0
        self.prefix = b""
        self.decompressor = zlib.decompressobj(31) if encoding == "gzip" else None

    def decode(self, data: bytes) -> bytes:
        if self.encoding in {"", "identity"}:
            self.produced += len(data)
            if self.produced > self.maximum_bytes:
                raise DownloadLimitError("Response exceeded its decoded byte limit.")
            return data
        if self.decompressor is None:
            data = self.prefix + data
            if len(data) < 2:
                self.prefix = data
                return b""
            self.prefix = b""
            wrapped = data[0] & 15 == 8 and (data[0] * 256 + data[1]) % 31 == 0
            self.decompressor = zlib.decompressobj(15 if wrapped else -15)
        output = bytearray()
        try:
            while data:
                if self.decompressor.eof:
                    if self.encoding != "gzip":
                        raise httpx.DecodingError("Unexpected trailing compressed response data.")
                    self.decompressor = zlib.decompressobj(31)
                piece = self.decompressor.decompress(data, self.maximum_bytes - self.produced + 1)
                self.produced += len(piece)
                if self.produced > self.maximum_bytes:
                    raise DownloadLimitError("Response exceeded its decoded byte limit.")
                output.extend(piece)
                data = self.decompressor.unused_data
        except zlib.error as error:
            raise httpx.DecodingError("Invalid compressed response data.") from error
        return bytes(output)

    def finish(self) -> None:
        # flush(length) is an initial buffer size, not a maximum output limit.
        if self.encoding in {"gzip", "deflate"} and (
            self.decompressor is None or not self.decompressor.eof
        ):
            raise httpx.DecodingError("Incomplete compressed response data.")


def _redirect_allowed(original: httpx.URL, target: httpx.URL) -> bool:
    if target.username or target.password or original.host != target.host:
        return False
    if original.scheme == target.scheme and original.port == target.port:
        return target.scheme in {"http", "https"}
    return (
        original.scheme == "http"
        and target.scheme == "https"
        and original.port in {None, 80}
        and target.port in {None, 443}
    )


async def bounded_get(
    client: httpx.AsyncClient,
    url: str,
    *,
    timeout_seconds: float,
    maximum_bytes: int = MAX_DOCUMENT_BYTES,
    headers: dict[str, str] | None = None,
    follow_redirects: bool = True,
    budget: DownloadBudget | None = None,
    on_bytes: Callable[[int], None] | None = None,
    before_request: Callable[[], None] | None = None,
) -> httpx.Response:
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0 or maximum_bytes <= 0:
        raise ValueError("Download limits must be positive and finite.")
    if budget is not None and budget.consumed_bytes >= budget.maximum_bytes:
        raise DownloadLimitError(
            "Source run exhausted its decoded byte budget.", reason="run_limit"
        )
    deadline = monotonic() + timeout_seconds
    if budget is not None:
        deadline = min(deadline, budget.deadline)
    request_headers = dict(headers or {})
    request_headers["Accept-Encoding"] = "gzip, deflate"
    original = httpx.URL(url)
    current = original
    try:
        async with asyncio.timeout_at(deadline):
            for redirect in range(MAX_REDIRECTS + 1):
                if monotonic() >= deadline:
                    raise TimeoutError
                if before_request is not None:
                    before_request()
                async with client.stream(
                    "GET",
                    current,
                    headers=request_headers,
                    follow_redirects=False,
                    timeout=max(0.001, deadline - monotonic()),
                ) as response:
                    if response.has_redirect_location and follow_redirects:
                        target = response.url.join(response.headers["location"])
                        if (
                            redirect == MAX_REDIRECTS
                            or not _redirect_allowed(original, target)
                            or not _redirect_allowed(response.url, target)
                        ):
                            raise DownloadLimitError(
                                "Source redirect exceeded its allowed origin or hop limit.",
                                reason="redirect",
                            )
                        current = target
                        continue
                    content = bytearray()
                    if response.status_code < 300:
                        length = response.headers.get("content-length", "")
                        maximum_wire_bytes = maximum_bytes * 2 + 65536
                        wire_limit = str(maximum_wire_bytes)
                        normalized_length = length.lstrip("0") or "0"
                        if (
                            length.isascii()
                            and length.isdecimal()
                            and (
                                len(normalized_length) > len(wire_limit)
                                or len(normalized_length) == len(wire_limit)
                                and normalized_length > wire_limit
                            )
                        ):
                            raise DownloadLimitError("Response exceeded its wire byte limit.")
                        if response.is_stream_consumed:
                            # Injected buffered transports have already decoded their body.
                            chunks = _buffered_chunks(response.content)
                            decoder = _Decoder("identity", maximum_bytes)
                        else:
                            chunks = response.aiter_raw()
                            decoder = _Decoder(
                                response.headers.get("content-encoding", "").strip().lower(),
                                maximum_bytes,
                            )
                        wire_bytes = 0
                        async for raw in chunks:
                            if monotonic() >= deadline:
                                raise TimeoutError
                            wire_bytes += len(raw)
                            if wire_bytes > maximum_wire_bytes:
                                raise DownloadLimitError("Response exceeded its wire byte limit.")
                            previous_size = decoder.produced
                            try:
                                decoded = decoder.decode(raw)
                            finally:
                                # Failed decompression/size checks also spend the run budget.
                                consumed = decoder.produced - previous_size
                                if budget is not None:
                                    budget.consume(consumed)
                                if on_bytes is not None:
                                    on_bytes(consumed)
                            content.extend(decoded)
                        decoder.finish()
                    result_headers = dict(response.headers)
                    result_headers.pop("content-encoding", None)
                    result_headers.pop("content-length", None)
                    return httpx.Response(
                        response.status_code,
                        headers=result_headers,
                        content=bytes(content),
                        request=response.request,
                    )
    except TimeoutError as error:
        raise httpx.ReadTimeout("Source request exceeded its total deadline.") from error
    raise RuntimeError("Unreachable redirect state")


async def _buffered_chunks(content: bytes) -> AsyncIterator[bytes]:
    yield content
