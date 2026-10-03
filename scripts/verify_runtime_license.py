"""Verify that the installed JobRadar package preserves its project license."""

import argparse
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path

DEFAULT_LICENSE = Path("/usr/local/share/licenses/jobradar/LICENSE")


def verify_runtime_license(license_path: Path) -> None:
    expected = license_path.read_text(encoding="utf-8")
    if not expected.strip():
        raise ValueError("The project license is empty")
    package = distribution("jobradar")
    if "LICENSE" not in package.metadata.get_all("License-File", []):
        raise ValueError("The installed package does not declare its LICENSE file")
    installed = package.read_text("licenses/LICENSE")
    if installed is None:
        raise ValueError("The installed package does not contain its LICENSE file")
    if installed != expected:
        raise ValueError("The installed package license differs from the project license")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--license", type=Path, default=DEFAULT_LICENSE)
    args = parser.parse_args()
    try:
        verify_runtime_license(args.license)
    except (OSError, ValueError, PackageNotFoundError) as exc:
        parser.exit(1, f"Runtime project license verification failed: {exc}\n")
    print("Installed JobRadar license matches the preserved project license")


if __name__ == "__main__":
    main()
