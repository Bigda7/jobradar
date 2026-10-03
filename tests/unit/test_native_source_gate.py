"""Regression tests for native source coverage and pre-publication gating."""

import hashlib
import json
import struct
from pathlib import Path
from typing import Any

import pytest
import yaml
from scripts import collect_runtime_licenses as collector
from scripts import prepare_runtime_sources as sources


def _elf() -> bytes:
    names = b"\0.text\0.shstrtab\0"
    header = bytearray(64)
    header[:6] = b"\x7fELF\x02\x01"
    struct.pack_into("<Q", header, 40, 64)
    struct.pack_into("<HHH", header, 58, 64, 3, 2)
    sections = b"\0" * 64
    sections += struct.pack("<IIQQQQIIQQ", 1, 1, 6, 0, 256, 4, 0, 0, 1, 0)
    sections += struct.pack("<IIQQQQIIQQ", 7, 3, 0, 0, 260, len(names), 0, 0, 1, 0)
    return bytes(header) + sections + b"code" + names


def test_elf_code_fingerprint_does_not_execute_library() -> None:
    assert collector.elf_evidence(_elf())["text_sha256"] == hashlib.sha256(b"code").hexdigest()


@pytest.mark.parametrize("content", [b"not ELF", b"\x7fELF" + b"\0" * 60, _elf()[:-5]])
def test_rejects_invalid_native_elf_evidence(content: bytes) -> None:
    with pytest.raises(ValueError):
        collector.elf_evidence(content)


def _review() -> dict[str, Any]:
    return {
        "schema": 1,
        "files": [
            {
                "package": "example",
                "path": "example.libs/lib.so",
                "sha256": "a" * 64,
                "group": "gcc",
            }
        ],
    }


def test_exact_native_content_is_required() -> None:
    review = _review()
    inventory = {"native_files": review["files"]}
    assert len(sources.native_rules(inventory, review)) == 1
    with pytest.raises(ValueError, match="differs"):
        sources.native_rules({"native_files": [{**review["files"][0], "sha256": "b" * 64}]}, review)


@pytest.mark.parametrize(
    "files", [[], [{"package": "new", "path": "new.libs/lib.so", "sha256": "b" * 64}]]
)
def test_removed_or_unknown_native_library_requires_review(files: list[dict[str, str]]) -> None:
    with pytest.raises(ValueError, match="differs"):
        sources.native_rules({"native_files": files}, _review())


def _complete_bundle(root: Path) -> dict[str, Any]:
    inventory = {"python_version": "3.13.15", "apk": [], "python": [], "native_files": []}
    records = [sources.preserve(root, "inventory.json", json.dumps(inventory).encode(), "test")]
    records.append(sources.preserve(root, "cpython/source.tar.xz", b"source", "test"))
    records.append(sources.preserve(root, "cpython/LICENSE.txt", b"license", "test"))
    records.append(sources.preserve(root, "cpython/build.json", b"build", "test"))
    manifest = {
        "schema": 1,
        "image": "sha256:" + "a" * 64,
        "complete": True,
        "issues": [],
        "registry_reference": "ghcr.io/example/image@sha256:" + "b" * 64,
        "artifacts": records,
        "coverage": {
            "cpython:3.13.15": {
                "reviewed": True,
                "source_paths": ["cpython/source.tar.xz"],
                "notice_paths": ["cpython/LICENSE.txt"],
                "build_paths": ["cpython/build.json"],
            }
        },
    }
    (root / "manifest.json").write_text(json.dumps(manifest))
    return manifest


def test_complete_bundle_passes_with_exact_image_binding(tmp_path: Path) -> None:
    manifest = _complete_bundle(tmp_path)
    sources.verify_bundle(
        tmp_path,
        expected_image=manifest["image"],
        expected_registry_reference=manifest["registry_reference"],
    )


def test_bundle_for_another_image_or_registry_digest_is_rejected(tmp_path: Path) -> None:
    _complete_bundle(tmp_path)
    with pytest.raises(ValueError, match="different runtime"):
        sources.verify_bundle(tmp_path, expected_image="sha256:" + "c" * 64)
    with pytest.raises(ValueError, match="different registry"):
        sources.verify_bundle(
            tmp_path, expected_registry_reference="ghcr.io/example/image@sha256:" + "c" * 64
        )


def test_source_bundle_cannot_include_unlisted_files(tmp_path: Path) -> None:
    _complete_bundle(tmp_path)
    (tmp_path / "unexpected.txt").write_text("must not be distributed")
    with pytest.raises(ValueError, match="Unlisted"):
        sources.verify_bundle(tmp_path)


def test_reviewed_coverage_cannot_omit_build_sources(tmp_path: Path) -> None:
    manifest = _complete_bundle(tmp_path)
    manifest["coverage"]["cpython:3.13.15"]["build_paths"] = []
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="build_paths"):
        sources.verify_bundle(tmp_path)


def test_cached_source_evidence_cannot_be_overwritten(tmp_path: Path) -> None:
    sources.preserve(tmp_path, "source", b"exact", "test")
    sources.preserve(tmp_path, "source", b"exact", "test")
    with pytest.raises(ValueError, match="differs"):
        sources.preserve(tmp_path, "source", b"different", "test")


def test_all_sixteen_reviewed_files_have_known_source_groups() -> None:
    root = Path(__file__).resolve().parents[2]
    review = json.loads((root / "docs/runtime-native-sources.json").read_text())
    assert len(review["files"]) == 16
    assert len({(file["package"], file["path"]) for file in review["files"]}) == 16
    assert {file["group"] for file in review["files"]} == set(review["groups"])
    assert review["groups"]["gcc-cross"]["reference_text_sha256"]


def test_publication_cannot_precede_source_verification_and_delivery() -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load((root / ".github/workflows/publish-image.yml").read_text())
    steps = workflow["jobs"]["publish"]["steps"]
    names = [step["name"] for step in steps]
    build = names.index("Build and verify ARM64 OCI candidate before publication")
    verify = names.index("Prepare and verify corresponding sources before publication")
    sources_upload = names.index("Publish verified sources on the existing release")
    image_upload = names.index("Publish the already verified image without rebuilding")
    assert build < verify < sources_upload < image_upload
    assert "--push" not in steps[build]["run"]
    assert "--verify --image" in steps[verify]["run"]
    assert "--registry-reference" in steps[verify]["run"]
    assert "gh release upload" in steps[sources_upload]["run"]
    assert "--clobber" not in steps[sources_upload]["run"]
    assert "buildx build" not in steps[image_upload]["run"]
    assert "docker image push" in steps[image_upload]["run"]
