from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class SkillRule:
    name: str
    aliases: tuple[str, ...]
    weight: int


@dataclass(frozen=True, slots=True)
class SearchProfile:
    profile_id: str
    rules_version: str
    notification_threshold: int
    minimum_monthly_salary_usd: Decimal
    maximum_junior_monthly_salary_usd: Decimal
    minimum_full_time_monthly_salary_usd: Decimal
    excessive_salary_penalty: int
    very_low_salary_penalty: int
    toxic_language_penalty: int
    minimum_freelance_hourly_usd: Decimal
    preferred_freelance_hourly_usd: Decimal
    minimum_freelance_fixed_usd: Decimal
    preferred_freelance_fixed_usd: Decimal
    skills: tuple[SkillRule, ...]


BOHDAN_PROFILE = SearchProfile(
    profile_id="bohdan",
    rules_version="bohdan-multi-source-v9-the-muse",
    notification_threshold=55,
    minimum_monthly_salary_usd=Decimal("1000"),
    maximum_junior_monthly_salary_usd=Decimal("4000"),
    minimum_full_time_monthly_salary_usd=Decimal("400"),
    excessive_salary_penalty=20,
    very_low_salary_penalty=15,
    toxic_language_penalty=5,
    minimum_freelance_hourly_usd=Decimal("8"),
    preferred_freelance_hourly_usd=Decimal("12"),
    minimum_freelance_fixed_usd=Decimal("75"),
    preferred_freelance_fixed_usd=Decimal("150"),
    skills=(
        SkillRule("React", ("react", "react.js", "reactjs"), 10),
        SkillRule("JavaScript", ("javascript", "js es6", "es6+"), 8),
        SkillRule("TypeScript", ("typescript",), 5),
        SkillRule("Python", ("python",), 10),
        SkillRule("Django", ("django",), 12),
        SkillRule("Django REST Framework", ("django rest framework", "drf"), 6),
        SkillRule("PostgreSQL", ("postgresql", "postgres"), 6),
        SkillRule("REST APIs", ("rest api", "restful api", "rest apis"), 5),
        SkillRule("Vite", ("vite",), 4),
        SkillRule("HTML/CSS", ("html5", "css3", "html", "css"), 4),
        SkillRule("Shopify/Liquid", ("shopify", "liquid", "online store 2.0"), 10),
        SkillRule("SQLAlchemy", ("sqlalchemy",), 4),
    ),
)
