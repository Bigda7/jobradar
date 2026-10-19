from dataclasses import replace

import httpx
import pytest
from tests.unit.test_dou_discovery import rss
from tests.unit.test_matching_context import candidate

from jobradar.domain.enums import WorkMode
from jobradar.matching.profile import BOHDAN_PROFILE
from jobradar.matching.scorer import score_candidate
from jobradar.sources.dou_jobs import DouJobsRequestBudgetExceeded, DouJobsSource, _work_mode
from jobradar.sources.request_budget import MemoryRequestBudget


@pytest.mark.parametrize(
    "description",
    (
        "Remote or hybrid work. The choice is yours!",
        "Work fully remote around the globe, or in a hybrid work model from our office hubs.",
        "Flexible working schedule with remote, office-based, or hybrid work options.",
        "Віддалений або гібридний графік роботи на вибір.",
        "Гібридний формат або дистанційна робота на вибір.",
        "Удаленная работа или гибридный формат на выбор.",
        "Hybrid work or fully remote work are available.",
        "Remote or hybrid work. Python is required. Office visits are optional.",
        "Remote or hybrid work, Python is required, office visits are optional.",
        "Remote or hybrid work. No mandatory office attendance.",
        "Remote or hybrid work. Office attendance is not required.",
    ),
)
def test_explicit_remote_choice_is_retained(description: str) -> None:
    assert (
        _work_mode("Junior Python Developer в Example, віддалено", description, ())
        is WorkMode.REMOTE
    )


@pytest.mark.parametrize(
    "description",
    (
        "Remote/Hybrid work.",
        "Hybrid work is available.",
        "Remote or hybrid work. Two days per week in the office.",
        "Remote or hybrid work. You must work onsite in Kyiv.",
        "Remote or hybrid work. Office attendance is mandatory.",
        "Remote or hybrid work. Hybrid work is required.",
        "No remote or hybrid work is available.",
        "Remote or hybrid work is not available.",
        "Remote work is not allowed.",
        "You must attend the office in Kyiv.",
        "Remote work is not allowed. Hybrid work is available.",
        "Віддалений або гібридний графік. Обов'язкова присутність в офісі Києва.",
        "Віддалений або гібридний графік. Два дні на тиждень в офісі Києва.",
    ),
)
def test_required_or_ambiguous_attendance_is_not_assumed_remote(description: str) -> None:
    assert _work_mode("Junior Developer в Example, Київ, віддалено", description, ()) is None


def test_remote_choice_does_not_invent_a_remote_location_tag() -> None:
    assert _work_mode("Junior Developer в Example, Київ", "Remote or hybrid work.", ()) is None


def test_required_prague_hybrid_remains_eligible() -> None:
    assert (
        _work_mode(
            "Junior Developer в Example, Prague, remote",
            "Hybrid work is required. Two days in the office.",
            BOHDAN_PROFILE.local_city_aliases,
        )
        is WorkMode.HYBRID
    )


def test_remote_option_does_not_remove_candidate_country_restrictions() -> None:
    description = "Remote or hybrid work. Only candidates residing in Ukraine can apply."
    mode = _work_mode("Junior Developer в Example, Київ, віддалено", description, ())
    assert mode is WorkMode.REMOTE
    result = score_candidate(
        candidate(description=description, location_text="Kyiv, remote", work_mode=mode),
        BOHDAN_PROFILE,
    )
    assert result.score == 0


@pytest.mark.parametrize("count,limited", ((199, False), (200, False), (201, True)))
async def test_default_cap_processes_two_hundred_records_and_marks_only_real_omissions(
    count: int, limited: bool
) -> None:
    payload = rss(
        *(
            (str(820000 + index), "Junior Developer в Example, remote", "React")
            for index in range(count)
        )
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text=payload))
    ) as client:
        source = DouJobsSource(client=client)
        rows = [row async for row in source.fetch()]
    assert len(rows) == min(count, 200)
    assert source.consume_run_metrics().limit_reached is limited


async def test_hourly_budget_survives_runs_and_counts_failed_attempts() -> None:
    budget = MemoryRequestBudget(lambda: 1000.0)
    for _ in range(89):
        assert await budget.reserve("dou_rss")
    calls = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: calls.append(request) or httpx.Response(502))
    ) as client:
        source = DouJobsSource(client=client)
        source.configure_request_budget(budget)
        with pytest.raises(RuntimeError, match="Every configured"):
            _ = [row async for row in source.fetch()]
        restarted = DouJobsSource(client=client)
        restarted.configure_request_budget(budget)
        with pytest.raises(DouJobsRequestBudgetExceeded, match="hourly"):
            _ = [row async for row in restarted.fetch()]
    assert len(calls) == 1
    assert restarted.consume_run_metrics().page_count == 0


async def test_redirect_hop_requires_another_reservation_before_network() -> None:
    budget = MemoryRequestBudget(lambda: 1000.0)
    for _ in range(89):
        assert await budget.reserve("dou_rss")
    calls = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: (
                calls.append(str(request.url))
                or httpx.Response(
                    302, headers={"Location": "/vacancies/feeds/?remote&redirected=1"}
                )
            )
        )
    ) as client:
        source = DouJobsSource(client=client)
        source.configure_request_budget(budget)
        with pytest.raises(DouJobsRequestBudgetExceeded):
            _ = [row async for row in source.fetch()]
    assert len(calls) == 1
    assert source.consume_run_metrics().page_count == 1


async def test_successful_feed_is_preserved_when_shared_hourly_budget_expires() -> None:
    budget = MemoryRequestBudget(lambda: 1000.0)
    for _ in range(89):
        assert await budget.reserve("dou_rss")
    calls = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: (
                calls.append(request)
                or httpx.Response(200, text=rss(("820001", "Developer в Example, remote", "React")))
            )
        )
    ) as client:
        source = DouJobsSource(
            client=client,
            additional_feed_urls=("https://jobs.dou.ua/vacancies/feeds/?remote&search=python",),
        )
        source.configure_request_budget(budget)
        rows = [row async for row in source.fetch()]
    assert [row.external_id for row in rows] == ["820001"]
    assert len(calls) == 1
    assert source.consume_warnings() == ("DOU Jobs hourly RSS request budget exhausted.",)


@pytest.mark.parametrize(
    "title",
    (
        "Talent Acquisition Specialist",
        "Junior Technical Talent Acquisition Partner",
        "Talent Acquisition Manager",
        "Junior/Middle User Acquisition Manager",
        "User Acquisition Specialist",
    ),
)
def test_acquisition_roles_do_not_qualify_through_technology_keywords(title: str) -> None:
    assert score_candidate(candidate(title=title), BOHDAN_PROFILE).score == 0


@pytest.mark.parametrize(
    "title",
    (
        "Junior Data Acquisition Engineer",
        "Junior Software Engineer for a talent acquisition product",
        "Junior User Acquisition Software Developer",
    ),
)
def test_technical_acquisition_roles_are_not_blanket_rejected(title: str) -> None:
    assert score_candidate(candidate(title=title), BOHDAN_PROFILE).score > 0


def test_acquisition_company_and_description_do_not_change_developer_fit() -> None:
    base = candidate()
    result = score_candidate(
        replace(
            base,
            company="Talent Acquisition Labs",
            description=base.description + " Build a talent acquisition platform.",
        ),
        BOHDAN_PROFILE,
    )
    assert result.score == score_candidate(base, BOHDAN_PROFILE).score
