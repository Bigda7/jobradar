"""Reassemble approved local PostgreSQL files without superseded image layers."""

import argparse
import hashlib
import json
import subprocess
import tarfile
import tempfile
import uuid
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[3]
INPUTS = {
    "amd64": "sha256:71d2c993b1ea1d465a649b11ab096585891033c2bb1328cd0c09371b77ecbe2f",
    "arm64": "sha256:fe051cc7f7d2770400e5302402c296ec484b108a3ab5cace81d0df771cdb0345",
}
CONFIG_KEYS = {
    "ExposedPorts",
    "Env",
    "Entrypoint",
    "Cmd",
    "Volumes",
    "WorkingDir",
    "Labels",
    "StopSignal",
}
ENV_NAMES = {
    "PATH",
    "GOSU_VERSION",
    "LANG",
    "PG_MAJOR",
    "PG_VERSION",
    "PG_SHA256",
    "DOCKER_PG_LLVM_DEPS",
    "PGDATA",
}
LABEL = "jobradar.postgres.clean.run"
RUNTIME_TIMESTAMP_PATHS = {
    ".dockerenv",
    "dev/console",
    "etc/hostname",
    "etc/hosts",
    "etc/mtab",
    "etc/resolv.conf",
}


def run(command: list[str], timeout: int = 180) -> bytes:
    result = subprocess.run(command, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"{command[0]} {command[1]} failed, status={result.returncode}")
    return result.stdout


def inspect(image: str) -> dict:
    records = json.loads(run(["docker", "image", "inspect", image], 30))
    if len(records) != 1:
        raise ValueError("Expected exactly one local image")
    return records[0]


def validate_source(record: dict, architecture: str) -> None:
    if record.get("Id") != INPUTS[architecture]:
        raise ValueError("Source image identity differs from the approved local artifact")
    if record.get("Os") != "linux" or record.get("Architecture") != architecture:
        raise ValueError("Source platform mismatch")
    config = record["Config"]
    if set(config) != CONFIG_KEYS:
        raise ValueError("Unexpected image configuration fields require explicit handling")
    names = [item.split("=", 1)[0] for item in config["Env"]]
    if set(names) != ENV_NAMES or len(names) != len(ENV_NAMES):
        raise ValueError("Unexpected or duplicate environment entries; values not logged")
    if config["Volumes"] != {"/var/lib/postgresql/data": {}}:
        raise ValueError("Unexpected database volume configuration")
    if config["Entrypoint"] != ["docker-entrypoint.sh"] or config["Cmd"] != ["postgres"]:
        raise ValueError("Unexpected PostgreSQL startup configuration")


def quote(value: str) -> str:
    if "\n" in value or "\r" in value or "\0" in value:
        raise ValueError("Multiline or NUL configuration is not supported")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$") + '"'


def dockerfile(record: dict, architecture: str) -> str:
    config = record["Config"]
    source = f"jobradar-postgres-libxml2:{architecture}-20261016@{record['Id']}"
    lines = [f"FROM {source} AS source", "FROM scratch", "COPY --from=source / /"]
    for item in config["Env"]:
        name, value = item.split("=", 1)
        lines.append(f"ENV {name}={quote(value)}")
    for key, value in config["Labels"].items():
        lines.append(f"LABEL {quote(key)}={quote(value)}")
    lines.extend(
        [
            f"WORKDIR {config['WorkingDir']}",
            f"ENTRYPOINT {json.dumps(config['Entrypoint'])}",
            f"CMD {json.dumps(config['Cmd'])}",
            f"VOLUME {json.dumps(list(config['Volumes']))}",
            "EXPOSE " + " ".join(config["ExposedPorts"]),
            f"STOPSIGNAL {config['StopSignal']}",
        ]
    )
    return "\n".join(lines) + "\n"


def validate_candidate(source: dict, candidate: dict) -> None:
    # BuildKit emits this Windows-only field even for Linux JSON-form commands.
    config = dict(candidate["Config"])
    if "ArgsEscaped" in config:
        if candidate.get("Os") != "linux" or type(config["ArgsEscaped"]) is not bool:
            raise ValueError("Unexpected command escaping metadata")
        config.pop("ArgsEscaped")
    if (
        any(candidate.get(key) != source.get(key) for key in ("Os", "Architecture"))
        or config != source["Config"]
    ):
        raise ValueError("Candidate configuration or platform differs from the approved input")
    layers = candidate["RootFS"]["Layers"]
    if len(layers) != 1 or set(layers) & set(source["RootFS"]["Layers"]):
        raise ValueError("Candidate must contain one fresh layer without source layer reuse")


def manifest(archive: Path) -> dict:
    records = {}
    with tarfile.open(archive) as content:
        for member in content:
            name = member.name.removeprefix("./").rstrip("/")
            if not name or name == ".":
                continue
            if PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts:
                raise ValueError("Unexpected rootfs archive path")
            if name in records:
                raise ValueError("Duplicate rootfs archive path")
            if name.startswith("var/lib/postgresql/data/"):
                raise ValueError("Refusing a root filesystem containing database data")
            record = {
                "type": member.type.decode("ascii"),
                "mode": member.mode,
                "uid": member.uid,
                "gid": member.gid,
                "link": member.linkname,
                "mtime": member.mtime,
                "device": [member.devmajor, member.devminor],
                "attributes": {
                    key: value
                    for key, value in member.pax_headers.items()
                    if key not in {"mtime", "atime", "ctime"}
                },
            }
            if member.isfile() or member.islnk():
                stream = content.extractfile(member)
                if stream is None:
                    raise ValueError("Missing regular file content")
                with stream:
                    record["sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
            records[name] = record
    if not records:
        raise ValueError("Empty root filesystem")
    return records


def export_manifest(image: str, architecture: str, folder: Path, role: str) -> dict:
    run_id = uuid.uuid4().hex
    name = f"jobradar-pg-clean-{run_id}"
    archive = folder / f"{role}.tar"
    created = False
    try:
        run(
            [
                "docker",
                "create",
                "--pull",
                "never",
                "--platform",
                f"linux/{architecture}",
                "--network",
                "none",
                "--label",
                f"{LABEL}={run_id}",
                "--name",
                name,
                "--entrypoint",
                "/bin/true",
                image,
            ]
        )
        created = True
        run(["docker", "export", "--output", str(archive), name])
        result = manifest(archive)
        (folder / f"{role}.manifest.json").write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        archive.unlink()
        return result
    finally:
        if created:
            record = json.loads(run(["docker", "container", "inspect", name]))[0]
            if record["Config"]["Labels"].get(LABEL) != run_id:
                raise RuntimeError("Refusing cleanup of an unrelated container")
            run(["docker", "rm", "--volumes", name])


def compare_manifests(before: dict, after: dict) -> list[str]:
    paths = set(before) | set(after)
    changed = []
    timestamp_changes = []
    for path in sorted(paths):
        left = dict(before.get(path, {}))
        right = dict(after.get(path, {}))
        if left != right and (path in RUNTIME_TIMESTAMP_PATHS or left.get("type") == "5"):
            if left.pop("mtime", None) != right.pop("mtime", None):
                timestamp_changes.append(path)
        if left != right:
            changed.append(path)
    if changed:
        raise ValueError(f"Root filesystem differs at {len(changed)} paths: {changed[:12]}")
    return timestamp_changes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--architecture", choices=tuple(INPUTS), required=True)
    args = parser.parse_args()
    architecture = args.architecture
    source_tag = f"jobradar-postgres-libxml2:{architecture}-20261016"
    source = inspect(source_tag)
    validate_source(source, architecture)
    parent = ROOT / ".deploy/postgres-clean-20261016"
    parent.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix=f"{architecture}-", dir=parent))
    (folder / "Dockerfile").write_text(
        dockerfile(source, architecture), encoding="utf-8", newline="\n"
    )
    tag = f"jobradar-postgres-clean:{architecture}-20261016"
    command = [
        "docker",
        "buildx",
        "build",
        "--builder",
        "desktop-linux",
        "--pull=false",
        "--network",
        "none",
        "--platform",
        f"linux/{architecture}",
        "--tag",
        tag,
        "--load",
        "--progress",
        "plain",
        str(folder),
    ]
    with (folder / "build.log").open("wb") as output:
        result = subprocess.run(command, stdout=output, stderr=subprocess.STDOUT, timeout=600)
    if result.returncode:
        raise RuntimeError(f"Local build failed; inspect {folder / 'build.log'}")
    candidate = inspect(tag)
    validate_candidate(source, candidate)
    before = export_manifest(source_tag, architecture, folder, "source")
    after = export_manifest(tag, architecture, folder, "candidate")
    timestamp_changes = compare_manifests(before, after)
    report = {
        "architecture": architecture,
        "source": source["Id"],
        "candidate": candidate["Id"],
        "image": tag,
        "paths_checked": len(before),
        "source_layers": len(source["RootFS"]["Layers"]),
        "candidate_layers": 1,
        "runtime_configuration_identical": True,
        "configuration_metadata_difference": {
            key: candidate["Config"][key]
            for key in set(candidate["Config"]) - set(source["Config"])
        },
        "file_content_and_security_metadata_identical": True,
        "directory_or_runtime_placeholder_timestamp_changes": timestamp_changes,
        "evidence": str(folder),
    }
    (folder / "result.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
