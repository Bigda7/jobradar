"""Protect reviewed fixture identity, bounded import, and CI-only database wiring."""

import copy
import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest
import yaml
from scripts import prepare_postgres_fixture as fixture
from scripts.prepare_postgres_sources import IMAGES

ROOT = Path(__file__).resolve().parents[2]


def synthetic_archive(tmp_path, monkeypatch, *, architecture="amd64", index_id="sha256:index"):
    config = json.dumps(
        {"os": "linux", "architecture": architecture, "rootfs": {"diff_ids": ["sha256:layer"]}}
    ).encode()
    config_id = "sha256:" + hashlib.sha256(config).hexdigest()
    config_path = "blobs/sha256/" + config_id.removeprefix("sha256:")
    path = tmp_path / "fixture.tar"
    with tarfile.open(path, "w") as archive:
        for name, payload in {
            "index.json": json.dumps({"manifests": [{"digest": index_id}]}).encode(),
            "manifest.json": json.dumps([{"Config": config_path}]).encode(),
            config_path: config,
        }.items():
            member = tarfile.TarInfo(name)
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
    expected = {
        "archive": path.name,
        "size": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "image_id": "sha256:index",
        "config_id": config_id,
        "layer_id": "sha256:layer",
    }
    monkeypatch.setattr(fixture, "metadata", lambda architecture: expected)
    return path, expected


@pytest.mark.parametrize("architecture", ["amd64", "arm64"])
def test_fixture_inventory_matches_reviewed_source_images_and_shell_selector(architecture):
    expected = fixture.metadata(architecture)
    assert expected["image_id"] == IMAGES[architecture]
    assert expected["archive"] == f"jobradar-postgres-17.11-clean-linux-{architecture}.tar"
    script = (ROOT / "scripts/postgres_fixture_image.sh").read_text()
    for key in ("image_id", "config_id", "layer_id"):
        assert f"{key}='{expected[key]}'" in script


@pytest.mark.parametrize("change", ["none", "size", "sha256", "image_id", "config_id", "layer_id"])
def test_archive_identity_checks_fail_closed_without_docker(tmp_path, monkeypatch, change):
    path, expected = synthetic_archive(tmp_path, monkeypatch)
    if change == "size":
        expected[change] += 1
    elif change != "none":
        expected[change] = "wrong"
    if change == "none":
        fixture.verify_archive(path, "amd64")
    else:
        with pytest.raises(ValueError):
            fixture.verify_archive(path, "amd64")


def test_wrong_archive_architecture_is_rejected(tmp_path, monkeypatch):
    path, _ = synthetic_archive(tmp_path, monkeypatch, architecture="arm64")
    with pytest.raises(ValueError, match="platform"):
        fixture.verify_archive(path, "amd64")


@pytest.mark.parametrize("change", ["append", "same_size_corruption", "missing"])
def test_bad_archive_bytes_are_rejected(tmp_path, monkeypatch, change):
    path, _ = synthetic_archive(tmp_path, monkeypatch)
    if change == "append":
        with path.open("ab") as target:
            target.write(b"tampered")
    elif change == "same_size_corruption":
        with path.open("r+b") as target:
            target.write(b"tampered")
    else:
        path.unlink()
    with pytest.raises(ValueError):
        fixture.verify_archive(path, "amd64")


@pytest.mark.parametrize("use_config", [False, True])
@pytest.mark.parametrize("change", ["none", "Id", "Os", "Architecture", "RootFS"])
def test_import_supports_both_docker_stores_but_checks_immutable_identity(
    monkeypatch, use_config, change
):
    monkeypatch.setattr(fixture.shutil, "which", lambda name: "/fixture/bin/docker")
    expected = fixture.metadata("amd64")
    selected = expected["config_id" if use_config else "image_id"]
    record = {
        "Id": selected,
        "Os": "linux",
        "Architecture": "amd64",
        "RootFS": {"Layers": [expected["layer_id"]]},
    }
    if change != "none":
        record[change] = {"Layers": ["wrong"]} if change == "RootFS" else "wrong"

    def inspect(args, **kwargs):
        assert kwargs["timeout"] == 30
        assert args[1:3] == ["image", "inspect"]
        if use_config and args[-1] == expected["image_id"]:
            return subprocess.CompletedProcess(args, 1, b"", b"not found")
        return subprocess.CompletedProcess(args, 0, json.dumps([record]).encode(), b"")

    monkeypatch.setattr(fixture.subprocess, "run", inspect)
    if change == "none":
        assert fixture.installed_image("amd64") == selected
    else:
        with pytest.raises(ValueError, match="identity"):
            fixture.installed_image("amd64")


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/asset",
        "https://untrusted.invalid/asset",
        "https://github.com@untrusted.invalid/asset",
        "https://user:secret@github.com/asset",
        "https://github.com:444/asset",
        "https://github.com/asset#fragment",
        "file:///tmp/asset",
    ],
)
def test_download_and_redirect_targets_reject_unapproved_endpoints(url):
    with pytest.raises(ValueError):
        fixture.public_url(url)


def test_cached_archive_is_verified_without_network(tmp_path, monkeypatch):
    path, _ = synthetic_archive(tmp_path, monkeypatch)
    monkeypatch.setattr(
        fixture.urllib.request,
        "build_opener",
        lambda *args: pytest.fail("Unexpected network access"),
    )
    assert fixture.download_archive(tmp_path, "amd64") == path


@pytest.mark.parametrize("change", ["none", "oversize", "short", "bad_checksum", "header", "error"])
def test_download_is_bounded_and_cleans_its_owned_partial_file(tmp_path, monkeypatch, change):
    source = tmp_path / "source"
    source.mkdir()
    path, expected = synthetic_archive(source, monkeypatch)
    payload = path.read_bytes()
    if change == "oversize":
        payload += b"unexpected"
    elif change == "short":
        payload = payload[:10]
    elif change == "bad_checksum":
        payload = b"x" * len(payload)

    class Response(io.BytesIO):
        headers = {"Content-Length": str(expected["size"] + 1)} if change == "header" else {}

        def geturl(self):
            return "https://release-assets.githubusercontent.com/asset?signature=not-logged"

        def read(self, count):
            assert count == 1024 * 1024
            if change == "error":
                raise OSError("Synthetic interrupted transfer")
            return super().read(count)

    class Opener:
        def open(self, request, timeout):
            assert timeout == 30
            assert request.full_url.endswith(expected["archive"])
            return Response(payload)

    monkeypatch.setattr(fixture.urllib.request, "build_opener", lambda *args: Opener())
    destination = tmp_path / "download"
    if change == "none":
        assert fixture.download_archive(destination, "amd64").read_bytes() == payload
    else:
        with pytest.raises((ValueError, OSError)):
            fixture.download_archive(destination, "amd64")
        assert not list(destination.glob("*.tar"))
    assert not list(destination.glob("*.part"))


@pytest.mark.parametrize("architecture", ["amd64", "arm64"])
@pytest.mark.parametrize("store", ["index", "config", "missing", "wrong_layer", "wrong_arch"])
def test_shell_selector_never_pulls_and_refuses_unreviewed_filesystems(
    tmp_path, architecture, store
):
    shell = "C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else shutil.which("bash")
    if not shell or not Path(shell).is_file():
        pytest.skip("Bash is required for the fixture selection regression")
    expected = fixture.metadata(architecture)
    calls = tmp_path / "calls.log"
    fake = tmp_path / "docker"
    selected = expected["config_id" if store == "config" else "image_id"]
    image_arch = "wrong" if store == "wrong_arch" else architecture
    layer = "wrong" if store == "wrong_layer" else expected["layer_id"]
    fake.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$1 $2" >> "$FIXTURE_CALLS"\n'
        f'if [ "$1" = info ]; then printf "{architecture}\\n"; exit 0; fi\n'
        'if [ "$1 $2" != "image inspect" ]; then exit 99; fi\n'
        + ("exit 1\n" if store == "missing" else "")
        + f'if [ "$5" != "{selected}" ]; then exit 1; fi\n'
        + f"printf '%s\\n' '{selected} linux/{image_arch} [\"{layer}\"]'\n"
    )
    fake.chmod(0o755)
    environment = dict(
        os.environ, FIXTURE_CALLS=str(calls), PATH=str(tmp_path) + os.pathsep + os.environ["PATH"]
    )
    result = subprocess.run(
        [shell, str(ROOT / "scripts/postgres_fixture_image.sh")],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert (result.returncode == 0) == (store in {"index", "config"})
    if result.returncode == 0:
        assert result.stdout.strip() == selected
    assert set(calls.read_text().splitlines()) <= {"info --format", "image inspect"}


def test_ci_requires_verified_import_and_cleans_owned_fixture_even_after_failure():
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    job = workflow["jobs"]["verify"]
    assert "services" not in job
    steps = {step["name"]: step for step in job["steps"]}
    load = steps["Load reviewed PostgreSQL fixture"]
    assert "if" not in load and not load.get("continue-on-error", False)
    assert "prepare_postgres_fixture.py load --architecture amd64" in load["run"]
    start = steps["Start isolated PostgreSQL fixture"]["run"]
    assert "--pull never" in start
    assert "--publish 127.0.0.1:5432:5432" in start
    cleanup = steps["Remove owned PostgreSQL fixture and volume"]
    assert cleanup["if"] == "always()"
    assert 'test "$owner" = "${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"' in cleanup["run"]
    assert 'docker rm --force --volumes "$name"' in cleanup["run"]
    backup = (ROOT / "scripts/verify_postgres_backup.sh").read_text()
    assert "--pull never" in backup and "postgres_fixture_image.sh" in backup
    assert "postgres:17-alpine" not in backup
    assert "postgres:17-alpine" not in (ROOT / ".github/workflows/ci.yml").read_text()
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text())
    assert compose["services"]["db-test"]["pull_policy"] == "never"
    assert IMAGES["amd64"] in compose["services"]["db-test"]["image"]
    assert "postgres:17-alpine@" in compose["services"]["db"]["image"]


def test_selector_uses_json_for_rootfs_on_older_docker_templates():
    script = (ROOT / "scripts/postgres_fixture_image.sh").read_text()
    assert "{{json .RootFS.Layers}}" in script
    assert "{{join .RootFS.Layers" not in script


def test_docker_test_target_contains_fixture_tools_but_runtime_target_is_unchanged():
    dockerfile = (ROOT / "Dockerfile").read_text()
    runtime, test_target = dockerfile.split("FROM runtime AS test\n", 1)
    for name in (
        "scripts/prepare_postgres_fixture.py",
        "scripts/postgres_fixture_image.sh",
        "deploy/postgres-fixture-images.json",
    ):
        assert name in test_target
        assert name not in runtime


def test_failed_import_does_not_start_any_database(tmp_path, monkeypatch):
    path, _ = synthetic_archive(tmp_path, monkeypatch)
    monkeypatch.setattr(fixture.shutil, "which", lambda name: "/fixture/bin/docker")
    calls = []

    def reject_load(args, **kwargs):
        calls.append(copy.deepcopy(args))
        raise subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(fixture.subprocess, "run", reject_load)
    monkeypatch.setattr(
        "sys.argv", ["fixture", "load", "--architecture", "amd64", "--archive", str(path)]
    )
    with pytest.raises(SystemExit, match="1"):
        fixture.main()
    assert len(calls) == 1
    assert calls[0][1:] == ["image", "load", "--input", str(path)]


def test_corrupt_archive_never_reaches_docker_import(tmp_path, monkeypatch):
    path, _ = synthetic_archive(tmp_path, monkeypatch)
    with path.open("r+b") as target:
        target.write(b"corrupt archive")
    monkeypatch.setattr(
        fixture.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("Unexpected Docker invocation"),
    )
    monkeypatch.setattr(
        "sys.argv", ["fixture", "load", "--architecture", "amd64", "--archive", str(path)]
    )
    with pytest.raises(SystemExit, match="1"):
        fixture.main()
