"""Tests for published registry image verification."""

import json
from pathlib import Path
from typing import Any

import pytest
from scripts.verify_registry_image import verify_registry_image

IMAGE_REF = f"ghcr.io/bigda7/jobradar@sha256:{'a' * 64}"
IMAGE_DIGEST = f"sha256:{'b' * 64}"


def _documents(tmp_path: Path) -> tuple[Path, Path, Path]:
    documents: dict[str, dict[str, Any]] = {
        "manifest": {
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [
                {
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "digest": IMAGE_DIGEST,
                    "platform": {"os": "linux", "architecture": "arm64"},
                },
                {
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "platform": {"os": "unknown", "architecture": "unknown"},
                    "annotations": {
                        "vnd.docker.reference.type": "attestation-manifest",
                        "vnd.docker.reference.digest": IMAGE_DIGEST,
                    },
                },
            ],
        },
        "provenance": {"SLSA": {"buildDefinition": {"buildType": "test"}, "runDetails": {}}},
        "sbom": {"SPDX": {"SPDXID": "SPDXRef-DOCUMENT", "spdxVersion": "SPDX-2.3"}},
    }
    paths = (tmp_path / "manifest.json", tmp_path / "provenance.json", tmp_path / "sbom.json")
    for path, document in zip(paths, documents.values(), strict=True):
        path.write_text(json.dumps(document), encoding="utf-8")
    return paths


def test_accepts_digest_pinned_arm64_image_with_attestations(tmp_path: Path) -> None:
    verify_registry_image(IMAGE_REF, *_documents(tmp_path))


@pytest.mark.parametrize("image_ref", ["ghcr.io/bigda7/jobradar:v1.2.11", "jobradar:latest"])
def test_rejects_unpinned_image(tmp_path: Path, image_ref: str) -> None:
    with pytest.raises(ValueError, match="pinned"):
        verify_registry_image(image_ref, *_documents(tmp_path))


@pytest.mark.parametrize("missing", ["attestation", "provenance", "sbom", "arm64"])
def test_rejects_missing_release_evidence(tmp_path: Path, missing: str) -> None:
    manifest, provenance, sbom = _documents(tmp_path)
    if missing == "attestation":
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["manifests"].pop()
        manifest.write_text(json.dumps(data), encoding="utf-8")
    elif missing == "provenance":
        provenance.write_text("{}", encoding="utf-8")
    elif missing == "sbom":
        sbom.write_text("{}", encoding="utf-8")
    else:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["manifests"][0]["platform"]["architecture"] = "amd64"
        manifest.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        verify_registry_image(IMAGE_REF, manifest, provenance, sbom)
