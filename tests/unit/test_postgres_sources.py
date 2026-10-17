"""Fail-closed source-package identity, archive and publication regressions."""

import base64
import hashlib
import io
import json
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest
from scripts import prepare_postgres_sources as pg
from scripts import prepare_runtime_sources as sources


@pytest.mark.parametrize(
    "url",
    [
        "http://go.dev/source",
        "https://go.dev.evil.invalid/source",
        "https://user:secret@go.dev/source",
        "https://go.dev:444/source",
        "https://go.dev/source#fragment",
        "file:///etc/passwd",
        "https://127.0.0.1/source",
    ],
)
def test_source_urls_reject_unsafe_endpoints(url: str) -> None:
    with pytest.raises(ValueError):
        pg.safe_url(url)


def test_module_h1_matches_go_dirhash_format() -> None:
    name = "example.org/module@v1.0.0/LICENSE"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, b"notice")
    line = f"{hashlib.sha256(b'notice').hexdigest()}  {name}\n"
    expected = "h1:" + base64.b64encode(hashlib.sha256(line.encode()).digest()).decode()
    assert pg.module_hash(buffer.getvalue(), "example.org/module", "v1.0.0") == expected


@pytest.mark.parametrize(
    "name",
    [
        "../escape",
        "example.org/module@v1.0.0/../escape",
        "example.org/other@v1.0.0/LICENSE",
        "example.org/module@v1.0.0/a\nb",
    ],
)
def test_module_archive_rejects_wrong_prefix_or_unsafe_names(name: str) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, b"untrusted")
    with pytest.raises(ValueError):
        pg.module_hash(buffer.getvalue(), "example.org/module", "v1.0.0")


def test_duplicate_module_entries_fail() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("m@v1/LICENSE", b"notice")
        with pytest.warns(UserWarning):
            archive.writestr("m@v1/LICENSE", b"replacement")
    with pytest.raises(ValueError):
        pg.module_hash(buffer.getvalue(), "m", "v1")


@pytest.mark.parametrize(
    "name,kind",
    [
        ("../escape", tarfile.REGTYPE),
        ("/absolute", tarfile.REGTYPE),
        ("gosu/source/../../escape", tarfile.REGTYPE),
        ("gosu/source/link", tarfile.SYMTYPE),
        ("unexpected/key.pem", tarfile.REGTYPE),
    ],
)
def test_embedded_tar_rejects_unsafe_or_unexpected_entries(
    tmp_path: Path, name: str, kind: bytes
) -> None:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        member = tarfile.TarInfo(name)
        member.type = kind
        member.size = 0
        archive.addfile(member, io.BytesIO())
    with pytest.raises(ValueError):
        pg.unpack_evidence(buffer.getvalue(), tmp_path)
    assert not (tmp_path.parent / "escape").exists()


def test_archive_hash_mismatch_is_not_preserved(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(pg, "fetch", lambda url: b"tampered")
    with pytest.raises(ValueError, match="checksum mismatch"):
        pg.archive(tmp_path, "go", "source.tar.gz", "https://go.dev/source", "0" * 64)
    assert not list(tmp_path.iterdir())


def test_audit_timeout_cleans_only_owned_container(monkeypatch) -> None:
    calls = []

    def run(executable, args):
        calls.append(args)
        if args[0] == "run":
            assert "--network" in args and "none" in args
            assert "--read-only" in args and "--memory-swap" in args
            assert pg.IMAGES["arm64"] in args
            raise subprocess.TimeoutExpired("docker", 120)
        if args[0] == "ps":
            assert f"label={pg.LABEL}=" in args[-1]
            return b"a" * 64
        return b""

    monkeypatch.setattr(sources, "run_tool", run)
    with pytest.raises(subprocess.TimeoutExpired):
        pg.audit("arm64", "tar", ["-cf", "-", "public-source"])
    assert calls[-1] == ["rm", "--force", "--volumes", "a" * 64]


@pytest.fixture
def bundle(tmp_path: Path, monkeypatch):
    inv = {"image": pg.IMAGES["arm64"], "architecture": "arm64", "apk": []}
    recipe = "scripts/prepare_postgres_sources.py"
    records = [
        sources.preserve(tmp_path, "inventory.json", json.dumps(inv).encode(), "exact image"),
        sources.preserve(tmp_path, "recipe/" + recipe, b"committed recipe", "git"),
    ]
    prefixes = [
        "postgres",
        "embedded/gosu",
        "go",
        *[f"modules/{m}@{v[0]}" for m, v in pg.MODULES.items()],
    ]
    for prefix in prefixes:
        records.append(sources.preserve(tmp_path, prefix + "/source", b"source", "public source"))
        records.append(sources.preserve(tmp_path, prefix + "/LICENSE", b"notice", "public notice"))
    manifest = {
        "schema": 1,
        "kind": "jobradar-postgres-sources",
        "image": pg.IMAGES["arm64"],
        "architecture": "arm64",
        "recipe_revision": "b" * 40,
        "recipe_files": [recipe],
        "complete": True,
        "issues": [],
        "artifacts": records,
        "coverage": pg.component_coverage(inv, {r["path"] for r in records}, [recipe]),
    }
    monkeypatch.setattr(pg, "recipe_files", lambda revision: [recipe])
    monkeypatch.setattr(pg, "verify_embedded_recipe", lambda root: None)
    monkeypatch.setattr(pg, "verify_source_payload", lambda root, inv: None)
    monkeypatch.setattr(sources, "run_tool", lambda executable, args: b"committed recipe")
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    return tmp_path, inv, manifest


def test_complete_exact_bundle_passes(bundle) -> None:
    root, inv, _ = bundle
    pg.verify_bundle(root, "arm64", "b" * 40, inv)


@pytest.mark.parametrize(
    "change",
    [
        "wrong_image",
        "missing_revision",
        "incomplete",
        "unknown_component",
        "missing_notice",
        "unreviewed",
        "wrong_recipe",
    ],
)
def test_bundle_rejects_forged_readiness_or_missing_evidence(bundle, change: str) -> None:
    root, inv, manifest = bundle
    if change == "wrong_image":
        manifest["image"] = "sha256:" + "0" * 64
    elif change == "missing_revision":
        manifest["recipe_revision"] = None
    elif change == "incomplete":
        manifest["issues"] = ["missing source"]
    elif change == "unknown_component":
        manifest["coverage"].pop(next(iter(manifest["coverage"])))
    elif change == "wrong_recipe":
        path = root / "recipe/scripts/prepare_postgres_sources.py"
        path.write_bytes(b"modified")
        next(a for a in manifest["artifacts"] if a["path"].startswith("recipe/"))["sha256"] = (
            hashlib.sha256(b"modified").hexdigest()
        )
    else:
        item = next(iter(manifest["coverage"].values()))
        item["notice_paths"] = [] if change == "missing_notice" else item["notice_paths"]
        item["reviewed"] = change != "unreviewed"
    (root / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        pg.verify_bundle(root, "arm64", "b" * 40, inv)


def test_changed_inventory_fails_even_when_manifest_claims_complete(bundle) -> None:
    root, inv, _ = bundle
    with pytest.raises(ValueError, match="inventory differs"):
        pg.verify_bundle(root, "arm64", "b" * 40, {**inv, "unreviewed": True})


def test_inline_apk_checksum_declaration_is_supported() -> None:
    assert sources.apk_checksums('sha512sums="' + "a" * 128 + '  source.tar.gz"') == {
        "source.tar.gz": "a" * 128
    }


def test_unsafe_alpine_repository_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="repository"):
        sources.collect_apk(
            tmp_path,
            {"origin": "nss_wrapper", "commit": "c" * 40, "repository": "../../private"},
            "v3.24",
        )


def test_incomplete_collected_artifact_cannot_be_promoted_by_manifest_flags(bundle) -> None:
    root, inv, manifest = bundle
    manifest["artifacts"][-1]["collection_status"] = "incomplete"
    (root / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="Incomplete source artifacts"):
        pg.verify_bundle(root, "arm64", "b" * 40, inv)


def test_component_cannot_borrow_another_component_source_files(bundle) -> None:
    root, inv, manifest = bundle
    manifest["coverage"]["postgres:17.11"]["source_paths"] = ["go/source"]
    (root / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="unrelated source files"):
        pg.verify_bundle(root, "arm64", "b" * 40, inv)


def test_missing_oci_labels_are_recorded_as_no_revision(monkeypatch) -> None:
    def run(executable, args):
        assert executable == "docker"
        assert '{{with index .Config "Labels"}}' in args[3]
        return b"\n"

    monkeypatch.setattr(sources, "run_tool", run)
    assert sources.image_revision("sha256:" + "c" * 64) == ""


def test_generated_krb5_config_preserves_recipe_without_executing_it(tmp_path, monkeypatch) -> None:
    recipe = b'pkgname=krb5-conf\nlicense="MIT"\npackage() { exit 99; }\n'

    def fetch(url):
        return b"[]" if "api.github.com" in url else recipe

    monkeypatch.setattr(sources, "fetch", fetch)
    records = sources.collect_apk(tmp_path, {"origin": "krb5-conf", "commit": "c" * 40}, "v3.24")
    assert len(records) == 1
    assert (tmp_path / records[0]["path"]).read_bytes() == recipe
