import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_failed_restore_container_creation_does_not_remove_existing_container(tmp_path):
    shell = "C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else shutil.which("bash")
    if not shell or not Path(shell).is_file():
        pytest.skip("Bash is required for the backup cleanup regression")
    calls = tmp_path / "calls.log"
    fake = tmp_path / "docker"
    fake.write_text('#!/bin/sh\nprintf "%s\\n" "$1" >> "$FIXTURE_CALLS"\nexit 1\n')
    fake.chmod(0o755)
    archive = tmp_path / "fixture.dump"
    archive.write_bytes(b"synthetic archive")
    environment = dict(
        os.environ, FIXTURE_CALLS=str(calls), PATH=str(tmp_path) + os.pathsep + os.environ["PATH"]
    )
    result = subprocess.run(
        [shell, str(ROOT / "scripts/verify_postgres_backup.sh"), str(archive)],
        env=environment,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert calls.read_text().splitlines() == ["run"]
