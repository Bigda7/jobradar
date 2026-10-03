"""Collect public package evidence, without reading application configuration."""

import argparse
import hashlib
import json
import re
import struct
import sys
from importlib.metadata import distributions
from pathlib import Path
from typing import Any


def canonical_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def elf_evidence(content: bytes) -> dict[str, str]:
    """Fingerprint code/build identity without loading or executing the shared library."""
    if len(content) < 64 or content[:5] != b"\x7fELF\x02" or content[5] not in (1, 2):
        raise ValueError("Expected a valid 64-bit ELF library")
    endian = "<" if content[5] == 1 else ">"
    offset = struct.unpack_from(endian + "Q", content, 40)[0]
    entry_size, count, string_index = struct.unpack_from(endian + "HHH", content, 58)
    if entry_size != 64 or not 0 < count <= 4096 or string_index >= count:
        raise ValueError("Invalid ELF section table")
    if offset + entry_size * count > len(content):
        raise ValueError("ELF section table exceeds the file")
    sections = [
        struct.unpack_from(endian + "IIQQQQIIQQ", content, offset + index * 64)
        for index in range(count)
    ]
    string_section = sections[string_index]
    if string_section[4] + string_section[5] > len(content):
        raise ValueError("ELF section names exceed the file")
    strings = content[string_section[4] : string_section[4] + string_section[5]]
    result = {}
    for section in sections:
        name_offset, start, size = section[0], section[4], section[5]
        if name_offset >= len(strings):
            raise ValueError("Invalid ELF section name")
        name_end = strings.find(b"\0", name_offset)
        if name_end < 0:
            raise ValueError("Unterminated ELF section name")
        name = strings[name_offset:name_end].decode("ascii", errors="strict")
        if name not in (".text", ".rodata", ".note.gnu.build-id", ".comment"):
            continue
        if start + size > len(content):
            raise ValueError("ELF evidence section exceeds the file")
        data = content[start : start + size]
        if name == ".comment":
            result["compiler"] = "; ".join(
                match.decode("ascii") for match in re.findall(rb"GCC: [^\x00]+", data)
            )
        else:
            result[name.removeprefix(".") + "_sha256"] = hashlib.sha256(data).hexdigest()
    if "text_sha256" not in result:
        raise ValueError("ELF library contains no code section")
    return result


def apk_inventory(database: str) -> list[dict[str, str]]:
    packages = []
    for record in database.strip().split("\n\n"):
        fields = dict(line.split(":", 1) for line in record.splitlines() if ":" in line)
        if "P" not in fields or "V" not in fields:
            raise ValueError("Invalid installed APK record")
        packages.append(
            {
                "name": fields["P"],
                "version": fields["V"],
                "license": fields.get("L", ""),
                "origin": fields.get("o", ""),
                "commit": fields.get("c", ""),
            }
        )
    return sorted(packages, key=lambda package: package["name"])


def collect(*, allow_missing_project_license: bool = False) -> dict[str, Any]:
    packages: list[dict[str, Any]] = []
    native_files: list[dict[str, str]] = []
    sboms: list[dict[str, Any]] = []
    for package in sorted(distributions(), key=lambda item: canonical_name(item.metadata["Name"])):
        name = canonical_name(package.metadata["Name"])
        notices = []
        for file in sorted(package.files or [], key=str):
            # Only installed package files are read; configuration and environment are excluded.
            path = Path(str(package.locate_file(file)))
            is_notice = re.match(r"^(LICENSE|LICENCE|COPYING|NOTICE)([._-]|$)", file.name, re.I)
            if is_notice:
                content = path.read_text(encoding="utf-8")
                if not content.strip():
                    raise ValueError(f"Empty notice: {name}/{file}")
                notices.append({"path": str(file), "text": content})
            if ".libs/" in str(file) and ".so" in file.name:
                with path.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                native_files.append(
                    {
                        "package": name,
                        "path": str(file),
                        "sha256": digest,
                        **elf_evidence(path.read_bytes()),
                    }
                )
            if str(file).endswith(".dist-info/sboms/auditwheel.cdx.json"):
                sboms.append({"package": name, "content": json.loads(path.read_text())})
        if not notices and not (name == "jobradar" and allow_missing_project_license):
            raise ValueError(f"No installed license text: {name}")
        packages.append(
            {
                "name": name,
                "version": package.version,
                "license": package.metadata.get("License-Expression")
                or package.metadata.get("License", ""),
                "notices": notices,
            }
        )
    return {
        "schema": 1,
        "python_version": sys.version.split()[0],
        "python_license": Path("/usr/local/lib/python3.13/LICENSE.txt").read_text(),
        "apk": apk_inventory(Path("/lib/apk/db/installed").read_text()),
        "python": packages,
        "native_files": sorted(native_files, key=lambda file: file["path"]),
        "wheel_sboms": sboms,
    }


def write_notices(inventory: dict[str, Any], output: Path) -> None:
    output.mkdir(parents=True, exist_ok=False)
    (output / "inventory.json").write_text(
        json.dumps(inventory, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    sections = [
        "Third-party runtime notices",
        "These preserved Python notices are not a complete corresponding-source bundle.",
        "Alpine and vendored native-library coverage is tracked separately.",
        "CPython " + inventory["python_version"] + "\n\n" + inventory["python_license"],
    ]
    for package in inventory["python"]:
        for notice in package["notices"]:
            sections.append(
                f"{package['name']} {package['version']} -- {notice['path']}\n\n{notice['text']}"
            )
    (output / "NOTICE.txt").write_text("\n\n".join(sections) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--allow-missing-project-license", action="store_true")
    args = parser.parse_args()
    try:
        if args.output and args.allow_missing_project_license:
            parser.error("Missing licenses may only be recorded in audit mode")
        inventory = collect(allow_missing_project_license=args.allow_missing_project_license)
        if args.output:
            write_notices(inventory, args.output)
        else:
            print(json.dumps(inventory, sort_keys=True))
    except (OSError, ValueError) as error:
        parser.exit(1, f"Runtime notice collection failed: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
