"""Run role, migration, backup and restore checks on owned synthetic fixtures."""

import json
import sys
import uuid

import psycopg
from check_candidate import LABEL, SEED, Fixture, run

from jobradar.db.access import ROLES, provision_access

PASSWORDS = {role: f"synthetic-isolated-{role}-{'x' * 32}-p@ss%/word:#?$" for role in ROLES}
IMAGE = "jobradar-postgres-clean:amd64-20261016"


def provision(fixture: Fixture) -> None:
    url = fixture.environment()["DATABASE_URL"].replace("postgresql+psycopg", "postgresql")
    with psycopg.connect(url) as connection:
        provision_access(connection, "jobradar_test", PASSWORDS)


def migrate(fixture: Fixture) -> None:
    environment = fixture.environment()
    url = environment["DATABASE_URL"]
    from sqlalchemy.engine import make_url

    environment.update(
        DATABASE_URL=make_url(url)
        .set(username="jobradar_migration", password=PASSWORDS["migration"])
        .render_as_string(hide_password=False),
        SERVICE_ROLE="migration",
    )
    for arguments in (("upgrade", "head"), ("check",)):
        run(
            [sys.executable, "deploy/postgres-prototype/test_bootstrap.py", "alembic", *arguments],
            env=environment,
        )


def main() -> None:
    run_id = uuid.uuid4().hex[:12]
    source = Fixture(IMAGE, "amd64", "access", run_id)
    restored = Fixture(IMAGE, "amd64", "access-restore", run_id)
    try:
        source.start()
        provision(source)
        migrate(source)
        provision(source)
        print("PASS: fresh database migrations with non-superuser owner", flush=True)
        result = run(
            [
                sys.executable,
                "deploy/postgres-prototype/test_bootstrap.py",
                "pytest",
                "-m",
                "integration",
                "-q",
                "--tb=short",
            ],
            env=source.environment(),
            timeout=300,
            allowed_failure=True,
        )
        print(result.stdout.decode(errors="replace"), flush=True)
        if result.returncode:
            raise RuntimeError("Synthetic PostgreSQL role integration checks failed")
        # Rebuild a legacy-admin-owned schema to verify adoption and preservation.
        source.migrate()
        source.sql(SEED)
        before = source.snapshot()
        provision(source)
        provision(source)
        migrate(source)
        assert before == source.snapshot()
        print("PASS: legacy ownership transition and repeatable provisioning", flush=True)
        run(["docker", "restart", source.name])
        source.ready()
        migrate(source)
        assert before == source.snapshot()
        print("PASS: role policy and data survive PostgreSQL restart", flush=True)
        # TCP + the real backup login, not trusted local superuser authentication.
        environment = dict(__import__("os").environ, PGPASSWORD=PASSWORDS["backup"])
        backup = run(
            [
                "docker",
                "exec",
                "--env",
                "PGPASSWORD",
                source.name,
                "pg_dump",
                "--host=127.0.0.1",
                "--username=jobradar_backup",
                "--dbname=jobradar_test",
                "--format=custom",
                "--compress=6",
            ],
            env=environment,
        ).stdout
        assert backup.startswith(b"PGDMP")
        restored.start()
        restored.restore(backup)
        provision(restored)
        migrate(restored)
        assert restored.snapshot() == before
        # Actual read access and denied write access after restoring without ACLs.
        from sqlalchemy.engine import make_url

        url = make_url(restored.environment()["DATABASE_URL"]).set(
            drivername="postgresql", username="jobradar_api", password=PASSWORDS["api"]
        )
        with psycopg.connect(url.render_as_string(hide_password=False), autocommit=True) as c:
            assert c.execute("SELECT count(*) FROM opportunities").fetchone() == (1,)
            c.execute("SET default_transaction_read_only=off")
            try:
                c.execute("UPDATE opportunities SET title='forbidden'")
            except psycopg.errors.InsufficientPrivilege:
                pass
            else:
                raise RuntimeError("Restored API role unexpectedly wrote a vacancy")
        restored.restore(b"synthetic corrupt archive", corrupt=True)
        assert restored.snapshot() == before
        # Old administrative backup remains a valid rollback path.
        assert source.dump().startswith(b"PGDMP")
        print(
            "PASS: read-only backup, clean restore, regrants, corrupt rejection and admin fallback",
            flush=True,
        )
        print(json.dumps({"backup_bytes": len(backup), "fixture_label": LABEL}), flush=True)
    finally:
        restored.close()
        source.close()


if __name__ == "__main__":
    main()
