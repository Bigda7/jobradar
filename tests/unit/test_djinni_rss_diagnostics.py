import json

from jobradar.sources.djinni_rss_diagnostics import (
    MAX_DIAGNOSTIC_CHILDREN,
    MAX_DIAGNOSTIC_IDS,
    FeedReadDiagnostic,
    consistency_diagnostic,
    feed_identity,
)


def test_feed_identity_does_not_log_arbitrary_query_values_or_full_urls() -> None:
    url = (
        "https://djinni.co/jobs/rss/?token=synthetic-secret&primary_keyword=private-category"
        "&employment=remote&editorial=nonhr&country=CZE&exp_level=2y&english_level=upper"
        "&country=private-country&employment=private-mode&fragment=private-fragment"
    )
    identity = feed_identity(url)
    encoded = json.dumps(identity)
    assert "https://" not in encoded and "private-" not in encoded
    assert "synthetic-secret" not in encoded and "token" not in encoded
    assert identity == feed_identity(url)
    assert identity["filters"]["country"] == ["CZE", "redacted"]
    assert identity["filters"]["employment"] == ["remote", "redacted"]
    assert identity["filters"]["exp_level"] == ["2y"]
    assert identity["filters"]["english_level"] == ["upper"]
    assert len(feed_identity(url + "&country=CZE" * 10)["filters"]["country"]) == 4


def test_diagnostic_samples_are_bounded_and_prioritize_attempted_rechecks() -> None:
    parent = "https://djinni.co/jobs/rss/"
    children = tuple(f"{parent}?exp_level={index}y" for index in range(25))
    missing = {str(index) for index in range(100)}
    reads = {
        url: FeedReadDiagnostic(sequence=index, outcome="processed", ids=missing)
        for index, url in enumerate((parent, *children), start=1)
    }
    rechecks = {children[-1]: FeedReadDiagnostic(sequence=27, outcome="failed")}
    diagnostic = consistency_diagnostic(
        parent,
        children,
        missing,
        reads,
        rechecks,
        {children[-1]},
        remaining_missing=missing,
        stop_reason="completed",
        recheck_selection="eligible",
    )
    assert len(diagnostic["children"]) == MAX_DIAGNOSTIC_CHILDREN
    assert diagnostic["children_omitted"] == len(children) - MAX_DIAGNOSTIC_CHILDREN
    assert len(diagnostic["missing_ids"]) == MAX_DIAGNOSTIC_IDS
    assert diagnostic["missing_ids_omitted"] == 100 - MAX_DIAGNOSTIC_IDS
    assert diagnostic["children"][0]["feed_id"] == feed_identity(children[-1])["feed_id"]
    assert diagnostic["children"][0]["removed_ids"] is None
