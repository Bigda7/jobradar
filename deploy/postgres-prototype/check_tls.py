"""Verify candidate TLS, authentication and UUID behavior using synthetic local fixtures."""

import argparse
import importlib.util
import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[2]


def load_checker():
    path = Path(__file__).with_name("check_candidate.py")
    spec = importlib.util.spec_from_file_location("postgres_candidate_checker", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load isolated PostgreSQL fixture checker")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def existing_openssl() -> str:
    if os.name == "nt":
        git = shutil.which("git")
        path = Path(git).parent.parent / "usr/bin/openssl.exe" if git else None
        if path is not None and path.is_file():
            return str(path)
    executable = shutil.which("openssl")
    if executable is None:
        raise RuntimeError("An existing OpenSSL command is required; nothing was installed")
    return executable


def connection_options(fixture, checker, certificate: Path) -> dict:
    port = fixture.inspect()["NetworkSettings"]["Ports"]["5432/tcp"][0]
    if port["HostIp"] != "127.0.0.1":
        raise RuntimeError("Refusing a non-loopback TLS fixture")
    return {
        "host": "127.0.0.1",
        "port": int(port["HostPort"]),
        "dbname": "jobradar_test",
        "user": "jobradar",
        "password": checker.PASSWORD,
        "connect_timeout": 10,
        "sslmode": "verify-full",
        "sslrootcert": str(certificate),
        "autocommit": True,
    }


def rejected_connection(options: dict, marker: str) -> None:
    try:
        with psycopg.connect(**options):
            pass
    except psycopg.OperationalError as error:
        if marker not in str(error).lower():
            raise RuntimeError(
                "Connection failed for an unexpected reason; output suppressed"
            ) from None
    else:
        raise RuntimeError("Invalid synthetic connection unexpectedly accepted")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--architecture", choices=("amd64", "arm64"), required=True)
    parser.add_argument("--candidate-image", required=True)
    args = parser.parse_args()
    checker = load_checker()
    fixture = checker.Fixture(args.candidate_image, args.architecture, "tls", uuid.uuid4().hex[:12])
    passed = []

    def success(check: str) -> None:
        passed.append(check)
        print(json.dumps({"architecture": args.architecture, "passed": check}), flush=True)

    try:
        fixture.start()
        fixture.sql('CREATE EXTENSION "uuid-ossp";')
        values = (
            fixture.sql(
                "SELECT uuid_generate_v1(), uuid_generate_v4(), "
                "uuid_generate_v5(uuid_ns_dns(), 'jobradar.invalid');"
            )
            .decode()
            .strip()
            .split("|")
        )
        if (
            uuid.UUID(values[0]).version != 1
            or uuid.UUID(values[1]).version != 4
            or values[2] != str(uuid.uuid5(uuid.NAMESPACE_DNS, "jobradar.invalid"))
        ):
            raise RuntimeError("UUID extension regression")
        success("uuid_extension_v1_v4_and_deterministic_v5")

        (ROOT / ".deploy").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="postgres-tls-fixture-", dir=ROOT / ".deploy"
        ) as temp:
            folder = Path(temp)
            certificate, key = folder / "server.crt", folder / "server.key"
            config = folder / "openssl.cnf"
            config.write_text("[req]\ndistinguished_name=dn\n[dn]\n", encoding="utf-8")
            checker.run(
                [
                    existing_openssl(),
                    "req",
                    "-x509",
                    "-newkey",
                    "rsa:2048",
                    "-nodes",
                    "-days",
                    "1",
                    "-config",
                    str(config),
                    "-subj",
                    "/CN=localhost",
                    "-addext",
                    "subjectAltName=DNS:localhost,IP:127.0.0.1",
                    "-keyout",
                    str(key),
                    "-out",
                    str(certificate),
                ]
            )
            fixture.inspect()
            for path in (certificate, key):
                destination = f"/var/lib/postgresql/data/prototype-{path.name}"
                checker.run(["docker", "cp", str(path), f"{fixture.name}:{destination}"])
                checker.run(
                    ["docker", "exec", fixture.name, "chown", "postgres:postgres", destination]
                )
                checker.run(["docker", "exec", fixture.name, "chmod", "600", destination])
            for setting, value in (
                ("ssl_cert_file", "/var/lib/postgresql/data/prototype-server.crt"),
                ("ssl_key_file", "/var/lib/postgresql/data/prototype-server.key"),
                ("ssl", "on"),
            ):
                fixture.sql(f"ALTER SYSTEM SET {setting} = '{value}';")
            checker.run(["docker", "restart", "--time", "30", fixture.name])
            fixture.ready()
            options = connection_options(fixture, checker, certificate)
            for version in ("TLSv1.2", "TLSv1.3"):
                with psycopg.connect(
                    **options, ssl_min_protocol_version=version, ssl_max_protocol_version=version
                ) as connection:
                    record = connection.execute(
                        "SELECT ssl, version, cipher FROM pg_stat_ssl WHERE pid=pg_backend_pid()"
                    ).fetchone()
                    if (
                        record is None
                        or record[0] is not True
                        or record[1] != version
                        or not record[2]
                    ):
                        raise RuntimeError("Expected verified TLS protocol was not negotiated")
                success(f"verified_certificate_and_authenticated_{version}")

            rejected_connection(
                {**options, "host": "wrong.example.invalid", "hostaddr": "127.0.0.1"},
                "does not match host name",
            )
            success("wrong_certificate_hostname_rejected")
            rejected_connection(
                {**options, "password": "wrong-synthetic-password"},
                "password authentication failed",
            )
            success("wrong_database_password_rejected_over_tls")
            with psycopg.connect(**{**options, "sslmode": "disable"}) as connection:
                record = connection.execute(
                    "SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()"
                ).fetchone()
                if record != (False,):
                    raise RuntimeError("Existing non-TLS connection behavior changed")
            success("existing_authenticated_non_tls_connection")
    finally:
        fixture.close()
    print(
        json.dumps({"architecture": args.architecture, "checks": passed, "fixtures_removed": True})
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"verification_failed": type(error).__name__, "reason": str(error)}))
        raise SystemExit(1) from None
