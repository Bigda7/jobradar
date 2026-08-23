import json
from collections.abc import Iterable
from html.parser import HTMLParser
from typing import Any


class _JsonLdScriptParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.documents: list[str] = []
        self._inside_json_ld = False
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "script":
            return
        attributes = {key.casefold(): value for key, value in attrs}
        if (attributes.get("type") or "").casefold() == "application/ld+json":
            self._inside_json_ld = True
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._inside_json_ld:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "script" and self._inside_json_ld:
            self.documents.append("".join(self._parts))
            self._inside_json_ld = False
            self._parts = []


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        value = " ".join(data.replace("\u00a0", " ").split())
        if value:
            self.parts.append(value)


def parse_job_postings(html: str) -> list[dict[str, Any]]:
    parser = _JsonLdScriptParser()
    parser.feed(html)
    postings: list[dict[str, Any]] = []
    for document in parser.documents:
        try:
            value = json.loads(document)
        except json.JSONDecodeError:
            continue
        for item in _json_ld_objects(value):
            if _has_type(item, "JobPosting"):
                postings.append(item)
    return postings


def html_to_text(value: str) -> str:
    parser = _TextParser()
    parser.feed(value)
    return " ".join(parser.parts)


def _json_ld_objects(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        graph = value.get("@graph")
        if isinstance(graph, list):
            yield from (item for item in graph if isinstance(item, dict))
        yield value
    elif isinstance(value, list):
        yield from (item for item in value if isinstance(item, dict))


def _has_type(item: dict[str, Any], expected: str) -> bool:
    item_type = item.get("@type")
    if isinstance(item_type, str):
        return item_type == expected
    if isinstance(item_type, list):
        return expected in item_type
    return False
