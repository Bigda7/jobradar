"""Check gosu semantics in disposable local containers, without a database."""

import argparse
import json
import subprocess
import uuid

CHECKS = {
    "named_user": 'test "$(gosu postgres id -u)" = "$(id -u postgres)"',
    "explicit_group": (
        'test "$(gosu postgres:root id -u)" = "$(id -u postgres)"; '
        'test "$(gosu postgres:root id -g)" = "$(id -g root)"'
    ),
    "numeric_user_group": (
        'test "$(gosu 12345:23456 id -u)" = 12345; test "$(gosu 12345:23456 id -g)" = 23456'
    ),
    "home_reset": (
        "expected_home=$(awk -F: '$1==\"postgres\" {print $6}' /etc/passwd); "
        'test "$(gosu postgres sh -c \'printenv HOME\')" = "$expected_home"'
    ),
    "unknown_user_rejected": "if gosu missing-synthetic-user id; then exit 1; fi",
    "missing_command_rejected": "if gosu postgres missing-synthetic-command; then exit 1; fi",
    "oversized_user_id_rejected": (
        "if gosu 99999999999999999999999999999999999:1 id; then exit 1; fi"
    ),
    "exec_preserves_pid": (
        'export EXPECTED_PID=$$; exec gosu postgres sh -c \'test "$$" = "$EXPECTED_PID"\''
    ),
    "setuid_binary_rejected": (
        "cp /usr/local/bin/gosu /tmp/gosu-test; chmod 4755 /tmp/gosu-test; "
        "if /tmp/gosu-test postgres id; then exit 1; fi"
    ),
    "setgid_binary_rejected": (
        "cp /usr/local/bin/gosu /tmp/gosu-test; chmod 2755 /tmp/gosu-test; "
        "if /tmp/gosu-test postgres id; then exit 1; fi"
    ),
}
SETID_CHECKS = {"setuid_binary_rejected", "setgid_binary_rejected"}
LABEL = "jobradar.task=postgres-gosu-runtime-20261016"


def cleanup_timeout(name: str) -> None:
    inspected = subprocess.run(
        ["docker", "inspect", "--format", '{{index .Config.Labels "jobradar.task"}}', name],
        capture_output=True,
        timeout=15,
    )
    if inspected.returncode == 0:
        if inspected.stdout.strip() != b"postgres-gosu-runtime-20261016":
            raise RuntimeError("Refusing cleanup of an unrelated container")
        subprocess.run(
            ["docker", "rm", "--force", name], capture_output=True, timeout=30, check=True
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--architecture", choices=("amd64", "arm64"), required=True)
    parser.add_argument("--candidate-image", required=True)
    parser.add_argument(
        "--emulated-arm64",
        action="store_true",
        help="Record setid guards as unverified under local ARM64 emulation, not passed",
    )
    args = parser.parse_args()
    if args.emulated_arm64 and args.architecture != "arm64":
        parser.error("--emulated-arm64 requires --architecture arm64")
    results = []
    unverified = []
    for check, command in CHECKS.items():
        if args.emulated_arm64 and check in SETID_CHECKS:
            unverified.append(check)
            continue
        name = f"jobradar-gosu-check-{uuid.uuid4().hex[:12]}"
        command_line = [
            "docker",
            "run",
            "--rm",
            "--name",
            name,
            "--pull",
            "never",
            "--platform",
            f"linux/{args.architecture}",
            "--network",
            "none",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,exec,nosuid,size=16m",
            "--memory",
            "128m",
            "--pids-limit",
            "32",
            "--cpus",
            "1",
            "--cap-drop",
            "ALL",
            "--cap-add",
            "SETUID",
            "--cap-add",
            "SETGID",
            "--security-opt",
            "no-new-privileges:true",
            "--label",
            LABEL,
            "--entrypoint",
            "/bin/sh",
            args.candidate_image,
            "-ec",
            command,
        ]
        try:
            result = subprocess.run(command_line, capture_output=True, timeout=30)
        except subprocess.TimeoutExpired:
            cleanup_timeout(name)
            raise RuntimeError(f"{check} timed out; task-owned container cleaned") from None
        if result.returncode:
            raise RuntimeError(f"{check} failed with exit code {result.returncode}")
        results.append(check)
        print(json.dumps({"architecture": args.architecture, "passed": check}), flush=True)
    print(
        json.dumps({"architecture": args.architecture, "checks": results, "unverified": unverified})
    )


if __name__ == "__main__":
    main()
