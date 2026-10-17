"""Collect allowlisted read-only capacity/maintenance/cloud facts over SSH stdin.

Run with python3 on the existing /opt/jobradar host. Never prints environment,
credential documents, complete container inspect data, or raw application logs.
Does not install packages, refresh apt, restart services, or trigger backups.
"""

import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path("/opt/jobradar")


def command(args, timeout=25):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, cwd=ROOT)
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"verified": False, "reason": type(error).__name__}
    if result.returncode:
        match = re.search(r"\(([A-Za-z0-9_.]+)\) when calling", result.stderr)
        return {
            "verified": False,
            "reason": match.group(1) if match else "command_failed",
            "exit_code": result.returncode,
        }
    return result.stdout.strip()


def aws(service, operation, *arguments):
    value = command(
        [
            "aws",
            service,
            operation,
            *arguments,
            "--output",
            "json",
            "--no-cli-pager",
            "--cli-connect-timeout",
            "5",
            "--cli-read-timeout",
            "10",
        ]
    )
    if isinstance(value, dict):
        return value
    return json.loads(value or "{}")


def metadata(path, token=None):
    request = urllib.request.Request("http://169.254.169.254/latest/" + path)
    if token is not None:
        request.add_header("X-aws-ec2-metadata-token", token)
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            return response.read(65536).decode()
    except urllib.error.HTTPError as error:
        return {"status": error.code}
    except OSError as error:
        return {"verified": False, "reason": type(error).__name__}


def cloud():
    result = {"imds_v1_identity": metadata("dynamic/instance-identity/document")}
    # Never return an identity document in the unauthenticated probe output.
    if isinstance(result["imds_v1_identity"], str):
        result["imds_v1_identity"] = {"status": 200}
    token_request = urllib.request.Request(
        "http://169.254.169.254/latest/api/token",
        method="PUT",
        headers={"X-aws-ec2-metadata-token-ttl-seconds": "120"},
    )
    try:
        with urllib.request.urlopen(token_request, timeout=3) as response:
            token = response.read(4096).decode()
    except OSError as error:
        result["imds_v2"] = {"verified": False, "reason": type(error).__name__}
        return result
    identity = metadata("dynamic/instance-identity/document", token)
    if not isinstance(identity, str):
        result["imds_v2"] = identity
        return result
    identity = json.loads(identity)
    result["imds_v2"] = {
        "verified": True,
        "region": identity["region"],
        "instance_id": identity["instanceId"],
        "instance_type": identity["instanceType"],
    }
    instance = aws(
        "ec2",
        "describe-instances",
        "--region",
        identity["region"],
        "--instance-ids",
        identity["instanceId"],
    )
    if "Reservations" in instance:
        item = instance["Reservations"][0]["Instances"][0]
        result["ec2"] = {
            name: item.get(name)
            for name in (
                "InstanceId",
                "InstanceType",
                "MetadataOptions",
                "SecurityGroups",
                "IamInstanceProfile",
            )
        }
    else:
        result["ec2"] = instance
    macs = metadata("meta-data/network/interfaces/macs/", token)
    group_ids = set()
    if isinstance(macs, str):
        for mac in macs.splitlines()[:4]:
            groups = metadata(
                "meta-data/network/interfaces/macs/" + mac + "security-group-ids", token
            )
            if isinstance(groups, str):
                group_ids.update(
                    group for group in groups.splitlines() if re.fullmatch(r"sg-[0-9a-f]+", group)
                )
    result["security_groups"] = []
    for group in sorted(group_ids):
        data = aws(
            "ec2", "describe-security-groups", "--region", identity["region"], "--group-ids", group
        )
        if "SecurityGroups" in data:
            data = [
                {
                    name: entry.get(name)
                    for name in ("GroupId", "IpPermissions", "IpPermissionsEgress")
                }
                for entry in data["SecurityGroups"]
            ]
        result["security_groups"].append({"id": group, "result": data})
    actor = aws("sts", "get-caller-identity")
    arn = actor.get("Arn", "")
    match = re.search(r":assumed-role/([^/]+)/", arn)
    if match:
        role = match.group(1)
        result["iam_role"] = {"name": role}
        for operation in ("get-role", "list-attached-role-policies", "list-role-policies"):
            data = aws("iam", operation, "--role-name", role)
            if operation == "get-role" and "Role" in data:
                data = {
                    name: data["Role"].get(name)
                    for name in ("RoleName", "AssumeRolePolicyDocument", "PermissionsBoundary")
                }
            result["iam_role"][operation] = data
    else:
        result["iam_role"] = {"verified": False, "reason": actor.get("reason", "unknown_identity")}
    # Read only the two nonsecret backup target fields, never copy the deployment store.
    backup = Path("/etc/jobradar/backup.env")
    target = {}
    if backup.is_file():
        for line in backup.read_text().splitlines():
            name, separator, value = line.partition("=")
            if separator and name in {"JOBRADAR_BACKUP_S3_BUCKET", "JOBRADAR_BACKUP_S3_PREFIX"}:
                target[name] = value.strip().strip("\"'")
    bucket = target.get("JOBRADAR_BACKUP_S3_BUCKET")
    if bucket and re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket):
        result["backup_bucket"] = {"name": bucket}
        for operation in (
            "get-public-access-block",
            "get-bucket-policy-status",
            "get-bucket-encryption",
            "get-bucket-versioning",
            "get-bucket-lifecycle-configuration",
        ):
            result["backup_bucket"][operation] = aws("s3api", operation, "--bucket", bucket)
        files = sorted((ROOT / "backups").glob("jobradar-*.dump"), key=lambda p: p.stat().st_mtime)
        if files:
            prefix = target.get("JOBRADAR_BACKUP_S3_PREFIX", "postgres").strip("/")
            key = f"{prefix}/{files[-1].name}" if prefix else files[-1].name
            data = aws("s3api", "head-object", "--bucket", bucket, "--key", key)
            if "ContentLength" in data:
                data = {
                    name: data.get(name)
                    for name in (
                        "ContentLength",
                        "LastModified",
                        "ServerSideEncryption",
                        "StorageClass",
                    )
                }
            result["backup_bucket"]["latest_known_object"] = data
    else:
        result["backup_bucket"] = {"verified": False, "reason": "target_not_found"}
    return result


def main():
    if not ROOT.is_dir():
        raise RuntimeError("Expected existing JobRadar host layout")
    memory = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        name, value = line.split(":", 1)
        if name in {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}:
            memory[name] = int(value.split()[0]) * 1024
    result = {
        "checked_at": datetime.now(UTC).isoformat(),
        "cpus": os.cpu_count(),
        "memory": memory,
        "kernel": command(["uname", "-r"]),
        "architecture": command(["uname", "-m"]),
        "uptime_seconds": float(Path("/proc/uptime").read_text().split()[0]),
        "load_average": os.getloadavg(),
    }
    containers = {}
    for service in ("api", "worker", "bot", "db"):
        identifier = command(["docker", "compose", "ps", "-q", service])
        if not isinstance(identifier, str) or not identifier:
            containers[service] = {"verified": False, "reason": "not_running"}
            continue
        record = json.loads(command(["docker", "inspect", identifier]))[0]
        state = record["State"]
        host = record["HostConfig"]
        containers[service] = {
            "id": record["Id"],
            "image_id": record["Image"],
            "started_at": state.get("StartedAt"),
            "restart_count": record["RestartCount"],
            "oom_killed": state.get("OOMKilled"),
            "health": state.get("Health", {}).get("Status"),
            "limits": {
                name: host.get(name)
                for name in ("Memory", "MemorySwap", "MemoryReservation", "NanoCpus", "PidsLimit")
            },
            "cgroup": command(
                [
                    "docker",
                    "exec",
                    identifier,
                    "sh",
                    "-c",
                    "for f in memory.current memory.peak memory.max memory.events "
                    "pids.current pids.max cpu.max; do printf '%s=' \"$f\"; "
                    "cat /sys/fs/cgroup/$f 2>/dev/null || true; done",
                ]
            ),
        }
    result["containers"] = containers
    result["docker_stats"] = command(
        [
            "docker",
            "stats",
            "--no-stream",
            "--format",
            "{{json .}}",
            *[v["id"] for v in containers.values() if "id" in v],
        ]
    )
    disk = os.statvfs(ROOT)
    result["disk"] = {
        "total": disk.f_blocks * disk.f_frsize,
        "available": disk.f_bavail * disk.f_frsize,
        "free": disk.f_bfree * disk.f_frsize,
    }
    result["disk_bytes_by_path"] = {}
    for path in (
        "/opt/jobradar/backups",
        "/opt/jobradar/.deploy",
        "/var/log",
        "/var/lib/docker",
        "/var/lib/containerd",
    ):
        data = command(["sudo", "-n", "du", "-sx", "--bytes", path], timeout=20)
        result["disk_bytes_by_path"][path] = int(data.split()[0]) if isinstance(data, str) else data
    files = sorted((ROOT / "backups").glob("jobradar-*.dump"), key=lambda p: p.stat().st_mtime)
    result["backups"] = {
        "count": len(files),
        "total_bytes": sum(p.stat().st_size for p in files),
        "latest": {
            "bytes": files[-1].stat().st_size,
            "mtime": datetime.fromtimestamp(files[-1].stat().st_mtime, UTC).isoformat(),
        }
        if files
        else None,
    }
    result["units"] = {}
    for unit in (
        "caddy.service",
        "docker.service",
        "jobradar-backup.service",
        "jobradar-backup.timer",
        "jobradar-cache-cleanup.service",
        "jobradar-cache-cleanup.timer",
    ):
        result["units"][unit] = command(
            [
                "systemctl",
                "show",
                unit,
                "--property=LoadState,ActiveState,SubState,Result,ExecMainStatus,"
                "ExecMainStartTimestamp,ExecMainExitTimestamp,LastTriggerUSec,NextElapseUSecRealtime",
            ]
        )
    result["failed_units"] = command(
        ["systemctl", "--failed", "--no-legend", "--plain", "--no-pager"]
    )
    result["reboot_required"] = Path("/var/run/reboot-required").exists()
    packages = Path("/var/run/reboot-required.pkgs")
    result["reboot_packages"] = packages.read_text().splitlines() if packages.exists() else []
    result["installed_kernels"] = command(
        [
            "dpkg-query",
            "-W",
            "-f=${Package}\t${Version}\t${Status}\n",
            "linux-image-*",
            "linux-aws*",
        ]
    )
    result["upgradable_packages_cached"] = command(["apt", "list", "--upgradable"])
    result["upgrade_simulation_cached"] = command(["apt-get", "--simulate", "upgrade"])
    lists = list(Path("/var/lib/apt/lists").glob("*InRelease"))
    result["apt_metadata_newest"] = (
        datetime.fromtimestamp(max(p.stat().st_mtime for p in lists), UTC).isoformat()
        if lists
        else None
    )
    result["listening_tcp_ports"] = []
    ports = command(["ss", "-lntH"])
    if isinstance(ports, str):
        for line in ports.splitlines():
            parts = line.split()
            if len(parts) >= 4 and parts[3].rsplit(":", 1)[-1].isdigit():
                address = parts[3]
                result["listening_tcp_ports"].append(
                    {
                        "port": int(address.rsplit(":", 1)[1]),
                        "loopback": address.startswith(("127.", "[::1]:")),
                    }
                )
    # Count OOM evidence only; journal text can contain application secrets.
    journal = command(
        ["sudo", "-n", "journalctl", "--dmesg", "--since", "7 days ago", "--no-pager", "-o", "cat"],
        timeout=15,
    )
    result["kernel_oom_mentions_7d"] = (
        len(re.findall(r"out of memory|oom-kill|killed process", journal, flags=re.I))
        if isinstance(journal, str)
        else journal
    )
    result["cloud"] = cloud()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"verified": False, "probe_failure_type": type(error).__name__}))
        raise SystemExit(1) from None
