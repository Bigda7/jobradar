import importlib.util
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

GUARD = (
    Path(__file__).resolve().parents[2] / "deploy/postgres-prototype/gosu/verify_apk_packages.sh"
)
ORIGINAL = [
    "libcrypto3-3.5.7-r0",
    "libssl3-3.5.7-r0",
    "libuuid-2.42.1-r0",
    "libxml2-2.13.9-r2",
    "zlib-1.3.2-r1",
]
APPROVED = [
    "libcrypto3-3.5.9-r0",
    "libssl3-3.5.9-r0",
    "libuuid-2.42.3-r1",
    *ORIGINAL[3:],
]


@pytest.fixture
def shell():
    if os.name == "nt":
        git = shutil.which("git")
        path = Path(git).parent.parent / "bin/bash.exe" if git else None
        if path is None or not path.is_file():
            pytest.skip("Existing Git Bash is required for the POSIX package guard test")
        return str(path)
    return "/bin/sh"


@pytest.mark.parametrize(
    "change",
    [
        "approved",
        "extra",
        "removed",
        "wrong_version",
        "unchanged",
        "duplicate",
        "baseline",
        "empty",
    ],
)
def test_exact_apk_transaction_guard(shell, tmp_path, change):
    before = list(ORIGINAL)
    after = list(APPROVED)
    if change == "extra":
        after.append("unexpected-1.0-r0")
    elif change == "removed":
        after.pop()
    elif change == "wrong_version":
        after[0] = "libcrypto3-3.5.10-r0"
    elif change == "unchanged":
        after[0] = before[0]
    elif change == "duplicate":
        after.append(after[0])
    elif change == "baseline":
        before[0] = "libcrypto3-3.5.6-r0"
    elif change == "empty":
        after = []
    original_path = tmp_path / "original.txt"
    updated_path = tmp_path / "updated.txt"
    original_path.write_text("\n".join(before) + "\n", encoding="utf-8", newline="\n")
    updated_path.write_text(
        "\n".join(after) + "\n" if after else "", encoding="utf-8", newline="\n"
    )
    result = subprocess.run(
        [shell, GUARD.as_posix(), original_path.as_posix(), updated_path.as_posix()],
        capture_output=True,
        timeout=10,
    )
    assert (result.returncode == 0) is (change == "approved")


@pytest.fixture
def tls_checker():
    path = GUARD.parent.parent / "check_tls.py"
    spec = importlib.util.spec_from_file_location("postgres_tls_checker", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("host", ["127.0.0.1", "0.0.0.0"])
def test_tls_connection_options_require_loopback(tls_checker, host, tmp_path):
    fixture = SimpleNamespace(
        inspect=lambda: {
            "NetworkSettings": {"Ports": {"5432/tcp": [{"HostIp": host, "HostPort": "23456"}]}}
        }
    )
    checker = SimpleNamespace(PASSWORD="synthetic-fixture-password")
    if host != "127.0.0.1":
        with pytest.raises(RuntimeError, match="non-loopback"):
            tls_checker.connection_options(fixture, checker, tmp_path / "synthetic.crt")
    else:
        options = tls_checker.connection_options(fixture, checker, tmp_path / "synthetic.crt")
        assert options["host"] == host
        assert options["dbname"] == "jobradar_test"
        assert options["sslmode"] == "verify-full"


@pytest.mark.parametrize("failure", ["expected", "unrelated", "accepted"])
def test_tls_negative_probe_requires_the_expected_failure(tls_checker, monkeypatch, failure):
    from contextlib import nullcontext

    def connect(**kwargs):
        if failure == "accepted":
            return nullcontext()
        message = (
            "password authentication failed" if failure == "expected" else "connection refused"
        )
        raise tls_checker.psycopg.OperationalError(message)

    monkeypatch.setattr(tls_checker.psycopg, "connect", connect)
    if failure == "expected":
        tls_checker.rejected_connection({}, "password authentication failed")
    else:
        with pytest.raises(RuntimeError, match="unexpected"):
            tls_checker.rejected_connection({}, "password authentication failed")
