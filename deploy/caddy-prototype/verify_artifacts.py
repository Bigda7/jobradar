"""Validate exported Caddy artifacts and report exact import/license coverage."""

import argparse
import hashlib
import json
import struct
from pathlib import Path


def objects(path: Path) -> list[dict]:
    decoder = json.JSONDecoder()
    content = path.read_text(encoding="utf-8")
    result = []
    offset = 0
    while offset < len(content):
        while offset < len(content) and content[offset].isspace():
            offset += 1
        if offset == len(content):
            break
        item, offset = decoder.raw_decode(content, offset)
        result.append(item)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifacts", type=Path)
    args = parser.parse_args()
    root = args.artifacts.resolve(strict=True)
    before = {
        item["Path"]: item.get("Version")
        for item in objects(root / "metadata/modules.original.json")
    }
    after = {
        item["Path"]: item.get("Version")
        for item in objects(root / "metadata/modules.updated.json")
    }
    expected = {
        "golang.org/x/net": "v0.61.0",
        "golang.org/x/crypto": "v0.58.0",
        "golang.org/x/sys": "v0.49.0",
        "golang.org/x/term": "v0.47.0",
        "golang.org/x/text": "v0.43.0",
        "golang.org/x/sync": "v0.24.0",
        "golang.org/x/tools": "v0.51.0",
    }
    changes = {path: version for path, version in after.items() if before.get(path) != version}
    assert set(before) == set(after), "Unexpected module addition or removal"
    assert changes == expected, "Dependency graph differs from approved versions"
    result = {"resolved_modules": len(after), "approved_changes": changes, "architectures": {}}
    for architecture, machine in (("amd64", 62), ("arm64", 183)):
        binary = root / f"caddy.{architecture}"
        content = binary.read_bytes()
        assert content[:6] == b"\x7fELF\x02\x01", "Expected little-endian ELF64"
        assert struct.unpack_from("<H", content, 18)[0] == machine, "Wrong binary architecture"
        build = (root / f"metadata/build.{architecture}.txt").read_text(encoding="utf-8")
        assert "go1.26.9" in build and "CGO_ENABLED=0" in build, "Unexpected toolchain/linkage"
        assert "-tags=nobadger,nomysql,nopgx" in build, "Official feature exclusions changed"
        for path, version in expected.items():
            if path == "golang.org/x/tools":
                continue
            assert f"{path}\t{version}" in build, f"Unexpected compiled dependency: {path}"
        imports = objects(root / f"metadata/imports.{architecture}.json")
        assert not any(
            item["ImportPath"].startswith("golang.org/x/crypto/openpgp") for item in imports
        ), "Unmaintained OpenPGP package linked"
        linked = {
            (item["Module"]["Path"], item["Module"].get("Version", ""))
            for item in imports
            if item.get("Module")
        }
        missing = []
        for path, version in sorted(linked):
            if not version:
                assert (root / "licenses/CADDY-LICENSE").is_file()
                continue
            license_dir = root / "licenses" / f"{path}@{version}"
            if not license_dir.is_dir() or not any(license_dir.iterdir()):
                missing.append(f"{path}@{version}")
        result["architectures"][architecture] = {
            "sha256": hashlib.sha256(content).hexdigest(),
            "bytes": len(content),
            "imported_packages": len(imports),
            "linked_modules": len(linked),
            "openpgp_imported": False,
            "missing_linked_module_license_files": missing,
            "module_identity_limitation": (
                "The local main module is recorded as (devel); "
                "upstream source commit/version are pinned separately."
            ),
        }
    assert (root / "caddy.arm64").read_bytes() == (root / "caddy.arm64.repeat").read_bytes(), (
        "Repeat compilation differs"
    )
    result["same_environment_arm64_repeat_identical"] = True
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
