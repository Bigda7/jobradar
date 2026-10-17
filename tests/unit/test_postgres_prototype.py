import importlib.util
import json
import subprocess
from contextlib import nullcontext
from pathlib import Path

import pytest

PROBE = Path(__file__).resolve().parents[2] / "deploy/postgres-prototype/check_candidate.py"


@pytest.fixture
def prototype():
    spec = importlib.util.spec_from_file_location("postgres_prototype", PROBE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def record(fixture, *, owner=None, volume=None, host="127.0.0.1"):
    return {
        "Config": {"Labels": {"jobradar.postgres.prototype.run": owner or fixture.run_id}},
        "Mounts": [{"Type": "volume", "Name": volume or fixture.volume}],
        "NetworkSettings": {"Ports": {"5432/tcp": [{"HostIp": host, "HostPort": "25432"}]}},
    }


@pytest.mark.parametrize("bad", ("owner", "volume"))
def test_fixture_rejects_unrelated_container_or_volume(prototype, monkeypatch, bad):
    fixture = prototype.Fixture("synthetic-image", "amd64", "candidate", "test-run")
    payload = record(fixture, **{bad: "unrelated-user-resource"})
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, json.dumps([payload]).encode(), b"")

    monkeypatch.setattr(prototype, "run", fake_run)
    fixture.created = True
    with pytest.raises(RuntimeError, match="ownership|volume"):
        fixture.close()
    assert len(calls) == 1
    assert calls[0][:2] == ["docker", "inspect"]


def test_fixture_rejects_non_loopback_database(prototype, monkeypatch):
    fixture = prototype.Fixture("synthetic-image", "amd64", "candidate", "test-run")
    monkeypatch.setattr(prototype.Fixture, "inspect", lambda _: record(fixture, host="0.0.0.0"))
    with pytest.raises(RuntimeError, match="non-loopback"):
        fixture.environment()


def test_fixture_overrides_inherited_database_and_notifications(prototype, monkeypatch):
    fixture = prototype.Fixture("synthetic-image", "amd64", "candidate", "test-run")
    monkeypatch.setattr(prototype.Fixture, "inspect", lambda _: record(fixture))
    monkeypatch.setenv("DATABASE_URL", "postgresql://production.invalid/production")
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://production.invalid/production")
    monkeypatch.setenv("TELEGRAM_ENABLED", "true")
    monkeypatch.setenv("API_BEARER_TOKEN", "synthetic-inherited-token")
    monkeypatch.setenv("API_ALLOWED_HOSTS", "production.invalid")
    environment = fixture.environment()
    assert environment["DATABASE_URL"] == environment["TEST_DATABASE_URL"]
    assert "@127.0.0.1:25432/jobradar_test" in environment["DATABASE_URL"]
    assert "production.invalid" not in environment["DATABASE_URL"]
    assert environment["APP_ENV"] == "test"
    assert environment["TELEGRAM_ENABLED"] == "false"
    assert environment["API_BEARER_TOKEN"] == ""
    assert environment["API_ALLOWED_HOSTS"] == "localhost;127.0.0.1;test"


def test_fixture_rejects_unowned_volume_cleanup(prototype, monkeypatch):
    fixture = prototype.Fixture("synthetic-image", "amd64", "candidate", "test-run")
    fixture.volume_created = True
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        payload = [{"Labels": {"jobradar.postgres.prototype.run": "unrelated"}}]
        return subprocess.CompletedProcess(args, 0, json.dumps(payload).encode(), b"")

    monkeypatch.setattr(prototype, "run", fake_run)
    with pytest.raises(RuntimeError, match="non-fixture"):
        fixture.close()
    assert len(calls) == 1
    assert calls[0][:3] == ["docker", "volume", "inspect"]


def test_bootstrap_restores_repository_module_search_path(monkeypatch):
    path = PROBE.with_name("test_bootstrap.py")
    spec = importlib.util.spec_from_file_location("postgres_test_bootstrap", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = str(PROBE.parents[2])
    monkeypatch.setattr(module.sys, "path", [value for value in module.sys.path if value != root])
    monkeypatch.setattr(module.sys, "argv", [str(path), "pytest", "--collect-only"])
    monkeypatch.setattr(module.sys, "platform", "linux")
    for name in ("DATABASE_URL", "TEST_DATABASE_URL"):
        monkeypatch.setenv(name, "postgresql+psycopg://fixture@127.0.0.1:25432/jobradar_test")
    calls = []
    monkeypatch.setattr(module.runpy, "run_module", lambda *args, **kwargs: calls.append(args))
    module.main()
    assert module.sys.path[0] == root
    assert calls == [("pytest",)]


def test_ready_requires_final_tcp_server_not_temporary_unix_server(prototype, monkeypatch):
    fixture = prototype.Fixture("synthetic-image", "arm64", "baseline", "test-run")
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, b"", b"")

    monkeypatch.setattr(prototype, "run", fake_run)
    monkeypatch.setattr(prototype.Fixture, "inspect", lambda _: record(fixture))
    monkeypatch.setattr("socket.create_connection", lambda *args, **kwargs: nullcontext())
    fixture.ready()
    assert calls[0][:4] == ["docker", "exec", fixture.name, "pg_isready"]
    assert calls[0][4:6] == ["-h", "127.0.0.1"]


def test_fixture_never_pulls_or_changes_selected_local_candidate(prototype, monkeypatch):
    fixture = prototype.Fixture("local-candidate:approved", "arm64", "candidate", "test-run")
    calls = []
    monkeypatch.setattr(prototype.Fixture, "ready", lambda _: None)

    def fake_run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, b"", b"")

    monkeypatch.setattr(prototype, "run", fake_run)
    fixture.start()
    command = calls[1]
    assert command[command.index("--pull") + 1] == "never"
    assert command[-1] == "local-candidate:approved"
    assert command[command.index("--platform") + 1] == "linux/arm64"
    assert command[command.index("--publish") + 1] == "127.0.0.1::5432"


@pytest.fixture
def gosu_runtime():
    path = PROBE.parent / "gosu/check_runtime.py"
    spec = importlib.util.spec_from_file_location("gosu_runtime", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("owner", [b"unrelated", b"postgres-gosu-runtime-20261016"])
def test_gosu_timeout_cleanup_requires_task_ownership(gosu_runtime, monkeypatch, owner):
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, owner, b"")

    monkeypatch.setattr(gosu_runtime.subprocess, "run", fake_run)
    if owner == b"unrelated":
        with pytest.raises(RuntimeError, match="unrelated"):
            gosu_runtime.cleanup_timeout("synthetic-fixture")
        assert len(calls) == 1
    else:
        gosu_runtime.cleanup_timeout("synthetic-fixture")
        assert calls[1] == ["docker", "rm", "--force", "synthetic-fixture"]


def test_emulated_setid_checks_are_reported_unverified_not_passed(
    gosu_runtime, monkeypatch, capsys
):
    import sys

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "check_runtime",
            "--architecture",
            "arm64",
            "--candidate-image",
            "fixture",
            "--emulated-arm64",
        ],
    )
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, b"", b"")

    monkeypatch.setattr(gosu_runtime.subprocess, "run", fake_run)
    gosu_runtime.main()
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert set(result["unverified"]) == gosu_runtime.SETID_CHECKS
    assert not set(result["checks"]) & gosu_runtime.SETID_CHECKS
    assert len(calls) == len(gosu_runtime.CHECKS) - len(gosu_runtime.SETID_CHECKS)
