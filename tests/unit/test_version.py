import tomllib
from pathlib import Path

from jobradar import __version__
from jobradar.api.app import create_app


def test_package_and_api_versions_match_project_metadata() -> None:
    project_file = Path(__file__).resolve().parents[2] / "pyproject.toml"
    with project_file.open("rb") as handle:
        project_version = tomllib.load(handle)["project"]["version"]

    assert __version__ == project_version
    assert create_app().version == project_version
