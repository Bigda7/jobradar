"""Tests for the OCI build attestation verifier."""

import hashlib
import io
import json
import tarfile
from pathlib import Path
from typing import Any

import pytest
from scripts.verify_image_attestations import verify_archive


def _write_archive(
    path: Path,
    *,
    include_sbom: bool = True,
    matching_predicate: bool = True,
    corrupt_blob: bool = False,
) -> None:
    blobs: dict[str, bytes] = {}

    def add_blob(value: dict[str, Any]) -> str:
        data = json.dumps(value).encode()
        digest = hashlib.sha256(data).hexdigest()
        blobs[f"blobs/sha256/{digest}"] = data
        return f"sha256:{digest}"

    image_digest = add_blob({"mediaType": "application/vnd.oci.image.manifest.v1+json"})
    layers = []
    predicates = ["https://slsa.dev/provenance/v1"]
    if include_sbom:
        predicates.append("https://spdx.dev/Document")
    for predicate in predicates:
        statement_type = predicate if matching_predicate else "https://example.invalid/other"
        statement_digest = add_blob({"predicateType": statement_type, "predicate": {}})
        layers.append(
            {
                "digest": statement_digest,
                "annotations": {"in-toto.io/predicate-type": predicate},
            }
        )

    attestation_digest = add_blob({"layers": layers})
    inner_index_digest = add_blob(
        {
            "manifests": [
                {
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "digest": image_digest,
                    "platform": {"os": "linux", "architecture": "amd64"},
                },
                {
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "digest": attestation_digest,
                    "platform": {"os": "unknown", "architecture": "unknown"},
                    "annotations": {
                        "vnd.docker.reference.type": "attestation-manifest",
                        "vnd.docker.reference.digest": image_digest,
                    },
                },
            ]
        }
    )
    index = {
        "manifests": [
            {"mediaType": "application/vnd.oci.image.index.v1+json", "digest": inner_index_digest}
        ]
    }
    entries = {"index.json": json.dumps(index).encode(), **blobs}
    if corrupt_blob:
        entries[f"blobs/sha256/{inner_index_digest.split(':')[1]}"] = b"{}"
    with tarfile.open(path, "w") as archive:
        for name, data in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))


def test_verifies_both_attestations(tmp_path: Path) -> None:
    archive = tmp_path / "image.tar"
    _write_archive(archive)

    assert verify_archive(archive) == 1


def test_rejects_missing_sbom(tmp_path: Path) -> None:
    archive = tmp_path / "image.tar"
    _write_archive(archive, include_sbom=False)

    with pytest.raises(ValueError, match="https://spdx.dev/Document"):
        verify_archive(archive)


def test_rejects_mismatched_predicate(tmp_path: Path) -> None:
    archive = tmp_path / "image.tar"
    _write_archive(archive, matching_predicate=False)

    with pytest.raises(ValueError, match="predicate mismatch"):
        verify_archive(archive)


def test_rejects_modified_oci_blob(tmp_path: Path) -> None:
    archive = tmp_path / "image.tar"
    _write_archive(archive, corrupt_blob=True)

    with pytest.raises(ValueError, match="digest mismatch"):
        verify_archive(archive)
