"""Compare cache memory and enforce an aggregate budget on synthetic local fixtures."""

import argparse
import hashlib
import importlib.util
import json
import os
import re
import time
import uuid
from pathlib import Path

import psycopg

from jobradar.db.access import provision_access

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("resource_checks", HERE / "check_resources.py")
resources = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resources)
run = resources.run
IMAGE = "jobradar-local:memory-20261017"
BASELINE = "jobradar-local:resource-20261017"
GROUP_MEMORY_MIB = 1408
LIMITS = {
    "api": (384, 1.0, 64),
    "worker": (448, 1.0, 64),
    "bot": (128, 0.25, 32),
    "db": (640, 1.0, 128),
}


class Budget:
    def __init__(self, owner):
        if re.fullmatch(r"[0-9a-f]{12}", owner) is None:
            raise ValueError("An exact synthetic fixture owner is required")
        self.owner = owner
        self.parent = f"/jobradar-memory-{owner}"
        self.created = False

    def command(self, operation):
        if operation not in {"create", "read", "remove"}:
            raise ValueError("Unsupported budget operation")
        args = ["wsl.exe", "-d", "docker-desktop", "-u", "root", "--", "sh", "-s", "--"]
        if os.name != "nt":
            args = ["sh", "-s", "--"]
        return run(args + [self.owner, operation], data=(HERE / "budget.sh").read_bytes())

    def create(self):
        self.command("create")
        self.created = True

    def read(self):
        values = {}
        for line in self.command("read").stdout.decode().splitlines():
            key, value = line.split("=", 1)
            values[key] = value.rstrip(",")
        return values

    def close(self):
        if self.created:
            self.command("remove")
            self.created = False


def cache_comparison():
    owner = uuid.uuid4().hex[:12]
    budget = Budget(owner)
    fixture = resources.Fixture(
        "jobradar-postgres-clean:amd64-20261016",
        "amd64",
        "memory",
        owner,
        cgroup_parent=budget.parent,
    )
    containers = []
    network = f"jobradar-memory-{owner}"
    network_created = False
    try:
        budget.create()
        fixture.start()
        fixture.migrate()
        fixture.sql(resources.SEED)
        url = fixture.environment()["DATABASE_URL"].replace("postgresql+psycopg", "postgresql")
        with psycopg.connect(url) as connection:
            provision_access(connection, "jobradar_test", resources.PASSWORDS)
        run(
            [
                "docker",
                "network",
                "create",
                "--internal",
                "--label",
                f"{resources.LABEL}={owner}",
                network,
            ]
        )
        network_created = True
        run(["docker", "network", "connect", network, fixture.name])
        for label, image, memory in (("baseline", BASELINE, 768), ("optimized", IMAGE, 448)):
            name = f"jobradar-memory-{owner}-{label}"
            args = [
                "docker",
                "run",
                "-d",
                "--pull",
                "never",
                "--name",
                name,
                "--label",
                f"{resources.LABEL}={owner}",
                "--cgroup-parent",
                budget.parent,
                "--memory",
                f"{memory}m",
                "--memory-swap",
                f"{memory}m",
                "--cpus",
                "1",
                "--pids-limit",
                "64",
                "--network",
                network,
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
                "--mount",
                f"type=bind,source={HERE},target=/probe,readonly",
                "--env",
                "APP_ENV=test",
                "--env",
                "SERVICE_ROLE=worker",
                "--env",
                "DB_ENFORCE_SERVICE_ROLE=true",
                "--env",
                "TELEGRAM_ENABLED=false",
                "--env",
                "RESOURCE_PROBE_MODE=cache-only",
                "--env",
                f"DATABASE_URL=postgresql+psycopg://jobradar_worker:"
                f"{resources.PASSWORDS['worker']}@{fixture.name}:5432/jobradar_test",
                image,
                "python",
                "/probe/probe.py",
            ]
            run(args)
            containers.append(name)
            print(json.dumps({"cache_comparison": label}), flush=True)
            resources.wait_exit(name, owner, timeout=120)
        values = budget.read()
        print(json.dumps({"comparison_budget": values}), flush=True)
        assert int(values["memory.max"]) == GROUP_MEMORY_MIB * 1024**2
        assert "oom 0" in values["memory.events.local"]
    finally:
        for name in containers:
            resources.owned(name, owner)
            run(["docker", "rm", "--force", name])
        fixture.close()
        if network_created:
            record = json.loads(run(["docker", "network", "inspect", network]).stdout)[0]
            if record["Labels"].get(resources.LABEL) != owner or record["Containers"]:
                raise RuntimeError("Refusing cleanup of an unrelated or occupied network")
            run(["docker", "network", "rm", network])
        budget.close()


def combined_workload(archive=None, *, recovery_only=False):
    owner = uuid.uuid4().hex[:12]
    budget = Budget(owner)
    original_run = resources.run
    original_fixture = resources.Fixture

    class BudgetFixture(original_fixture):
        def __init__(self, *args):
            super().__init__(*args, cgroup_parent=budget.parent)

    def limited_run(args, **kwargs):
        args = list(args)
        if args[:2] == ["docker", "run"] and "--cgroup-parent" not in args:
            args[2:2] = ["--cgroup-parent", budget.parent]
        if args[:2] == ["docker", "update"]:
            args[args.index("--memory") + 1] = "640m"
            args[args.index("--memory-swap") + 1] = "640m"
        if len(args) > 1 and args[1] == "scripts/verify_postgres_backup.sh":
            kwargs["env"] = dict(
                kwargs.get("env", os.environ),
                JOBRADAR_BACKUP_CGROUP_PARENT=budget.parent,
                MSYS_NO_PATHCONV="1",
            )
        result = original_run(args, **kwargs)
        if args[:2] == ["docker", "run"] and "-d" in args:
            record = json.loads(
                original_run(["docker", "inspect", result.stdout.decode().strip()]).stdout
            )[0]
            assert record["HostConfig"]["CgroupParent"] == budget.parent
        return result

    try:
        budget.create()
        resources.IMAGE = IMAGE
        resources.LIMITS = LIMITS
        resources.run = resources.module.run = limited_run
        resources.Fixture = BudgetFixture
        resources.main(
            recovery_only=recovery_only,
            fixture_owner=owner,
            combined_backup=True,
            backup_parent=budget.parent,
            fixture_dump=load_archive(archive) if archive else None,
        )
        values = budget.read()
        print(json.dumps({"combined_budget": values}), flush=True)
        assert int(values["memory.max"]) == GROUP_MEMORY_MIB * 1024**2
        assert int(values["memory.swap.current"]) == 0
        assert "oom 0" in values["memory.events.local"]
        assert values["pids.current"] == "0"
        print(
            json.dumps({"passed": "combined_1408_mib_no_swap", "host_reserve_mib": 384}), flush=True
        )
    finally:
        resources.run = resources.module.run = original_run
        resources.Fixture = original_fixture
        if budget.created:
            print(json.dumps({"final_budget": budget.read()}), flush=True)
        budget.close()


def verify_existing_backup(owner):
    budget = Budget(owner)
    fixture = resources.Fixture(
        "jobradar-postgres-clean:amd64-20261016", "amd64", "resources", owner
    )
    assert fixture.inspect()["HostConfig"]["CgroupParent"] == budget.parent
    resources.verify_backup(fixture, cgroup_parent=budget.parent)
    print(json.dumps({"verified_existing_backup_budget": budget.read()}), flush=True)


def load_archive(path):
    path = Path(path).resolve()
    expected = HERE.parents[1] / ".deploy/memory-2gb-20261017"
    if (
        path.parent != expected.resolve()
        or re.fullmatch(r"completed-fixture-[0-9a-f]{12}\.dump", path.name) is None
    ):
        raise ValueError("Only this task's synthetic memory fixture archives are accepted")
    metadata = json.loads(path.with_suffix(".json").read_text())
    data = path.read_bytes()
    if metadata != {
        "kind": "synthetic-jobradar-memory",
        "sha256": hashlib.sha256(data).hexdigest(),
    }:
        raise ValueError("Synthetic fixture archive metadata does not match")
    if not data.startswith(b"PGDMP"):
        raise ValueError("A custom synthetic PostgreSQL archive is required")
    return data


def local_image(value):
    if re.fullmatch(r"jobradar-local:[a-z0-9][a-z0-9_.-]*|sha256:[0-9a-f]{64}", value) is None:
        raise argparse.ArgumentTypeError("An existing local JobRadar image is required")
    return value


def capture_existing(owner):
    budget = Budget(owner)
    fixture = resources.Fixture(
        "jobradar-postgres-clean:amd64-20261016", "amd64", "resources", owner
    )
    assert fixture.inspect()["HostConfig"]["CgroupParent"] == budget.parent
    deadline = time.monotonic() + 1800
    while time.monotonic() < deadline:
        count = int(fixture.sql("SELECT count(*) FROM match_evaluations;"))
        print(json.dumps({"waiting_for_synthetic_snapshot": count}), flush=True)
        if count == 10000:
            path = HERE.parents[1] / f".deploy/memory-2gb-20261017/completed-fixture-{owner}.dump"
            data = fixture.dump()
            with path.open("xb") as output:
                output.write(data)
            with path.with_suffix(".json").open("x") as output:
                json.dump(
                    {
                        "kind": "synthetic-jobradar-memory",
                        "sha256": hashlib.sha256(data).hexdigest(),
                    },
                    output,
                )
            print(
                json.dumps({"synthetic_snapshot_saved": path.name, "bytes": len(data)}), flush=True
            )
            return
        time.sleep(15)
    raise RuntimeError("Synthetic snapshot exceeded its bounded deadline")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=local_image, default=IMAGE)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--combined", action="store_true")
    modes.add_argument("--backup-existing")
    modes.add_argument("--capture-existing")
    modes.add_argument("--restore-fixture")
    parser.add_argument(
        "--recovery-only", action="store_true", help="Repeat only isolated failure/restart checks"
    )
    options = parser.parse_args()
    IMAGE = options.image
    if options.recovery_only and not options.restore_fixture:
        parser.error("--recovery-only requires this task's checked synthetic --restore-fixture")
    if options.capture_existing:
        capture_existing(options.capture_existing)
    elif options.restore_fixture:
        combined_workload(options.restore_fixture, recovery_only=options.recovery_only)
    elif options.backup_existing:
        verify_existing_backup(options.backup_existing)
    elif options.combined:
        combined_workload()
    else:
        cache_comparison()
