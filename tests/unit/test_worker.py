from contextlib import asynccontextmanager

import pytest

from jobradar import worker


@asynccontextmanager
async def _acquired_lock(*args, **kwargs):  # type: ignore[no-untyped-def]
    yield True


@pytest.mark.asyncio
async def test_background_worker_cycle_contains_failures_and_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failing_cycle(*, force_sources: bool = False) -> None:
        raise RuntimeError("simulated cycle failure")

    monkeypatch.setattr(worker, "try_transaction_advisory_lock", _acquired_lock)
    monkeypatch.setattr(worker, "run_cycle", failing_cycle)

    succeeded = await worker.run_worker_cycle(
        force_sources=False,
        failure_retry_seconds=30,
    )

    assert succeeded is False


@pytest.mark.asyncio
async def test_manual_worker_cycle_returns_a_nonzero_failure_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failing_cycle(*, force_sources: bool = False) -> None:
        raise RuntimeError("simulated manual failure")

    monkeypatch.setattr(worker, "try_transaction_advisory_lock", _acquired_lock)
    monkeypatch.setattr(worker, "run_cycle", failing_cycle)

    with pytest.raises(RuntimeError, match="simulated manual failure"):
        await worker.run_worker_cycle(
            force_sources=True,
            failure_retry_seconds=30,
        )


@pytest.mark.asyncio
async def test_worker_cycle_refuses_to_overlap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @asynccontextmanager
    async def unavailable_lock(*args, **kwargs):  # type: ignore[no-untyped-def]
        yield False

    async def unexpected_cycle(*, force_sources: bool = False) -> None:
        raise AssertionError("run_cycle must not start without the lock")

    monkeypatch.setattr(worker, "try_transaction_advisory_lock", unavailable_lock)
    monkeypatch.setattr(worker, "run_cycle", unexpected_cycle)

    with pytest.raises(worker.WorkerCycleLockUnavailable):
        await worker.run_worker_cycle(
            force_sources=True,
            failure_retry_seconds=30,
        )
