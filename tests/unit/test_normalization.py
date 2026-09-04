from jobradar.domain.enums import WorkMode
from jobradar.domain.models import NormalizedOpportunity
from jobradar.domain.normalization import (
    build_canonical_key,
    build_content_hash,
    canonicalize_url,
    normalize_company_identity,
    normalize_text,
    normalize_title_identity,
)


def test_normalize_text_collapses_whitespace_and_case() -> None:
    assert normalize_text("  Junior   PYTHON Developer ") == "junior python developer"


def test_company_identity_ignores_common_legal_suffixes() -> None:
    assert normalize_company_identity("Example Labs, LLC") == "example labs"
    assert normalize_company_identity("Example Labs s.r.o.") == "example labs"
    assert normalize_company_identity("ТОВ Example Labs") == "example labs"


def test_title_identity_ignores_only_non_semantic_context_tags() -> None:
    assert normalize_title_identity("Python Developer (Remote / EU)") == "python developer"
    assert normalize_title_identity("Python Developer — Remote") == "python developer"
    assert normalize_title_identity("Python Developer (Django / FastAPI)") == (
        "python developer (django fastapi)"
    )
    assert normalize_title_identity("Senior Python Developer") != normalize_title_identity(
        "Junior Python Developer"
    )


def test_canonicalize_url_removes_tracking_and_fragment() -> None:
    assert (
        canonicalize_url(
            "HTTPS://Example.COM:443/jobs/42/?utm_source=feed&ref=email&page=2#details"
        )
        == "https://example.com/jobs/42?page=2"
    )


def test_hashes_are_stable_and_sensitive_to_content() -> None:
    opportunity = NormalizedOpportunity(
        title="Junior Django Developer",
        company="Example Labs",
        location_text="Remote Europe",
        work_mode=WorkMode.REMOTE,
    )
    same_opportunity = opportunity.model_copy()
    changed_opportunity = opportunity.model_copy(update={"title": "Junior React Developer"})

    assert build_canonical_key(opportunity) == build_canonical_key(same_opportunity)
    assert build_canonical_key(opportunity) != build_canonical_key(changed_opportunity)
    assert build_content_hash(opportunity, {"id": 1}) == build_content_hash(
        same_opportunity, {"id": 1}
    )
    assert build_content_hash(opportunity, {"id": 1}) != build_content_hash(opportunity, {"id": 2})
