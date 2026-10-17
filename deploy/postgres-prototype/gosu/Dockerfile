FROM --platform=$BUILDPLATFORM golang:1.26.9-bookworm@sha256:d9c68c2c51161e12fd77e4c6320687c9cd86e1af1e3ad6e6cd63ff970641453c AS build

ENV GOTOOLCHAIN=local CGO_ENABLED=0 GOOS=linux
WORKDIR /source
COPY source.tar.gz /source.tar.gz
RUN echo '33d7537d588ea49458b9509bcf4554bdf5ceacc66da71e5caa1058ea3b689c3b  /source.tar.gz' | sha256sum -c - \
    && tar -xzf /source.tar.gz --strip-components=1 -C /source
COPY build_candidate.sh verify_graph.go verify_graph_test.go parser_regression_test.go /tools/
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build \
    bash /tools/build_candidate.sh

FROM scratch AS artifacts
COPY --from=build /artifacts/ /

FROM postgres:17-alpine@sha256:18cfe3ef5e6815560c98237d6216d1e5119702fb0f3894c8785dd58b8bbe5d73 AS candidate
ARG TARGETARCH
LABEL jobradar.prototype="postgres-gosu" jobradar.prototype.version="20261016" \
    jobradar.gosu.source="6456aaa0f3c854d199d0f037f068eb97515b7513" \
    jobradar.gosu.toolchain="go1.26.9" jobradar.gosu.user="v0.4.1" jobradar.gosu.sys="v0.49.0"
RUN set -eu; \
    mkdir -p /usr/local/share/jobradar-postgres-prototype; \
    apk info -v | sort > /usr/local/share/jobradar-postgres-prototype/packages.before; \
    sha256sum /usr/local/bin/postgres /usr/lib/libxml2.so* /usr/lib/libssl.so* /usr/lib/libcrypto.so* > /usr/local/share/jobradar-postgres-prototype/binaries.before; \
    apk add --no-cache zlib=1.3.2-r1; \
    apk info -v | sort > /usr/local/share/jobradar-postgres-prototype/packages.after; \
    grep -v '^zlib-' /usr/local/share/jobradar-postgres-prototype/packages.before > /tmp/packages.before; \
    grep -v '^zlib-' /usr/local/share/jobradar-postgres-prototype/packages.after > /tmp/packages.after; \
    cmp /tmp/packages.before /tmp/packages.after; \
    grep -Fx 'zlib-1.3.2-r0' /usr/local/share/jobradar-postgres-prototype/packages.before; \
    grep -Fx 'zlib-1.3.2-r1' /usr/local/share/jobradar-postgres-prototype/packages.after; \
    sha256sum -c /usr/local/share/jobradar-postgres-prototype/binaries.before; \
    test "$(postgres --version)" = 'postgres (PostgreSQL) 17.11'; \
    sha256sum /usr/local/bin/gosu > /usr/local/share/jobradar-postgres-prototype/gosu.before
COPY --from=build /artifacts/gosu.${TARGETARCH} /usr/local/bin/gosu
COPY --from=build /artifacts/licenses/ /usr/local/share/jobradar-postgres-prototype/gosu/licenses/
COPY --from=build /artifacts/source/ /usr/local/share/jobradar-postgres-prototype/gosu/source/
COPY --from=build /artifacts/metadata/ /usr/local/share/jobradar-postgres-prototype/gosu/metadata/
RUN set -eu; \
    sha256sum -c /usr/local/share/jobradar-postgres-prototype/binaries.before; \
    test "$(gosu --version)" = "1.19 (go1.26.9 on linux/${TARGETARCH}; gc)"; \
    test "$(stat -c '%a' /usr/local/bin/gosu)" = '755'; \
    test "$(gosu postgres id -u)" = "$(id -u postgres)"; \
    test "$(gosu postgres id -g)" = "$(id -g postgres)"
ADD --checksum=sha256:e32ff4e00d9d94930537635291da39e7e612703334bf6fde8c7f1686fe8a45a2 \
    https://raw.githubusercontent.com/madler/zlib/v1.3.2/LICENSE \
    /usr/local/share/jobradar-postgres-prototype/ZLIB-LICENSE

FROM candidate AS apk-candidate
LABEL jobradar.prototype="postgres-apk" jobradar.prototype.version="20261016" \
    jobradar.apk.openssl="3.5.9-r0" jobradar.apk.libuuid="2.42.3-r1"
COPY verify_apk_packages.sh /usr/local/share/jobradar-postgres-prototype/verify_apk_packages.sh
RUN set -eu; \
    evidence=/usr/local/share/jobradar-postgres-prototype; \
    apk info -v | sort > "$evidence/apk.before"; \
    sha256sum /usr/local/bin/postgres /usr/local/bin/gosu /usr/lib/libxml2.so* /usr/lib/libz.so* > "$evidence/apk.binaries.before"; \
    apk add --no-cache libcrypto3=3.5.9-r0 libssl3=3.5.9-r0 libuuid=2.42.3-r1; \
    apk info -v | sort > "$evidence/apk.after"; \
    sh "$evidence/verify_apk_packages.sh" "$evidence/apk.before" "$evidence/apk.after"; \
    sha256sum -c "$evidence/apk.binaries.before"; \
    test "$(postgres --version)" = 'postgres (PostgreSQL) 17.11'; \
    ldd /usr/local/bin/postgres > "$evidence/apk.postgres.ldd"; \
    ldd /usr/lib/libuuid.so.1 > "$evidence/apk.uuid.ldd"; \
    if grep -E 'not found|Error loading' "$evidence/apk.postgres.ldd" "$evidence/apk.uuid.ldd"; then exit 1; fi
