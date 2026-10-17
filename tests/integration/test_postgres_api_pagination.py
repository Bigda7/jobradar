import pytest
from tests.unit.test_api_pagination_queries import (
    PAGE_CASES,
    verify_page_query_shape,
    verify_page_results,
)

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("path", ["/jobs", "/matches"])
async def test_postgres_single_canonical_query(postgres_engine, postgres_session_factory, path):
    await verify_page_query_shape(postgres_engine, postgres_session_factory, path)


@pytest.mark.parametrize("path,order,source_filter", PAGE_CASES)
async def test_postgres_page_fields_and_canonical_safety(
    postgres_session_factory, path, order, source_filter
):
    await verify_page_results(postgres_session_factory, path, order, source_filter)
