from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from jobradar.sources.djinni_rss import ENGLISH_FILTERS, EXPERIENCE_FILTERS

MAX_DIAGNOSTIC_PARENTS = 20
MAX_DIAGNOSTIC_CHILDREN = 16
MAX_DIAGNOSTIC_IDS = 10


def feed_identity(url: str) -> dict[str, Any]:
    filters: dict[str, list[str]] = {}
    allowed_values = {
        "exp_level": EXPERIENCE_FILTERS,
        "english_level": ENGLISH_FILTERS,
        "employment": ("remote", "office"),
        "editorial": ("nonhr",),
    }
    for key, value in parse_qsl(urlsplit(url).query):
        if key == "primary_keyword":
            # Category fingerprints correlate feeds without logging arbitrary query text.
            safe_value = "sha256:" + hashlib.sha256(value.encode()).hexdigest()[:16]
        elif key == "country":
            safe_value = value if re.fullmatch(r"[A-Z]{3}", value) else "redacted"
        elif key in allowed_values:
            safe_value = value if value in allowed_values[key] else "redacted"
        else:
            continue
        values = filters.setdefault(key, [])
        if len(values) < 4:
            values.append(safe_value)
    return {
        "feed_id": hashlib.sha256(url.encode()).hexdigest()[:16],
        "filters": filters,
    }


@dataclass(slots=True)
class FeedReadDiagnostic:
    sequence: int
    outcome: str = "started"
    attempts: int = 0
    elapsed_seconds: float = 0.0
    http_status: int | None = None
    response_items: int = 0
    ids: set[str] = field(default_factory=set, repr=False)

    def summary(self, missing: set[str]) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "outcome": self.outcome,
            "attempts": self.attempts,
            "elapsed_seconds": self.elapsed_seconds,
            "http_status": self.http_status,
            "response_items": self.response_items,
            "accepted_ids": len(self.ids),
            "target_ids_present": sorted(self.ids & missing)[:MAX_DIAGNOSTIC_IDS],
        }


def consistency_diagnostic(
    parent: str,
    children: tuple[str, ...],
    missing: set[str],
    first_reads: dict[str, FeedReadDiagnostic],
    rechecks: dict[str, FeedReadDiagnostic],
    scheduled_rechecks: set[str],
    *,
    remaining_missing: set[str],
    stop_reason: str,
    recheck_selection: str,
) -> dict[str, Any]:
    # Attempted rechecks and failed initial reads take priority in the bounded sample.
    ordered = sorted(
        children,
        key=lambda url: (
            url not in rechecks,
            url in first_reads and first_reads[url].outcome == "processed",
            url,
        ),
    )
    child_samples = []
    for url in ordered[:MAX_DIAGNOSTIC_CHILDREN]:
        initial = first_reads.get(url)
        recheck = rechecks.get(url)
        comparable = bool(
            initial
            and recheck
            and initial.outcome == "processed"
            and recheck.outcome == "processed"
        )
        child_samples.append(
            {
                **feed_identity(url),
                "initial": initial.summary(missing) if initial else None,
                "recheck_scheduled": url in scheduled_rechecks,
                "recheck": recheck.summary(missing) if recheck else None,
                "added_ids": len(recheck.ids - initial.ids)
                if comparable and recheck and initial
                else None,
                "removed_ids": len(initial.ids - recheck.ids)
                if comparable and recheck and initial
                else None,
            }
        )
    return {
        "parent": feed_identity(parent),
        "parent_read": first_reads[parent].summary(missing),
        "missing_count": len(missing),
        "missing_ids": sorted(missing)[:MAX_DIAGNOSTIC_IDS],
        "missing_ids_omitted": max(0, len(missing) - MAX_DIAGNOSTIC_IDS),
        "remaining_missing_count": len(remaining_missing),
        "remaining_missing_ids": sorted(remaining_missing)[:MAX_DIAGNOSTIC_IDS],
        "children_count": len(children),
        "children_omitted": max(0, len(children) - MAX_DIAGNOSTIC_CHILDREN),
        "initial_children_processed": sum(
            url in first_reads and first_reads[url].outcome == "processed" for url in children
        ),
        "rechecks_scheduled": sum(url in scheduled_rechecks for url in children),
        "rechecks_attempted": sum(url in rechecks for url in children),
        "rechecks_processed": sum(
            url in rechecks and rechecks[url].outcome == "processed" for url in children
        ),
        "stop_reason": stop_reason,
        "recheck_selection": recheck_selection,
        "children": child_samples,
    }
