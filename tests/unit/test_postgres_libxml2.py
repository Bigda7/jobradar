import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "deploy/postgres-prototype/libxml2"


def load_module(name):
    spec = importlib.util.spec_from_file_location(f"libxml2_{name}", TOOLS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def shell():
    if os.name == "nt":
        git = shutil.which("git")
        path = Path(git).parent.parent / "bin/bash.exe" if git else None
        if path is None or not path.is_file():
            pytest.skip("Existing Git Bash is required")
        return str(path)
    return "/bin/sh"


@pytest.mark.parametrize("mode", ["builder", "runtime"])
@pytest.mark.parametrize(
    "change",
    [
        "approved",
        "added",
        "removed",
        "swapped",
        "duplicate",
        "wrong_version",
        "wrong_baseline",
        "empty",
        "invalid_mode",
    ],
)
def test_inventory_guard(shell, tmp_path, mode, change):
    before = ["xz-libs-5.8.3-r0", "libxml2-2.13.9-r2"] + [
        f"synthetic-{index}-1.0-r0" for index in range(43)
    ]
    if mode == "builder":
        additions = (TOOLS / "builder-packages.txt").read_text().replace("=", "-").splitlines()
        after = [line for line in before if not line.startswith("xz-libs-")] + additions
        version_index = after.index("xz-libs-5.8.4-r0")
    else:
        after = ["libxml2-2.13.9-r200" if line.startswith("libxml2-") else line for line in before]
        version_index = after.index("libxml2-2.13.9-r200")
    if change == "added":
        after.append("unapproved-1.0-r0")
    elif change == "removed":
        after.pop()
    elif change == "swapped":
        after[3] = "unapproved-1.0-r0"
    elif change == "duplicate":
        after[3] = after[4]
    elif change == "wrong_version":
        after[version_index] += ".unexpected"
    elif change == "wrong_baseline":
        before[0 if mode == "builder" else 1] += ".unexpected"
    elif change == "empty":
        after = []
    elif change == "invalid_mode":
        mode = "unsupported"
    for name, lines in (("before", before), ("after", after)):
        (tmp_path / name).write_text(
            "\n".join(lines) + "\n" if lines else "", encoding="utf-8", newline="\n"
        )
    result = subprocess.run(
        [
            shell,
            (TOOLS / "verify_inventory.sh").as_posix(),
            mode,
            (tmp_path / "before").as_posix(),
            (tmp_path / "after").as_posix(),
        ],
        capture_output=True,
        timeout=15,
    )
    assert (result.returncode == 0) is (change == "approved")


@pytest.mark.parametrize("architecture", ["amd64", "arm64"])
@pytest.mark.parametrize("change", ["approved", "id", "architecture", "os", "missing"])
def test_build_requires_exact_base(architecture, change):
    module = load_module("build_candidate")
    record = {"Id": module.BASE_IMAGES[architecture], "Architecture": architecture, "Os": "linux"}
    if change == "id":
        record["Id"] = "unapproved"
    elif change == "architecture":
        record["Architecture"] = "other"
    elif change == "os":
        record["Os"] = "windows"
    elif change == "missing":
        record = {}
    if change == "approved":
        module.validate_base(architecture, record)
    else:
        with pytest.raises(ValueError):
            module.validate_base(architecture, record)


@pytest.mark.parametrize("change", ["approved", "content", "path", "extra"])
def test_patch_manifest_fails_closed(tmp_path, monkeypatch, change):
    module = load_module("build_candidate")
    monkeypatch.setattr(module, "HERE", tmp_path)
    folder = tmp_path / "generated"
    folder.mkdir()
    content = b"synthetic patch\n"
    (folder / "0002-security-backports.patch").write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    entry = f"{digest}  0002-security-backports.patch\n"
    if change == "content":
        (folder / "0002-security-backports.patch").write_bytes(b"modified")
    elif change == "path":
        entry = f"{digest}  ../outside.patch\n"
    elif change == "extra":
        entry += entry
    (folder / "SHA256SUMS").write_text(entry, encoding="utf-8")
    if change == "approved":
        module.verify_generated_patches()
    else:
        with pytest.raises(ValueError):
            module.verify_generated_patches()


@pytest.mark.parametrize("text", ["missing", "old old"])
def test_backport_rejects_missing_or_ambiguous_context(text):
    module = load_module("generate_backports")
    with pytest.raises(ValueError, match="context count"):
        module.replace(text, "old", "new")


def test_backport_replaces_exact_context():
    assert load_module("generate_backports").replace("prefix old suffix", "old", "new") == (
        "prefix new suffix"
    )


@pytest.mark.parametrize("actual", ["42\n", "wrong\n"])
def test_xml_result_guard_checks_value(actual):
    module = load_module("check_xml")
    fixture = SimpleNamespace(sql=lambda query: actual.encode())
    if actual == "42\n":
        module.check_result(fixture, "synthetic query", "42")
    else:
        with pytest.raises(RuntimeError, match="XML query result"):
            module.check_result(fixture, "synthetic query", "42")


def test_source_and_patch_inventory_are_exact():
    generator = load_module("generate_backports")
    assert len(generator.UPSTREAM) == 11
    for commit, digest in generator.UPSTREAM.items():
        assert (
            hashlib.sha256((TOOLS / "upstream" / f"{commit}.patch").read_bytes()).hexdigest()
            == digest
        )
    load_module("build_candidate").verify_generated_patches()
    assert len((TOOLS / "builder-packages.txt").read_text().splitlines()) == 29
    assert len(set(generator.DIRECT)) == 4


def test_xml_fixture_cleanup_on_failure(monkeypatch):
    module = load_module("check_xml")
    closed = []

    class Fixture:
        def __init__(self, *args):
            pass

        def start(self):
            raise RuntimeError("synthetic startup failure")

        def close(self):
            closed.append(True)

    monkeypatch.setattr(module, "load_checker", lambda: SimpleNamespace(Fixture=Fixture))
    monkeypatch.setattr(
        "sys.argv",
        ["check_xml.py", "--architecture", "amd64", "--candidate-image", "synthetic-local-image"],
    )
    with pytest.raises(RuntimeError, match="synthetic startup failure"):
        module.main()
    assert closed == [True]


def test_approved_base_digest_metadata_has_no_mutable_reference():
    values = load_module("build_candidate").BASE_IMAGES
    assert set(values) == {"amd64", "arm64"}
    assert all(len(value) == 71 and value.startswith("sha256:") for value in values.values())
    assert json.loads(json.dumps(values)) == values


def test_build_keeps_alpine_hardening_and_ephemeral_signing_key():
    script = (TOOLS / "build.sh").read_text()
    for flag in (
        "-fstack-clash-protection",
        "-fstack-protector-strong",
        "-Werror=format-security",
        "-fno-plt",
        "-z,relro",
        "-z,now",
        "-z,pack-relative-relocs",
    ):
        assert flag in script
    dockerfile = (TOOLS / "Dockerfile").read_text()
    assert "RUN --mount=type=tmpfs,target=/signing --network=none" in dockerfile
    assert "PACKAGER_PRIVKEY=/signing/local.rsa" in script
    assert "COPY --from=build /artifacts/" in dockerfile
    assert "COPY --from=build /signing" not in dockerfile
