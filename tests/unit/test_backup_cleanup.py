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
    fake.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$1" >> "$FIXTURE_CALLS"\n'
        'case "$1" in\n'
        'info) printf "x86_64\\n"; exit 0;;\n'
        'image) printf "%s linux/amd64 [\\"%s\\"]\\n" '
        '"sha256:ad68598f8c2901e3fc40dbbe77d85b85ed21a87eecec830ebf4b6782ff5c34b6" '
        '"sha256:6aed3ffae62ca1d8ddeb4f2f98792fc31cd951cd889173eed7856db032096913"; '
        "exit 0;;\nesac\nexit 1\n"
    )
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
    assert calls.read_text().splitlines() == ["info", "image", "run"]
