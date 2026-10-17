FROM postgres:17-alpine@sha256:18cfe3ef5e6815560c98237d6216d1e5119702fb0f3894c8785dd58b8bbe5d73

LABEL jobradar.prototype="postgres-zlib" jobradar.prototype.version="20261013"

RUN set -eu; \
    mkdir -p /usr/local/share/jobradar-postgres-prototype; \
    apk info -v | sort > /usr/local/share/jobradar-postgres-prototype/packages.before; \
    sha256sum /usr/local/bin/postgres /usr/local/bin/gosu /usr/lib/libxml2.so* /usr/lib/libssl.so* /usr/lib/libcrypto.so* > /usr/local/share/jobradar-postgres-prototype/binaries.before; \
    apk add --no-cache zlib=1.3.2-r1; \
    apk info -v | sort > /usr/local/share/jobradar-postgres-prototype/packages.after; \
    grep -v '^zlib-' /usr/local/share/jobradar-postgres-prototype/packages.before > /tmp/packages.before; \
    grep -v '^zlib-' /usr/local/share/jobradar-postgres-prototype/packages.after > /tmp/packages.after; \
    cmp /tmp/packages.before /tmp/packages.after; \
    grep -Fx 'zlib-1.3.2-r0' /usr/local/share/jobradar-postgres-prototype/packages.before; \
    grep -Fx 'zlib-1.3.2-r1' /usr/local/share/jobradar-postgres-prototype/packages.after; \
    sha256sum -c /usr/local/share/jobradar-postgres-prototype/binaries.before; \
    test "$(postgres --version)" = 'postgres (PostgreSQL) 17.11'

ADD --checksum=sha256:e32ff4e00d9d94930537635291da39e7e612703334bf6fde8c7f1686fe8a45a2 \
    https://raw.githubusercontent.com/madler/zlib/v1.3.2/LICENSE \
    /usr/local/share/jobradar-postgres-prototype/ZLIB-LICENSE
