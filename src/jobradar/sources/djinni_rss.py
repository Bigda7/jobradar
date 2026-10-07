from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from typing import TYPE_CHECKING
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

if TYPE_CHECKING:
    # Only annotations use Element; responses are parsed by defusedxml.
    from xml.etree.ElementTree import Element  # nosec B405


# These are source filter values verified against the public jobs filter form.
EXPERIENCE_FILTERS = ("no_exp", *(f"{years}y" for years in range(1, 11)))
ENGLISH_FILTERS = (
    "no_english",
    "basic",
    "pre",
    "intermediate",
    "upper",
    "fluent",
    "proficient",
    "native",
)
FEED_SATURATION = 100
MAX_CATALOG_CATEGORIES = 250


@dataclass(frozen=True, slots=True)
class FeedPartition:
    url: str
    expected_category: str | None = None
    split_categories: bool = True


def split_partition(partition: FeedPartition, root: Element) -> tuple[FeedPartition, ...]:
    query = parse_qsl(urlsplit(partition.url).query, keep_blank_values=True)
    categories = list(
        dict.fromkeys(value for key, value in query if key == "primary_keyword" and value.strip())
    )
    if partition.split_categories and len(categories) != 1:
        if not categories:
            categories = list(
                dict.fromkeys(
                    category.text.strip()
                    for category in root.findall("./channel/category")
                    if category.text and category.text.strip()
                )
            )
        if len(categories) > MAX_CATALOG_CATEGORIES:
            return ()
        children = tuple(
            FeedPartition(_replace_filter(partition.url, "primary_keyword", category), category)
            for category in categories
        )
        if any(
            not any(
                category.text and category.text.strip() for category in item.findall("category")
            )
            for item in root.findall("./channel/item")
        ):
            # Category filters cannot cover records that have no category. Keep a parallel
            # experience/English traversal within the same run budgets and original filters.
            children += split_partition(
                FeedPartition(partition.url, partition.expected_category, split_categories=False),
                root,
            )
        return children
    for key, defaults in (("exp_level", EXPERIENCE_FILTERS), ("english_level", ENGLISH_FILTERS)):
        values = list(dict.fromkeys(value for name, value in query if name == key))
        if len(values) == 1:
            continue
        return tuple(
            FeedPartition(
                _replace_filter(partition.url, key, value),
                partition.expected_category or (categories[0] if len(categories) == 1 else None),
                split_categories=partition.split_categories,
            )
            for value in (values or defaults)
        )
    return ()


def category_filter_is_ignored(partition: FeedPartition, items: list[Element]) -> bool:
    if not partition.expected_category or not items:
        return False
    expected = partition.expected_category.casefold()
    return not any(
        category.text and category.text.strip().casefold() == expected
        for item in items
        for category in item.findall("category")
    )


def _replace_filter(url: str, key: str, value: str) -> str:
    parsed = urlsplit(url)
    query = [
        (name, item)
        for name, item in parse_qsl(parsed.query, keep_blank_values=True)
        if name != key
    ]
    query.append((key, value))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))


class _DescriptionParser(HTMLParser):
    block_tags = frozenset({"p", "div", "ul", "ol", "li", "h1", "h2", "h3", "h4", "blockquote"})
    ignored_tags = frozenset({"script", "style", "template", "noscript", "iframe"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.ignored_tags:
            self.ignored_depth += 1
        if self.ignored_depth:
            return
        if tag in self.block_tags or tag == "br":
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("- ")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.ignored_tags and self.ignored_depth:
            self.ignored_depth -= 1
            return
        if not self.ignored_depth and tag in self.block_tags:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.ignored_depth:
            self.parts.append(data.replace("\u00a0", " "))


def description_text(value: str) -> str:
    parser = _DescriptionParser()
    parser.feed(value)
    return "\n".join(
        line for part in "".join(parser.parts).splitlines() if (line := " ".join(part.split()))
    )
