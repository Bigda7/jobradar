from urllib.parse import urlsplit

SOURCE_LISTING_HOSTS: dict[str, frozenset[str]] = {
    "djinni": frozenset({"djinni.co", "www.djinni.co"}),
    "dou_jobs": frozenset({"jobs.dou.ua"}),
    "workua": frozenset({"work.ua", "www.work.ua"}),
    "robota_ua": frozenset({"robota.ua", "www.robota.ua"}),
}


def is_trusted_source_link(url: str, allowed_hosts: frozenset[str]) -> bool:
    if not url or any(ord(character) <= 32 or ord(character) == 127 for character in url):
        return False
    try:
        parsed = urlsplit(url)
        return (
            parsed.scheme == "https"
            and parsed.hostname in allowed_hosts
            and parsed.username is None
            and parsed.password is None
            and parsed.port in {None, 443}
        )
    except ValueError:
        return False
