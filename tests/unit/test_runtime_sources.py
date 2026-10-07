"""Source-evidence safety and fail-closed regression tests."""

import hashlib
import io
import json
import tarfile
import urllib.error
import urllib.request
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


def test_runtime_pins_fixed_zlib_and_reviews_its_exact_build() -> None:
    root = Path(__file__).resolve().parents[2]
    dockerfile = (root / "Dockerfile").read_text()
    runtime = dockerfile.split(" AS runtime", 1)[1].split("FROM builder AS test-builder", 1)[0]
    assert "zlib=1.3.2-r1" in runtime
    assert "apk upgrade" not in runtime
    review = json.loads((root / "docs/runtime-package-review.json").read_text())
    assert [entry for entry in review["components"] if entry.startswith("apk:zlib:")] == [
        "apk:zlib:1.3.2-r1:Zlib:0afa2da0e8c8051c6f8f64a7a388e5a259904245"
    ]


def test_fixed_zlib_inventory_retains_source_and_license_identity() -> None:
    package = collector.apk_inventory(
        "P:zlib\nV:1.3.2-r1\nL:Zlib\no:zlib\nc:0afa2da0e8c8051c6f8f64a7a388e5a259904245\n"
    )
    assert package == [
        {
            "name": "zlib",
            "version": "1.3.2-r1",
            "license": "Zlib",
            "origin": "zlib",
            "commit": "0afa2da0e8c8051c6f8f64a7a388e5a259904245",
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


@pytest.fixture
def native_archive(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    primary, checksum = next(iter(sources.NATIVE_ARCHIVE_FALLBACKS))
    mirror = sources.NATIVE_ARCHIVE_FALLBACKS[(primary, checksum)]
    archive = {
        "name": "gcc-12.4.0.tar.xz",
        "url": primary,
        "sha256": hashlib.sha256(b"source").hexdigest(),
    }
    monkeypatch.setattr(sources, "NATIVE_ARCHIVE_FALLBACKS", {(primary, archive["sha256"]): mirror})
    return archive


def test_native_fallback_is_pinned_to_existing_review() -> None:
    review = json.loads(
        (Path(__file__).resolve().parents[2] / "docs/runtime-native-sources.json").read_text()
    )
    archives = [
        archive for group in review["groups"].values() for archive in group.get("archives", [])
    ]
    assert len(sources.NATIVE_ARCHIVE_FALLBACKS) == 1
    for (url, checksum), mirror in sources.NATIVE_ARCHIVE_FALLBACKS.items():
        assert any(archive["url"] == url and archive["sha256"] == checksum for archive in archives)
        assert mirror == "https://mirrors.kernel.org/gnu/gcc/gcc-12.4.0/gcc-12.4.0.tar.xz"
        assert sources.safe_url(mirror) == mirror


def test_native_primary_success_does_not_contact_mirror(
    monkeypatch: pytest.MonkeyPatch, native_archive: dict[str, str]
) -> None:
    calls = []

    def fetch(url: str) -> bytes:
        calls.append(url)
        return b"source"

    monkeypatch.setattr(sources, "fetch", fetch)
    assert sources.download_native_archive("gcc", native_archive) == (
        b"source",
        native_archive["url"],
    )
    assert calls == [native_archive["url"]]


@pytest.mark.parametrize(
    "error",
    [
        urllib.error.URLError(OSError("Network is unreachable")),
        TimeoutError("private diagnostic"),
        ConnectionResetError("private diagnostic"),
        OSError("private diagnostic"),
        *[
            urllib.error.HTTPError("private-url", code, "private", {}, None)
            for code in (500, 502, 503, 504)
        ],
    ],
)
def test_native_transient_failure_uses_one_checksummed_mirror(
    monkeypatch: pytest.MonkeyPatch,
    native_archive: dict[str, str],
    error: Exception,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = []
    mirror = next(iter(sources.NATIVE_ARCHIVE_FALLBACKS.values()))

    def fetch(url: str) -> bytes:
        calls.append(url)
        if len(calls) == 1:
            raise error
        return b"source"

    monkeypatch.setattr(sources, "fetch", fetch)
    assert sources.download_native_archive("gcc", native_archive) == (b"source", mirror)
    assert calls == [native_archive["url"], mirror]
    output = capsys.readouterr().out
    assert "gcc/gcc-12.4.0.tar.xz" in output
    assert "ftp.gnu.org" in output
    assert "private" not in output


@pytest.mark.parametrize("code", [400, 403, 404, 408, 429])
def test_native_non_retryable_http_response_does_not_contact_mirror(
    monkeypatch: pytest.MonkeyPatch, native_archive: dict[str, str], code: int
) -> None:
    calls = []

    def fetch(url: str) -> bytes:
        calls.append(url)
        raise urllib.error.HTTPError(
            "https://user:secret@example.invalid/?token=hidden", code, "private", {}, None
        )

    monkeypatch.setattr(sources, "fetch", fetch)
    with pytest.raises(ValueError, match=f"ftp.gnu.org: HTTP {code}") as caught:
        sources.download_native_archive("gcc", native_archive)
    assert calls == [native_archive["url"]]
    assert "secret" not in str(caught.value)
    assert "hidden" not in str(caught.value)


def test_native_fallback_failures_are_bounded_and_sanitized(
    monkeypatch: pytest.MonkeyPatch, native_archive: dict[str, str]
) -> None:
    calls = []

    def fetch(url: str) -> bytes:
        calls.append(url)
        raise urllib.error.URLError("secret-token")

    monkeypatch.setattr(sources, "fetch", fetch)
    with pytest.raises(ValueError, match="mirrors.kernel.org: URLError") as caught:
        sources.download_native_archive("gcc", native_archive)
    assert len(calls) == 2
    assert "secret-token" not in str(caught.value)


@pytest.mark.parametrize("primary_fails", [False, True])
def test_native_checksum_mismatch_never_passes_or_triggers_another_download(
    monkeypatch: pytest.MonkeyPatch, native_archive: dict[str, str], primary_fails: bool
) -> None:
    calls = []

    def fetch(url: str) -> bytes:
        calls.append(url)
        if primary_fails and len(calls) == 1:
            raise TimeoutError
        return b"tampered"

    monkeypatch.setattr(sources, "fetch", fetch)
    with pytest.raises(ValueError, match="checksum mismatch"):
        sources.download_native_archive("gcc", native_archive)
    assert len(calls) == (2 if primary_fails else 1)


@pytest.mark.parametrize("reason", ["Source download exceeds the size limit", "Unsafe redirect"])
def test_native_download_validation_failure_does_not_trigger_mirror(
    monkeypatch: pytest.MonkeyPatch, native_archive: dict[str, str], reason: str
) -> None:
    calls = []

    def fetch(url: str) -> bytes:
        calls.append(url)
        raise ValueError(reason)

    monkeypatch.setattr(sources, "fetch", fetch)
    with pytest.raises(ValueError, match="validation failed"):
        sources.download_native_archive("gcc", native_archive)
    assert calls == [native_archive["url"]]


def test_native_changed_checksum_has_no_reviewed_fallback(
    monkeypatch: pytest.MonkeyPatch, native_archive: dict[str, str]
) -> None:
    native_archive["sha256"] = "a" * 64
    calls = []

    def fetch(url: str) -> bytes:
        calls.append(url)
        raise TimeoutError

    monkeypatch.setattr(sources, "fetch", fetch)
    with pytest.raises(ValueError, match="download failed"):
        sources.download_native_archive("gcc", native_archive)
    assert calls == [native_archive["url"]]


@pytest.mark.parametrize(
    "field,value", [("url", "https://localhost/archive"), ("sha256", "invalid")]
)
def test_native_invalid_review_fails_before_download(
    monkeypatch: pytest.MonkeyPatch, native_archive: dict[str, str], field: str, value: str
) -> None:
    native_archive[field] = value

    def fetch(url: str) -> bytes:
        pytest.fail("Invalid review must not perform a request")

    monkeypatch.setattr(sources, "fetch", fetch)
    with pytest.raises(ValueError):
        sources.download_native_archive("gcc", native_archive)


@pytest.mark.parametrize(
    "url",
    [
        "http://mirrors.kernel.org/source",
        "https://localhost/source",
        "https://user:secret@mirrors.kernel.org/source",
    ],
)
def test_reviewed_mirror_cannot_redirect_to_unsafe_endpoint(url: str) -> None:
    request = urllib.request.Request("https://mirrors.kernel.org/source")
    with pytest.raises(ValueError, match="allowed"):
        sources.SafeRedirect().redirect_request(request, None, 302, "Found", {}, url)


def test_native_manifest_records_successful_mirror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, native_archive: dict[str, str]
) -> None:
    mirror = next(iter(sources.NATIVE_ARCHIVE_FALLBACKS.values()))

    def fetch(url: str) -> bytes:
        if url == native_archive["url"]:
            raise TimeoutError
        return b"source"

    monkeypatch.setattr(sources, "fetch", fetch)
    records, _ = sources.collect_native(
        tmp_path,
        {"native_files": []},
        {"schema": 1, "files": [], "groups": {"gcc": {"archives": [native_archive]}}},
        [],
    )
    assert records == [
        {"path": "native/gcc/gcc-12.4.0.tar.xz", "sha256": native_archive["sha256"], "url": mirror}
    ]
    assert (tmp_path / records[0]["path"]).read_bytes() == b"source"
