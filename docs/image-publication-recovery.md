# Verified image publication and recovery

## Failure addressed

The v1.2.21 publication stopped at a GHCR push connection EOF after corresponding
sources had been published. The exact OCI candidate was not retained. Rebuilding
that tag can produce a different digest and must not overwrite its source evidence.
This change does not recover the already lost v1.2.21 candidate. Publish a new version
only after explicit owner authorization and all normal release gates.

## Publication changes

After building and verifying the OCI archive and corresponding sources, and after
checking public source availability, the workflow uploads three additional assets
to the same existing public release without `--clobber`:

- `runtime-image-vX.Y.Z-linux-arm64.oci.tar`: the exact verified OCI archive.
- `runtime-image-vX.Y.Z-linux-arm64.build.json`: its Buildx metadata and index digest.
- `runtime-image-vX.Y.Z-linux-arm64.sha256`: both asset checksums.

These assets contain the distributable image and build evidence, not environment
files, credentials or production data. They add public release storage and download
size approximately equal to one OCI archive. Retain them with the corresponding
source assets; do not remove source evidence while distributing the image.

`scripts/push_verified_image.py` makes at most three pushes, waiting 10 and 30 seconds
between failures, with a five-minute timeout for each push. Before **every** attempt
it checks the local tag's index identity against the already verified digest.
A changed/missing identity stops publication. There is no rebuild, retag or source
overwrite. Registry provenance/SBOM verification still follows a successful push.

## Recovery procedure for the engineering agent

This is a separate publication operation requiring authorization, not a production
deployment. Do not rerun the build job blindly. Use a clean temporary directory and
the exact release commit containing these scripts. With existing authenticated
GitHub access, download the three recovery assets and the corresponding source ZIP
and checksum from that exact repository/release. Never accept assets from another
tag, an arbitrary URL or a user-supplied archive.

1. Verify both recovery checksums and the source ZIP checksum. Read checksum paths
   before using them; require only the two expected recovery filenames and the
   expected source filename, with no absolute paths or parent components.
2. Verify `containerimage.digest` in the saved build metadata. Verify the OCI
   provenance/SBOM with `scripts/verify_image_attestations.py` before loading it.
3. Safely unpack and verify the source bundle with `prepare_runtime_sources.verify_bundle`,
   requiring `expected_image` to equal that digest and `expected_registry_reference`
   to equal the original GHCR repository plus that digest. Reject unsafe ZIP paths,
   duplicates, unexpected entries or incomplete source coverage.
4. On an isolated runner with containerd image storage, load the retained OCI archive.
   Verify the loaded release tag's identity, ARM64 platform, source URL and release
   revision. Do not rebuild or fix a mismatch by retagging another image.
5. Recheck anonymous corresponding-source availability, authenticate to GHCR, then
   run `python scripts/push_verified_image.py ghcr.io/bigda7/jobradar:vX.Y.Z sha256:...`
   with the actual verified digest. Verify the immutable registry index and
   provenance/SBOM using `scripts/verify_registry_image.py` afterward.
6. Run the final unfiltered security scan and independent source verification before
   any deployment. Recovery assets or a successful push do not waive those gates,
   the fresh restored/encrypted backup, rollout rehearsal or notification checks.

No automatic recovery dispatch is added. The agent performs recovery from retained
assets when needed; the owner does not need to find or verify files manually.
An upload failure before the complete recovery set exists remains a failed release,
not proof of recoverability. The release workflow has not been executed for this
local package, so actual GitHub/GHCR delivery remains unverified until publication.
