"""Verify PostgreSQL candidates using labeled isolated synthetic databases only."""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = "postgres:17-alpine@sha256:18cfe3ef5e6815560c98237d6216d1e5119702fb0f3894c8785dd58b8bbe5d73"
LABEL = "jobradar.postgres.prototype.run"
PASSWORD = "synthetic-local-fixture-password"
SEED = """
BEGIN;
INSERT INTO sources (name,display_name,opportunity_kind,enabled,failure_alert_active,
coverage_alert_active,request_budget,created_at,updated_at)
VALUES ('prototype-fixture','Prototype Fixture','employment',false,false,false,'{}',now(),now());
INSERT INTO opportunities (kind,status,canonical_key,title,work_mode,first_seen_at,
last_seen_at,created_at,updated_at)
VALUES ('employment','active','prototype-fixture','Synthetic Python Developer',
'remote',now(),now(),now(),now());
INSERT INTO listings (source_id,opportunity_id,external_id,source_url,canonical_url,
content_hash,raw_data,quality_score,first_seen_at,last_seen_at,is_active,created_at,updated_at)
SELECT s.id,o.id,'prototype-1','https://example.invalid/prototype',
'https://example.invalid/prototype','synthetic-hash',
json_build_object('description',repeat('Synthetic fixture description. ',40000)),
0,now(),now(),true,now(),now()
FROM sources s CROSS JOIN opportunities o
WHERE s.name='prototype-fixture' AND o.canonical_key='prototype-fixture';
INSERT INTO notification_deliveries (opportunity_id,profile_id,channel,event_key,status,
attempts,created_at,sent_at)
SELECT id,'synthetic-profile','telegram','synthetic-sent-event','sent',1,now(),now()
FROM opportunities WHERE canonical_key='prototype-fixture';
COMMIT;
"""
SNAPSHOT = """
SELECT json_build_object(
 'schema',(SELECT version_num FROM alembic_version),
 'sources',(SELECT count(*) FROM sources),
 'opportunities',(SELECT count(*) FROM opportunities),
 'listings',(SELECT count(*) FROM listings),
 'description_md5',(SELECT md5(raw_data::text) FROM listings WHERE external_id='prototype-1'),
 'description_bytes',(SELECT octet_length(raw_data->>'description') FROM listings
                      WHERE external_id='prototype-1'),
 'deliveries',(SELECT json_agg(json_build_object('id',id,'opportunity_id',opportunity_id,
 'status',status,'attempts',attempts,'event_key',event_key,'created_at',created_at,
 'sent_at',sent_at) ORDER BY id) FROM notification_deliveries),
 'invalid_foreign_keys',(SELECT count(*) FROM pg_constraint WHERE contype='f' AND NOT convalidated),
 'invalid_indexes',(SELECT count(*) FROM pg_index WHERE NOT indisvalid),
 'encoding',current_setting('server_encoding'),
 'collate',(SELECT datcollate FROM pg_database WHERE datname=current_database()),
 'ctype',(SELECT datctype FROM pg_database WHERE datname=current_database()),
 'trgm',similarity('prototype','prototype'),
 'xml',xpath('/fixture/value/text()','<fixture><value>trusted</value></fixture>'::xml)::text);
"""


def run(
    args: list[str],
    *,
    data: bytes | None = None,
    env: dict | None = None,
    timeout: int = 120,
    allowed_failure: bool = False,
) -> subprocess.CompletedProcess:
    result = subprocess.run(
        args, input=data, capture_output=True, cwd=ROOT, env=env, timeout=timeout
    )
    if result.returncode and not allowed_failure:
        output = (result.stdout + result.stderr).decode(errors="replace")
        markers = [
            value
            for value in (
                "ValidationError",
                "OperationalError",
                "Connection refused",
                "InvalidPassword",
                "New upgrade operations detected",
                "CommandError",
                "Permission denied",
                "UndefinedTable",
                "UndefinedColumn",
                "No module named",
                "can't open file",
                "No such file",
                "TEST_DATABASE_URL is required",
                "Refusing to reset a database other than jobradar_test",
                "ProactorEventLoop",
            )
            if value in output
        ]
        markers.extend(sorted(set(re.findall(r"\b[A-Z][A-Za-z_]*(?:Error|Exception)\b", output))))
        markers.append(f"output_bytes={len(result.stdout)}/{len(result.stderr)}")
        diagnostics = re.findall(
            r"(?m)^(?:pg_restore: error:|psql: error:|Error response from daemon:|docker: Error)"
            r"[^\r\n]{0,220}",
            output,
        )
        diagnostics = [
            re.sub(r"https?://\S+|[A-Za-z0-9_+/=-]{40,}", "[REDACTED]", line)
            for line in diagnostics
        ]
        stage = " ".join(args[:3]) if args[0] == "docker" else args[0]
        raise RuntimeError(
            f"{stage} failed with exit code {result.returncode}; "
            f"diagnostic markers: {markers}; tool errors: {diagnostics}"
        )
    return result


class Fixture:
    def __init__(
        self,
        image: str,
        architecture: str,
        role: str,
        run_id: str,
        *,
        cgroup_parent: str | None = None,
    ):
        self.name = f"jobradar-pg-prototype-{run_id}-{architecture}-{role}"
        self.volume = self.name + "-data"
        self.run_id = run_id
        self.image = image
        self.architecture = architecture
        self.created = False
        self.volume_created = False
        self.cgroup_parent = cgroup_parent

    def start(self) -> None:
        run(["docker", "volume", "create", "--label", f"{LABEL}={self.run_id}", self.volume])
        self.volume_created = True
        run(
            [
                "docker",
                "run",
                "-d",
                "--pull",
                "never",
                "--name",
                self.name,
                "--platform",
                f"linux/{self.architecture}",
                "--label",
                f"{LABEL}={self.run_id}",
                *(["--cgroup-parent", self.cgroup_parent] if self.cgroup_parent else []),
                "--security-opt",
                "no-new-privileges:true",
                "--memory",
                "512m",
                "--cpus",
                "2",
                "--pids-limit",
                "128",
                "--publish",
                "127.0.0.1::5432",
                "--mount",
                f"type=volume,source={self.volume},target=/var/lib/postgresql/data",
                "--env",
                "POSTGRES_DB=jobradar_test",
                "--env",
                "POSTGRES_USER=jobradar",
                "--env",
                f"POSTGRES_PASSWORD={PASSWORD}",
                self.image,
            ],
            timeout=180,
        )
        self.created = True
        self.ready()

    def inspect(self) -> dict:
        record = json.loads(run(["docker", "inspect", self.name]).stdout)[0]
        if record["Config"]["Labels"].get(LABEL) != self.run_id:
            raise RuntimeError("Fixture ownership mismatch")
        volumes = [item["Name"] for item in record["Mounts"] if item["Type"] == "volume"]
        if volumes != [self.volume]:
            raise RuntimeError("Unexpected fixture volume")
        return record

    def ready(self) -> None:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            result = run(
                [
                    "docker",
                    "exec",
                    self.name,
                    "pg_isready",
                    # The entrypoint's temporary initialization server is Unix-only.
                    # TCP readiness excludes it before migrations or pg_restore start.
                    "-h",
                    "127.0.0.1",
                    "-U",
                    "jobradar",
                    "-d",
                    "jobradar_test",
                ],
                timeout=10,
                allowed_failure=True,
            )
            if result.returncode == 0:
                # Final PID1 startup must finish before migrations use the published port.
                port = self.inspect()["NetworkSettings"]["Ports"]["5432/tcp"][0]
                if port["HostIp"] != "127.0.0.1":
                    raise RuntimeError("Fixture unexpectedly exposed beyond loopback")
                import socket

                try:
                    with socket.create_connection(("127.0.0.1", int(port["HostPort"])), timeout=1):
                        return
                except OSError:
                    pass
            time.sleep(0.5)
        raise RuntimeError("Isolated database did not become ready")

    def environment(self) -> dict[str, str]:
        record = self.inspect()
        port = record["NetworkSettings"]["Ports"]["5432/tcp"][0]
        if port["HostIp"] != "127.0.0.1":
            raise RuntimeError("Refusing non-loopback fixture database")
        url = f"postgresql+psycopg://jobradar:{PASSWORD}@127.0.0.1:{int(port['HostPort'])}/jobradar_test"
        environment = dict(os.environ)
        environment.update(
            APP_ENV="test",
            DATABASE_URL=url,
            TEST_DATABASE_URL=url,
            TELEGRAM_ENABLED="false",
            TELEGRAM_BOT_TOKEN="synthetic-test-token",
            # ASGI integration tests use the same unauthenticated test mode as CI.
            # No HTTP server is started or exposed by this checker.
            API_BEARER_TOKEN="",
            API_ALLOWED_HOSTS="localhost;127.0.0.1;test",
        )
        return environment

    def sql(self, query: str) -> bytes:
        self.inspect()
        return run(
            [
                "docker",
                "exec",
                "-i",
                self.name,
                "psql",
                "--no-psqlrc",
                "-tA",
                "-v",
                "ON_ERROR_STOP=1",
                "-U",
                "jobradar",
                "-d",
                "jobradar_test",
            ],
            data=query.encode(),
        ).stdout

    def migrate(self) -> None:
        run(
            [sys.executable, "deploy/postgres-prototype/test_bootstrap.py", "reset"],
            env=self.environment(),
        )
        run(
            [
                sys.executable,
                "deploy/postgres-prototype/test_bootstrap.py",
                "alembic",
                "upgrade",
                "head",
            ],
            env=self.environment(),
        )
        run(
            [sys.executable, "deploy/postgres-prototype/test_bootstrap.py", "alembic", "check"],
            env=self.environment(),
        )

    def snapshot(self) -> dict:
        return json.loads(self.sql(SNAPSHOT))

    def dump(self) -> bytes:
        self.inspect()
        result = run(
            [
                "docker",
                "exec",
                self.name,
                "pg_dump",
                "-U",
                "jobradar",
                "-d",
                "jobradar_test",
                "--format=custom",
                "--compress=6",
            ]
        )
        if not result.stdout.startswith(b"PGDMP"):
            raise RuntimeError("Synthetic dump was not a custom PostgreSQL archive")
        return result.stdout

    def restore(self, data: bytes, *, corrupt: bool = False) -> None:
        self.inspect()
        result = run(
            [
                "docker",
                "exec",
                "-i",
                self.name,
                "pg_restore",
                "-U",
                "jobradar",
                "-d",
                "jobradar_test",
                "--exit-on-error",
                "--single-transaction",
                "--clean",
                "--if-exists",
                "--no-owner",
                "--no-privileges",
            ],
            data=data,
            allowed_failure=corrupt,
        )
        if corrupt and result.returncode == 0:
            raise RuntimeError("Corrupt synthetic backup accepted")

    def close(self) -> None:
        if self.created:
            self.inspect()
            run(["docker", "rm", "--force", self.name])
            self.created = False
        if self.volume_created:
            record = json.loads(run(["docker", "volume", "inspect", self.volume]).stdout)[0]
            if record["Labels"].get(LABEL) != self.run_id:
                raise RuntimeError("Refusing cleanup of a non-fixture volume")
            run(["docker", "volume", "rm", self.volume])
            self.volume_created = False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--architecture", choices=("amd64", "arm64"), required=True)
    parser.add_argument("--candidate-image", help="Existing local image; never pulled implicitly")
    args = parser.parse_args()
    run_id = uuid.uuid4().hex[:12]
    candidate = Fixture(
        args.candidate_image or f"jobradar-postgres-zlib:{args.architecture}-20261013",
        args.architecture,
        "candidate",
        run_id,
    )
    baseline = Fixture(BASE, args.architecture, "baseline", run_id)
    report = {"architecture": args.architecture, "candidate": candidate.image, "checks": []}

    def passed(check: str) -> None:
        report["checks"].append(check)
        print(json.dumps({"architecture": args.architecture, "passed": check}), flush=True)

    try:
        candidate.start()
        candidate.migrate()
        passed("fresh_migrations_and_alembic_check")
        tests = run(
            [
                sys.executable,
                "deploy/postgres-prototype/test_bootstrap.py",
                "pytest",
                "-m",
                "integration",
                "-q",
            ],
            env=candidate.environment(),
            timeout=600,
            allowed_failure=True,
        )
        summary = re.findall(
            r"\d+ (?:passed|failed|deselected|skipped|error)s?",
            tests.stdout.decode(errors="replace"),
        )
        report["integration_summary"] = summary
        if tests.returncode:
            output = (tests.stdout + tests.stderr).decode(errors="replace")
            print(
                json.dumps(
                    {
                        "integration_summary": summary,
                        "failed_tests": re.findall(r"(?m)^FAILED (tests/\S+)", output),
                        "failure_classes": sorted(
                            set(re.findall(r"\b[A-Z][A-Za-z_]*(?:Error|Exception)\b", output))
                        ),
                        "proactor_marker": "ProactorEventLoop" in output,
                    }
                )
            )
            raise RuntimeError("PostgreSQL integration tests failed; raw output suppressed")
        passed("all_postgres_integration_tests")
        candidate.migrate()
        candidate.sql(SEED)
        expected = candidate.snapshot()
        if (
            expected["description_bytes"] < 1_000_000
            or expected["deliveries"][0]["status"] != "sent"
        ):
            raise RuntimeError("Synthetic fixture not populated correctly")
        candidate_dump = candidate.dump()
        candidate.inspect()
        run(["docker", "restart", "--time", "30", candidate.name])
        candidate.ready()
        if candidate.snapshot() != expected:
            raise RuntimeError("Existing-volume restart altered fixture data")
        passed("existing_volume_restart_preserves_data")
        candidate.restore(candidate_dump)
        if candidate.snapshot() != expected:
            raise RuntimeError("Candidate-to-candidate restore altered fixture data")
        passed("candidate_to_candidate_compressed_restore")
        candidate.restore(b"synthetic invalid archive", corrupt=True)
        if candidate.snapshot() != expected:
            raise RuntimeError("Failed restore altered fixture data")
        passed("corrupt_backup_rejected_without_data_change")
        baseline.start()
        baseline.restore(candidate_dump)
        if baseline.snapshot() != expected:
            raise RuntimeError("Candidate-to-baseline restore altered fixture data")
        passed("candidate_to_original_compressed_restore")
        baseline.migrate()
        baseline.sql(SEED)
        original = baseline.snapshot()
        original_dump = baseline.dump()
        candidate.restore(original_dump)
        if candidate.snapshot() != original:
            raise RuntimeError("Original-to-candidate restore altered fixture data")
        passed("original_to_candidate_compressed_restore")
        candidate.sql("REINDEX INDEX ix_opportunities_title_trgm;")
        if candidate.snapshot() != original:
            raise RuntimeError("Index rebuild altered fixture data")
        passed("trigram_reindex_and_integrity")
        report["dump_sizes"] = {"candidate": len(candidate_dump), "original": len(original_dump)}
        report["fixture_notifications_preserved"] = True
    finally:
        candidate.close()
        baseline.close()
    report["fixtures_removed"] = True
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"verification_failed": type(error).__name__, "reason": str(error)}))
        raise SystemExit(1) from None
