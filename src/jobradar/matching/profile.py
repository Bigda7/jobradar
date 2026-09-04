from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class SkillRule:
    name: str
    aliases: tuple[str, ...]
    weight: int


@dataclass(frozen=True, slots=True)
class NegativeSkillRule:
    name: str
    aliases: tuple[str, ...]


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
    negative_skill_penalty: int
    negative_skills: tuple[NegativeSkillRule, ...]


BOHDAN_PROFILE = SearchProfile(
    profile_id="bohdan",
    rules_version="bohdan-multi-source-v12-eligibility",
    notification_threshold=55,
    minimum_monthly_salary_usd=Decimal("1000"),
    maximum_junior_monthly_salary_usd=Decimal("2000"),
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
    negative_skill_penalty=15,
    negative_skills=(
        NegativeSkillRule("PHP", ("php",)),
        NegativeSkillRule("Laravel", ("laravel",)),
        NegativeSkillRule("Symfony", ("symfony",)),
        NegativeSkillRule("Java", ("java",)),
        NegativeSkillRule("Spring", ("spring", "spring boot", "spring framework", "java spring")),
        NegativeSkillRule("C#", ("c#", "c sharp")),
        NegativeSkillRule(".NET", (".net", "dotnet", "asp.net", "asp net")),
        NegativeSkillRule("Ruby", ("ruby",)),
        NegativeSkillRule("Ruby on Rails", ("ruby on rails", "rails developer", "rails framework")),
        NegativeSkillRule(
            "Go",
            (
                "go",
                "golang",
                "go developer",
                "go engineer",
                "go programming",
                "go language",
                "experience with go",
                "knowledge of go",
            ),
        ),
        NegativeSkillRule("Rust", ("rust", "rustlang")),
        NegativeSkillRule("C++", ("c++", "cpp")),
        NegativeSkillRule("Angular", ("angular", "angular.js", "angularjs")),
        NegativeSkillRule("Vue", ("vue", "vue.js", "vuejs", "vue 3", "vue developer")),
        NegativeSkillRule("Nuxt", ("nuxt", "nuxt.js", "nuxtjs")),
        NegativeSkillRule("WordPress", ("wordpress", "wp developer")),
        NegativeSkillRule("Bitrix", ("bitrix", "1c-bitrix", "1c bitrix")),
        NegativeSkillRule("Magento", ("magento", "adobe commerce")),
    ),
)
