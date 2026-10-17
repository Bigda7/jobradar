"""Compare a public vacancy snapshot locally without database or network access."""

from collections import Counter
from dataclasses import asdict
from decimal import Decimal
from typing import Any

from jobradar.domain.enums import OpportunityKind, WorkMode
from jobradar.matching.models import MatchCandidate
from jobradar.matching.profile import BOHDAN_PROFILE
from jobradar.matching.scorer import score_candidate


def snapshot_candidate(row: dict[str, Any]) -> MatchCandidate:
    return MatchCandidate(
        kind=OpportunityKind(row["kind"]),
        title=row["title"],
        company=row.get("company"),
        description=row.get("description"),
        location_text=row.get("location_text"),
        work_mode=WorkMode(row["work_mode"]),
        employment_type=row.get("employment_type"),
        contract_type=row.get("contract_type"),
        salary_min=Decimal(str(row["salary_min"])) if row.get("salary_min") is not None else None,
        salary_max=Decimal(str(row["salary_max"])) if row.get("salary_max") is not None else None,
        salary_currency=row.get("salary_currency"),
        salary_period=row.get("salary_period"),
        raw_data=row.get("raw_data") or {},
    )


def compare_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    baseline_version = snapshot["baseline_rules_version"]
    if baseline_version == BOHDAN_PROFILE.rules_version:
        raise ValueError("Baseline and candidate rules must differ")
    counts: Counter[str] = Counter()
    transitions: list[dict[str, Any]] = []
    for row in snapshot["records"]:
        baseline = row["baseline"]
        result = score_candidate(snapshot_candidate(row), BOHDAN_PROFILE)
        old_main = baseline["score"] >= BOHDAN_PROFILE.notification_threshold
        new_main = result.score >= BOHDAN_PROFILE.notification_threshold
        counts["records"] += 1
        counts["baseline_main"] += old_main
        counts["candidate_main"] += new_main
        counts["added_main"] += new_main and not old_main
        counts["removed_main"] += old_main and not new_main
        counts["explore_35_54"] += 35 <= result.score < 55
        counts["changed_score"] += baseline["score"] != result.score
        counts["stretch_experience"] += any("Stretch match:" in item for item in result.concerns)
        if baseline["score"] != result.score or old_main != new_main:
            transitions.append(
                {
                    "id": row["id"],
                    "title": row["title"],
                    "source": row.get("source_name"),
                    "baseline": baseline,
                    "candidate": asdict(result),
                    "transition": "added"
                    if new_main and not old_main
                    else "removed"
                    if old_main and not new_main
                    else "score_only",
                }
            )
    return {
        "snapshot_at": snapshot.get("checked_at"),
        "baseline_rules_version": baseline_version,
        "candidate_rules_version": BOHDAN_PROFILE.rules_version,
        "threshold": BOHDAN_PROFILE.notification_threshold,
        "counts": dict(counts),
        "transitions": transitions,
        "limitations": [
            "Local pure scoring only; no database writes, notification delivery or collection.",
            "Both versions use the same public snapshot without a live exchange-rate provider.",
            "Counts describe these records, not full source coverage or verified suitability.",
        ],
    }
