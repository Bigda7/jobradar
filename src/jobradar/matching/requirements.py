"""Bounded, explainable evidence for technology mentions in vacancy text."""

import re
from dataclasses import dataclass
from enum import IntEnum
from functools import cached_property, lru_cache
from html import unescape
from typing import Any

from jobradar.domain.normalization import normalize_text


class Evidence(IntEnum):
    ABSENT = 0
    DOMAIN = 1
    NEGATED = 2
    OPTIONAL = 3
    UNSPECIFIED = 4
    REQUIRED = 5


REQUIRED = re.compile(
    r"\b(?:required|requirements?|mandatory|must(?:[- ]have)?|essential|proficien\w*|"
    r"strong knowledge|solid knowledge|experience (?:with|in|of)|knowledge of|"
    r"tech(?:nology)? stack|core stack|main stack|primary stack|our stack|responsibilities|"
    r"you will|you'll|your tasks?|build|develop|maintain|implement|"
    r"обязатель\w*|требуется|необходим\w*|основной стек|обов['’]язков\w*|"
    r"вимоги|потріб\w*|досвід|опыт|požadujeme|požadavky|nutn\w*|zkušenosti s)\b"
)
OPTIONAL = re.compile(
    r"\b(?:nice[- ]to[- ]have|good to have|optional|preferred|bonus|advantage|"
    r"(?:a |the )?plus|desirable|желательно|будет плюсом|необязательно|"
    r"бажано|буде плюсом|перевагою|výhodou|volitel\w*)\b"
)
NEGATED = re.compile(
    r"\b(?:no (?:prior |previous |commercial )?(?:experience|knowledge|expertise)"
    r"[^,;]{0,70}?(?:required|necessary|needed)|"
    r"(?:do|does) not (?:need|require)[^,;]{0,70}|"
    r"no [^,;]{1,35} experience (?:required|necessary|needed)|"
    r"not (?:strictly )?(?:required|mandatory|necessary|essential|a requirement|needed)|"
    r"no (?:prior |previous |commercial )?(?:experience|knowledge|expertise)|"
    r"(?:do|does) not (?:need|require)|"
    r"without (?:prior |previous )?(?:experience|knowledge)|"
    r"(?:experience|knowledge).{0,18}(?:not needed|is unnecessary)|"
    r"опыт.{0,35}не (?:нужен|требуется|обязателен)|"
    r"знание.{0,35}не требуется|не требуется|не обязател\w*|"
    r"досвід.{0,35}не (?:потріб\w*|вимагається)|не потребує\w*|"
    r"není (?:nutn\w*|podmínkou)|nevyžadujeme)\b"
)
DOMAIN = re.compile(
    r"\b(?:about (?:us|the company|the product)|our (?:company|product|platform|client)|"
    r"we (?:are|serve|help)|we have (?:managed|been|worked|served|built)|"
    r"having worked in this field|"
    r"company (?:develops|builds|uses)|"
    r"industry|sector|ecosystem|про компанію|о компании|наша компан\w*|"
    r"наша компания|наш продукт|o nás)\b"
)
HEADINGS: tuple[tuple[re.Pattern[str], Evidence], ...] = (
    (
        re.compile(
            r"^(?:requirements?|main requirements|must[- ]haves?|what you (?:need|bring|have)|"
            r"what we expect(?: from you)?|qualifications|"
            r"responsibilities|tech(?:nology)? stack|your tasks|вимоги|обов['’]язки|"
            r"требования|обязанности|požadavky|náplň práce)$"
        ),
        Evidence.REQUIRED,
    ),
    (
        re.compile(
            r"^(?:nice[- ]to[- ]have|optional|bonus|advantages?|preferred|"
            r"буде плюсом|будет плюсом|бажано|додатковою перевагою буде|буде перевагою|výhodou)$"
        ),
        Evidence.OPTIONAL,
    ),
    (
        re.compile(
            r"^(?:about us|about (?:the )?(?:company|product|team)|company overview|"
            r"our (?:company|product)|about the project|benefits|what we offer|we provide|"
            r"про нас|про компанію|"
            r"о нас|о компании|мы предлагаем|ми пропонуємо|o nás|nabízíme)$"
        ),
        Evidence.DOMAIN,
    ),
)
INLINE_HEADINGS = re.compile(
    r"(?<!\w)("
    + "|".join(pattern.pattern.removeprefix("^").removesuffix("$") for pattern, _ in HEADINGS)
    + r")\s*:\s*"
)
BOUNDARIES = re.compile(r"\n+|[;!?]+\s*|\.\s+|\s+(?:but|however|whereas|але|однак|но|однако)\s+")
TITLE_CONTEXT = re.compile(
    r"\s+(?:for|at|для)\s+|\s+[-–—|]\s+(?=.{0,60}(?:product|company|industry|platform))"
)
AMBIGUOUS_ALIASES = frozenset({"go", "spring", "vue", "express", "liquid"})


@lru_cache(maxsize=1024)
def term_pattern(alias: str) -> re.Pattern[str]:
    return re.compile(rf"(?<!\w){re.escape(normalize_text(alias))}(?!\w)")


@lru_cache(maxsize=512)
def alternative_pattern(alias: str, alternatives: tuple[str, ...]) -> re.Pattern[str]:
    supported = "(?:" + "|".join(term_pattern(other).pattern for other in alternatives) + ")"
    target = term_pattern(alias).pattern
    return re.compile(
        rf"{supported}\s+(?:or|або|или)\s+{target}|"
        rf"{target}\s+(?:or|або|или)\s+{supported}"
    )


def has_supported_alternative(text: str, alias: str, alternatives: tuple[str, ...]) -> bool:
    if alternative_pattern(alias, alternatives).search(text):
        return True
    for group in re.finditer(
        r"\b(?:one of|одним із|одним из|любой из)\b[^()]{0,100}\(([^()]+)\)", text
    ):
        choices = group.group(1)
        if term_pattern(alias).search(choices) and any(
            term_pattern(other).search(choices) for other in alternatives
        ):
            return True
    return False


def string_values(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, dict):
        return tuple(
            item
            for key in ("name", "short_name", "label", "title", "slug")
            if isinstance((item := value.get(key)), str) and item.strip()
        )
    if isinstance(value, list | tuple | set):
        return tuple(item for child in value for item in string_values(child))
    return ()


def role_title(title: str) -> str:
    return TITLE_CONTEXT.split(normalize_text(title), maxsplit=1)[0]


@dataclass(frozen=True)
class Clause:
    text: str
    section: Evidence = Evidence.UNSPECIFIED

    @cached_property
    def markers(self) -> tuple[tuple[re.Match[str], ...], ...]:
        negated = tuple(NEGATED.finditer(self.text))
        optional = tuple(OPTIONAL.finditer(self.text))
        required = tuple(
            marker
            for marker in REQUIRED.finditer(self.text)
            if not any(start.start() <= marker.start() < start.end() for start in negated)
        )
        return negated, optional, required


def requirement_clauses(text: str | None) -> tuple[Clause, ...]:
    plain = unescape(text or "")
    plain = re.sub(r"(?i)<(?:br\b[^>]*|/(?:p|li|div|h[1-6]|ul|ol))>", "\n", plain)
    plain = re.sub(r"<[^>]+>", " ", plain)
    plain = INLINE_HEADINGS.sub(r"\n\1: ", plain.casefold())
    section = Evidence.UNSPECIFIED
    clauses: list[Clause] = []
    for part in BOUNDARIES.split(plain):
        value = normalize_text(part).strip(" *-•:#")
        if not value:
            continue
        heading, colon, remainder = value.partition(":")
        heading_kind = next(
            (kind for pattern, kind in HEADINGS if pattern.fullmatch(heading)), None
        )
        if heading_kind is not None:
            section = heading_kind
            value = remainder.strip() if colon else ""
        if value:
            clauses.append(Clause(value, section))
    return tuple(clauses)


def _distance(match: re.Match[str], marker: re.Match[str]) -> int:
    return max(match.start() - marker.end(), marker.start() - match.end(), 0)


def mention_evidence(clause: Clause, alias: str) -> Evidence:
    pattern = term_pattern(alias)
    mentions = tuple(pattern.finditer(clause.text))
    if not mentions:
        return Evidence.ABSENT
    negated, optional, required = clause.markers
    if clause.section is Evidence.OPTIONAL and not re.search(
        r"\b(?:required|mandatory|must|обов['’]язков\w*|требуется|обязательно|požadujeme)\b",
        clause.text,
    ):
        return Evidence.OPTIONAL
    if (clause.section is Evidence.DOMAIN or DOMAIN.search(clause.text)) and not re.search(
        r"\b(?:required|mandatory|must|you will|you'll|your tasks|you need|we require|"
        r"strong hands[- ]on experience|обов['’]язков\w*|требуется|обязательно|požadujeme)\b",
        clause.text,
    ):
        return Evidence.DOMAIN
    found: list[Evidence] = []
    for mention in mentions:
        tail = clause.text[mention.start() : mention.end() + 30]
        if (alias == "go" and re.match(r"go\s+(?:to|ahead|live|through|beyond)\b", tail)) or (
            alias == "express" and re.match(r"express\s+(?:your|our|the|an?)\b", tail)
        ):
            continue
        choices = [
            (_distance(mention, marker), priority, kind)
            for priority, (kind, markers) in enumerate(
                (
                    (Evidence.NEGATED, negated),
                    (Evidence.OPTIONAL, optional),
                    (Evidence.REQUIRED, required),
                )
            )
            for marker in markers
            if _distance(mention, marker) <= 90
        ]
        if choices:
            found.append(min(choices)[2])
        elif clause.section is Evidence.DOMAIN or DOMAIN.search(clause.text):
            found.append(Evidence.DOMAIN)
        elif alias in AMBIGUOUS_ALIASES and clause.section is Evidence.UNSPECIFIED:
            found.append(Evidence.ABSENT)
        else:
            found.append(clause.section)
    return max(found, default=Evidence.ABSENT)


@dataclass(frozen=True, slots=True)
class RequirementContext:
    title: str
    clauses: tuple[Clause, ...]
    structured: tuple[str, ...]
    text: str

    @classmethod
    def from_text(
        cls, title: str, description: str | None, raw_data: dict[str, Any]
    ) -> "RequirementContext":
        clauses = requirement_clauses(description)
        structured = tuple(
            normalize_text(value)
            for key in ("skills", "tags", "categories", "parentCategories")
            for value in string_values(raw_data.get(key))
        )
        return cls(
            role_title(title),
            clauses,
            structured,
            normalize_text(" ".join((title, description or "", *structured))),
        )

    def evidence(self, aliases: tuple[str, ...], *, alternatives: tuple[str, ...] = ()) -> Evidence:
        if not any(term_pattern(alias).search(self.text) for alias in aliases):
            return Evidence.ABSENT
        found: list[Evidence] = []
        for alias in aliases:
            if term_pattern(alias).search(self.title):
                title_evidence = mention_evidence(Clause(self.title, Evidence.REQUIRED), alias)
                if alternatives and has_supported_alternative(self.title, alias, alternatives):
                    title_evidence = Evidence.OPTIONAL
                found.append(title_evidence)
            for clause in self.clauses:
                evidence = mention_evidence(clause, alias)
                if (
                    evidence >= Evidence.UNSPECIFIED
                    and alternatives
                    and has_supported_alternative(clause.text, alias, alternatives)
                ):
                    evidence = Evidence.OPTIONAL
                if evidence is not Evidence.ABSENT:
                    found.append(evidence)
        if found:
            return max(found)
        if any(term_pattern(alias).search(value) for alias in aliases for value in self.structured):
            return Evidence.REQUIRED
        return Evidence.ABSENT
