import asyncio
from collections.abc import Iterable
from dataclasses import dataclass
from time import perf_counter
from typing import Any

from jobradar.security import redact_sensitive_text
from jobradar.sources.base import BaseSource


@dataclass(frozen=True, slots=True)
class SourceDiagnostic:
    name: str
    display_name: str
    status: str
    discovered: int
    normalized: int
    duration_seconds: float
    error: str | None = None


async def diagnose_source(
    source: BaseSource,
    *,
    timeout_seconds: float = 30.0,
    sample_size: int | None = None,
) -> SourceDiagnostic:
    started_at = perf_counter()
    discovered = 0
    normalized = 0
    errors: list[str] = []
    iterator = source.fetch()
    try:
        async with asyncio.timeout(timeout_seconds):
            async for listing in iterator:
                discovered += 1
                try:
                    source.normalize(listing)
                    normalized += 1
                except Exception as error:
                    errors.append(redact_sensitive_text(str(error)))
                if sample_size is not None and discovered >= sample_size:
                    break
    except TimeoutError:
        errors.append(f"Timed out after {timeout_seconds:g} seconds.")
    except Exception as error:
        errors.append(redact_sensitive_text(str(error)))
    finally:
        close = getattr(iterator, "aclose", None)
        if close is not None:
            await close()

    errors.extend(source.consume_warnings())
    if errors and discovered == 0:
        status = "FAIL"
    elif errors:
        status = "PARTIAL"
    elif discovered == 0:
        status = "EMPTY"
    else:
        status = "OK"
    return SourceDiagnostic(
        name=source.name,
        display_name=source.display_name,
        status=status,
        discovered=discovered,
        normalized=normalized,
        duration_seconds=perf_counter() - started_at,
        error="; ".join(errors)[:500] or None,
    )


async def diagnose_sources(
    sources: Iterable[BaseSource],
    *,
    timeout_seconds: float = 30.0,
    sample_size: int | None = None,
) -> tuple[SourceDiagnostic, ...]:
    return tuple(
        await asyncio.gather(
            *(
                diagnose_source(
                    source,
                    timeout_seconds=timeout_seconds,
                    sample_size=sample_size,
                )
                for source in sources
            )
        )
    )


def format_diagnostic_table(results: Iterable[SourceDiagnostic]) -> str:
    rows = [
        (
            result.display_name,
            result.status,
            str(result.discovered),
            str(result.normalized),
            f"{result.duration_seconds:.2f}s",
            result.error or "-",
        )
        for result in results
    ]
    headers = ("Source", "Status", "Found", "Valid", "Latency", "Error")
    widths = [
        max(len(headers[index]), *(len(row[index]) for row in rows))
        for index in range(len(headers))
    ] if rows else [len(header) for header in headers]

    def render(row: tuple[str, ...]) -> str:
        return "| " + " | ".join(
            value.ljust(widths[index]) for index, value in enumerate(row)
        ) + " |"

    separator = "+-" + "-+-".join("-" * width for width in widths) + "-+"
    table_rows = (separator, render(headers), separator, *(render(row) for row in rows), separator)
    return "\n".join(table_rows)


def diagnostic_as_dict(result: SourceDiagnostic) -> dict[str, Any]:
    return {
        "name": result.name,
        "display_name": result.display_name,
        "status": result.status,
        "discovered": result.discovered,
        "normalized": result.normalized,
        "duration_seconds": round(result.duration_seconds, 3),
        "error": result.error,
    }
