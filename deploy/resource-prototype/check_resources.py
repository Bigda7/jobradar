"""Verify resource candidates on labeled, isolated, synthetic Docker fixtures."""

import argparse
import concurrent.futures
import importlib.util
import json
import os
import re
import shutil
import tempfile
import time
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

import psycopg

from jobradar.db.access import ROLES, provision_access

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "resource_pg_fixture", ROOT / "deploy/postgres-prototype/check_candidate.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
Fixture, run = module.Fixture, module.run
LABEL = "jobradar.resource.prototype.run"
IMAGE = "jobradar-local:memory-20261017"
LIMITS = {
    "api": (384, 1.0, 64),
    "worker": (448, 1.0, 64),
    "bot": (128, 0.25, 32),
    "db": (640, 1.0, 128),
}
PASSWORDS = {role: f"synthetic-resource-{role}-{'x' * 32}" for role in ROLES}
SEED = """
INSERT INTO sources (name,display_name,opportunity_kind,enabled,failure_alert_active,
coverage_alert_active,request_budget,created_at,updated_at)
VALUES ('djinni','Synthetic Djinni','employment',true,false,false,'{}',now(),now()),
('resource-fixture','Resource Fixture','employment',false,false,false,'{}',now(),now());
INSERT INTO opportunities (kind,status,canonical_key,title,company,description,location_text,
work_mode,employment_type,first_seen_at,last_seen_at,created_at,updated_at)
SELECT 'employment','active',md5(g::text),'Junior Python Developer '||g,'Synthetic Company '||g,
left(repeat('Build React Django Python PostgreSQL applications remotely worldwide. ',120),7983),
'Remote Worldwide','remote','full_time',now(),now(),now(),now() FROM generate_series(1,14000) g;
INSERT INTO listings (source_id,opportunity_id,external_id,source_url,canonical_url,content_hash,
raw_data,normalized_data,quality_score,first_seen_at,last_seen_at,is_active,created_at,updated_at)
SELECT (SELECT id FROM sources WHERE name='djinni'),o.id,o.id::text,
'https://djinni.co/jobs/'||(8000000+o.id)||'/', 'https://djinni.co/jobs/'||(8000000+o.id)||'/',
md5(o.id::text),jsonb_build_object('id',o.id::text,'url','https://djinni.co/jobs/'||(8000000+o.id)||'/',
'title',o.title,'company',o.company,'description',o.description,'work_mode','remote',
'location','Remote Worldwide','employment_type','full_time'),
jsonb_build_object('kind','employment','title',o.title,'company',o.company,
'description',o.description,'location_text','Remote Worldwide','work_mode','remote',
'employment_type','full_time'),0,now(),now(),o.id<=10000,now(),now()
FROM opportunities o;
INSERT INTO source_runs (source_id,status,started_at,error_message,discovered_count,
candidate_count,filtered_count,detail_failure_count,page_count,limit_reached,created_count,
updated_count,unchanged_count,duplicate_count,deactivated_count,normalization_error_count,
warning_count,error_count)
SELECT id,'running',now(),'synthetic-oom-armed',0,0,0,0,0,false,0,0,0,0,0,0,0,0
FROM sources WHERE name='resource-fixture';
INSERT INTO notification_deliveries (opportunity_id,profile_id,channel,event_key,status,attempts,
message_text,source_url,created_at,sent_at)
SELECT 1,'resource-profile','telegram',key,status,attempts,'Synthetic stored message',
'https://djinni.co/jobs/8000001/',now(),CASE WHEN status='sent' THEN now() END
FROM (VALUES ('already-sent','sent',1),('linked-pending','pending',0),
('unconfirmed-pending','pending',0),('queued','queued',0)) v(key,status,attempts);
INSERT INTO telegram_opportunity_messages (opportunity_id,telegram_message_id,delivery_id,
created_at,updated_at)
SELECT opportunity_id,700000+id,id,now(),now() FROM notification_deliveries
WHERE event_key IN ('already-sent','linked-pending');
"""


def owned(name, owner):
    record = json.loads(run(["docker", "inspect", name]).stdout)[0]
    if record["Config"]["Labels"].get(LABEL) != owner:
        raise RuntimeError("Refusing access to an unrelated container")
    return record


def api_base(name, owner):
    port = owned(name, owner)["NetworkSettings"]["Ports"]["8000/tcp"][0]
    if port["HostIp"] != "127.0.0.1":
        raise RuntimeError("Refusing a non-loopback API fixture")
    return f"http://127.0.0.1:{int(port['HostPort'])}"


def wait_exit(name, owner, timeout=600, restart=False):
    deadline = time.monotonic() + timeout
    progress = 0
    while time.monotonic() < deadline:
        record = owned(name, owner)
        if record["State"]["Status"] == "exited" and (not restart or record["RestartCount"] >= 1):
            output = run(["docker", "logs", name])
            # All workloads use synthetic data and no real transport/client credentials.
            print(output.stdout.decode(errors="replace"), flush=True)
            print(output.stderr.decode(errors="replace"), flush=True)
            if record["State"]["ExitCode"]:
                raise RuntimeError(
                    f"Synthetic workload failed: exit={record['State']['ExitCode']}, "
                    f"oom_killed={record['State']['OOMKilled']}"
                )
            return record
        if time.monotonic() >= progress:
            print(
                json.dumps(
                    {
                        "waiting_for": name,
                        "state": record["State"]["Status"],
                        "restarts": record["RestartCount"],
                    }
                ),
                flush=True,
            )
            progress = time.monotonic() + 30
        time.sleep(2)
    raise RuntimeError("Synthetic workload exceeded its deadline")


def verify_backup(fixture, *, cgroup_parent=None):
    git = shutil.which("git")
    shell = str(Path(git).parent.parent / "bin/bash.exe") if os.name == "nt" else "bash"
    environment = None
    if cgroup_parent:
        environment = dict(
            os.environ, JOBRADAR_BACKUP_CGROUP_PARENT=cgroup_parent, MSYS_NO_PATHCONV="1"
        )

    def assert_fixture_volume_removed(result):
        volumes = re.findall(rb"Restore volume: ([a-f0-9]{64})", result.stdout)
        assert len(volumes) == 1
        assert (
            run(
                ["docker", "volume", "inspect", volumes[0].decode()], allowed_failure=True
            ).returncode
            != 0
        )
        if cgroup_parent:
            assert b"Backup restore resource group verified." in result.stdout

    with tempfile.NamedTemporaryFile(suffix=".dump", delete=False) as archive:
        path = Path(archive.name)
        archive.write(fixture.dump())
    try:
        result = run(
            [shell, "scripts/verify_postgres_backup.sh", path.as_posix()],
            env=environment,
            timeout=120,
        )
        print(result.stdout.decode(errors="replace"), flush=True)
        assert b"opportunities=14000" in result.stdout
        assert b"listings=14000" in result.stdout
        assert_fixture_volume_removed(result)
        path.write_bytes(b"synthetic corrupt archive")
        result = run(
            [shell, "scripts/verify_postgres_backup.sh", path.as_posix()],
            timeout=120,
            env=environment,
            allowed_failure=True,
        )
        assert result.returncode != 0
        assert b"could not be restored" in result.stderr
        assert_fixture_volume_removed(result)
    finally:
        path.unlink()
    print(
        json.dumps({"bounded_backup_restore": True, "corrupt_archive_rejected": True}), flush=True
    )


def restore_completed_fixture(fixture, data):
    fixture.restore(data)
    counts = json.loads(
        fixture.sql(
            "SELECT json_build_object('marker',(SELECT count(*) FROM sources "
            "WHERE name='resource-fixture'),'listings',(SELECT count(*) FROM listings),"
            "'evaluations',(SELECT count(*) FROM match_evaluations));"
        )
    )
    if counts != {"marker": 1, "listings": 14000, "evaluations": 10000}:
        raise RuntimeError("Restored synthetic memory checkpoint does not match")


def daemon_timestamp():
    value = run(["docker", "info", "--format", "{{.SystemTime}}"])
    timestamp = value.stdout.decode().strip()
    if datetime.fromisoformat(timestamp).utcoffset() is None:
        raise ValueError("Docker event bounds require an explicit timezone")
    return timestamp


def main(
    resume=None,
    recovery_only=False,
    *,
    fixture_owner=None,
    combined_backup=False,
    backup_parent=None,
    fixture_dump=None,
):
    if resume and recovery_only:
        raise ValueError("Resume and recovery-only are separate fixture modes")
    if resume is not None and re.fullmatch(r"[0-9a-f]{12}", resume) is None:
        raise ValueError("Resume requires an exact synthetic fixture owner")
    if fixture_owner is not None and re.fullmatch(r"[0-9a-f]{12}", fixture_owner) is None:
        raise ValueError("An exact synthetic fixture owner is required")
    owner = resume or fixture_owner or uuid.uuid4().hex[:12]
    network = f"jobradar-resource-{owner}"
    fixture = Fixture("jobradar-postgres-clean:amd64-20261016", "amd64", "resources", owner)
    containers = []
    network_created = False
    try:
        if resume:
            fixture.inspect()
            volume = json.loads(run(["docker", "volume", "inspect", fixture.volume]).stdout)[0]
            record = json.loads(run(["docker", "network", "inspect", network]).stdout)[0]
            if volume["Labels"].get(module.LABEL) != owner or record["Labels"].get(LABEL) != owner:
                raise RuntimeError("Refusing resume of unrelated fixture resources")
            existing = run(["docker", "ps", "-aq", "--filter", f"label={LABEL}={owner}"])
            for identifier in existing.stdout.decode().splitlines():
                record = owned(identifier, owner)
                if (
                    record["Image"]
                    != json.loads(run(["docker", "image", "inspect", IMAGE]).stdout)[0]["Id"]
                ):
                    raise RuntimeError("Refusing resume with a different runtime image")
                containers.append(record["Name"].removeprefix("/"))
            fixture.created = fixture.volume_created = network_created = True
            run(["docker", "start", fixture.name])
            fixture.ready()
            counts = json.loads(
                fixture.sql(
                    "SELECT json_build_object('opportunities',(SELECT count(*) FROM opportunities),"
                    "'listings',(SELECT count(*) FROM listings),'active',"
                    "(SELECT count(*) FROM listings WHERE is_active),'completed_source',"
                    "(SELECT count(*) FROM source_runs WHERE status='succeeded' "
                    "AND discovered_count=10000));"
                )
            )
            assert counts == {
                "opportunities": 14000,
                "listings": 14000,
                "active": 10000,
                "completed_source": 1,
            }
            print(json.dumps({"resumed_fixture": owner, "checkpoint": counts}), flush=True)
        else:
            fixture.start()
        fixture.inspect()
        run(
            [
                "docker",
                "update",
                "--memory",
                f"{LIMITS['db'][0]}m",
                "--memory-swap",
                f"{LIMITS['db'][0]}m",
                "--cpus",
                "1",
                "--pids-limit",
                "128",
                fixture.name,
            ]
        )
        if not resume:
            if fixture_dump is None:
                fixture.migrate()
                fixture.sql(SEED)
            else:
                restore_completed_fixture(fixture, fixture_dump)
            url = fixture.environment()["DATABASE_URL"].replace("postgresql+psycopg", "postgresql")
            with psycopg.connect(url) as connection:
                provision_access(connection, "jobradar_test", PASSWORDS)
            run(
                [
                    "docker",
                    "network",
                    "create",
                    "--internal",
                    "--label",
                    f"{LABEL}={owner}",
                    network,
                ]
            )
            network_created = True
            run(["docker", "network", "connect", network, fixture.name])

        def launch(role, mode=None, *, restart=False):
            name = f"jobradar-resource-{owner}-{role}-{mode or 'http'}"
            memory, cpus, pids = LIMITS[role]
            if name in containers:
                record = owned(name, owner)
                if role == "api":
                    run(["docker", "start", name])
                elif record["State"]["ExitCode"] != 0:
                    raise RuntimeError("Existing completed workload did not succeed")
                return name
            args = [
                "docker",
                "run",
                "-d",
                "--pull",
                "never",
                "--name",
                name,
                "--label",
                f"{LABEL}={owner}",
                "--network",
                "bridge" if role == "api" else network,
                "--read-only",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,size=64m",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
                "--init",
                "--memory",
                f"{memory}m",
                "--memory-swap",
                f"{memory}m",
                "--cpus",
                str(cpus),
                "--pids-limit",
                str(pids),
            ]
            if restart:
                args += ["--restart", "on-failure:1"]
            values = {
                "APP_ENV": "test",
                "SERVICE_ROLE": role,
                "DB_ENFORCE_SERVICE_ROLE": "true",
                "DATABASE_URL": f"postgresql+psycopg://jobradar_{role}:{PASSWORDS[role]}@"
                f"{fixture.name}:5432/jobradar_test",
                "TELEGRAM_ENABLED": "false",
                "API_BEARER_TOKEN": "",
                "API_ALLOWED_HOSTS": "localhost;127.0.0.1;test",
            }
            if mode:
                values["RESOURCE_PROBE_MODE"] = mode
                if combined_backup and mode == "worker":
                    values["RESOURCE_CACHE_HOLD_SECONDS"] = "12"
                args += [
                    "--mount",
                    f"type=bind,source={Path(__file__).parent},target=/probe,readonly",
                ]
            else:
                args += ["--publish", "127.0.0.1::8000"]
            for key, value in values.items():
                args += ["--env", f"{key}={value}"]
            args.append(IMAGE)
            if mode:
                args += ["python", "/probe/probe.py"]
            run(args)
            containers.append(name)
            if role == "api":
                # Internal-only bridges do not expose published ports on this Docker backend.
                # API traffic is loopback-only; collectors/stubs remain on the internal network.
                run(["docker", "network", "connect", network, name])
            record = owned(name, owner)
            host = record["HostConfig"]
            assert host["Memory"] == host["MemorySwap"] == memory * 1024 * 1024
            assert host["PidsLimit"] == pids and host["NanoCpus"] == int(cpus * 10**9)
            return name

        api = launch("api")
        base = api_base(api, owner)

        def ready():
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                try:
                    with urllib.request.urlopen(base + "/ready", timeout=3) as response:
                        if response.status == 200:
                            return
                except OSError:
                    pass
                time.sleep(0.5)
            raise RuntimeError("Synthetic API did not become ready")

        def load(limit=200):
            paths = [
                f"/jobs?limit={limit}",
                f"/jobs?q=Python&limit={limit}",
                f"/matches?min_score=0&limit={limit}",
                "/sources",
            ] * 8

            def request(path):
                start = time.monotonic()
                with urllib.request.urlopen(base + path, timeout=30) as response:
                    body = response.read(10_000_001)
                    assert response.status == 200 and len(body) < 10_000_000
                    json.loads(body)
                    return len(body), time.monotonic() - start

            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                values = list(executor.map(request, paths))
            print(
                json.dumps(
                    {
                        "api_requests": len(values),
                        "limit": limit,
                        "concurrency": 8,
                        "max_response_bytes": max(v[0] for v in values),
                        "max_seconds": round(max(v[1] for v in values), 3),
                    }
                ),
                flush=True,
            )
            assert max(v[1] for v in values) < 10

        ready()
        if not recovery_only:
            worker = launch("worker", "resume-worker" if resume else "worker")
            bot = launch("bot", "bot")
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                backup = (
                    executor.submit(verify_backup, fixture, cgroup_parent=backup_parent)
                    if combined_backup
                    else None
                )
                load(50)
                load()
                wait_exit(bot, owner, timeout=120)
                # Full 10k p95-description rescoring is intentionally CPU-bound at one core.
                wait_exit(worker, owner, timeout=1800)
                load()
                if backup:
                    backup.result()
                else:
                    verify_backup(fixture)
        else:
            print(
                json.dumps({"mode": "recovery_only", "collection_and_scoring": "not_run"}),
                flush=True,
            )
            load()
        identifier = owned(api, owner)["Id"]
        metrics = run(
            [
                "docker",
                "exec",
                api,
                "sh",
                "-c",
                "cat /sys/fs/cgroup/memory.peak /sys/fs/cgroup/cpu.stat",
            ]
        )
        print(json.dumps({"api_cgroup": metrics.stdout.decode().strip()}), flush=True)
        assert not owned(api, owner)["State"]["OOMKilled"]
        before = json.loads(
            fixture.sql(
                "SELECT json_build_object('opportunities',(SELECT count(*) FROM opportunities),"
                "'listings',(SELECT count(*) FROM listings),'descriptions',"
                "(SELECT md5(string_agg(md5(description),'' ORDER BY id)) FROM opportunities));"
            )
        )
        # Use one clock for both event bounds and the daemon's event timestamps.
        since = daemon_timestamp()
        crash = launch("worker", "crash-worker", restart=True)
        record = wait_exit(crash, owner, timeout=120, restart=True)
        until = daemon_timestamp()
        print(json.dumps({"oom_event_window": {"since": since, "until": until}}), flush=True)
        events = run(
            [
                "docker",
                "events",
                "--since",
                since,
                "--until",
                until,
                "--filter",
                f"container={record['Id']}",
                "--filter",
                "event=oom",
                "--format",
                "{{json .}}",
            ]
        )
        assert events.stdout.strip() and record["RestartCount"] == 1
        print(json.dumps({"real_worker_oom_event": True, "automatic_restarts": 1}), flush=True)
        assert before == json.loads(
            fixture.sql(
                "SELECT json_build_object('opportunities',(SELECT count(*) FROM opportunities),"
                "'listings',(SELECT count(*) FROM listings),'descriptions',"
                "(SELECT md5(string_agg(md5(description),'' ORDER BY id)) FROM opportunities));"
            )
        )
        pid = launch("worker", "pid")
        wait_exit(pid, owner, timeout=30)
        # Abrupt database stop is separate from the actual worker OOM experiment.
        fixture.inspect()
        run(["docker", "kill", "--signal", "KILL", fixture.name])
        run(["docker", "start", fixture.name])
        fixture.ready()
        ready()
        assert before == json.loads(
            fixture.sql(
                "SELECT json_build_object('opportunities',(SELECT count(*) FROM opportunities),"
                "'listings',(SELECT count(*) FROM listings),'descriptions',"
                "(SELECT md5(string_agg(md5(description),'' ORDER BY id)) FROM opportunities));"
            )
        )
        # The API is stateless; its manual restart must retain access to persisted data.
        owned(api, owner)
        run(["docker", "kill", "--signal", "KILL", api])
        run(["docker", "start", api])
        previous_base, base = base, api_base(api, owner)
        print(json.dumps({"api_port_reloaded": True, "changed": previous_base != base}), flush=True)
        ready()
        load()
        assert owned(api, owner)["Id"] == identifier
        print(
            json.dumps({"passed": "db_crash_and_api_restart_preserve_data", "snapshot": before}),
            flush=True,
        )
    finally:
        for name in reversed(containers):
            owned(name, owner)
            run(["docker", "rm", "--force", name])
        fixture.close()
        if network_created:
            record = json.loads(run(["docker", "network", "inspect", network]).stdout)[0]
            if record["Labels"].get(LABEL) != owner or record["Containers"]:
                raise RuntimeError("Refusing cleanup of an unrelated or occupied network")
            run(["docker", "network", "rm", network])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--resume", help="Exact owner of an interrupted synthetic fixture")
    modes.add_argument(
        "--recovery-only", action="store_true", help="Run failure/restart checks only"
    )
    options = parser.parse_args()
    main(options.resume, options.recovery_only)
