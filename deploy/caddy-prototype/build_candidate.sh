#!/usr/bin/env bash
set -Eeuo pipefail

mkdir -p /artifacts/metadata /artifacts/licenses
if [[ "${1:-}" == resolve ]]; then
    go version | tee /artifacts/metadata/toolchain.txt
    cp go.mod /artifacts/metadata/go.mod.original
    cp go.sum /artifacts/metadata/go.sum.original
    go list -m -json all > /artifacts/metadata/modules.original.json
    go get golang.org/x/net@v0.61.0
    go mod download all
    go list -m -json all > /artifacts/metadata/modules.updated.json
    exit 0
fi
if [[ "${1:-}" != compile ]]; then
    printf 'Expected resolve or compile stage.\n' >&2
    exit 1
fi
go run /tools/verify_graph.go /artifacts/metadata/modules.original.json /artifacts/metadata/modules.updated.json /artifacts/licenses
go mod verify
cp go.mod go.sum /artifacts/metadata/
cp LICENSE /artifacts/licenses/CADDY-LICENSE
cp /usr/local/go/LICENSE /artifacts/licenses/GO-LICENSE
for architecture in amd64 arm64; do
    GOARCH="${architecture}" go list -tags=nobadger,nomysql,nopgx -deps -json ./cmd/caddy > "/artifacts/metadata/imports.${architecture}.json"
    GOARCH="${architecture}" go build -mod=readonly -trimpath -buildvcs=false \
        -tags=nobadger,nomysql,nopgx \
        -ldflags='-s -w -X github.com/caddyserver/caddy/v2.CustomVersion=v2.11.7-jobradar.1' \
        -o "/artifacts/caddy.${architecture}" ./cmd/caddy
    go version -m "/artifacts/caddy.${architecture}" > "/artifacts/metadata/build.${architecture}.txt"
    GOARCH="${architecture}" go build -trimpath -buildvcs=false -o "/artifacts/probe.${architecture}" /tools/integration_probe.go
done
GOARCH=arm64 go build -mod=readonly -trimpath -buildvcs=false \
    -tags=nobadger,nomysql,nopgx \
    -ldflags='-s -w -X github.com/caddyserver/caddy/v2.CustomVersion=v2.11.7-jobradar.1' \
    -o /artifacts/caddy.arm64.repeat ./cmd/caddy
cmp /artifacts/caddy.arm64 /artifacts/caddy.arm64.repeat
sha256sum /artifacts/caddy.* /artifacts/probe.* > /artifacts/metadata/SHA256SUMS
