#!/usr/bin/env bash

set -Eeuo pipefail

architecture="$(docker info --format '{{.Architecture}}')"
case "${architecture}" in
  x86_64|amd64)
    architecture=amd64
    image_id='sha256:ad68598f8c2901e3fc40dbbe77d85b85ed21a87eecec830ebf4b6782ff5c34b6'
    config_id='sha256:05b61769646a7ab9326aae4c0e92b7978e0cd87d55ca2c4be319067fc2c9d007'
    layer_id='sha256:6aed3ffae62ca1d8ddeb4f2f98792fc31cd951cd889173eed7856db032096913'
    ;;
  aarch64|arm64)
    architecture=arm64
    image_id='sha256:51958852a6c4b54ffd5f768cf4e6e6a7786ffe1b78a30dbd638b835381de0e41'
    config_id='sha256:24a3ac5c8f0871cdec773de8a7962f2eae68d114a2f141e9dad9ce4a23c59635'
    layer_id='sha256:ef9acd06139ac6fcded8cd807dbe9784fc0cc22ebcf80811f23a4e1241649622'
    ;;
  *)
    printf 'Unsupported PostgreSQL fixture architecture.\n' >&2
    exit 1
    ;;
esac

# Containerd uses the OCI index ID; classic Docker uses the config ID.
for candidate in "${image_id}" "${config_id}"; do
  if record="$(docker image inspect --format '{{.Id}} {{.Os}}/{{.Architecture}} {{json .RootFS.Layers}}' "${candidate}" 2>/dev/null)"; then
    if [[ "${record}" != "${candidate} linux/${architecture} [\"${layer_id}\"]" ]]; then
      printf 'PostgreSQL fixture identity or filesystem mismatch.\n' >&2
      exit 1
    fi
    printf '%s\n' "${candidate}"
    exit 0
  fi
done

printf 'The reviewed PostgreSQL fixture is not installed; load its verified archive first.\n' >&2
exit 1
