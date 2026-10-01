"""Verify a published ARM64 image and its registry attestations."""

import argparse
import json
import re
from pathlib import Path
from typing import Any

IMAGE_INDEX = "application/vnd.oci.image.index.v1+json"
IMAGE_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
ATTESTATION_TYPE = "attestation-manifest"
IMAGE_REFERENCE = re.compile(r"ghcr\.io/[a-z0-9_.-]+/[a-z0-9_.-]+@sha256:[0-9a-f]{64}\Z")


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path.name}")
    return value


def verify_registry_image(
    image_ref: str, manifest_path: Path, provenance_path: Path, sbom_path: Path
) -> None:
    if IMAGE_REFERENCE.fullmatch(image_ref) is None:
        raise ValueError("Image must be a GHCR repository pinned by SHA-256 digest")

    index = _read_object(manifest_path)
    if index.get("mediaType") != IMAGE_INDEX:
        raise ValueError("Published image is not an OCI image index")
    descriptors = index.get("manifests")
    if not isinstance(descriptors, list):
        raise ValueError("Published image index has no manifests")

    images = [
        descriptor
        for descriptor in descriptors
        if isinstance(descriptor, dict)
        and descriptor.get("mediaType") == IMAGE_MANIFEST
        and isinstance(descriptor.get("platform"), dict)
        and descriptor["platform"].get("os") == "linux"
        and descriptor["platform"].get("architecture") == "arm64"
    ]
    runnable = [
        descriptor
        for descriptor in descriptors
        if isinstance(descriptor, dict)
        and descriptor.get("mediaType") == IMAGE_MANIFEST
        and isinstance(descriptor.get("platform"), dict)
        and descriptor["platform"].get("os") != "unknown"
    ]
    if len(images) != 1 or len(runnable) != 1:
        raise ValueError("Expected exactly one runnable linux/arm64 image")

    image_digest = images[0].get("digest")
    if not isinstance(image_digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_digest):
        raise ValueError("ARM64 manifest has an invalid digest")
    attestations = [
        descriptor
        for descriptor in descriptors
        if isinstance(descriptor, dict)
        and isinstance(descriptor.get("annotations"), dict)
        and descriptor["annotations"].get("vnd.docker.reference.type") == ATTESTATION_TYPE
        and descriptor["annotations"].get("vnd.docker.reference.digest") == image_digest
    ]
    if not attestations:
        raise ValueError("No attestation manifest is linked to the ARM64 image")

    provenance = _read_object(provenance_path).get("SLSA")
    if (
        not isinstance(provenance, dict)
        or not isinstance(provenance.get("buildDefinition"), dict)
        or not isinstance(provenance.get("runDetails"), dict)
    ):
        raise ValueError("Published image lacks a SLSA provenance document")
    sbom = _read_object(sbom_path).get("SPDX")
    if (
        not isinstance(sbom, dict)
        or sbom.get("SPDXID") != "SPDXRef-DOCUMENT"
        or not isinstance(sbom.get("spdxVersion"), str)
        or not sbom["spdxVersion"].startswith("SPDX-")
    ):
        raise ValueError("Published image lacks an SPDX SBOM document")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image_ref")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("provenance", type=Path)
    parser.add_argument("sbom", type=Path)
    args = parser.parse_args()
    try:
        verify_registry_image(args.image_ref, args.manifest, args.provenance, args.sbom)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.exit(1, f"Registry image verification failed: {exc}\n")
    print(f"Verified ARM64 image and published attestations: {args.image_ref}")


if __name__ == "__main__":
    main()
