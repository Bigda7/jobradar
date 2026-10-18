"""Build source searches from canonical profile skills, not duplicated keyword lists."""

from urllib.parse import quote_plus, urlencode

from jobradar.matching.profile import BOHDAN_PROFILE

ROLE_QUERIES = (
    "developer",
    "programmer",
    "software engineer",
    "junior developer",
    "front-end developer",
    "back-end developer",
    "full-stack developer",
)
DISCOVERY_TERMS = tuple(skill.aliases[0] for skill in BOHDAN_PROFILE.skills if skill.discovery)
PORTFOLIO_QUERIES = tuple(dict.fromkeys((*ROLE_QUERIES, *DISCOVERY_TERMS)))
WORKUA_SEARCH_URLS = tuple(
    f"https://www.work.ua/en/jobs-remote-{quote_plus(term)}/" for term in PORTFOLIO_QUERIES
)


def _robota_slug(term: str) -> str:
    return quote_plus(
        term.replace("front-end", "frontend").replace("back-end", "backend").replace(" ", "-")
    )


ROBOTA_SEARCH_URLS = tuple(
    f"https://robota.ua/zapros/{_robota_slug(term)}-remote/ukraine" for term in PORTFOLIO_QUERIES
)
DOU_FEED_URL = "https://jobs.dou.ua/vacancies/feeds/?remote"
DOU_PRAGUE_FEED = "https://jobs.dou.ua/vacancies/feeds/?" + urlencode(
    {"city": BOHDAN_PROFILE.local_city_aliases[-1]}
)
DOU_ADDITIONAL_FEEDS = (
    DOU_PRAGUE_FEED,
    *(
        "https://jobs.dou.ua/vacancies/feeds/?"
        + urlencode({"remote": "", "search": term, "descr": "1"})
        for term in DISCOVERY_TERMS
    ),
)


def merge_searches(
    configured: str, baseline: tuple[str, ...], *, portfolio: bool
) -> tuple[str, ...]:
    values = tuple(url.strip() for url in configured.split(";") if url.strip())
    return tuple(dict.fromkeys((*baseline, *values) if portfolio else values))


def search_batch(
    urls: tuple[str, ...], maximum: int | None, run_index: int, *, pinned: int = 0
) -> tuple[str, ...]:
    """Rotate bounded batches using the persisted per-source run ordinal."""
    urls = tuple(dict.fromkeys(urls))
    if maximum is None or len(urls) <= maximum:
        return urls
    if maximum <= pinned or run_index < 0:
        raise ValueError("Search batch requires room for rotation and a nonnegative run index.")
    rotating = urls[pinned:]
    size = maximum - pinned
    start = run_index * size % len(rotating)
    return (*urls[:pinned], *(rotating[(start + i) % len(rotating)] for i in range(size)))
