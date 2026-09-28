"""Verify required build attestations in a Buildx OCI archive."""

import argparse
import hashlib
import json
import re
import tarfile
from pathlib import Path
from typing import Any

REQUIRED_PREDICATES = {"https://slsa.dev/provenance/v1", "https://spdx.dev/Document"}
IMAGE_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
IMAGE_INDEX = "application/vnd.oci.image.index.v1+json"
ATTESTATION_TYPE = "attestation-manifest"
DIGEST_PATTERN = re.compile(r"sha256:([0-9a-f]{64})\Z")


def _read_json(archive: tarfile.TarFile, name: str) -> dict[str, Any]:
    try:
        member = archive.getmember(name)
        stream = archive.extractfile(member)
    except KeyError as exc:
        raise ValueError(f"Missing OCI entry: {name}") from exc
    if stream is None:
        raise ValueError(f"OCI entry is not a file: {name}")
    with stream:
        data = stream.read()
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError(f"OCI entry is not a JSON object: {name}")
    return value


def _read_blob(archive: tarfile.TarFile, digest: str) -> dict[str, Any]:
    match = DIGEST_PATTERN.fullmatch(digest)
    if match is None:
        raise ValueError(f"Invalid OCI digest: {digest}")
    name = f"blobs/sha256/{match.group(1)}"
    try:
        member = archive.getmember(name)
        stream = archive.extractfile(member)
    except KeyError as exc:
        raise ValueError(f"Missing OCI blob: {digest}") from exc
    if stream is None:
        raise ValueError(f"OCI blob is not a file: {digest}")
    with stream:
        data = stream.read()
    if hashlib.sha256(data).hexdigest() != match.group(1):
        raise ValueError(f"OCI blob digest mismatch: {digest}")
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError(f"OCI blob is not a JSON object: {digest}")
    return value


def _manifest_descriptors(
    archive: tarfile.TarFile, index: dict[str, Any], depth: int = 0
) -> list[dict[str, Any]]:
    if depth > 3:
        raise ValueError("OCI index nesting is too deep")
    descriptors: list[dict[str, Any]] = []
    for descriptor in index.get("manifests", []):
        if descriptor.get("mediaType") == IMAGE_INDEX:
            descriptors.extend(
                _manifest_descriptors(archive, _read_blob(archive, descriptor["digest"]), depth + 1)
            )
        elif descriptor.get("mediaType") == IMAGE_MANIFEST:
            descriptors.append(descriptor)
    return descriptors


def verify_archive(path: Path) -> int:
    with tarfile.open(path, "r:*") as archive:
        descriptors = _manifest_descriptors(archive, _read_json(archive, "index.json"))
        images = {
            descriptor["digest"]
            for descriptor in descriptors
            if descriptor.get("platform", {}).get("os") != "unknown"
        }
        if not images:
            raise ValueError("OCI archive has no runnable image manifest")

        predicates_by_image: dict[str, set[str]] = {digest: set() for digest in images}
        for descriptor in descriptors:
            annotations = descriptor.get("annotations", {})
            if annotations.get("vnd.docker.reference.type") != ATTESTATION_TYPE:
                continue
            image_digest = annotations.get("vnd.docker.reference.digest")
            if image_digest not in images:
                raise ValueError("Attestation does not reference a runnable image")
            manifest = _read_blob(archive, descriptor["digest"])
            for layer in manifest.get("layers", []):
                predicate_type = layer.get("annotations", {}).get("in-toto.io/predicate-type")
                if predicate_type not in REQUIRED_PREDICATES:
                    continue
                statement = _read_blob(archive, layer["digest"])
                if statement.get("predicateType") != predicate_type:
                    raise ValueError(f"Attestation predicate mismatch: {predicate_type}")
                if not isinstance(statement.get("predicate"), dict):
                    raise ValueError(f"Attestation has no predicate document: {predicate_type}")
                predicates_by_image[image_digest].add(predicate_type)

        for image_digest, predicates in predicates_by_image.items():
            missing = REQUIRED_PREDICATES - predicates
            if missing:
                raise ValueError(
                    f"Image {image_digest} lacks attestations: {', '.join(sorted(missing))}"
                )
        return len(images)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path, help="OCI archive created by docker buildx build")
    args = parser.parse_args()
    try:
        image_count = verify_archive(args.archive)
    except (OSError, ValueError, tarfile.TarError, json.JSONDecodeError) as exc:
        parser.exit(1, f"Image attestation verification failed: {exc}\n")
    print(f"Verified SBOM and provenance attestations for {image_count} image(s)")


if __name__ == "__main__":
    main()
