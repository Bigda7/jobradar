# Local PostgreSQL image reassembly

This owner-approved local package removes superseded compiled files from the
distributable image layers without changing the approved runtime filesystem.
It does not remove existing Docker images/caches, install dependencies, change
image pins, publish an image or access production.

## Build and verify

From the backend root, using the existing Docker Desktop and locked Python tools:

```powershell
.venv/Scripts/python.exe deploy/postgres-prototype/clean/build_candidate.py --architecture amd64
.venv/Scripts/python.exe deploy/postgres-prototype/clean/build_candidate.py --architecture arm64
.venv/Scripts/python.exe -m pytest tests/unit/test_postgres_clean.py -q
```

The helper accepts only the exact final local libxml2 image identities in `INPUTS`.
It checks architecture, the complete supported image configuration and environment
names before generating a Dockerfile in an owned `.deploy/postgres-clean-20261016/`
directory. Environment values are not printed. No secret or unknown environment
variable is accepted. Existing inputs are not rebuilt or retagged.

The generated Dockerfile uses the digest-qualified local input as a source stage,
then `FROM scratch` with `COPY --from=source / /`. The final image receives only the
merged current filesystem, not the source image's 22 historical layers. All eight
environment entries (including literal whitespace), startup commands, working
directory, labels, port, volume and SIGINT stop signal are reconstructed from the
verified configuration. No packages are added, upgraded or intentionally removed.

BuildKit adds `ArgsEscaped=true`; Docker documents this as Windows-specific.
The helper permits only a boolean value for Linux, records the metadata difference
and compares every other configuration field exactly. See
[Docker's configuration definition](https://github.com/moby/moby/blob/master/api/types/container/config.go).
This is runtime-configuration equivalence, not byte-identical image configuration.

The helper verifies one fresh rootfs layer with no shared old layer digest. It
exports two newly created, never-started, network-disabled labeled containers and
compares every filesystem path: file content hashes, node types, permissions,
numeric ownership, symlink/hardlink targets, device numbers and exported extended
attributes. It never extracts an archive. Database data files, duplicate entries,
unsafe paths, unexpected metadata/configuration or differences fail verification.

Directory modification times and timestamps on six Docker runtime placeholders
can change during creation/copy/export. Only those timestamp differences are
allowed, and their paths are reported; file contents, permissions and ownership
are still checked. Other file timestamps must match. Raw manifests retain all
timestamps. Docker export does not include mounted volume data; the never-started
fixtures contain no initialized database. Runtime database behavior is checked
separately, not inferred from the export.

Both task containers and their newly allocated anonymous volumes are removed after
ownership verification. Successful temporary rootfs tar exports are removed after
manifests are saved; failure evidence is retained. No broad Docker prune is used.

## Runtime acceptance

Run the existing checkers with `jobradar-postgres-clean:<architecture>-20261016`:

```powershell
.venv/Scripts/python.exe deploy/postgres-prototype/check_candidate.py --architecture amd64 --candidate-image jobradar-postgres-clean:amd64-20261016
.venv/Scripts/python.exe deploy/postgres-prototype/libxml2/check_xml.py --architecture amd64 --candidate-image jobradar-postgres-clean:amd64-20261016
.venv/Scripts/python.exe deploy/postgres-prototype/check_tls.py --architecture amd64 --candidate-image jobradar-postgres-clean:amd64-20261016
.venv/Scripts/python.exe deploy/postgres-prototype/gosu/check_runtime.py --architecture amd64 --candidate-image jobradar-postgres-clean:amd64-20261016
```

Use matching ARM64 arguments and add `--emulated-arm64` to its gosu checker on this
workstation. Emulation does not verify the two native setid checks.
On 2026-10-17, both guards passed on real aarch64 hardware for the exact clean image
`sha256:51958852a6c4b54ffd5f768cf4e6e6a7786ffe1b78a30dbd638b835381de0e41`.
The isolated checks required successful ordinary identity switching followed by the
exact upstream setuid/setgid rejection messages. Existing production container IDs,
restart counts and health states were unchanged; owned containers, the imported
image and temporary archive were removed. This was not a database/server rollout
or whole-host resource acceptance. Future candidates require their own verification.
See workspace `FINAL_VERIFICATION_2026-10-17.md` for the evidence and boundaries.
Run full Scout CVE/SBOM scans sequentially, without severity, package, layer or VEX
exclusions. A zero-finding scan means no known matches at scan time, not proof that
every possible vulnerability is absent. Retain old reports for comparison.

The current workspace report `POSTGRES_CLEAN_IMAGE_LOCAL_PACKAGE_2026-10-16.md`
records actual artifacts, results, failures investigated and remaining limitations.
The source/backport/toolchain maintenance obligations from previous packages remain.
No Compose, CI, backup or production image reference is switched by these commands.
