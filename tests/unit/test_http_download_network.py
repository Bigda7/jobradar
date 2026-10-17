import asyncio
import gzip
from contextlib import asynccontextmanager

import httpx
import pytest

from jobradar.http_download import DownloadLimitError, bounded_get


@asynccontextmanager
async def serve(handler):
    tasks = set()
    writers = set()

    async def connection(reader, writer):
        task = asyncio.current_task()
        tasks.add(task)
        writers.add(writer)
        try:
            await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 1)
            await handler(reader, writer)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writers.discard(writer)
            tasks.discard(task)
            writer.close()

    server = await asyncio.start_server(connection, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}/document"
    finally:
        server.close()
        await server.wait_closed()
        for writer in tuple(writers):
            writer.close()
        pending = tuple(tasks)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


async def test_real_http_gzip_is_counted_after_bounded_decompression():
    wire = gzip.compress(b"x" * 100_000)

    async def handler(reader, writer):
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Encoding: gzip\r\nContent-Length: "
            + str(len(wire)).encode()
            + b"\r\nConnection: close\r\n\r\n"
            + wire
        )
        await writer.drain()

    async with serve(handler) as url, httpx.AsyncClient(trust_env=False) as client:
        with pytest.raises(DownloadLimitError, match="decoded"):
            await bounded_get(client, url, timeout_seconds=1, maximum_bytes=1024)


@pytest.mark.parametrize("stage", ["headers", "body"])
async def test_real_http_deadline_rejects_stalled_headers_or_body(stage):
    async def handler(reader, writer):
        if stage == "body":
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 10\r\n\r\nx")
            await writer.drain()
        await asyncio.sleep(2)

    async with serve(handler) as url, httpx.AsyncClient(trust_env=False) as client:
        with pytest.raises(httpx.ReadTimeout):
            await asyncio.wait_for(bounded_get(client, url, timeout_seconds=0.03), 1)


async def test_real_http_oversized_response_disconnects_before_tail_is_sent():
    closed = asyncio.Event()
    sent = 0

    async def handler(reader, writer):
        nonlocal sent
        writer.write(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n")
        chunk = b"x" * 1024
        for _ in range(100):
            writer.write(b"400\r\n" + chunk + b"\r\n")
            try:
                await writer.drain()
            except ConnectionError:
                closed.set()
                return
            sent += 1
            await asyncio.sleep(0.002)
        writer.write(b"0\r\n\r\n")

    async with serve(handler) as url, httpx.AsyncClient(trust_env=False) as client:
        with pytest.raises(DownloadLimitError):
            await bounded_get(client, url, timeout_seconds=1, maximum_bytes=8192)
        await asyncio.wait_for(closed.wait(), 1)
    assert sent < 100
