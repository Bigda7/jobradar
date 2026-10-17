"""Verify and optionally load the exact reviewed PostgreSQL fixture archives.

Only public release assets are downloaded. Archives are never extracted and
neither database containers nor publication actions are started by this tool.
"""

import argparse
import hashlib
import json
import shutil
import subprocess
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "deploy/postgres-fixture-images.json"
DOWNLOAD_HOSTS = {
    "github.com",
    "release-assets.githubusercontent.com",
    "objects.githubusercontent.com",
}


def public_url(url):
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in DOWNLOAD_HOSTS
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
        or parsed.fragment
    ):
        raise ValueError("Only approved public HTTPS release endpoints are supported")
    return url


class PublicRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return super().redirect_request(req, fp, code, msg, headers, public_url(newurl))


def metadata(architecture):
    return json.loads(MANIFEST.read_text(encoding="utf-8"))["images"][architecture]


def verify_archive(path, architecture):
    expected = metadata(architecture)
    if not path.is_file() or path.stat().st_size != expected["size"]:
        raise ValueError("PostgreSQL fixture archive size mismatch")
    with path.open("rb") as source:
        if hashlib.file_digest(source, "sha256").hexdigest() != expected["sha256"]:
            raise ValueError("PostgreSQL fixture archive checksum mismatch")
    with tarfile.open(path, "r:") as archive:
        index = read_json(archive, "index.json")
        if [item["digest"] for item in index["manifests"]] != [expected["image_id"]]:
            raise ValueError("PostgreSQL fixture archive index mismatch")
        manifests = read_json(archive, "manifest.json")
        config_path = "blobs/sha256/" + expected["config_id"].removeprefix("sha256:")
        if len(manifests) != 1 or manifests[0]["Config"] != config_path:
            raise ValueError("PostgreSQL fixture archive config identity mismatch")
        source = archive.extractfile(config_path)
        if source is None:
            raise ValueError("PostgreSQL fixture archive config is missing")
        with source:
            payload = source.read(1024 * 1024 + 1)
        if "sha256:" + hashlib.sha256(payload).hexdigest() != expected["config_id"]:
            raise ValueError("PostgreSQL fixture archive config checksum mismatch")
        config = json.loads(payload)
        if (
            config["os"] != "linux"
            or config["architecture"] != architecture
            or config["rootfs"]["diff_ids"] != [expected["layer_id"]]
        ):
            raise ValueError("PostgreSQL fixture archive platform or filesystem mismatch")


def read_json(archive, name):
    source = archive.extractfile(name)
    if source is None:
        raise ValueError("PostgreSQL fixture archive metadata is missing")
    with source:
        payload = source.read(1024 * 1024 + 1)
    if len(payload) > 1024 * 1024:
        raise ValueError("PostgreSQL fixture archive metadata is too large")
    return json.loads(payload)


def download_archive(directory, architecture):
    expected = metadata(architecture)
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / expected["archive"]
    if destination.exists():
        verify_archive(destination, architecture)
        return destination
    release_url = json.loads(MANIFEST.read_text(encoding="utf-8"))["release_url"]
    url = public_url(release_url + "/" + expected["archive"])
    opener = urllib.request.build_opener(PublicRedirect())
    request = urllib.request.Request(url, headers={"User-Agent": "JobRadar-fixture-verifier"})
    with tempfile.NamedTemporaryFile(dir=directory, suffix=".part", delete=False) as target:
        temporary = Path(target.name)
        try:
            deadline = time.monotonic() + 600
            with opener.open(request, timeout=30) as response:
                public_url(response.geturl())
                length = response.headers.get("Content-Length")
                if length is not None and int(length) != expected["size"]:
                    raise ValueError("PostgreSQL fixture response size mismatch")
                received = 0
                while block := response.read(1024 * 1024):
                    received += len(block)
                    if received > expected["size"] or time.monotonic() > deadline:
                        raise ValueError("PostgreSQL fixture download exceeded its bound")
                    target.write(block)
            target.close()
            verify_archive(temporary, architecture)
            temporary.replace(destination)
        finally:
            target.close()
            temporary.unlink(missing_ok=True)
    return destination


def installed_image(architecture):
    expected = metadata(architecture)
    executable = shutil.which("docker")
    if executable is None:
        raise ValueError("Docker is required to inspect the reviewed fixture")
    for candidate in (expected["image_id"], expected["config_id"]):
        result = subprocess.run(
            [executable, "image", "inspect", candidate],
            capture_output=True,
            check=False,
            timeout=30,
        )
        if result.returncode:
            continue
        record = json.loads(result.stdout)[0]
        if (
            record["Id"] != candidate
            or record["Os"] != "linux"
            or record["Architecture"] != architecture
            or record["RootFS"]["Layers"] != [expected["layer_id"]]
        ):
            raise ValueError("Installed PostgreSQL fixture identity mismatch")
        return candidate
    raise ValueError("The reviewed PostgreSQL fixture is not installed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("verify", "load", "image-id"))
    parser.add_argument("--architecture", choices=("amd64", "arm64"), required=True)
    paths = parser.add_mutually_exclusive_group()
    paths.add_argument("--archive", type=Path)
    paths.add_argument("--download-directory", type=Path)
    args = parser.parse_args()
    try:
        if args.action == "image-id":
            if args.archive or args.download_directory:
                raise ValueError("image-id does not download or load archives")
            print(installed_image(args.architecture))
            return
        if args.archive is None and args.download_directory is None:
            raise ValueError("An archive or explicit download directory is required")
        path = args.archive or download_archive(args.download_directory, args.architecture)
        verify_archive(path, args.architecture)
        if args.action == "load":
            executable = shutil.which("docker")
            if executable is None:
                raise ValueError("Docker is required to load the reviewed fixture")
            subprocess.run(
                [executable, "image", "load", "--input", str(path)],
                capture_output=True,
                check=True,
                timeout=600,
            )
            print(installed_image(args.architecture))
        else:
            print("PostgreSQL fixture archive identity and checksum verified.")
    except urllib.error.HTTPError as error:
        parser.exit(
            1,
            f"Public PostgreSQL fixture download returned HTTP {error.code}; "
            "no fallback image was selected.\n",
        )
    except (OSError, ValueError, KeyError, tarfile.TarError, subprocess.SubprocessError):
        # Redirect URLs may contain CDN credentials; do not print exception payloads.
        parser.exit(1, "PostgreSQL fixture verification failed; no fallback image was selected.\n")


if __name__ == "__main__":
    main()
