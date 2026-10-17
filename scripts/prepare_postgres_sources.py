"""Prepare exact-source evidence for the approved local PostgreSQL images.

Public source archives and recipes are treated as data, never executed. This is
an engineering evidence gate, not a legal-compliance or reproducible-build certificate.
"""

import argparse
import base64
import hashlib
import io
import json
import re
import tarfile
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path, PurePosixPath

from scripts import prepare_runtime_sources as sources

ROOT = Path(__file__).resolve().parents[1]
IMAGES = {
    "amd64": "sha256:ad68598f8c2901e3fc40dbbe77d85b85ed21a87eecec830ebf4b6782ff5c34b6",
    "arm64": "sha256:51958852a6c4b54ffd5f768cf4e6e6a7786ffe1b78a30dbd638b835381de0e41",
}
BASE_REVISION = "2603e26e245e558218728ee14e0a42dcb020dc7f"
PG_SHA256 = "dd27f2b3c59e73ed14aa3324901242bf69a032a6347805f274e6260322d42979"
GO_SHA256 = "9735d7dcdb65b35d3fa577f04064737c03b89cf1a2b71e6e69fe2f3c6f9fd4ca"
MODULES = {
    "github.com/moby/sys/user": ("v0.4.1", "h1:RgjRlaDKi/Xmyrz4t8lyzXT6v2ooFeO/7xtchmhVWE0="),
    "golang.org/x/sys": ("v0.49.0", "h1:XbzkgYJHdqh/8m2Uu0W/dQv8nktxx4BFHp1M0gROTXA="),
}
EXTRA_HOSTS = {
    "ftp.postgresql.org",
    "go.dev",
    "dl.google.com",
    "proxy.golang.org",
    "storage.googleapis.com",
}
LABEL = "jobradar.postgres.sources.run"
BUILD_ROOTS = ("deploy/postgres-prototype",)
BUILD_FILES = (
    "scripts/prepare_postgres_sources.py",
    "scripts/prepare_runtime_sources.py",
    "LICENSE",
    "docs/postgres-source-bundle.md",
)
LICENSES = {
    "MIT": "b05785f9f18e6716bab63424b11454513b9943a222595b70411009202fc592b5",
    "GPL-2.0-only": "aaf135472f81c5b4a0dca9367e5bb5e9750032b5bebe5442b36e4c0a47430df3",
    "MPL-2.0": "66a3107d5ad6a058aab753eaac2047ccb2ed0e39465dd0fe5844da3e300d5172",
}
# Public Alpine aports revision for the APK verification-key package.
APORTS_TRUST_STORE_REVISION = "b9f23becced4d7b3ccc0fa0f28530243ccd314a0"
INLINE_NOTICES = {
    ("alpine-baselayout", "60a7585bbab2fa0f762504eb617dbca90216e31f"): ("GPL-2.0-only",),
    ("alpine-keys", APORTS_TRUST_STORE_REVISION): ("MIT",),
    ("alpine-base", "0934530484bbcde7498e2c694c710a49616a450e"): ("MIT",),
    ("krb5-conf", "4a46b2cb955e25fd6d6f913794d35bc6d876aea9"): ("MIT",),
    ("ca-certificates", "e41cbd2ae991adfe8df298ba8e8e777e90bd0e03"): ("MPL-2.0", "MIT"),
}


def safe_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in sources.ALLOWED_HOSTS | EXTRA_HOSTS
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
        or parsed.fragment
    ):
        raise ValueError("Source URL is not an allowed public HTTPS endpoint")
    return url


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return super().redirect_request(req, fp, code, msg, headers, safe_url(newurl))


def fetch(url: str) -> bytes:
    request = urllib.request.Request(
        safe_url(url), headers={"User-Agent": "JobRadar-source-review"}
    )
    with urllib.request.build_opener(SafeRedirect()).open(request, timeout=45) as response:
        data = response.read(sources.MAX_DOWNLOAD + 1)
    if len(data) > sources.MAX_DOWNLOAD:
        raise ValueError("Source download exceeds the size limit")
    return data


def audit(architecture: str, entrypoint: str, arguments: list[str]) -> bytes:
    owner = uuid.uuid4().hex
    command = [
        "run",
        "--rm",
        "--pull",
        "never",
        "--name",
        f"jobradar-pg-sources-{owner}",
        "--label",
        f"{LABEL}={owner}",
        "--platform",
        f"linux/{architecture}",
        "--network",
        "none",
        "--read-only",
        "--tmpfs",
        "/var/lib/postgresql/data:rw,noexec,nosuid,size=1m",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--memory",
        "128m",
        "--memory-swap",
        "128m",
        "--pids-limit",
        "32",
        "--entrypoint",
        entrypoint,
        IMAGES[architecture],
        *arguments,
    ]
    try:
        return sources.run_tool("docker", command)
    finally:
        remaining = (
            sources.run_tool("docker", ["ps", "-aq", "--filter", f"label={LABEL}={owner}"])
            .decode()
            .strip()
        )
        if remaining:
            if not re.fullmatch(r"[a-f0-9]{12,64}", remaining):
                raise ValueError("Unexpected source-audit container ownership")
            sources.run_tool("docker", ["rm", "--force", "--volumes", remaining])


def inventory(architecture: str) -> dict:
    image = IMAGES[architecture]
    actual = sources.run_tool(
        "docker", ["image", "inspect", "--format", "{{.Id}} {{.Architecture}}", image]
    )
    if actual.decode().strip() != f"{image} {architecture}":
        raise ValueError("Exact approved PostgreSQL image/platform is required")
    command = (
        "awk '/^[PVLociI]:/ {print} /^$/ {print \"\"}' /lib/apk/db/installed; "
        'printf \'\\nPOSTGRES_FIELDS\\n%s\\n%s\\n\' "$PG_VERSION" "$PG_SHA256"; '
        "printf 'BINARIES\\n'; sha256sum /usr/local/bin/postgres /usr/local/bin/gosu "
        "/usr/local/bin/docker-entrypoint.sh /usr/local/bin/docker-ensure-initdb.sh "
        "/usr/lib/libxml2.so.2.13.9"
    )
    raw = audit(architecture, "/bin/sh", ["-ec", command]).decode()
    apk, fields = raw.split("\nPOSTGRES_FIELDS\n", 1)
    version, checksum, binaries = fields.strip().split("\n", 2)
    if version != "17.11" or checksum != PG_SHA256 or not binaries.startswith("BINARIES\n"):
        raise ValueError("PostgreSQL source identity differs from the reviewed image")
    packages = []
    for block in apk.strip().split("\n\n"):
        values = dict(line.split(":", 1) for line in block.splitlines() if ":" in line)
        if not values:
            continue
        name = values["P"]
        package = {
            "name": name,
            "version": values["V"],
            "license": values.get("L", ""),
            "origin": values.get("o", ""),
            "commit": values.get("c", ""),
        }
        if name == ".postgresql-rundeps":
            if values.get("I") != "0" or package["origin"] or package["license"]:
                raise ValueError(
                    "Virtual PostgreSQL dependency package unexpectedly contains payload"
                )
        elif (
            not package["license"]
            or not package["origin"]
            or (
                not re.fullmatch(r"[a-f0-9]{40}", package["commit"])
                and (name, package["version"], package["commit"]) != ("libxml2", "2.13.9-r200", "")
            )
        ):
            raise ValueError("Unreviewed PostgreSQL package provenance")
        packages.append(package)
    hashes = {}
    for line in binaries.removeprefix("BINARIES\n").splitlines():
        digest, name = line.split("  ", 1)
        if not re.fullmatch(r"[a-f0-9]{64}", digest) or name in hashes:
            raise ValueError("Invalid installed binary identity")
        hashes[name] = digest
    if len(packages) != len({p["name"] for p in packages}) or len(hashes) != 5:
        raise ValueError("Incomplete or duplicate PostgreSQL inventory")
    return {
        "image": image,
        "architecture": architecture,
        "postgres_version": version,
        "postgres_source_sha256": checksum,
        "apk": sorted(packages, key=lambda p: p["name"]),
        "installed_sha256": hashes,
    }


def unpack_evidence(content: bytes, root: Path) -> list[dict]:
    records = []
    allowed = ("gosu/source/", "gosu/licenses/", "gosu/metadata/", "libxml2/source/")
    total = 0
    seen = set()
    with tarfile.open(fileobj=io.BytesIO(content)) as archive:
        for item in archive:
            name = item.name.removeprefix("./")
            parts = PurePosixPath(name).parts
            if PurePosixPath(name).is_absolute() or ".." in parts or "\\" in name:
                raise ValueError("Unsafe embedded evidence path")
            if item.isdir():
                continue
            if name in seen:
                raise ValueError("Duplicate embedded evidence member")
            seen.add(name)
            if not item.isfile() or (not name.startswith(allowed) and name != "libxml2/LICENSE"):
                raise ValueError("Unexpected embedded evidence member")
            total += item.size
            if item.size > 64 * 1024**2 or total > 128 * 1024**2 or len(records) >= 4000:
                raise ValueError("Embedded evidence exceeds limits")
            stream = archive.extractfile(item)
            if stream is None:
                raise ValueError("Embedded source cannot be read")
            records.append(
                sources.preserve(root, "embedded/" + name, stream.read(), "exact approved image")
            )
    return records


def embedded(architecture: str, root: Path) -> list[dict]:
    command = [
        "-C",
        "/usr/local/share/jobradar-postgres-prototype",
        "-cf",
        "-",
        "gosu/source",
        "gosu/licenses",
        "gosu/metadata",
        "libxml2/source",
        "libxml2/LICENSE",
    ]
    return unpack_evidence(audit(architecture, "tar", command), root)


def module_hash(data: bytes, module: str, version: str) -> str:
    lines = []
    seen = set()
    total = 0
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for item in sorted(archive.infolist(), key=lambda item: item.filename):
            name = item.filename
            total += item.file_size
            if (
                name in seen
                or "\n" in name
                or "\\" in name
                or item.is_dir()
                or not name.startswith(f"{module}@{version}/")
                or ".." in PurePosixPath(name).parts
                or item.file_size > 32 * 1024**2
                or total > 128 * 1024**2
                or len(seen) >= 20000
                or (item.external_attr >> 16) & 0o170000 not in (0, 0o100000)
            ):
                raise ValueError("Invalid Go module source archive")
            seen.add(name)
            lines.append(f"{hashlib.sha256(archive.read(item)).hexdigest()}  {name}\n")
    if not lines:
        raise ValueError("Empty Go module archive")
    return "h1:" + base64.b64encode(hashlib.sha256("".join(lines).encode()).digest()).decode()


def archive(root: Path, prefix: str, name: str, url: str, expected: str) -> list[dict]:
    content = fetch(url)
    if hashlib.sha256(content).hexdigest() != expected:
        raise ValueError(f"Source checksum mismatch: {name}")
    records = [sources.preserve(root, f"{prefix}/{name}", content, url)]
    for index, (member, notice) in enumerate(sorted(sources.archive_notices(content).items())):
        record = sources.preserve(root, f"{prefix}/notices/{index}.txt", notice, url)
        record["archive_member"] = member
        records.append(record)
    return records


def reuse_alpine(root: Path, seed: Path, wanted: set[str]) -> list[dict]:
    manifest = json.loads((seed / "manifest.json").read_text(encoding="utf-8"))
    sources.verify_artifacts(seed, manifest)
    records = []
    incomplete = {
        "/".join(a["path"].split("/")[:2])
        for a in manifest["artifacts"]
        if a.get("collection_status")
    }
    for artifact in manifest["artifacts"]:
        if any(artifact["path"].startswith(prefix + "/") for prefix in wanted - incomplete):
            record = sources.preserve(
                root, artifact["path"], (seed / artifact["path"]).read_bytes(), artifact["url"]
            )
            records.append({**artifact, **record})
    return records


def verify_embedded_recipe(root: Path) -> None:
    prefix = root / "embedded/libxml2/source"
    for item in prefix.rglob("*"):
        if item.is_file() and item.name != "source.tar.xz":
            recipe = root / "recipe/deploy/postgres-prototype/libxml2" / item.relative_to(prefix)
            if not recipe.is_file() or item.read_bytes() != recipe.read_bytes():
                raise ValueError("Installed libxml2 source differs from the source recipe")
    gosu = root / "embedded/gosu/source/gosu-1.19.tar.gz"
    if hashlib.sha256(gosu.read_bytes()).hexdigest() != (
        "33d7537d588ea49458b9509bcf4554bdf5ceacc66da71e5caa1058ea3b689c3b"
    ):
        raise ValueError("Installed gosu source differs from the reviewed upstream archive")


def failure_reason(error: Exception) -> str:
    if isinstance(error, urllib.error.HTTPError):
        return f"HTTP {error.code}"
    return str(error) if isinstance(error, ValueError) else type(error).__name__


def recipe_files(revision: str | None) -> list[str]:
    args = ["-C", str(ROOT)]
    if revision:
        if not re.fullmatch(r"[a-f0-9]{40}", revision):
            raise ValueError("A full real Git commit is required")
        sources.run_tool("git", [*args, "cat-file", "-e", f"{revision}^{{commit}}"])
        names = (
            sources.run_tool(
                "git",
                [*args, "ls-tree", "-r", "--name-only", revision, "--", *BUILD_ROOTS, *BUILD_FILES],
            )
            .decode()
            .splitlines()
        )
    else:
        names = (
            sources.run_tool(
                "git",
                [
                    *args,
                    "ls-files",
                    "--cached",
                    "--others",
                    "--exclude-standard",
                    "--",
                    *BUILD_ROOTS,
                    *BUILD_FILES,
                ],
            )
            .decode()
            .splitlines()
        )
    if not set(BUILD_FILES).issubset(names):
        raise ValueError("Source revision does not contain the full packaging tools")
    return names


def component_keys(inv: dict) -> set[str]:
    return {f"apk:{p['name']}:{p['version']}:{p['license']}:{p['commit']}" for p in inv["apk"]} | {
        "postgres:17.11",
        "gosu:1.19",
        "toolchain:go1.26.9",
        *[f"go:{name}:{value[0]}" for name, value in MODULES.items()],
    }


def component_coverage(inv: dict, paths: set[str], names: list[str]) -> dict:
    coverage = {}
    for key in component_keys(inv):
        if key.startswith("apk:"):
            package = next(
                p
                for p in inv["apk"]
                if key == f"apk:{p['name']}:{p['version']}:{p['license']}:{p['commit']}"
            )
            prefix = (
                "upstream/"
                if package["name"] == ".postgresql-rundeps"
                else "embedded/libxml2/"
                if package["name"] == "libxml2"
                else f"alpine/{package['origin']}-{package['commit']}/"
            )
        elif key.startswith("go:"):
            _, module, version = key.split(":")
            prefix = f"modules/{module}@{version}/"
        else:
            prefix = {
                "postgres:17.11": "postgres/",
                "gosu:1.19": "embedded/gosu/",
                "toolchain:go1.26.9": "go/",
            }[key]
        evidence = sorted(p for p in paths if p.startswith(prefix))
        notices = [
            p
            for p in evidence
            if "/notices/" in p
            or PurePosixPath(p).name
            in {
                "LICENSE",
                "GO-LICENSE",
                "GOSU-LICENSE",
                "MOBY-USER-LICENSE",
                "X-SYS-LICENSE",
            }
        ]
        source_paths = [p for p in evidence if "/notices/" not in p]
        if key.startswith("apk:"):
            for license_id in INLINE_NOTICES.get((package["origin"], package["commit"]), ()):
                if f"licenses/{license_id}.txt" in paths:
                    notices += [
                        f"licenses/{license_id}.txt",
                        "recipe/docs/postgres-source-bundle.md",
                        *source_paths,
                    ]
        coverage[key] = {
            "source_paths": source_paths,
            "notice_paths": notices,
            "build_paths": ["recipe/" + name for name in names],
            "reviewed": bool(source_paths and notices),
        }
    return coverage


def verify_source_payload(root: Path, inv: dict) -> None:
    origins = {(p["origin"], p["commit"]) for p in inv["apk"] if p["commit"]}
    for origin, commit in origins:
        prefix = root / f"alpine/{origin}-{commit}"
        text = (prefix / "APKBUILD").read_text(encoding="utf-8")
        if origin in {"alpine-base", "krb5-conf"} and not re.search(
            r"^(source|sha512sums)=", text, re.M
        ):
            continue
        for name, expected in sources.apk_checksums(text).items():
            with (prefix / sources.safe_name(name)).open("rb") as stream:
                if hashlib.file_digest(stream, "sha512").hexdigest() != expected:
                    raise ValueError("Alpine source payload checksum mismatch")
    for name, expected in {
        "postgres/postgresql-17.11.tar.bz2": PG_SHA256,
        "go/go1.26.9.src.tar.gz": GO_SHA256,
        **{f"licenses/{n}.txt": h for n, h in LICENSES.items()},
    }.items():
        with (root / name).open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                raise ValueError("Pinned upstream source/notice checksum mismatch")
    sums = (root / "embedded/gosu/source/go.sum").read_text()
    for module, (version, expected) in MODULES.items():
        if f"{module} {version} {expected}\n" not in sums:
            raise ValueError("Embedded module graph mismatch")
        data = (root / f"modules/{module}@{version}/source.zip").read_bytes()
        if module_hash(data, module, version) != expected:
            raise ValueError("Go module source h1 mismatch")
    for name in ("docker-entrypoint.sh", "docker-ensure-initdb.sh"):
        if (
            hashlib.sha256((root / "upstream" / name).read_bytes()).hexdigest()
            != inv["installed_sha256"][f"/usr/local/bin/{name}"]
        ):
            raise ValueError("Entrypoint source does not match the installed image")


def prepare(architecture: str, root: Path, revision: str | None, seed: Path | None) -> dict:
    inv = inventory(architecture)
    names = recipe_files(revision)
    root.mkdir(parents=True, exist_ok=False)
    records = embedded(architecture, root)
    issues = [] if revision else ["Missing committed source recipe revision"]
    for name in names:
        data = (
            sources.run_tool("git", ["-C", str(ROOT), "show", f"{revision}:{name}"])
            if revision
            else (ROOT / name).read_bytes()
        )
        records.append(
            sources.preserve(
                root, f"recipe/{name}", data, f"git:{revision or 'uncommitted'}:{name}"
            )
        )
    verify_embedded_recipe(root)
    records.append(
        sources.preserve(
            root,
            "inventory.json",
            (json.dumps(inv, indent=2, sort_keys=True) + "\n").encode(),
            inv["image"],
        )
    )
    origins = {(p["origin"], p["commit"]): p for p in inv["apk"] if p["commit"]}
    for package in origins.values():
        if package["origin"] == "nss_wrapper":
            package["repository"] = "community"
    if seed:
        records.extend(reuse_alpine(root, seed, {f"alpine/{o}-{c}" for o, c in origins}))
    for package in origins.values():
        print(f"Collecting PostgreSQL Alpine origin: {package['origin']}", flush=True)
        try:
            records.extend(sources.collect_apk(root, package, "v3.24", records))
        except (OSError, ValueError) as error:
            issues.append(f"Alpine {package['origin']}: {failure_reason(error)}")
    try:
        for name, checksum in LICENSES.items():
            url = (
                f"https://raw.githubusercontent.com/spdx/license-list-data/v3.27.0/text/{name}.txt"
            )
            data = fetch(url)
            if hashlib.sha256(data).hexdigest() != checksum:
                raise ValueError("Supplemental license text checksum mismatch")
            records.append(sources.preserve(root, f"licenses/{name}.txt", data, url))
        records.extend(
            archive(
                root,
                "postgres",
                "postgresql-17.11.tar.bz2",
                "https://ftp.postgresql.org/pub/source/v17.11/postgresql-17.11.tar.bz2",
                PG_SHA256,
            )
        )
        for name in [
            "17/alpine3.24/Dockerfile",
            "docker-entrypoint.sh",
            "docker-ensure-initdb.sh",
            "LICENSE",
        ]:
            url = (
                f"https://raw.githubusercontent.com/docker-library/postgres/{BASE_REVISION}/{name}"
            )
            content = fetch(url)
            installed = inv["installed_sha256"].get(f"/usr/local/bin/{name}")
            if installed and hashlib.sha256(content).hexdigest() != installed:
                raise ValueError("Installed upstream entrypoint differs from the pinned source")
            if name.endswith("Dockerfile") and PG_SHA256.encode() not in content:
                raise ValueError(
                    "Pinned Dockerfile does not identify the installed PostgreSQL source"
                )
            records.append(sources.preserve(root, "upstream/" + name, content, url))
        records.extend(
            archive(
                root,
                "go",
                "go1.26.9.src.tar.gz",
                "https://go.dev/dl/go1.26.9.src.tar.gz",
                GO_SHA256,
            )
        )
        sums = (root / "embedded/gosu/source/go.sum").read_text()
        for module, (version, expected) in MODULES.items():
            if f"{module} {version} {expected}\n" not in sums:
                raise ValueError("Embedded Go module checksums differ from the reviewed graph")
            url = f"https://proxy.golang.org/{module}/@v/{version}.zip"
            data = fetch(url)
            if module_hash(data, module, version) != expected:
                raise ValueError("Go module h1 checksum mismatch")
            prefix = f"modules/{module}@{version}"
            records.append(sources.preserve(root, prefix + "/source.zip", data, url))
            with zipfile.ZipFile(io.BytesIO(data)) as zipped:
                license_name = f"{module}@{version}/LICENSE"
                records.append(
                    sources.preserve(root, prefix + "/LICENSE", zipped.read(license_name), url)
                )
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        issues.append(f"Non-APK source collection: {failure_reason(error)}")
    records = list({r["path"]: r for r in records}.values())
    tracked = {r["path"] for r in records}
    for item in sorted(root.rglob("*")):
        if item.is_file() and item.relative_to(root).as_posix() not in tracked:
            relative = item.relative_to(root).as_posix()
            record = sources.preserve(
                root, relative, item.read_bytes(), "incomplete source collection"
            )
            record["collection_status"] = "incomplete"
            records.append(record)
    paths = {r["path"] for r in records}
    coverage = component_coverage(inv, paths, names)
    for key, review in coverage.items():
        if not review["reviewed"]:
            issues.append(f"Missing source/notice coverage: {key}")
    manifest = {
        "schema": 1,
        "kind": "jobradar-postgres-sources",
        "image": inv["image"],
        "architecture": architecture,
        "recipe_revision": revision,
        "recipe_files": names,
        "upstream_base_revision": BASE_REVISION,
        "description": (
            "Exact source evidence; not legal certification or byte-reproducible binaries"
        ),
        "complete": not issues,
        "issues": issues,
        "coverage": coverage,
        "artifacts": sorted(records, key=lambda r: r["path"]),
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def verify_bundle(
    root: Path, architecture: str, revision: str, live_inventory: dict | None = None
) -> None:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    paths = sources.verify_artifacts(root, manifest)
    if (
        manifest.get("kind") != "jobradar-postgres-sources"
        or manifest.get("image") != IMAGES[architecture]
        or manifest.get("architecture") != architecture
        or manifest.get("recipe_revision") != revision
        or not re.fullmatch(r"[a-f0-9]{40}", revision)
        or not manifest.get("complete")
        or manifest.get("issues")
    ):
        raise ValueError("PostgreSQL source bundle identity/revision/readiness gate failed")
    inv = json.loads((root / "inventory.json").read_text(encoding="utf-8"))
    if inv != (live_inventory if live_inventory is not None else inventory(architecture)):
        raise ValueError("Source inventory differs from the exact image")
    if any(record.get("collection_status") for record in manifest["artifacts"]):
        raise ValueError("Incomplete source artifacts cannot be approved")
    verify_embedded_recipe(root)
    if set(manifest["coverage"]) != component_keys(inv):
        raise ValueError("Incomplete PostgreSQL component coverage")
    if manifest["coverage"] != component_coverage(inv, paths, manifest["recipe_files"]):
        raise ValueError("Component evidence points to unrelated source files")
    for review in manifest["coverage"].values():
        if review.get("reviewed") is not True:
            raise ValueError("Unreviewed PostgreSQL component")
        for field in ("source_paths", "notice_paths", "build_paths"):
            if not review.get(field) or not set(review[field]).issubset(paths):
                raise ValueError("Missing PostgreSQL source/notice/build evidence")
    if manifest["recipe_files"] != recipe_files(revision):
        raise ValueError("Build recipe file inventory differs from the commit")
    for name in manifest["recipe_files"]:
        expected = sources.run_tool("git", ["-C", str(ROOT), "show", f"{revision}:{name}"])
        if (root / "recipe" / name).read_bytes() != expected:
            raise ValueError("Build recipe differs from the committed source")
    verify_source_payload(root, inv)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--architecture", choices=tuple(IMAGES), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--revision")
    parser.add_argument("--reuse-draft", type=Path)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.verify:
        if not args.revision:
            parser.error("--verify requires the real source --revision")
        verify_bundle(args.output, args.architecture, args.revision)
        print("PostgreSQL exact-source bundle verified")
        return
    manifest = prepare(args.architecture, args.output, args.revision, args.reuse_draft)
    sources.archive_bundle(args.output)
    print(
        json.dumps(
            {
                "complete": manifest["complete"],
                "artifacts": len(manifest["artifacts"]),
                "components": len(manifest["coverage"]),
                "issues": manifest["issues"],
            },
            indent=2,
        )
    )
    if not manifest["complete"]:
        raise SystemExit(1)
    verify_bundle(args.output, args.architecture, args.revision)


if __name__ == "__main__":
    main()
