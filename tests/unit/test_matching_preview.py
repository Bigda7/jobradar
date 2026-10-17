from dataclasses import asdict

import pytest
from tests.unit.test_matching_context import candidate

from jobradar.matching.preview import compare_snapshot
from jobradar.matching.profile import BOHDAN_PROFILE


def test_preview_compares_the_same_snapshot_without_exposing_description_or_raw_data() -> None:
    row = asdict(candidate())
    row.update(id=1, baseline={"score": 20, "concerns": []})
    result = compare_snapshot({"baseline_rules_version": "v15", "records": [row]})
    assert result["counts"]["added_main"] == 1
    assert result["counts"]["candidate_main"] == 1
    transition = result["transitions"][0]
    assert transition["transition"] == "added"
    assert "description" not in transition
    assert "raw_data" not in transition


def test_preview_rejects_a_non_comparison_baseline() -> None:
    with pytest.raises(ValueError, match="must differ"):
        compare_snapshot({"baseline_rules_version": BOHDAN_PROFILE.rules_version, "records": []})
