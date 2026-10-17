"""Build only the approved local libxml2 candidate from verified existing base images."""

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
BASE_IMAGES = {
    "amd64": "sha256:e973ea05a7bf7be3c9ef25afe9c91afa2273e0ab6ddab49df6dbf98dc3214539",
    "arm64": "sha256:8a49162272a678ab0ebd8645cf8b812f6eede545cccb90c1ccc16d090110bbe9",
}


def validate_base(architecture: str, record: dict) -> None:
    if record.get("Id") != BASE_IMAGES[architecture]:
        raise ValueError("Local APK base image identity differs from the approved input")
    if record.get("Architecture") != architecture or record.get("Os") != "linux":
        raise ValueError("Local base platform differs from the requested platform")


def verify_generated_patches() -> None:
    entries = (HERE / "generated/SHA256SUMS").read_text(encoding="utf-8").splitlines()
    if len(entries) != 1:
        raise ValueError("Unexpected generated patch inventory")
    expected, name = entries[0].split("  ", 1)
    if name != "0002-security-backports.patch":
        raise ValueError("Unexpected generated patch name")
    actual = hashlib.sha256((HERE / "generated" / name).read_bytes()).hexdigest()
    if actual != expected:
        raise ValueError("Generated patch checksum mismatch")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--architecture", choices=tuple(BASE_IMAGES), required=True)
    args = parser.parse_args()
    architecture = args.architecture
    base = f"jobradar-postgres-apk:{architecture}-20261016"
    result = subprocess.run(
        ["docker", "image", "inspect", base], capture_output=True, check=True, timeout=30
    )
    records = json.loads(result.stdout)
    if len(records) != 1:
        raise ValueError("Unexpected local image inspection result")
    validate_base(architecture, records[0])
    verify_generated_patches()
    tag = f"jobradar-postgres-libxml2:{architecture}-20261016"
    folder = ROOT / ".deploy/postgres-libxml-review-20261016"
    folder.mkdir(exist_ok=True, parents=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    log = folder / f"candidate-{architecture}-{stamp}.txt"
    command = [
        "docker",
        "buildx",
        "build",
        "--builder",
        "desktop-linux",
        "--pull=false",
        "--platform",
        f"linux/{architecture}",
        "--target",
        "candidate",
        "--build-arg",
        f"BASE_IMAGE={base}",
        "--tag",
        tag,
        "--load",
        "--progress",
        "plain",
        str(HERE),
    ]
    with log.open("wb") as output:
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT) as process:
            assert process.stdout is not None
            for line in process.stdout:
                output.write(line)
                output.flush()
                sys.stdout.buffer.write(line)
                sys.stdout.buffer.flush()
            status = process.wait()
    print(json.dumps({"image": tag, "status": status, "log": str(log)}))
    if status:
        raise SystemExit(status)


if __name__ == "__main__":
    main()
