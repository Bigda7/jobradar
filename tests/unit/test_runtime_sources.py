"""Source-evidence safety and fail-closed regression tests."""

import hashlib
import io
import json
import tarfile
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts import collect_runtime_licenses as collector
from scripts import prepare_runtime_sources as sources


def test_apk_inventory_preserves_build_provenance() -> None:
    result = collector.apk_inventory("P:busybox\nV:1.0-r1\nL:GPL-2.0-only\no:busybox\nc:abc\n")
    assert result == [
        {
            "name": "busybox",
            "version": "1.0-r1",
            "license": "GPL-2.0-only",
            "origin": "busybox",
            "commit": "abc",
        }
    ]


def test_invalid_apk_record_fails() -> None:
    with pytest.raises(ValueError, match="Invalid"):
        collector.apk_inventory("P:busybox\n")


def test_canonical_package_names() -> None:
    assert collector.canonical_name("Psycopg_Binary") == "psycopg-binary"


def test_missing_installed_dependency_notice_fails_build(monkeypatch: pytest.MonkeyPatch) -> None:
    package = SimpleNamespace(metadata={"Name": "example"}, files=[], version="1")
    monkeypatch.setattr(collector, "distributions", lambda: [package])
    with pytest.raises(ValueError, match="No installed license"):
        collector.collect()


@pytest.mark.parametrize(
    "url",
    [
        "http://raw.githubusercontent.com/source",
        "https://localhost/source",
        "https://raw.githubusercontent.com@evil.invalid/source",
        "https://user:secret@raw.githubusercontent.com/source",
        "https://raw.githubusercontent.com:8443/source",
        "file:///etc/passwd",
    ],
)
def test_rejects_unsafe_download_endpoints(url: str) -> None:
    with pytest.raises(ValueError, match="allowed"):
        sources.safe_url(url)


def test_accepts_public_https_endpoint() -> None:
    url = "https://distfiles.alpinelinux.org/distfiles/v3.24/source.tar.gz"
    assert sources.safe_url(url) == url


def test_checksums_are_parsed_without_executing_shell() -> None:
    checksum = "a" * 128
    recipe = f'pkgver=$(do_not_execute)\nsha512sums="\n{checksum}  source.tar.gz\n"\n'
    assert sources.apk_checksums(recipe) == {"source.tar.gz": checksum}


@pytest.mark.parametrize("entry", ["SKIP  file", "a  file", "a" * 128 + "  ../outside"])
def test_rejects_missing_checksum_or_unsafe_source_name(entry: str) -> None:
    with pytest.raises(ValueError):
        sources.apk_checksums(f'sha512sums="\n{entry}\n"')


def test_rejects_duplicate_source_names() -> None:
    entry = "a" * 128 + "  file"
    with pytest.raises(ValueError, match="Duplicate"):
        sources.apk_checksums(f'sha512sums="\n{entry}\n{entry}\n"')


@pytest.mark.parametrize("name", ["../outside", "/outside", "a\\b", "a/b"])
def test_rejects_unsafe_names(name: str) -> None:
    with pytest.raises(ValueError):
        sources.safe_name(name)


def test_checksum_mismatch_is_not_retried_as_missing_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sources, "fetch", lambda _: b"tampered")
    with pytest.raises(ValueError, match="checksum mismatch"):
        sources.download_apk_source(
            "https://raw.githubusercontent.com/source", "v3.24", "file", "a" * 128
        )


def test_unknown_or_changed_python_version_fails() -> None:
    lock = {"package": [{"name": "certifi", "version": "1", "sdist": {}}]}
    with pytest.raises(ValueError, match="not locked"):
        sources.python_source({"name": "certifi", "version": "2"}, lock)


def test_binary_wheel_without_source_is_not_silently_covered() -> None:
    lock = {"package": [{"name": "psycopg-binary", "version": "3.3.4"}]}
    with pytest.raises(ValueError, match="No locked source"):
        sources.python_source({"name": "psycopg-binary", "version": "3.3.4"}, lock)


def test_reviewed_source_override_is_version_specific() -> None:
    lock = {"package": [{"name": "psycopg-binary", "version": "3.3.4"}]}
    override = {"url": "https://codeload.github.com/source", "hash": "sha256:" + "a" * 64}
    package = {"name": "psycopg-binary", "version": "3.3.4"}
    assert sources.python_source(package, lock, {"psycopg-binary@3.3.4": override}) == override
    with pytest.raises(ValueError, match="No locked source"):
        sources.python_source(package, lock, {"psycopg-binary@3.3.5": override})


def test_archived_notice_paths_are_not_extracted(tmp_path: Path) -> None:
    content = io.BytesIO()
    with tarfile.open(fileobj=content, mode="w") as archive:
        entry = tarfile.TarInfo("../../LICENSE")
        entry.size = 4
        archive.addfile(entry, io.BytesIO(b"text"))
        symlink = tarfile.TarInfo("COPYING")
        symlink.type = tarfile.SYMTYPE
        symlink.linkname = "/etc/passwd"
        archive.addfile(symlink)
    assert sources.archive_notices(content.getvalue()) == {"../../LICENSE": b"text"}
    assert list(tmp_path.iterdir()) == []


def _manifest(tmp_path: Path, complete: bool = False) -> None:
    (tmp_path / "file").write_bytes(b"source")
    manifest = {
        "schema": 1,
        "complete": complete,
        "issues": [] if complete else ["missing native source"],
        "artifacts": [{"path": "file", "sha256": hashlib.sha256(b"source").hexdigest()}],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))


def test_incomplete_bundle_cannot_pass_publication_gate(tmp_path: Path) -> None:
    _manifest(tmp_path)
    with pytest.raises(ValueError, match="incomplete"):
        sources.verify_bundle(tmp_path)


def test_corrupted_bundle_fails_before_completeness_check(tmp_path: Path) -> None:
    _manifest(tmp_path, complete=True)
    (tmp_path / "file").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="checksum mismatch"):
        sources.verify_bundle(tmp_path)


def test_notice_collection_preserves_original_text(tmp_path: Path) -> None:
    inventory = {
        "python_version": "3.13.15",
        "python_license": "Python license\n",
        "python": [
            {
                "name": "example",
                "version": "1",
                "notices": [{"path": "LICENSE", "text": "Original notice\n"}],
            }
        ],
    }
    output = tmp_path / "notices"
    collector.write_notices(inventory, output)
    assert "Original notice\n" in (output / "NOTICE.txt").read_text()
    assert json.loads((output / "inventory.json").read_text()) == inventory
    with pytest.raises(FileExistsError):
        collector.write_notices(inventory, output)


def test_docker_runtime_automatically_preserves_notices() -> None:
    dockerfile = (Path(__file__).resolve().parents[2] / "Dockerfile").read_text()
    assert "RUN python /usr/local/libexec/jobradar/collect_runtime_licenses.py" in dockerfile
    assert "--output /usr/local/share/licenses/jobradar/third-party" in dockerfile


def test_marking_incomplete_inventory_as_complete_cannot_bypass_coverage(tmp_path: Path) -> None:
    _manifest(tmp_path, complete=True)
    with pytest.raises(ValueError, match="inventoried"):
        sources.verify_bundle(tmp_path)


def test_package_version_and_license_changes_require_new_coverage() -> None:
    inventory = {
        "python_version": "3.13.15",
        "apk": [],
        "native_files": [],
        "python": [{"name": "example", "version": "1", "license": "MIT"}],
    }
    before = sources.coverage_keys(inventory)
    inventory["python"][0]["license"] = "GPL-3.0-only"
    assert before != sources.coverage_keys(inventory)
    inventory["python"][0]["version"] = "2"
    assert "python:example:2:GPL-3.0-only" in sources.coverage_keys(inventory)


def test_draft_archive_preserves_versioned_filename_and_is_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "one" / "v1.2.12"
    second = tmp_path / "two" / "v1.2.12"
    for root in (first, second):
        root.mkdir(parents=True)
        (root / "manifest.json").write_bytes(b"draft")
    path = sources.archive_bundle(first)
    assert path.name == "v1.2.12.zip"
    assert path.read_bytes() == sources.archive_bundle(second).read_bytes()
    with zipfile.ZipFile(path) as archive:
        assert archive.namelist() == ["manifest.json"]
    with pytest.raises(FileExistsError):
        sources.archive_bundle(first)


def test_allows_legitimate_alpine_source_names_but_not_windows_devices() -> None:
    assert sources.safe_name("__stack_chk_fail_local.c") == "__stack_chk_fail_local.c"
    assert (
        sources.safe_name("alpine-devel@example.org.rsa.pub") == "alpine-devel@example.org.rsa.pub"
    )
    with pytest.raises(ValueError, match="Reserved"):
        sources.safe_name("NUL.txt")


def test_rejects_mutable_image_tag_before_running_docker() -> None:
    with pytest.raises(ValueError, match="immutable"):
        sources.collect_image("ghcr.io/bigda7/jobradar:latest", "linux/arm64")


def test_rejects_wrong_image_architecture(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sources, "run_tool", lambda *args: b"amd64\n")
    with pytest.raises(ValueError, match="platform"):
        sources.collect_image("sha256:" + "a" * 64, "linux/arm64")
