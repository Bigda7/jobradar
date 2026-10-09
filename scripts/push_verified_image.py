"""Retry publication of an unchanged, already verified local image."""

import argparse
import re
import shutil

# Required CLI with validated arguments and no shell execution.
import subprocess  # nosec B404
import time
from pathlib import Path

IMAGE_TAG = re.compile(r"ghcr\.io/[a-z0-9_.-]+/[a-z0-9_.-]+:v[0-9]+\.[0-9]+\.[0-9]+\Z")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
RETRY_DELAYS = (10, 30)
PUSH_TIMEOUT_SECONDS = 300


def push_verified_image(image_tag: str, expected_digest: str) -> None:
    if IMAGE_TAG.fullmatch(image_tag) is None or DIGEST.fullmatch(expected_digest) is None:
        raise ValueError("Expected a GHCR release tag and a verified SHA-256 image digest")
    executable = shutil.which("docker")
    if executable is None:
        raise RuntimeError("Docker is not available on the publication runner")
    docker = str(Path(executable).resolve())
    for attempt in range(len(RETRY_DELAYS) + 1):
        # Fixed executable and strictly allowlisted release tag; shell=False.
        identity = subprocess.run(  # nosec B603
            [docker, "image", "inspect", "--format", "{{.Id}}", image_tag],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
        if identity != expected_digest:
            raise ValueError("Local image differs from the verified candidate; publication stopped")
        print(f"Publishing the verified candidate (attempt {attempt + 1}/3)", flush=True)
        try:
            # Fixed executable and strictly allowlisted release tag; shell=False.
            result = subprocess.run(  # nosec B603
                [docker, "image", "push", image_tag],
                check=False,
                capture_output=True,
                timeout=PUSH_TIMEOUT_SECONDS,
            )
            if result.returncode == 0:
                return
            print(f"Push attempt exited with status {result.returncode}", flush=True)
        except subprocess.TimeoutExpired:
            print("Push attempt reached its time limit", flush=True)
        if attempt < len(RETRY_DELAYS):
            time.sleep(RETRY_DELAYS[attempt])
    raise RuntimeError("Verified image publication failed after three bounded attempts")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image_tag")
    parser.add_argument("expected_digest")
    args = parser.parse_args()
    try:
        push_verified_image(args.image_tag, args.expected_digest)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        parser.exit(1, "Image publication failed; retain the verified recovery assets.\n")


if __name__ == "__main__":
    main()
