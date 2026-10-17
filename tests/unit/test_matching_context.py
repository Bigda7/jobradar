from dataclasses import replace

import pytest

from jobradar.domain.enums import OpportunityKind, WorkMode
from jobradar.matching.models import MatchCandidate
from jobradar.matching.profile import BOHDAN_PROFILE
from jobradar.matching.requirements import Evidence, RequirementContext
from jobradar.matching.scorer import score_candidate


def candidate(**changes: object) -> MatchCandidate:
    base = MatchCandidate(
        kind=OpportunityKind.EMPLOYMENT,
        title="Junior Frontend React Developer",
        company="Example Company",
        description="Build React and TypeScript interfaces.",
        location_text="Europe",
        work_mode=WorkMode.REMOTE,
        employment_type="full_time",
        contract_type=None,
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        salary_period=None,
        raw_data={},
    )
    return replace(base, **changes)


@pytest.mark.parametrize("technology", ("PHP", "Java", "Angular", "Solidity", "NestJS"))
@pytest.mark.parametrize(
    "description",
    (
        "{technology} is not required.",
        "No prior experience with {technology} required.",
        "No {technology} experience required.",
        "{technology} is not a requirement.",
        "Nice to have: {technology}.",
        "About us:\nOur company builds platforms with {technology}.\nRequirements:\nReact.",
    ),
)
def test_optional_negated_and_company_technology_do_not_lower_fit(
    technology: str,
    description: str,
) -> None:
    base = candidate()
    baseline = score_candidate(base, BOHDAN_PROFILE)
    result = score_candidate(
        replace(
            base, description=base.description + "\n" + description.format(technology=technology)
        ),
        BOHDAN_PROFILE,
    )
    assert result.score == baseline.score


@pytest.mark.parametrize(
    "description",
    (
        "PHP is required. PHP is also nice to have.",
        "PHP optional.\nRequirements:\nPHP.",
        "<h2>Requirements</h2><ul><li>PHP</li></ul><h2>Nice to have</h2><p>React</p>",
        "PHP is not required, but maintaining PHP services is mandatory.",
        "Java is optional. Solidity and smart contract development are mandatory.",
        "React is required.\nResponsibilities:\nMaintain Angular services.",
        "Requirements: Python and Java.",
    ),
)
def test_required_occurrence_wins_over_optional_negation_and_supported_skills(
    description: str,
) -> None:
    result = score_candidate(candidate(description=description), BOHDAN_PROFILE)
    assert result.score < BOHDAN_PROFILE.notification_threshold
    assert any("основная или обязательная" in value for value in result.concerns)


@pytest.mark.parametrize(
    "description",
    (
        "Requirements: Python or Java.",
        "Requirements: Java or Python.",
        "Requirements: React or Angular.",
    ),
)
def test_supported_alternative_does_not_require_every_technology(description: str) -> None:
    result = score_candidate(candidate(description=description), BOHDAN_PROFILE)
    assert result.score >= BOHDAN_PROFILE.notification_threshold
    assert not any("основная или обязательная" in value for value in result.concerns)


@pytest.mark.parametrize("domain", ("Web3", "Blockchain", "AI", "FinTech", "Java"))
def test_company_domain_in_title_does_not_change_frontend_specialization(domain: str) -> None:
    base = candidate()
    result = score_candidate(
        replace(base, title=f"{base.title} for a {domain} product"), BOHDAN_PROFILE
    )
    assert result.score == score_candidate(base, BOHDAN_PROFILE).score


def test_company_name_and_negated_positive_technology_do_not_create_skill_matches() -> None:
    result = score_candidate(
        candidate(
            title="Junior Software Developer",
            company="React Python Next.js Labs",
            description="We do not need experience with React. Python is not required.",
            raw_data={"skills": ["React", "Python"]},
        ),
        BOHDAN_PROFILE,
    )
    assert not result.matched_skills
    assert result.score < BOHDAN_PROFILE.notification_threshold


@pytest.mark.parametrize(
    ("title", "description", "skill"),
    (
        ("Junior FastAPI Developer", "Build REST APIs using FastAPI and PostgreSQL.", "FastAPI"),
        (
            "Junior Next.js Developer",
            "Build Next.js interfaces with TypeScript and Tailwind.",
            "Next.js",
        ),
        ("Junior Backend Developer", "Build APIs with Node.js, Express and TypeScript.", "Node.js"),
        (
            "Junior API Integration Developer",
            "Implement LLM API integration in Python.",
            "LLM API integration",
        ),
    ),
)
def test_demonstrated_portfolio_roles_can_reach_main_matches(
    title: str,
    description: str,
    skill: str,
) -> None:
    result = score_candidate(candidate(title=title, description=description), BOHDAN_PROFILE)
    assert skill in result.matched_skills
    assert result.score >= BOHDAN_PROFILE.notification_threshold


def test_supporting_tools_alone_do_not_qualify_a_role() -> None:
    result = score_candidate(
        candidate(
            title="Junior Software Developer",
            description="Docker AWS GitHub Actions pytest Vitest Playwright Zod Prisma Tailwind.",
        ),
        BOHDAN_PROFILE,
    )
    assert result.score < BOHDAN_PROFILE.notification_threshold


@pytest.mark.parametrize(
    "title", ("Junior/Middle React Developer", "Junior / Mid-level React Developer")
)
def test_mixed_junior_middle_roles_are_visible_but_not_main_matches(title: str) -> None:
    result = score_candidate(candidate(title=title), BOHDAN_PROFILE)
    assert 0 < result.score < BOHDAN_PROFILE.notification_threshold
    assert any("Stretch match" in value for value in result.concerns)


@pytest.mark.parametrize(
    "description",
    (
        "Build React interfaces. 3 years of experience is preferred.",
        "Build React interfaces. 3 years of experience is not required.",
        "About us:\nWe have 10 years of experience.\nRequirements:\nReact.",
    ),
)
def test_optional_or_company_experience_does_not_become_a_hard_requirement(
    description: str,
) -> None:
    assert score_candidate(candidate(description=description), BOHDAN_PROFILE).score >= 55


@pytest.mark.parametrize(
    "description",
    (
        "German is not required. React is required.",
        "Nice to have: German C1.\nRequirements:\nReact.",
        "About us:\nOur company serves German clients.\nRequirements:\nReact.",
    ),
)
def test_optional_or_company_language_does_not_reject_role(description: str) -> None:
    assert score_candidate(candidate(description=description), BOHDAN_PROFILE).score >= 55


def test_optional_language_does_not_hide_later_required_language() -> None:
    result = score_candidate(
        candidate(
            description="German optional. German C1 required for daily work.",
        ),
        BOHDAN_PROFILE,
    )
    assert result.score == 0


def test_evidence_retains_uncertainty_instead_of_inventing_a_requirement() -> None:
    context = RequirementContext.from_text("Frontend Developer", "Legacy system: PHP.", {})
    assert context.evidence(("php",)) is Evidence.UNSPECIFIED


@pytest.mark.parametrize(
    "description",
    (
        "Requirements:\nStrong PostgreSQL/SQL skills: 5+ years.",
        "4+ years of backend development with Node.js and TypeScript.",
        "Three years of professional experience with Python are required.",
        "About us:\nOur company builds products.\nWhat you have\n"
        "Strong hands-on experience with Python and Django 3-4 years.",
    ),
)
def test_required_experience_cannot_disappear_due_to_headings_or_missing_word_experience(
    description: str,
) -> None:
    assert score_candidate(candidate(description=description), BOHDAN_PROFILE).score == 0


def test_inline_optional_heading_resets_requirements_in_flattened_source_text() -> None:
    context = RequirementContext.from_text(
        "Software Developer",
        "Requirements: Python and REST APIs. Nice to have: PyTorch, Java. Benefits: insurance.",
        {},
    )
    assert context.evidence(("python",)) is Evidence.REQUIRED
    assert context.evidence(("pytorch",)) is Evidence.OPTIONAL
    assert context.evidence(("java",)) is Evidence.OPTIONAL


def test_experience_word_does_not_turn_optional_section_into_required_stack() -> None:
    context = RequirementContext.from_text(
        "Developer",
        "Nice to have:\nExperience with Java, PyTorch and PHP.",
        {},
    )
    assert context.evidence(("java",)) is Evidence.OPTIONAL
    assert context.evidence(("pytorch",)) is Evidence.OPTIONAL


def test_company_history_with_expertise_word_is_not_required_candidate_experience() -> None:
    result = score_candidate(
        candidate(
            description=(
                "Having worked in this field for 7+ years, "
                "we have managed to accumulate expertise. "
                "Requirements: 1+ years of commercial experience with React and TypeScript."
            )
        ),
        BOHDAN_PROFILE,
    )
    assert result.score >= 55


def test_company_heading_does_not_hide_a_new_requirement_without_a_section_heading() -> None:
    context = RequirementContext.from_text(
        "Developer",
        "About us:\nWe build tools. Strong hands-on experience with Java is required.",
        {},
    )
    assert context.evidence(("java",)) is Evidence.REQUIRED


def test_one_of_a_framework_list_does_not_require_all_frameworks() -> None:
    result = score_candidate(
        candidate(
            description=("Experience with one of these frameworks (React, Vue.js, Angular).")
        ),
        BOHDAN_PROFILE,
    )
    assert result.score >= 55
    assert not any("основная или обязательная" in item for item in result.concerns)


@pytest.mark.parametrize(
    "title",
    (
        "Search Engine Optimization Specialist",
        "Application Security Researcher",
        "Research and Development Engineer / Reverse Engineering Specialist",
    ),
)
def test_portfolio_tools_do_not_turn_other_professions_into_developer_matches(title: str) -> None:
    assert score_candidate(candidate(title=title), BOHDAN_PROFILE).score == 0


@pytest.mark.parametrize(("level", "score"), (("senior", 0), ("middle", 54)))
def test_structured_seniority_cannot_be_promoted_by_many_portfolio_keywords(
    level: str, score: int
) -> None:
    result = score_candidate(candidate(raw_data={"levels": [level]}), BOHDAN_PROFILE)
    assert result.score == score


def test_explicit_alternative_in_title_is_not_a_mandatory_second_stack() -> None:
    context = RequirementContext.from_text("Junior Python or Java Developer", "", {})
    assert context.evidence(("java",), alternatives=("python",)) is Evidence.OPTIONAL


@pytest.mark.parametrize(
    "description",
    (
        "Requirements: React. Досвід Java не потрібен.",
        "Requirements: React. Опыт PHP не требуется.",
        "Requirements: React. Java буде перевагою.",
    ),
)
def test_multilingual_optional_or_negated_technology_does_not_block(description: str) -> None:
    assert score_candidate(candidate(description=description), BOHDAN_PROFILE).score >= 55
