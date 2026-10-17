ARG BASE_IMAGE=jobradar-postgres-apk:amd64-20261016
FROM ${BASE_IMAGE} AS tools
COPY builder-packages.txt verify_inventory.sh /tools/
RUN set -eu; \
    mkdir -p /evidence; \
    apk info -v | sort > /evidence/builder.before; \
    apk add --no-cache $(cat /tools/builder-packages.txt); \
    apk info -v | sort > /evidence/builder.after; \
    sh /tools/verify_inventory.sh builder /evidence/builder.before /evidence/builder.after

FROM tools AS compiled
COPY . /tools/
RUN --network=none sh /tools/build.sh compile

FROM compiled AS verified
RUN --network=none sh /tools/build.sh verify

FROM verified AS build
RUN --mount=type=tmpfs,target=/signing --network=none sh /tools/build.sh package

FROM scratch AS artifacts
COPY --from=build /artifacts/ /

FROM ${BASE_IMAGE} AS candidate
LABEL jobradar.prototype="postgres-libxml2" jobradar.prototype.version="20261016" \
    jobradar.libxml2.version="2.13.9-r200" jobradar.libxml2.origin="local-security-backport"
COPY builder-packages.txt verify_inventory.sh /tools/
COPY --from=build /artifacts/ /usr/local/share/jobradar-postgres-prototype/libxml2/
RUN --network=none set -eu; \
    evidence=/usr/local/share/jobradar-postgres-prototype/libxml2; \
    apk info -v | sort > "$evidence/runtime.before"; \
    sha256sum /usr/local/bin/postgres /usr/local/bin/gosu /usr/lib/libxslt.so* \
      /usr/lib/libssl.so* /usr/lib/libcrypto.so* /usr/lib/libuuid.so* \
      /usr/lib/liblzma.so* /usr/lib/libz.so* > "$evidence/unrelated.before"; \
    cd "$evidence"; sha256sum -c APK-SHA256SUMS; \
    apk --no-network --allow-untrusted add ./libxml2-2.13.9-r200.apk; \
    apk info -v | sort > "$evidence/runtime.after"; \
    sh /tools/verify_inventory.sh runtime "$evidence/runtime.before" "$evidence/runtime.after"; \
    sha256sum -c "$evidence/unrelated.before"; \
    test "$(postgres --version)" = 'postgres (PostgreSQL) 17.11'; \
    ldd /usr/local/bin/postgres > "$evidence/postgres.ldd"; \
    ldd /usr/lib/libxslt.so.1 > "$evidence/libxslt.ldd"; \
    if grep -E 'not found|Error loading|symbol not found' "$evidence/postgres.ldd" "$evidence/libxslt.ldd"; then exit 1; fi; \
    /usr/local/share/jobradar-postgres-prototype/libxml2/runtime-regressions
