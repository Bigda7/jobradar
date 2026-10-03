"""Verify narrowly scoped cleanup and safe default maintenance behavior."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_restore_cleanup_removes_only_its_temporary_anonymous_volume() -> None:
    script = (ROOT / "scripts/verify_postgres_backup.sh").read_text()
    assert 'docker rm --force --volumes "${container_name}"' in script
    assert "--label jobradar.restore-check=true" in script
    assert "--network none" in script
    assert 'container_name="jobradar-restore-check-' in script
    assert "volume prune" not in script


def test_cache_cleanup_defaults_to_dry_run_and_never_prunes_other_data() -> None:
    script = (ROOT / "scripts/cleanup_build_cache.sh").read_text()
    assert 'if [[ "$#" -eq 0 ]]' in script
    assert script.index("Dry run: no data was deleted.") < script.index("builder prune --all")
    assert "builder prune --all --filter until=168h --keep-storage 2GB --force" in script
    assert "docker --host unix:///var/run/docker.sock" in script
    assert "systemctl is-active --quiet jobradar-backup.service" in script
    assert "used_percent < 75 && available_bytes >= 6442450944" in script
    assert "volume prune" not in script
    assert "image prune" not in script
    assert "system prune" not in script
    assert "rm -" not in script


def test_ci_checks_backup_cleanup_on_success_and_failure() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    steps = {step["name"]: step for step in workflow["jobs"]["verify"]["steps"]}
    assert "bash -n scripts/backup_postgres.sh" in steps["Run static checks"]["run"]
    restoration = steps["Verify PostgreSQL backup restoration"]["run"]
    assert 'initial_volumes="$(docker volume ls --quiet | sort)"' in restoration
    assert "Successful restore check left an unexpected Docker volume." in restoration
    assert "Failed restore check left an unexpected Docker volume." in restoration
    assert restoration.count('!= "$initial_volumes"') == 2
