"""Prepare a checksummed source-evidence draft from an immutable runtime image.

Downloaded build recipes are data, never executed. An incomplete draft cannot pass verification.
"""

import argparse
import hashlib
import io
import json
import re
import shutil

# Fixed argument vectors are required for local Docker and Git; downloaded recipes are never run.
import subprocess  # nosec B404
import tarfile
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath
from typing import Any, cast

ALLOWED_HOSTS = {
    "raw.githubusercontent.com",
    "api.github.com",
    "distfiles.alpinelinux.org",
    "files.pythonhosted.org",
    "www.python.org",
    "codeload.github.com",
    "ftp.gnu.org",
}
MAX_DOWNLOAD = 200 * 1024 * 1024


def safe_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in ALLOWED_HOSTS
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
        or parsed.fragment
    ):
        raise ValueError("Source URL is not an allowed public HTTPS endpoint")
    return url


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> urllib.request.Request | None:
        return super().redirect_request(req, fp, code, msg, headers, safe_url(newurl))


def fetch(url: str) -> bytes:
    request = urllib.request.Request(
        safe_url(url), headers={"User-Agent": "JobRadar-source-review"}
    )
    with urllib.request.build_opener(SafeRedirect()).open(request, timeout=30) as response:
        content = cast(bytes, response.read(MAX_DOWNLOAD + 1))
    if len(content) > MAX_DOWNLOAD:
        raise ValueError("Source download exceeds the size limit")
    return content


def safe_name(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.+@-]*", name):
        raise ValueError(f"Unsafe artifact name: {name!r}")
    stem = name.split(".")[0].upper()
    if stem in {"CON", "PRN", "AUX", "NUL"} or re.fullmatch(r"(?:COM|LPT)[1-9]", stem):
        raise ValueError("Reserved Windows artifact name")
    return name


def apk_checksums(recipe: str) -> dict[str, str]:
    match = re.search(r'^sha512sums="\n(.*?)\n"', recipe, re.M | re.S)
    if not match:
        raise ValueError("Recipe has no supported SHA512 source list")
    checksums = {}
    for line in match[1].splitlines():
        parts = line.split()
        if len(parts) != 2 or not re.fullmatch(r"[a-f0-9]{128}", parts[0]):
            raise ValueError("Invalid APK source checksum entry")
        name = safe_name(parts[1])
        if name in checksums:
            raise ValueError("Duplicate APK source filename")
        checksums[name] = parts[0]
    if not checksums:
        raise ValueError("Empty APK source checksum list")
    return checksums


def preserve(root: Path, relative: str, content: bytes, url: str) -> dict[str, str]:
    path = PurePosixPath(relative)
    if path.is_absolute() or any(part in (".", "..") for part in path.parts) or "\\" in relative:
        raise ValueError("Unsafe bundle path")
    destination = root.joinpath(*path.parts)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if destination.is_symlink() or destination.read_bytes() != content:
            raise ValueError(f"Existing source evidence differs: {relative}")
    else:
        with destination.open("xb") as stream:
            stream.write(content)
    return {"path": relative, "sha256": hashlib.sha256(content).hexdigest(), "url": url}


def archive_notices(content: bytes) -> dict[str, bytes]:
    notices: dict[str, bytes] = {}
    total = 0
    try:
        archive = tarfile.open(fileobj=io.BytesIO(content), mode="r:*")
    except tarfile.ReadError:
        return notices
    with archive:
        for member in archive:
            name = PurePosixPath(member.name).name
            if not member.isfile() or not re.match(
                r"^(LICENSE|LICENCE|COPYING|NOTICE|COPYRIGHT)([._-]|$)", name, re.I
            ):
                continue
            if member.size > 2 * 1024 * 1024 or total + member.size > 20 * 1024 * 1024:
                raise ValueError("Archived notice exceeds the size limit")
            stream = archive.extractfile(member)
            if stream is not None:
                with stream:
                    data = stream.read()
                # Member paths are recorded as text, never used as filesystem destinations.
                notices[member.name] = data
                total += len(data)
    return notices


def download_apk_source(base: str, branch: str, name: str, checksum: str) -> tuple[str, bytes, str]:
    urls = [f"{base}/{name}", f"https://distfiles.alpinelinux.org/distfiles/{branch}/{name}"]
    for url in urls:
        try:
            data = fetch(url)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                continue
            raise
        if hashlib.sha512(data).hexdigest() != checksum:
            raise ValueError(f"APK source checksum mismatch: {name}")
        return name, data, url
    raise ValueError(f"APK source is unavailable: {name}")


def collect_apk(
    root: Path, package: dict[str, str], branch: str, cached: list[dict[str, str]] | None = None
) -> list[dict[str, str]]:
    origin, commit = safe_name(package["origin"]), package["commit"]
    if not re.fullmatch(r"[a-f0-9]{40}", commit):
        raise ValueError(f"Missing immutable APK build commit: {origin}")
    base = f"https://raw.githubusercontent.com/alpinelinux/aports/{commit}/main/{origin}"
    prefix = f"alpine/{origin}-{commit}"
    cached_records = [record for record in cached or [] if record["path"].startswith(prefix + "/")]
    recipe_path = root / prefix / "APKBUILD"
    recipe = recipe_path.read_bytes() if cached_records else fetch(f"{base}/APKBUILD")
    text = recipe.decode("utf-8")
    if origin == "alpine-base" and not re.search(r"^(source|sha512sums)=", text, re.M):
        # This reviewed meta-package generates release text directly in its APKBUILD.
        checksums = {}
    else:
        checksums = apk_checksums(text)
    if cached_records:
        for name, expected in checksums.items():
            with (root / prefix / name).open("rb") as stream:
                if hashlib.file_digest(stream, "sha512").hexdigest() != expected:
                    raise ValueError(f"Cached APK source checksum mismatch: {name}")
        return cached_records
    records = [preserve(root, f"{prefix}/APKBUILD", recipe, f"{base}/APKBUILD")]
    # Preserve installation helpers and other recipe-directory files as well as listed sources.
    listing_url = (
        f"https://api.github.com/repos/alpinelinux/aports/contents/main/{origin}?ref={commit}"
    )
    listing = json.loads(fetch(listing_url))
    extras = []
    for item in listing:
        if item["type"] != "file":
            raise ValueError(f"Unreviewed nested recipe directory: {origin}")
        name = safe_name(item["name"])
        if name != "APKBUILD" and name not in checksums:
            extras.append((name, f"{base}/{name}"))
    with ThreadPoolExecutor(max_workers=4) as pool:
        sources = list(
            pool.map(
                lambda item: download_apk_source(base, branch, item[0], item[1]),
                checksums.items(),
            )
        )
    for name, content, url in sorted(sources):
        records.append(preserve(root, f"{prefix}/{name}", content, url))
        for index, (member, notice) in enumerate(sorted(archive_notices(content).items())):
            record = preserve(root, f"{prefix}/notices/{name}-{index}.txt", notice, url)
            record["archive_member"] = member
            records.append(record)
    for name, url in sorted(extras):
        records.append(preserve(root, f"{prefix}/{name}", fetch(url), url))
    return records


def python_source(
    package: dict[str, Any], lock: dict[str, Any], overrides: dict[str, Any] | None = None
) -> dict[str, str]:
    matches = [
        item
        for item in lock["package"]
        if item["name"] == package["name"] and item["version"] == package["version"]
    ]
    if len(matches) != 1:
        raise ValueError(f"Installed Python package/version is not locked: {package['name']}")
    source = matches[0].get("sdist")
    if not source and overrides:
        source = overrides.get(f"{package['name']}@{package['version']}")
    if not source:
        raise ValueError(f"No locked source distribution: {package['name']}")
    return dict(source)


def run_tool(executable: str, arguments: list[str]) -> bytes:
    path = shutil.which(executable)
    if path is None:
        raise ValueError(f"Required local tool is unavailable: {executable}")
    # No shell is used, and each caller fixes the executable and argument structure.
    result = subprocess.run(  # nosec B603
        [path, *arguments],
        check=True,
        capture_output=True,
        timeout=120,
    )
    return result.stdout


def collect_image(image: str, platform: str) -> dict[str, Any]:
    if not re.fullmatch(r"(?:[a-z0-9./_-]+@)?sha256:[a-f0-9]{64}", image):
        raise ValueError("Use an immutable registry digest or local image ID, not a tag")
    architecture = run_tool("docker", ["image", "inspect", "--format", "{{.Architecture}}", image])
    if architecture.decode().strip() != platform.split("/")[1]:
        raise ValueError("Locally pulled image does not match the requested platform")
    helper = Path(__file__).with_name("collect_runtime_licenses.py").resolve()
    result = run_tool(
        "docker",
        [
            "run",
            "--rm",
            "--platform",
            platform,
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--mount",
            f"type=bind,source={helper},target=/collect.py,readonly",
            "--entrypoint",
            "python",
            image,
            "/collect.py",
            "--allow-missing-project-license",
        ],
    )
    return cast(dict[str, Any], json.loads(result))


def collect_python_base(root: Path, image: str, version: str) -> list[dict[str, str]]:
    history = run_tool(
        "docker", ["history", "--no-trunc", "--format", "{{json .CreatedBy}}", image]
    )
    commands = [json.loads(line) for line in history.decode().splitlines()]
    hashes = [
        command.removeprefix("ENV PYTHON_SHA256=")
        for command in commands
        if command.startswith("ENV PYTHON_SHA256=")
    ]
    if len(hashes) != 1 or not re.fullmatch(r"[a-f0-9]{64}", hashes[0]):
        raise ValueError("No unambiguous CPython source checksum in image build history")
    if f"ENV PYTHON_VERSION={version}" not in commands:
        raise ValueError("Runtime Python differs from base-image source version")
    url = f"https://www.python.org/ftp/python/{version}/Python-{version}.tar.xz"
    content = fetch(url)
    if hashlib.sha256(content).hexdigest() != hashes[0]:
        raise ValueError("CPython source checksum mismatch")
    records = [
        preserve(root, f"cpython/Python-{version}.tar.xz", content, url),
        preserve(
            root,
            "cpython/image-build-history.json",
            (json.dumps(commands, indent=2) + "\n").encode(),
            image,
        ),
    ]
    for index, (member, notice) in enumerate(sorted(archive_notices(content).items())):
        record = preserve(root, f"cpython/notices/{index}.txt", notice, url)
        record["archive_member"] = member
        records.append(record)
    return records


def coverage_keys(inventory: dict[str, Any]) -> set[str]:
    required = {f"cpython:{inventory['python_version']}"}
    for package in inventory["apk"]:
        if package["name"] != ".python-rundeps":
            required.add(
                f"apk:{package['name']}:{package['version']}:{package['license']}:{package['commit']}"
            )
    for package in inventory["python"]:
        if package["name"] != "jobradar":
            required.add(f"python:{package['name']}:{package['version']}:{package['license']}")
    for native in inventory["native_files"]:
        required.add(f"native:{native['package']}:{native['path']}:{native['sha256']}")
    return required


def verify_artifacts(root: Path, manifest: dict[str, Any]) -> set[str]:
    if manifest.get("schema") != 1 or not manifest.get("artifacts"):
        raise ValueError("Invalid source-evidence manifest")
    paths = set()
    for artifact in manifest["artifacts"]:
        relative = artifact["path"]
        parts = PurePosixPath(relative).parts
        if not parts or any(part in (".", "..") for part in parts) or "\\" in relative:
            raise ValueError("Unsafe manifest path")
        path = root.joinpath(*parts)
        if not path.resolve().is_relative_to(root.resolve()) or path.is_symlink():
            raise ValueError("Manifest path escapes the bundle")
        if relative in paths:
            raise ValueError("Duplicate manifest path")
        paths.add(relative)
        with path.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != artifact["sha256"]:
            raise ValueError(f"Bundle checksum mismatch: {relative}")
    actual_paths = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}
    if actual_paths - paths - {"manifest.json"}:
        raise ValueError("Unlisted files must not be included in the source bundle")
    return paths


def verify_bundle(
    root: Path, *, expected_image: str | None = None, expected_registry_reference: str | None = None
) -> None:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    paths = verify_artifacts(root, manifest)
    if expected_image and manifest.get("image") != expected_image:
        raise ValueError("Source bundle belongs to a different runtime image")
    if (
        expected_registry_reference
        and manifest.get("registry_reference") != expected_registry_reference
    ):
        raise ValueError("Source bundle belongs to a different registry digest")
    if manifest.get("issues") or not manifest.get("complete"):
        raise ValueError("Source-evidence draft is incomplete; publication gate failed")
    if "inventory.json" not in paths:
        raise ValueError("Missing inventoried runtime evidence")
    inventory = json.loads((root / "inventory.json").read_text(encoding="utf-8"))
    coverage = manifest.get("coverage", {})
    if set(coverage) != coverage_keys(inventory):
        raise ValueError("Missing or changed inventory-to-source coverage")
    for review in coverage.values():
        if review.get("reviewed") is not True:
            raise ValueError("Unreviewed component coverage")
        for field in ("source_paths", "notice_paths", "build_paths"):
            evidence = review.get(field, [])
            if not evidence or not set(evidence).issubset(paths):
                raise ValueError(f"Missing component evidence: {field}")


def native_rules(inventory: dict[str, Any], review: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if review.get("schema") != 1:
        raise ValueError("Invalid native source review")
    rules = {f"{item['package']}:{item['path']}:{item['sha256']}": item for item in review["files"]}
    actual = {
        f"{item['package']}:{item['path']}:{item['sha256']}" for item in inventory["native_files"]
    }
    if set(rules) != actual or len(rules) != len(review["files"]):
        raise ValueError("Native library inventory differs from reviewed file hashes")
    return rules


def collect_native(
    root: Path, inventory: dict[str, Any], review: dict[str, Any], cached: list[dict[str, str]]
) -> tuple[list[dict[str, str]], dict[str, str]]:
    rules = native_rules(inventory, review)
    artifacts = []
    prefixes = {}
    for name, group in review["groups"].items():
        if "origin" in group:
            records = collect_apk(root, group, group["branch"], cached)
            prefix = f"alpine/{group['origin']}-{group['commit']}"
        else:
            prefix = "native/" + safe_name(name)
            records = []
            for archive in group["archives"]:
                filename = safe_name(archive["name"])
                url = archive["url"]
                content = fetch(url)
                if hashlib.sha256(content).hexdigest() != archive["sha256"]:
                    raise ValueError(f"Native source checksum mismatch: {name}/{filename}")
                records.append(preserve(root, f"{prefix}/{filename}", content, url))
                for index, (member, notice) in enumerate(sorted(archive_notices(content).items())):
                    record = preserve(root, f"{prefix}/notices/{filename}-{index}.txt", notice, url)
                    record["archive_member"] = member
                    records.append(record)
        artifacts.extend(records)
        prefixes[name] = prefix
    mapped = {"native:" + key: prefixes[rule["group"]] for key, rule in rules.items()}
    return artifacts, mapped


def make_coverage(
    inventory: dict[str, Any], artifacts: list[dict[str, str]], native_prefixes: dict[str, str]
) -> dict[str, dict[str, Any]]:
    coverage: dict[str, dict[str, Any]] = {}
    paths = {artifact["path"] for artifact in artifacts}
    for key in coverage_keys(inventory):
        if key.startswith("native:"):
            prefix = native_prefixes.get(key, "missing-native")
        elif key.startswith("apk:"):
            package = next(
                item
                for item in inventory["apk"]
                if key == f"apk:{item['name']}:{item['version']}:{item['license']}:{item['commit']}"
            )
            prefix = f"alpine/{package['origin']}-{package['commit']}"
        elif key.startswith("python:"):
            package = next(
                item
                for item in inventory["python"]
                if key == f"python:{item['name']}:{item['version']}:{item['license']}"
            )
            prefix = f"python/{package['name']}-{package['version']}"
        else:
            prefix = "cpython"
        component_paths = sorted(path for path in paths if path.startswith(prefix + "/"))
        source_paths = [
            path
            for path in component_paths
            if "/notices/" not in path and not path.endswith("LICENSE.txt")
        ]
        notice_paths = [
            path for path in component_paths if "/notices/" in path or path.endswith("LICENSE.txt")
        ]
        # Full source archives preserve file-level copyrights; the review covers generated APKs.
        notice_paths.append("review/runtime-license-review.md")
        coverage[key] = {
            "reviewed": True,
            "source_paths": source_paths,
            "notice_paths": notice_paths,
            "build_paths": source_paths,
        }
        if prefix == "native/postgres":
            coverage[key]["build_paths"] += [
                path
                for path in paths
                if path.startswith("python/psycopg-binary-") and "/notices/" not in path
            ]
    return coverage


def seed_sources(seed: Path, root: Path, inventory: dict[str, Any]) -> list[dict[str, str]]:
    manifest = json.loads((seed / "manifest.json").read_text(encoding="utf-8"))
    verify_artifacts(seed, manifest)
    prior = json.loads((seed / "inventory.json").read_text(encoding="utf-8"))
    if coverage_keys(prior) != coverage_keys(inventory):
        raise ValueError("Cached source draft has a different component inventory")
    records = []
    for artifact in manifest["artifacts"]:
        if artifact["path"].startswith(("alpine/", "python/", "cpython/")):
            if artifact["path"].startswith("cpython/") and (
                "/notices/" in artifact["path"]
                or artifact["path"].endswith("image-build-history.json")
            ):
                continue
            if artifact.get("collection_status"):
                raise ValueError("Cannot reuse an incomplete source collection")
            record = preserve(
                root, artifact["path"], (seed / artifact["path"]).read_bytes(), artifact["url"]
            )
            records.append({**artifact, **record})
    return records


def prepare(
    image: str,
    platform: str,
    root: Path,
    branch: str,
    project: Path,
    seed: Path | None = None,
    registry_reference: str | None = None,
) -> dict[str, Any]:
    if not re.fullmatch(r"v\d+\.\d+", branch):
        raise ValueError("Use a reviewed Alpine release branch such as v3.24")
    if registry_reference and not re.fullmatch(
        r"ghcr\.io/[a-z0-9/_-]+@sha256:[a-f0-9]{64}", registry_reference
    ):
        raise ValueError("Invalid intended public registry digest")
    inventory = collect_image(image, platform)
    root.mkdir(parents=True, exist_ok=False)
    artifacts = seed_sources(seed, root, inventory) if seed else []
    issues = []
    package_review = json.loads((project / "docs/runtime-package-review.json").read_text())
    review_keys = {key for key in coverage_keys(inventory) if not key.startswith("native:")}
    if package_review["platform"] != platform or review_keys != set(package_review["components"]):
        issues.append(
            "Package versions/licenses/build commits differ from reviewed runtime inventory"
        )
    for name in (
        "runtime-license-review.md",
        "runtime-native-review.md",
        "runtime-native-evidence.json",
        "runtime-native-sources.json",
        "runtime-package-review.json",
    ):
        artifacts.append(
            preserve(
                root,
                f"review/{name}",
                (project / "docs" / name).read_bytes(),
                "versioned project license review",
            )
        )
    artifacts.append(
        preserve(
            root,
            "inventory.json",
            (json.dumps(inventory, indent=2, sort_keys=True) + "\n").encode(),
            image,
        )
    )
    revision = (
        run_tool(
            "docker",
            [
                "image",
                "inspect",
                "--format",
                '{{index .Config.Labels "org.opencontainers.image.revision"}}',
                image,
            ],
        )
        .decode()
        .strip()
    )
    build_files = {}
    for name in ("Dockerfile", "uv.lock", "pyproject.toml", "LICENSE"):
        if re.fullmatch(r"[a-f0-9]{40}", revision):
            content = run_tool("git", ["-C", str(project), "show", f"{revision}:{name}"])
            provenance = f"https://github.com/Bigda7/jobradar/blob/{revision}/{name}"
        else:
            content, provenance = (project / name).read_bytes(), "uncommitted local build recipe"
        build_files[name] = content
        artifacts.append(preserve(root, f"jobradar-build/{name}", content, provenance))
    if not re.fullmatch(r"[a-f0-9]{40}", revision):
        issues.append("Image lacks a verified project source-revision label")
    lock = tomllib.loads(build_files["uv.lock"].decode("utf-8"))
    overrides_path = project / "docs" / "runtime-source-overrides.json"
    overrides = json.loads(overrides_path.read_text(encoding="utf-8"))
    artifacts.append(
        preserve(
            root,
            "review/source-overrides.json",
            overrides_path.read_bytes(),
            "reviewed local source mapping",
        )
    )
    origins = {}
    for package in inventory["apk"]:
        if package["name"] == ".python-rundeps":
            continue
        origins[(package["origin"], package["commit"])] = package
    for package in origins.values():
        print(f"Collecting Alpine origin: {package['origin']}", flush=True)
        try:
            records = collect_apk(root, package, branch, artifacts)
            artifacts.extend(
                record
                for record in records
                if record["path"] not in {item["path"] for item in artifacts}
            )
        except (OSError, ValueError) as error:
            issues.append(f"Alpine {package['origin']}: {error}")
    for package in inventory["python"]:
        if package["name"] == "jobradar":
            if not package["notices"]:
                issues.append("JobRadar: reviewed image lacks its installed project license")
            continue
        prefix = f"python/{safe_name(package['name'])}-{safe_name(package['version'])}"
        for index, notice in enumerate(package["notices"]):
            record = preserve(root, f"{prefix}/notices/{index}.txt", notice["text"].encode(), image)
            record["installed_path"] = notice["path"]
            artifacts.append(record)
        try:
            source = python_source(package, lock, overrides)
            expected = source["hash"]
            if not re.fullmatch(r"sha256:[a-f0-9]{64}", expected):
                raise ValueError("Invalid locked source checksum")
            filename = safe_name(urllib.parse.urlsplit(source["url"]).path.rsplit("/", 1)[-1])
            cached_source = root / prefix / filename
            content = cached_source.read_bytes() if cached_source.exists() else fetch(source["url"])
            if hashlib.sha256(content).hexdigest() != expected.split(":")[1]:
                raise ValueError("Python source checksum mismatch")
            artifacts.append(preserve(root, f"{prefix}/{filename}", content, source["url"]))
        except (OSError, ValueError) as error:
            issues.append(f"Python {package['name']}: {error}")
    artifacts.append(
        preserve(root, "cpython/LICENSE.txt", inventory["python_license"].encode(), image)
    )
    try:
        artifacts.extend(collect_python_base(root, image, inventory["python_version"]))
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        issues.append(f"CPython: {error}")
    native_prefixes: dict[str, str] = {}
    try:
        native_review = json.loads((project / "docs/runtime-native-sources.json").read_text())
        if native_review["platform"] != platform:
            raise ValueError("Native review does not cover this platform")
        records, native_prefixes = collect_native(root, inventory, native_review, artifacts)
        artifacts.extend(records)
    except (OSError, ValueError) as error:
        issues.append(f"Native source coverage: {error}")
    tracked = {artifact["path"] for artifact in artifacts}
    for file in sorted(root.rglob("*")):
        relative = file.relative_to(root).as_posix()
        if file.is_file() and relative not in tracked:
            artifacts.append(
                {
                    "path": relative,
                    "sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
                    "url": "incomplete source collection; see issues",
                    "collection_status": "incomplete",
                }
            )
    artifacts = list({artifact["path"]: artifact for artifact in artifacts}.values())
    coverage = make_coverage(inventory, artifacts, native_prefixes)
    manifest = {
        "schema": 1,
        "image": image,
        "platform": platform,
        "source_revision": revision,
        "complete": not issues,
        "coverage": coverage,
        "registry_reference": registry_reference,
        "description": "Source evidence draft, not a license-compliance certification",
        "artifacts": sorted(artifacts, key=lambda item: item["path"]),
        "issues": issues,
    }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def archive_bundle(root: Path) -> Path:
    destination = Path(str(root) + ".zip")
    # Fixed timestamps and permissions make the same directory produce the same ZIP bytes.
    with zipfile.ZipFile(destination, "x") as archive:
        for file in sorted(root.rglob("*")):
            if file.is_file():
                entry = zipfile.ZipInfo(file.relative_to(root).as_posix())
                entry.external_attr = 0o100644 << 16
                archive.writestr(entry, file.read_bytes())
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image")
    parser.add_argument("--platform", choices=("linux/arm64", "linux/amd64"), default="linux/arm64")
    parser.add_argument("--alpine-branch", default="v3.24")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--reuse-draft", type=Path)
    parser.add_argument("--registry-reference")
    args = parser.parse_args()
    try:
        if args.verify:
            verify_bundle(
                args.output,
                expected_image=args.image,
                expected_registry_reference=args.registry_reference,
            )
        else:
            if not args.image:
                parser.error("--image is required for preparation")
            manifest = prepare(
                args.image,
                args.platform,
                args.output,
                args.alpine_branch,
                Path(__file__).resolve().parents[1],
                args.reuse_draft,
                args.registry_reference,
            )
            archive_bundle(args.output)
            print(
                json.dumps(
                    {
                        "complete": manifest["complete"],
                        "artifacts": len(manifest["artifacts"]),
                        "issues": manifest["issues"],
                    },
                    indent=2,
                )
            )
            verify_bundle(args.output)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        parser.exit(1, f"Source-evidence preparation failed: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
