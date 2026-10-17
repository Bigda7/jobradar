#!/usr/bin/env bash
set -Eeuo pipefail

test "$(go version)" = 'go version go1.26.9 linux/amd64'
mkdir -p /artifacts/metadata /artifacts/licenses /artifacts/source
cp /source.tar.gz /artifacts/source/gosu-1.19.tar.gz
cp go.mod /artifacts/source/go.mod.original
cp go.sum /artifacts/source/go.sum.original
sha256sum ./*.go > /artifacts/metadata/source.before.sha256
go version > /artifacts/metadata/toolchain.txt
go list -m -json all > /artifacts/metadata/modules.original.json
cp /tools/parser_regression_test.go ./jobradar_parser_regression_test.go
if go test -mod=readonly -run TestOversizedPasswdAndGroupLines -v ./... > /artifacts/metadata/parser-original-tests.txt 2>&1; then
    printf 'Original parser unexpectedly passed the oversized group regression.\n' >&2
    exit 1
fi
grep -F 'oversized group line accepted' /artifacts/metadata/parser-original-tests.txt
rm ./jobradar_parser_regression_test.go
go get github.com/moby/sys/user@v0.4.1 golang.org/x/sys@v0.49.0
go mod download all
go list -m -json all > /artifacts/metadata/modules.updated.json
go run /tools/verify_graph.go /artifacts/metadata/modules.original.json /artifacts/metadata/modules.updated.json
go test -v /tools/verify_graph.go /tools/verify_graph_test.go | tee /artifacts/metadata/graph-tests.txt
go mod verify
sha256sum -c /artifacts/metadata/source.before.sha256
cp go.mod go.sum /artifacts/source/
cp LICENSE /artifacts/licenses/GOSU-LICENSE
cp /usr/local/go/LICENSE /artifacts/licenses/GO-LICENSE
cp "$(go env GOMODCACHE)/github.com/moby/sys/user@v0.4.1/LICENSE" /artifacts/licenses/MOBY-USER-LICENSE
cp "$(go env GOMODCACHE)/golang.org/x/sys@v0.49.0/LICENSE" /artifacts/licenses/X-SYS-LICENSE

# Additional tests are kept outside the pristine upstream source archive.
cp /tools/parser_regression_test.go ./jobradar_parser_regression_test.go
go test -mod=readonly -v ./... | tee /artifacts/metadata/parser-tests.txt
rm ./jobradar_parser_regression_test.go
sha256sum -c /artifacts/metadata/source.before.sha256
for architecture in amd64 arm64; do
    GOARCH="$architecture" go list -mod=readonly -deps -json . > "/artifacts/metadata/imports.${architecture}.json"
    # Preserve symbols for later function-level vulnerability analysis, as upstream does.
    GOARCH="$architecture" go build -mod=readonly -trimpath -buildvcs=false -ldflags='-d -w' -o "/artifacts/gosu.${architecture}" .
    go version -m "/artifacts/gosu.${architecture}" > "/artifacts/metadata/build.${architecture}.txt"
    GOARCH="$architecture" go build -mod=readonly -trimpath -buildvcs=false -ldflags='-d -w' -o "/artifacts/gosu.${architecture}.repeat" .
    cmp "/artifacts/gosu.${architecture}" "/artifacts/gosu.${architecture}.repeat"
    rm "/artifacts/gosu.${architecture}.repeat"
done
sha256sum /artifacts/gosu.* /artifacts/licenses/* /artifacts/source/* > /artifacts/metadata/SHA256SUMS
