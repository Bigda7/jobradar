"""Regression tests for preserving the project license in runtime images."""

from email.message import Message
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts import verify_runtime_license as verifier


def _package(license_text: str | None, declared: bool) -> SimpleNamespace:
    metadata = Message()
    if declared:
        metadata["License-File"] = "LICENSE"
    return SimpleNamespace(metadata=metadata, read_text=lambda _: license_text)


def test_accepts_matching_license(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "LICENSE"
    path.write_text("Project license\n", encoding="utf-8")
    monkeypatch.setattr(verifier, "distribution", lambda _: _package("Project license\n", True))
    verifier.verify_runtime_license(path)


@pytest.mark.parametrize(
    ("declared", "installed", "error"),
    [
        (False, "Project license\n", "does not declare"),
        (True, None, "does not contain"),
        (True, "Different license\n", "differs"),
    ],
)
def test_rejects_missing_or_changed_package_license(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    declared: bool,
    installed: str | None,
    error: str,
) -> None:
    path = tmp_path / "LICENSE"
    path.write_text("Project license\n", encoding="utf-8")
    monkeypatch.setattr(verifier, "distribution", lambda _: _package(installed, declared))
    with pytest.raises(ValueError, match=error):
        verifier.verify_runtime_license(path)


def test_rejects_empty_project_license(tmp_path: Path) -> None:
    path = tmp_path / "LICENSE"
    path.write_text(" \n", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        verifier.verify_runtime_license(path)


def test_docker_build_preserves_and_verifies_project_license() -> None:
    root = Path(__file__).resolve().parents[2]
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    builder = dockerfile.split(" AS builder", 1)[1].split(" AS runtime", 1)[0]
    assert "COPY pyproject.toml uv.lock README.md LICENSE ./" in builder
    assert builder.index("README.md LICENSE ./") < builder.index("RUN uv sync")
    assert "LICENSE /usr/local/share/licenses/jobradar/LICENSE" in dockerfile
    assert "RUN python /usr/local/libexec/jobradar/verify_runtime_license.py" in dockerfile
