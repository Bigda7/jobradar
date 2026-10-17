import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def load_module(path):
    spec = importlib.util.spec_from_file_location("resource_fixture_test", ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "memory,expected",
    [
        (1924182016, True),
        (1792 * 1024**2 - 1, False),
        (1792 * 1024**2, True),
        (4 * 1024**3, True),
    ],
)
def test_capacity_gate_requires_measured_ram_not_swap(memory: int, expected: bool) -> None:
    module = load_module("scripts/check_resource_capacity.py")
    assert module.capacity_result(memory)["verified"] is expected


@pytest.mark.parametrize("memory", [0, -1])
def test_capacity_gate_refuses_invalid_measurements(memory: int) -> None:
    module = load_module("scripts/check_resource_capacity.py")
    with pytest.raises(ValueError):
        module.capacity_result(memory)


def test_capacity_cli_accepts_current_server_and_refuses_insufficient_ram() -> None:
    for memory, code in ((1924182016, 0), (1024**3, 1), (-1, 1)):
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/check_resource_capacity.py"),
                "--memory-bytes",
                str(memory),
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert result.returncode == code
        assert json.loads(result.stdout.splitlines()[0])["verified"] is (code == 0)


def test_resource_override_is_opt_in_and_changes_only_limits() -> None:
    base = yaml.safe_load((ROOT / "compose.yaml").read_text())
    override = yaml.safe_load((ROOT / "compose.resources.yaml").read_text())
    expected = {
        "api": (384, 1.0, 64),
        "worker": (448, 1.0, 64),
        "bot": (128, 0.25, 32),
        "db": (640, 1.0, 128),
        "migrate": (256, 1.0, 64),
    }
    assert set(override) == {"services"}
    assert set(override["services"]) == set(expected)
    for name, (memory, cpu, pids) in expected.items():
        values = override["services"][name]
        assert values == {
            "cgroup_parent": "jobradar.slice",
            "mem_limit": f"{memory}m",
            "memswap_limit": f"{memory}m",
            "cpus": cpu,
            "pids_limit": pids,
        }
        assert "mem_limit" not in base["services"][name]
        assert "oom_kill_disable" not in values
    capacity = load_module("scripts/check_resource_capacity.py")
    assert capacity.GROUP_MEMORY_MIB + capacity.HOST_RESERVE_MIB == capacity.MINIMUM_MEMORY_MIB
    runner = load_module("deploy/resource-prototype/check_resources.py")
    assert runner.LIMITS == {name: value for name, value in expected.items() if name != "migrate"}


def test_fixture_refuses_unrelated_containers(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_module("deploy/resource-prototype/check_resources.py")
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        record = {"Config": {"Labels": {module.LABEL: "another-task"}}}
        return subprocess.CompletedProcess(args, 0, json.dumps([record]).encode(), b"")

    monkeypatch.setattr(module, "run", fake_run)
    with pytest.raises(RuntimeError, match="unrelated"):
        module.owned("unrelated-fixture", "expected-owner")
    assert calls == [["docker", "inspect", "unrelated-fixture"]]


def test_resume_refuses_invalid_owner_before_access(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_module("deploy/resource-prototype/check_resources.py")

    def forbidden_run(*args, **kwargs):
        pytest.fail("Invalid resume owner reached Docker")

    monkeypatch.setattr(module, "run", forbidden_run)
    with pytest.raises(ValueError, match="exact synthetic fixture owner"):
        module.main("not-an-owned-fixture")


@pytest.mark.parametrize("address", ["127.0.0.1", "0.0.0.0"])
def test_api_port_is_resolved_from_current_loopback_mapping(monkeypatch, address) -> None:
    module = load_module("deploy/resource-prototype/check_resources.py")
    port = {"HostIp": address, "HostPort": "54321"}
    monkeypatch.setattr(
        module, "owned", lambda *args: {"NetworkSettings": {"Ports": {"8000/tcp": [port]}}}
    )
    if address == "127.0.0.1":
        assert module.api_base("fixture", "owner") == "http://127.0.0.1:54321"
        port["HostPort"] = "54322"
        assert module.api_base("fixture", "owner") == "http://127.0.0.1:54322"
    else:
        with pytest.raises(RuntimeError, match="non-loopback"):
            module.api_base("fixture", "owner")


def test_server_probe_does_not_fetch_credentials_or_mutate_production() -> None:
    script = (ROOT / "scripts/review_server_capacity.py").read_text()
    for forbidden in (
        "iam/security-credentials",
        "apt-get update",
        "apt-get upgrade",
        "systemctl restart",
        "docker restart",
        "docker update",
        "docker compose config",
    ):
        assert forbidden not in script
    assert '"--simulate"' in script
    assert '"--instance-ids"' in script
    assert '"--group-ids"' in script
    assert '"head-object"' in script
    assert '"jobradar-cache-cleanup.timer"' in script
    assert '"--property=LoadState,' in script


def test_backup_verifier_has_a_separate_bounded_memory_allowance() -> None:
    script = (ROOT / "scripts/verify_postgres_backup.sh").read_text()
    for option in (
        "--memory 256m",
        "--memory-swap 256m",
        "--cpus 1",
        "--pids-limit 128",
        "--network none",
        "--exit-on-error",
        "--single-transaction",
    ):
        assert option in script
    assert "--oom-kill-disable" not in script


@pytest.mark.parametrize("change", ["none", "missing", "inactive", "unlimited", "swap", "pids"])
def test_slice_preflight_refuses_missing_or_weakened_configuration(monkeypatch, change):
    module = load_module("scripts/check_resource_capacity.py")
    values = {
        "LoadState": "loaded",
        "ActiveState": "active",
        "MemoryMax": "1476395008",
        "MemorySwapMax": "0",
        "TasksMax": "512",
    }
    if change == "missing":
        values.pop("MemoryMax")
    if change == "inactive":
        values["ActiveState"] = "inactive"
    if change == "unlimited":
        values["MemoryMax"] = "infinity"
    if change == "swap":
        values["MemorySwapMax"] = "infinity"
    if change == "pids":
        values["TasksMax"] = "infinity"
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args, 0, "\n".join(f"{key}={value}" for key, value in values.items()), ""
        ),
    )
    assert module.slice_is_verified() is (change == "none")


def test_shared_budget_and_backup_dropin_match_profile():
    unit = (ROOT / "deploy/systemd/jobradar.slice").read_text()
    dropin = (ROOT / "deploy/systemd/jobradar-backup.service.d/resources.conf").read_text()
    capacity = load_module("scripts/check_resource_capacity.py")
    runner = load_module("deploy/resource-prototype/check_2gb.py")
    assert f"MemoryMax={capacity.GROUP_MEMORY_MIB}M" in unit
    assert "MemorySwapMax=0" in unit and "TasksMax=512" in unit
    assert runner.GROUP_MEMORY_MIB == capacity.GROUP_MEMORY_MIB
    assert "Slice=jobradar.slice" in dropin
    assert "JOBRADAR_BACKUP_CGROUP_PARENT=jobradar.slice" in dropin


def test_budget_helper_refuses_bad_owner_and_bad_operation(monkeypatch):
    module = load_module("deploy/resource-prototype/check_2gb.py")
    monkeypatch.setattr(module, "run", lambda *args, **kwargs: pytest.fail("Unsafe control call"))
    with pytest.raises(ValueError):
        module.Budget("../outside")
    with pytest.raises(ValueError):
        module.Budget("123456abcdef").command("delete-all")


@pytest.mark.parametrize("kind", ["valid", "changed", "outside"])
def test_memory_archive_is_scoped_to_checked_synthetic_task_data(tmp_path, monkeypatch, kind):
    module = load_module("deploy/resource-prototype/check_2gb.py")
    monkeypatch.setattr(module, "HERE", tmp_path / "deploy/resource-prototype")
    directory = tmp_path / ".deploy/memory-2gb-20261017"
    directory.mkdir(parents=True)
    data = b"PGDMPsynthetic fixture"
    path = directory / "completed-fixture-123456abcdef.dump"
    path.write_bytes(data)
    path.with_suffix(".json").write_text(
        json.dumps(
            {"kind": "synthetic-jobradar-memory", "sha256": hashlib.sha256(data).hexdigest()}
        )
    )
    if kind == "changed":
        path.write_bytes(b"changed")
    if kind == "outside":
        path = tmp_path / "not-a-task-backup.dump"
    if kind == "valid":
        assert module.load_archive(path) == data
    else:
        with pytest.raises(ValueError):
            module.load_archive(path)


@pytest.mark.parametrize("valid", [True, False])
def test_restored_memory_checkpoint_uses_real_bytes_and_validates_counts(valid):
    module = load_module("deploy/resource-prototype/check_resources.py")

    class Fixture:
        restored = False

        def restore(self, data):
            assert data == b"synthetic checkpoint"
            self.restored = True

        def sql(self, query):
            assert self.restored
            return json.dumps(
                {"marker": 1, "listings": 14000, "evaluations": 10000 if valid else 0}
            ).encode()

    fixture = Fixture()
    if valid:
        module.restore_completed_fixture(fixture, b"synthetic checkpoint")
    else:
        with pytest.raises(RuntimeError, match="checkpoint does not match"):
            module.restore_completed_fixture(fixture, b"synthetic checkpoint")


@pytest.mark.parametrize(
    "timestamp", ["2026-10-17T10:38:22.363504378Z", "2026-10-17T10:38:22", "invalid"]
)
def test_docker_event_bounds_use_a_valid_daemon_clock(monkeypatch, timestamp):
    module = load_module("deploy/resource-prototype/check_resources.py")

    def daemon_info(args):
        assert args == ["docker", "info", "--format", "{{.SystemTime}}"]
        return subprocess.CompletedProcess(args, 0, timestamp.encode(), b"")

    monkeypatch.setattr(module, "run", daemon_info)
    if timestamp.endswith("Z"):
        assert module.daemon_timestamp() == timestamp
    else:
        with pytest.raises(ValueError):
            module.daemon_timestamp()


@pytest.mark.parametrize(
    "image,valid",
    [
        ("jobradar-local:api-performance-20261017", True),
        ("sha256:" + "a" * 64, True),
        ("remote.example/image:latest", False),
        ("--privileged", False),
    ],
)
def test_resource_candidate_requires_an_existing_local_image(image, valid):
    module = load_module("deploy/resource-prototype/check_2gb.py")
    if valid:
        assert module.local_image(image) == image
    else:
        with pytest.raises(module.argparse.ArgumentTypeError):
            module.local_image(image)
