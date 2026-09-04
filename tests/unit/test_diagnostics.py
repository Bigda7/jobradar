from collections.abc import AsyncIterator

import pytest

from jobradar.diagnostics import diagnose_source, format_diagnostic_table
from jobradar.domain.enums import OpportunityKind, WorkMode
from jobradar.domain.models import NormalizedOpportunity, RawListing
from jobradar.sources.base import BaseSource


class DiagnosticSource(BaseSource):
    name = "diagnostic"
    display_name = "Diagnostic"
    opportunity_kind = OpportunityKind.EMPLOYMENT

    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail

    async def fetch(self) -> AsyncIterator[RawListing]:
        if self._fail:
            raise RuntimeError("diagnostic failure")
        for index in range(3):
            yield RawListing(
                external_id=str(index),
                source_url=f"https://example.com/jobs/{index}",
                payload={"title": f"Job {index}"},
            )

    def normalize(self, raw_listing: RawListing) -> NormalizedOpportunity:
        return NormalizedOpportunity(
            kind=self.opportunity_kind,
            title=str(raw_listing.payload["title"]),
            company="Example",
            description="React and Django",
            location_text="Europe",
            work_mode=WorkMode.REMOTE,
        )


@pytest.mark.asyncio
async def test_diagnostics_samples_and_normalizes_source() -> None:
    result = await diagnose_source(DiagnosticSource(), sample_size=2)

    assert result.status == "OK"
    assert result.discovered == 2
    assert result.normalized == 2
    assert result.error is None
    assert "Diagnostic" in format_diagnostic_table((result,))


@pytest.mark.asyncio
async def test_diagnostics_reports_source_failure() -> None:
    result = await diagnose_source(DiagnosticSource(fail=True))

    assert result.status == "FAIL"
    assert result.discovered == 0
    assert result.normalized == 0
    assert result.error == "diagnostic failure"
