# Runtime Source Evidence Preparation

Status on 2026-10-03: native mappings and the pre-publication gate are implemented locally.
Publication and full legal certification are not claimed.
No dependency versions or the project's MIT license are changed by these tools.

## Automatic Runtime Notices

The Docker runtime stage executes `scripts/collect_runtime_licenses.py` after installing the
production virtual environment. It reads installed package files, the APK package database, and
CPython's license file. It never reads application environment settings, deployment credentials,
databases, logs, or vacancy data. All installed Python distributions must have nonempty notice
texts; a missing notice fails the runtime build. The original notice text is preserved in
`/usr/local/share/licenses/jobradar/third-party/NOTICE.txt`. The accompanying `inventory.json`
records package versions, declared licenses, APK build commits, native-library file hashes, and
wheel-embedded SBOMs. This covers Python notices, not all Alpine/native distribution obligations.

The audit-only `--allow-missing-project-license` flag records the known missing JobRadar notice in
the old v1.2.12 image. It cannot be combined with notice-output mode and does not relax Docker
build checks for new images. Missing third-party Python notices still fail collection.

## Prepare an Immutable Image's Source Bundle

Requirements: Docker, Git, Python 3.13, public HTTPS access, and the image's source commit in the
local Git repository. No new Python dependency is needed. Pull the intended platform before
collection; architecture and immutable reference syntax are checked.

```powershell
docker pull --platform linux/arm64 ghcr.io/bigda7/jobradar@sha256:183506dbc1db3bc6624a7892604871ccdaba6a7db7fa59a49a8acb7fe5876728
uv run --frozen python scripts/prepare_runtime_sources.py --image ghcr.io/bigda7/jobradar@sha256:183506dbc1db3bc6624a7892604871ccdaba6a7db7fa59a49a8acb7fe5876728 --output .deploy/runtime-source-draft-v1.2.12
uv run --frozen python scripts/prepare_runtime_sources.py --verify --output .deploy/runtime-source-draft-v1.2.12
```

Preparation returns **exit 1** for unresolved coverage, including the old v1.2.12 image's missing
own-project notice, even after producing a directory and ZIP. The manifest explains issues. Reuse of an
existing destination is rejected rather than overwriting previous evidence. Use a new destination
for another run. Downloads are limited to reviewed public HTTPS hosts and bounded in size/time;
redirects are checked too. Source archives are not executed or extracted onto the filesystem.
Only regular archived license/notice files are read into separately named evidence files.

Collection includes:

- Installed inventory from a read-only, network-disabled, capability-restricted audit container.
- JobRadar build files from the exact image source-revision label, not the current working tree.
  Images without this label can produce only explicitly unverified local-build evidence.
- Alpine origin recipes at the APK database's exact build commits, all listed upstream files,
  patches/configuration, and remaining recipe-directory helpers. Listed sources must match the
  recipe's SHA512 values. APKBUILD is parsed as data, never sourced or run.
- Installed Python packages' exact source distributions from the image revision's `uv.lock`,
  verified against locked SHA256 values. A package/version absent from that lock is an error.
- A narrowly versioned, checksummed upstream-repository override for Psycopg Binary 3.3.4, which
  has no PyPI source distribution. Its wheel build scripts accompany the separate native sources.
- Exact CPython source verified against the source hash recorded in image build history, plus
  the actual build-history instructions and upstream notices. Version mismatch fails collection.
- Source/license/build mappings for all 16 vendored native files: seven additional Alpine origin
  sets, PostgreSQL 18.0, GCC 12.4.0, and pinned cross-toolchain recipe/patch repositories.
  Full GPL and GCC runtime-exception texts are retained. See [native review](runtime-native-review.md)
  for exact versions, binary identification, and the unpinned upstream builder checkout limitation.
- A versioned reviewed package baseline and native file-hash mapping. New or changed components
  are rejected rather than silently assigned generic license coverage.

The manifest records a SHA256 for every saved artifact and ties it to the image digest/platform.
ZIP entry timestamps/permissions and ordering are fixed. This makes archives of identical evidence
byte-reproducible; it does not claim that every upstream binary build is bit-reproducible.

## Verification Boundary

`--verify` checks artifact integrity, rejects unlisted files and incomplete drafts. A manifest must
also map every inventoried APK/Python/native component and CPython version to preserved sources,
notices, and build evidence. Package/version/license changes or native-file changes require new
coverage. Changing only `complete` to true cannot bypass those checks.

The local `publish-image.yml` now builds an attested ARM64 OCI archive without pushing it,
verifies attestations, and imports that exact image into containerd-backed Docker storage.
It prepares sources for the immutable local image ID and binds the manifest to the intended
public registry digest. Both checks must pass before any release asset or image is uploaded.
The existing, public, non-draft GitHub release receives a source ZIP and SHA256 file without
overwriting assets. Their anonymous HTTPS availability is checked; only then is the same
verified image pushed without rebuilding. The image has an
`io.jobradar.corresponding-source` label pointing to its release asset. Old release commits
without these scripts/mappings cannot pass the workflow's initial check.

Containerd storage is enabled only on the ephemeral GitHub runner, not on production.
The workflow must be run after separately approved publication to verify actual GitHub/GHCR
delivery and digest preservation there. Release assets must remain publicly available for
the corresponding distributed images. Asset deletion or release removal is not a harmless
cleanup operation. Automated file/hash coverage is engineering evidence, not legal certification.

## Verified Draft on 2026-10-03

The ignored local directory `.deploy/runtime-source-draft-v1.2.12-20261003-r3` and its `.zip`
correspond to the public v1.2.12 ARM64 digest above and source revision `3b4dfb8`.
It contains 322 checksummed artifacts: all 19 installed Alpine origin recipes/source sets,
31 locked Python source distributions, the pinned Psycopg 3.3.4 repository, CPython 3.13.15
sources/build history, and preserved notice evidence. The old image's missing own-project notice
is recorded rather than hidden. The local candidate fixes that packaging defect.

The subsequent `.deploy/runtime-source-native-review-v1.2.12-20261003` source set contains
512 checksummed artifacts and source/notice/build coverage for every inventoried component,
including all 16 native files. Its sole recorded issue is the old image's missing installed
JobRadar notice; the gate correctly rejects it. This source set predates the final explanatory
review/evidence documents and is retained as an audit snapshot, not a release-ready asset.

A new image must be built from the committed local fix with the correct revision label.
Only a candidate with unchanged reviewed dependency/native inventory and its own project notice
can pass the readiness check. Publication of sources for the old digest still needs separate
approval and an explicit historical-notice remedy; a new image does not alter old bytes.

No source draft, notice image, release, or code changes were published or deployed in this work.

## Verified Local Candidate

The `.deploy/runtime-source-candidate-reviewed-20261003` directory and ZIP passed the complete
gate with 535 checksummed artifacts, 78 third-party component coverage records, and all 16
native file mappings. There were no collection issues. This candidate's actual source revision
is `8368090d2e7074569b7662c440917ac01121b690`; later collector/workflow-only corrections do not
change its application or runtime build inputs. The ARM64 OCI archive's SBOM/provenance were
verified and its index digest survived Docker import unchanged:

```text
sha256:9162a58074fe862edbac3245d5db4fc3b0350448d65fbcedd7ad2eb5c784f24d
```

The ZIP is 440,505,541 bytes with SHA256
`ede11aac10dd7848abf3e2a68b4af766bd10c218033da4be81d1adb38393ed90`.
It is local audit evidence, not an uploaded release asset. The candidate's source URL label
uses a non-published local-validation placeholder and must not be used for deployment.
The release workflow generates the real URL for an approved tag and verifies access there.
