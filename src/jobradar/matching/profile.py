from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class SkillRule:
    name: str
    aliases: tuple[str, ...]
    weight: int
    supporting: bool = False
    discovery: bool = False


@dataclass(frozen=True, slots=True)
class NegativeSkillRule:
    name: str
    aliases: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SearchProfile:
    profile_id: str
    rules_version: str
    english_level: str
    czech_level: str
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
    residence_country_aliases: tuple[str, ...] = ()
    residence_region_aliases: tuple[str, ...] = ()
    local_city_aliases: tuple[str, ...] = ()


BOHDAN_PROFILE = SearchProfile(
    profile_id="bohdan",
    rules_version="bohdan-multi-source-v16-context-aware-portfolio",
    residence_country_aliases=(
        "CZ",
        "CZE",
        "Czechia",
        "Czech Republic",
        "Cesko",
        "Česko",
        "Česká republika",
        "Чехия",
        "Чехія",
    ),
    residence_region_aliases=(
        "Europe",
        "EU",
        "European Union",
        "EEA",
        "European Economic Area",
        "EMEA",
        "Worldwide",
        "World",
        "Anywhere",
    ),
    local_city_aliases=("Prague", "Praha", "Прага"),
    english_level="B2",
    czech_level="A2",
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
        SkillRule("React", ("react", "react.js", "reactjs"), 10, discovery=True),
        SkillRule("JavaScript", ("javascript", "js es6", "es6+"), 8, discovery=True),
        SkillRule("TypeScript", ("typescript",), 5, discovery=True),
        SkillRule("Python", ("python",), 10, discovery=True),
        SkillRule("Django", ("django",), 12, discovery=True),
        SkillRule("Django REST Framework", ("django rest framework", "drf"), 6),
        SkillRule("PostgreSQL", ("postgresql", "postgres"), 6),
        SkillRule("REST APIs", ("rest api", "restful api", "rest apis"), 5),
        SkillRule("Vite", ("vite",), 4),
        SkillRule("HTML/CSS", ("html5", "css3", "html", "css"), 4),
        SkillRule("Shopify/Liquid", ("shopify", "liquid", "online store 2.0"), 10, discovery=True),
        SkillRule("SQLAlchemy", ("sqlalchemy",), 4),
        SkillRule("FastAPI", ("fastapi", "fast api"), 10, discovery=True),
        SkillRule("Next.js", ("next.js", "nextjs", "next js"), 10, discovery=True),
        SkillRule("Node.js", ("node.js", "nodejs", "node js"), 8, discovery=True),
        SkillRule("Express", ("express", "express.js", "expressjs"), 6, discovery=True),
        SkillRule("WebSockets", ("websocket", "websockets", "web sockets"), 4),
        SkillRule(
            "LLM API integration",
            (
                "llm api",
                "llm integration",
                "tool calling",
                "function calling",
                "openai api",
                "deepseek",
            ),
            6,
            discovery=True,
        ),
        SkillRule("Tailwind CSS", ("tailwind", "tailwindcss"), 2, supporting=True),
        SkillRule("Prisma", ("prisma",), 2, supporting=True),
        SkillRule("Validation", ("zod", "pydantic"), 2, supporting=True),
        SkillRule("Browser automation", ("playwright",), 3, supporting=True),
        SkillRule("Testing", ("pytest", "vitest"), 2, supporting=True),
        SkillRule("Docker", ("docker",), 2, supporting=True),
        SkillRule("AWS", ("aws", "amazon web services"), 2, supporting=True),
        SkillRule("CI/CD", ("github actions", "ci/cd"), 2, supporting=True),
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
        NegativeSkillRule("NestJS", ("nestjs", "nest.js")),
        NegativeSkillRule("React Native", ("react native",)),
        NegativeSkillRule("Semantic graph engineering", ("sparql", "shacl", "metaphactory")),
        NegativeSkillRule("Solidity", ("solidity",)),
        NegativeSkillRule("Smart contracts", ("smart contract", "smart contracts")),
        NegativeSkillRule(
            "Blockchain engineering", ("blockchain developer", "blockchain engineer")
        ),
        NegativeSkillRule("ML engineering", ("machine learning engineer", "ml engineer")),
        NegativeSkillRule(
            "Model training",
            (
                "model training",
                "train models",
                "training models",
                "pytorch",
                "tensorflow",
                "computer vision",
            ),
        ),
    ),
)
