"""Protect strict runtime and development dependency audits in CI."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_ci_audits_frozen_runtime_requirements() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    steps = {step["name"]: step for step in workflow["jobs"]["verify"]["steps"]}
    audit = steps["Audit Python dependencies"]["run"]
    assert "uv export --frozen --no-dev --no-emit-project" in audit
    assert "--output-file requirements-audit.txt" in audit
    assert "pip-audit --strict --requirement requirements-audit.txt" in audit


def test_ci_audits_frozen_development_requirements() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    steps = {step["name"]: step for step in workflow["jobs"]["verify"]["steps"]}
    audit = steps["Audit development dependencies"]["run"]
    assert "uv export --frozen --extra dev --no-emit-project" in audit
    assert "--output-file requirements-audit-dev.txt" in audit
    assert "pip-audit --strict --requirement requirements-audit-dev.txt" in audit
    assert "--no-dev" not in audit


def test_dependency_audits_are_required_before_tests_and_image_build() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    job = workflow["jobs"]["verify"]
    assert not job.get("continue-on-error", False)
    steps = job["steps"]
    names = [step["name"] for step in steps]
    for name in ("Audit Python dependencies", "Audit development dependencies"):
        audit = steps[names.index(name)]
        assert names.index("Install Python and locked dependencies") < names.index(name)
        assert names.index(name) < names.index("Run tests")
        assert names.index(name) < names.index("Build and verify attested runtime image")
        assert "if" not in audit
        assert not audit.get("continue-on-error", False)
        assert "--ignore-vuln" not in audit["run"]
        assert "||" not in audit["run"]


def test_ci_scans_all_history_and_refs_with_required_redaction() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    job = workflow["jobs"]["verify"]
    steps = {step["name"]: step for step in job["steps"]}
    assert steps["Check out repository"]["with"]["fetch-depth"] == 0
    scan = steps["Scan Git history for secrets"]
    assert (
        'git /repo --redact=100 --no-banner --log-opts="--all --full-history -m" --exit-code=1'
        in scan["run"]
    )
    assert "if" not in scan
    assert not job.get("continue-on-error", False)
    assert not scan.get("continue-on-error", False)
    assert "||" not in scan["run"]
