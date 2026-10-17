import copy
import importlib.util
import io
import tarfile
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "deploy/postgres-prototype/clean"


def load_module():
    spec = importlib.util.spec_from_file_location("postgres_clean", TOOLS / "build_candidate.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def source_record(architecture="amd64"):
    module = load_module()
    return {
        "Id": module.INPUTS[architecture],
        "Os": "linux",
        "Architecture": architecture,
        "RootFS": {"Layers": ["old-layer"]},
        "Config": {
            "Env": [f"{name}=synthetic" for name in sorted(module.ENV_NAMES)],
            "Entrypoint": ["docker-entrypoint.sh"],
            "Cmd": ["postgres"],
            "Volumes": {"/var/lib/postgresql/data": {}},
            "ExposedPorts": {"5432/tcp": {}},
            "WorkingDir": "/",
            "StopSignal": "SIGINT",
            "Labels": {"synthetic": "fixture"},
        },
    }


@pytest.mark.parametrize("architecture", ["amd64", "arm64"])
@pytest.mark.parametrize(
    "change",
    [
        "none",
        "id",
        "os",
        "architecture",
        "field",
        "env",
        "duplicate_env",
        "volume",
        "entrypoint",
        "cmd",
    ],
)
def test_source_requires_exact_input_and_supported_configuration(architecture, change):
    module = load_module()
    record = source_record(architecture)
    if change in {"id", "os", "architecture"}:
        record[{"id": "Id", "os": "Os", "architecture": "Architecture"}[change]] = "wrong"
    elif change == "field":
        record["Config"]["User"] = "unexpected"
    elif change == "env":
        record["Config"]["Env"].append("UNEXPECTED_SECRET=not-logged")
    elif change == "duplicate_env":
        record["Config"]["Env"].append(record["Config"]["Env"][0])
    elif change == "volume":
        record["Config"]["Volumes"] = {"/other": {}}
    elif change in {"entrypoint", "cmd"}:
        record["Config"]["Entrypoint" if change == "entrypoint" else "Cmd"] = ["wrong"]
    if change == "none":
        module.validate_source(record, architecture)
    else:
        with pytest.raises(ValueError):
            module.validate_source(record, architecture)


@pytest.mark.parametrize(
    "change",
    [
        "none",
        "configuration",
        "platform",
        "old_layer",
        "layers",
        "linux_args_metadata",
        "invalid_args_metadata",
    ],
)
def test_candidate_must_preserve_configuration_without_old_layers(change):
    module = load_module()
    source = source_record()
    candidate = copy.deepcopy(source)
    candidate["RootFS"]["Layers"] = ["fresh-layer"]
    if change == "configuration":
        candidate["Config"]["StopSignal"] = "SIGTERM"
    elif change == "platform":
        candidate["Architecture"] = "arm64"
    elif change == "old_layer":
        candidate["RootFS"]["Layers"] = ["old-layer"]
    elif change == "layers":
        candidate["RootFS"]["Layers"].append("another-layer")
    elif change in {"linux_args_metadata", "invalid_args_metadata"}:
        candidate["Config"]["ArgsEscaped"] = True if change == "linux_args_metadata" else "wrong"
    if change in {"none", "linux_args_metadata"}:
        module.validate_candidate(source, candidate)
    else:
        with pytest.raises(ValueError):
            module.validate_candidate(source, candidate)


def test_dockerfile_preserves_whitespace_and_uses_only_merged_files():
    module = load_module()
    record = source_record()
    record["Config"]["Env"][0] = 'PATH=space \t\ttab $literal "quoted" \\slash'
    text = module.dockerfile(record, "amd64")
    assert "FROM scratch\nCOPY --from=source / /\n" in text
    assert f"@{module.INPUTS['amd64']} AS source" in text
    assert 'ENV PATH="space \t\ttab \\$literal \\"quoted\\" \\\\slash"' in text
    assert 'ENTRYPOINT ["docker-entrypoint.sh"]' in text
    assert 'VOLUME ["/var/lib/postgresql/data"]' in text
    assert "EXPOSE 5432/tcp" in text
    assert "STOPSIGNAL SIGINT" in text
    assert "RUN " not in text


@pytest.mark.parametrize("value", ["line\nbreak", "line\rbreak", "nul\0value"])
def test_config_quote_rejects_instruction_injection(value):
    with pytest.raises(ValueError):
        load_module().quote(value)


def make_archive(path, *, name="usr/lib/example", mode=0o755, uid=70, payload=b"fixture"):
    with tarfile.open(path, "w") as archive:
        member = tarfile.TarInfo(name)
        member.mode = mode
        member.uid = uid
        member.gid = 70
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))
        if name == "usr/lib/example":
            link = tarfile.TarInfo("usr/lib/link")
            link.type = tarfile.SYMTYPE
            link.linkname = "example"
            archive.addfile(link)


@pytest.mark.parametrize("change", ["none", "mode", "uid", "payload", "removed", "link", "mtime"])
def test_manifest_detects_content_ownership_permissions_and_symlink_changes(tmp_path, change):
    module = load_module()
    path = tmp_path / "fixture.tar"
    make_archive(path)
    before = module.manifest(path)
    after = copy.deepcopy(before)
    if change in {"mode", "uid", "mtime"}:
        after["usr/lib/example"][change] += 1
    elif change == "payload":
        after["usr/lib/example"]["sha256"] = "wrong"
    elif change == "removed":
        del after["usr/lib/example"]
    elif change == "link":
        after["usr/lib/link"]["link"] = "wrong"
    if change == "none":
        module.compare_manifests(before, after)
    else:
        with pytest.raises(ValueError, match="Root filesystem differs"):
            module.compare_manifests(before, after)


@pytest.mark.parametrize("name", ["../outside", "/absolute", "var/lib/postgresql/data/PG_VERSION"])
def test_manifest_never_extracts_or_accepts_database_content(tmp_path, name):
    path = tmp_path / "fixture.tar"
    make_archive(path, name=name)
    with pytest.raises(ValueError):
        load_module().manifest(path)


def test_manifest_rejects_empty_archive(tmp_path):
    path = tmp_path / "fixture.tar"
    with tarfile.open(path, "w"):
        pass
    with pytest.raises(ValueError, match="Empty root"):
        load_module().manifest(path)


@pytest.mark.parametrize("path,kind", [("etc/hostname", "0"), ("tmp", "5")])
def test_only_known_runtime_or_directory_timestamp_changes_are_recorded(path, kind):
    module = load_module()
    before = {path: {"type": kind, "mtime": 1, "mode": 0o755}}
    after = copy.deepcopy(before)
    after[path]["mtime"] = 2
    assert module.compare_manifests(before, after) == [path]
    after[path]["mode"] = 0o777
    with pytest.raises(ValueError):
        module.compare_manifests(before, after)
