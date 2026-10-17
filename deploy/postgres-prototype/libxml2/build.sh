#!/bin/sh
set -eu
export SOURCE_DATE_EPOCH=1792108800
export CFLAGS='-Os -fPIC -fstack-clash-protection -fstack-protector-strong -fno-plt -Wformat -Werror=format-security'
export LDFLAGS='-Wl,--as-needed,-O1,--sort-common,-z,relro,-z,now,-z,pack-relative-relocs'
phase=${1:?Expected compile, verify or package phase}
case "$phase" in compile|verify|package) ;; *) exit 1 ;; esac
if test "$phase" = compile; then
mkdir -p /work/original /work/final /work/package /artifacts/evidence /work/probes
printf '%s\n%s\n' "$CFLAGS" "$LDFLAGS" > /artifacts/evidence/compiler-flags.txt
cd /tools/upstream
sha256sum -c SHA256SUMS
cd /tools/generated
sha256sum -c SHA256SUMS
echo '62d4813860124c969f204aaf33b497105dbc32a6c5655f5a86168743660e10987d687d7e5e7ee49fdfdeb8f6ad9fa4503f81fcce2e4d459094895f02436d1b13  /tools/source.tar.xz' | sha512sum -c -
echo '9117a6a060d9c760ae4aa88c33131b02b4677a6703fe02ea36a474035c901d5da778a4d98402a87187410e9bcfe9819d382f9df4e6fe42ff3cf33acc88ba28a9  /tools/upstream/CVE-2026-6732-test.patch' | sha512sum -c -
for stage in original final; do
    tar -xJf /tools/source.tar.xz --strip-components=1 -C "/work/$stage"
done
cp /work/original/xmlregexp.c /work/probes/regexp.original.c
cp /work/original/valid.c /work/probes/valid.original.c
cd /work/final
patch --batch --fuzz=0 -p1 < /tools/patches/0001-preserve-CVE-2026-6732.patch
patch --batch --fuzz=0 -p1 < /tools/upstream/CVE-2026-6732-test.patch
for commit in 76fe08d97de88bfaef2f7d5cd27f11954cc5bee2 e89a8aae4c9b40cdafcf66b3f9e57c62db37bb55 a4cba4b5b5a8c42e155ed42d2d2a44955465a2e4 d1686f91dbda141a752200419d35639fd6b38340; do
    patch --batch --fuzz=0 -p1 < "/tools/upstream/$commit.patch"
    case "$commit" in
        76fe*) cp xmlregexp.c /work/probes/regexp.intermediate.c ;;
        d168*) cp valid.c /work/probes/valid.intermediate.c ;;
    esac
done
patch --batch --fuzz=0 -p1 < /tools/generated/0002-security-backports.patch
if find /work/final -name '*.rej' | grep .; then exit 1; fi

for stage in original final; do
    cd "/work/$stage"
    ./configure --prefix=/usr --sysconfdir=/etc --disable-maintainer-mode \
        --enable-static --with-legacy --with-lzma --with-zlib --without-python \
        > "/artifacts/evidence/configure.$stage.log" 2>&1
    make -j2 > "/artifacts/evidence/make.$stage.log" 2>&1 || {
        tail -80 "/artifacts/evidence/make.$stage.log"; exit 1;
    }
    printf 'Built %s source\n' "$stage"
done
exit 0
fi

if test "$phase" = verify; then
# Preserve the exact old exported symbols and their version names.
readelf -d /work/final/.libs/libxml2.so.2.13.9 > /artifacts/evidence/libxml2.dynamic
grep -F 'Library soname: [libxml2.so.2]' /artifacts/evidence/libxml2.dynamic
nm -D --defined-only /usr/lib/libxml2.so.2.13.9 | awk '{print $3}' | sort > /artifacts/evidence/symbols.before
nm -D --defined-only /work/final/.libs/libxml2.so.2.13.9 | awk '{print $3}' | sort > /artifacts/evidence/symbols.after
comm -23 /artifacts/evidence/symbols.before /artifacts/evidence/symbols.after > /artifacts/evidence/symbols.missing
test ! -s /artifacts/evidence/symbols.missing
cp /evidence/builder.* /artifacts/evidence/

compile() {
    source=$1
    name=$2
    shift 2
    cc $CFLAGS -DHAVE_CONFIG_H -I"$source" -I"$source/include" "$@" \
        /tools/regressions.c "$source/.libs/libxml2.a" -lz -llzma -lm -pthread \
        $LDFLAGS -o "/work/probes/$name" > "/artifacts/evidence/compile.$name.log" 2>&1 || {
        cat "/artifacts/evidence/compile.$name.log"; exit 1;
    }
}
for stage in original final; do
    source="/work/$stage"
    compile "$source" "runtime.$stage"
    for mode in VALID DICT URI XPOINTER IO; do
        compile "$source" "$mode.$stage" "-DTEST_$mode"
    done
done
compile /work/original REGEXP.original -DTEST_REGEXP
compile /work/final REGEXP.final -DTEST_REGEXP -DHAS_REGEXP_LENGTH
# Compile the two historically incomplete fixes separately to test their follow-ups.
mkdir -p /work/intermediate
cp /work/probes/regexp.intermediate.c /work/intermediate/xmlregexp.c
cp /work/probes/valid.intermediate.c /work/intermediate/valid.c
cc $CFLAGS -DHAVE_CONFIG_H -DTEST_REGEXP -DHAS_REGEXP_LENGTH \
    -I/work/intermediate -I/work/final -I/work/final/include /tools/regressions.c \
    /work/final/.libs/libxml2.a -lz -llzma -lm -pthread $LDFLAGS \
    -o /work/probes/REGEXP.intermediate
cc $CFLAGS -DHAVE_CONFIG_H -DTEST_VALID -I/work/intermediate -I/work/final \
    -I/work/final/include /tools/regressions.c /work/final/.libs/libxml2.a \
    -lz -llzma -lm -pthread $LDFLAGS -o /work/probes/VALID.intermediate

expect_failure() {
    expected=$1
    name=$2
    shift 2
    set +e
    timeout 10 "$@" > "/artifacts/evidence/before.$name.log" 2>&1
    status=$?
    set -e
    grep -F 'PROBE:' "/artifacts/evidence/before.$name.log"
    case ",$expected," in
        *",$status,"*) ;;
        *) cat "/artifacts/evidence/before.$name.log"; echo "Unexpected control status: $status"; exit 1 ;;
    esac
    echo "Confirmed failing control: $name (status=$status)"
}
expect_failure 139,134 regexp-lookahead /work/probes/REGEXP.original lookahead
expect_failure 139,134 regexp-alloc /work/probes/REGEXP.intermediate alloc
expect_failure 10 validation-tiny /work/probes/VALID.original tiny
expect_failure 139,134 validation-empty /work/probes/VALID.intermediate empty
expect_failure 11 dictionary /work/probes/DICT.original
expect_failure 12 uri /work/probes/URI.original 0
expect_failure 12 xpointer /work/probes/XPOINTER.original 0
expect_failure 13 io-plain /work/probes/IO.original 0 1
expect_failure 14 xinclude /work/probes/runtime.original xinclude
expect_failure 139,134 reader-schema /work/probes/runtime.original schema

mkdir -p /work/rng /work/catalog
for index in 0 1 2; do
    next=$((index + 1))
    printf '<grammar xmlns="http://relaxng.org/ns/structure/1.0"><include href="%s.rng"/></grammar>' "$next" > "/work/rng/$index.rng"
done
printf '<grammar xmlns="http://relaxng.org/ns/structure/1.0"><start><element name="a"><empty/></element></start></grammar>' > /work/rng/3.rng
printf 'CATALOG recursive.sgml\n' > /work/catalog/recursive.sgml
expect_failure 15 relaxng env RNG_INCLUDE_LIMIT=2 /work/probes/runtime.original relaxng
expect_failure 139,134,124 catalog /work/probes/runtime.original catalog

exec 3>&1
exec > /artifacts/evidence/regressions.after.log 2>&1
    /work/probes/REGEXP.final lookahead
    /work/probes/REGEXP.final alloc
    /work/probes/VALID.final tiny
    /work/probes/VALID.final empty
    /work/probes/DICT.final
    /work/probes/URI.final 0
    /work/probes/URI.final 2147483647
    /work/probes/URI.final 2147483628
    /work/probes/XPOINTER.final 0
    /work/probes/XPOINTER.final 2147483647
    for encoded in 0 1; do
        for flush in 0 1; do
            /work/probes/IO.final "$encoded" "$flush"
        done
    done
    RNG_INCLUDE_LIMIT=2 /work/probes/runtime.final relaxng
    /work/probes/runtime.final catalog
    /work/probes/runtime.final
exec 1>&3 2>&3
exec 3>&-
cat /artifacts/evidence/regressions.after.log

cd /work/final
# Match the two documented Alpine test exclusions; do not hide new failures.
rm test/icu_parse_test.xml test/ebcdic_566012.xml
make -j2 runtests > /artifacts/evidence/upstream-tests.log 2>&1 || {
    tail -100 /artifacts/evidence/upstream-tests.log; exit 1;
}
tail -25 /artifacts/evidence/upstream-tests.log

# Test malformed utility input in a process isolated from the runtime image.
ldd /work/final/.libs/xmlcatalog > /artifacts/evidence/xmlcatalog.default-loader.ldd
export LD_LIBRARY_PATH=/work/final/.libs
ldd /work/final/.libs/xmlcatalog > /artifacts/evidence/xmlcatalog.test-loader.ldd
grep -F 'libxml2.so.2 => /work/final/.libs/libxml2.so.2' /artifacts/evidence/xmlcatalog.test-loader.ldd
for prefix in '' 'public '; do
    { printf '%s' "$prefix"; head -c 470 /dev/zero | tr '\000' A; printf '\n'; } \
        | /work/final/.libs/xmlcatalog --shell /work/catalog/recursive.sgml \
        > /artifacts/evidence/xmlcatalog-long.log 2>&1
done
{ printf 'public '; index=0; while test "$index" -lt 80; do printf ' x'; index=$((index + 1)); done; printf '\n'; } \
    | /work/final/.libs/xmlcatalog --shell /work/catalog/recursive.sgml \
    > /artifacts/evidence/xmlcatalog-args.log 2>&1
unset LD_LIBRARY_PATH
exit 0
fi

# The ephemeral package-signing key never enters an image layer or exported artifact.
mkdir -p /signing
openssl genrsa -out /signing/local.rsa 2048 >/dev/null 2>&1
export PACKAGER_PRIVKEY=/signing/local.rsa
cp /tools/APKBUILD /work/package/
cd /work/package
REPODEST=/work/packages abuild -F -d rootpkg > /artifacts/evidence/package.log 2>&1 || {
    tail -80 /artifacts/evidence/package.log; exit 1;
}
find /work/packages -name 'libxml2-2.13.9-r200.apk' -exec cp '{}' /artifacts/libxml2-2.13.9-r200.apk \;
test -s /artifacts/libxml2-2.13.9-r200.apk
cd /artifacts
sha256sum libxml2-2.13.9-r200.apk > APK-SHA256SUMS
cp /work/final/Copyright LICENSE
cc $CFLAGS -I/work/final/include /tools/regressions.c -L/work/final/.libs \
    -lxml2 $LDFLAGS -o /artifacts/runtime-regressions
mkdir -p source
cp /tools/source.tar.xz /tools/generate_backports.py /tools/regressions.c /tools/build.sh /tools/APKBUILD source/
cp -a /tools/upstream /tools/generated /tools/patches source/
sha256sum /work/original/.libs/libxml2.so.2.13.9 /work/final/.libs/libxml2.so.2.13.9 > evidence/libraries.sha256
test ! -e /artifacts/source/local.rsa
echo 'Local library, controlled regressions, upstream tests and APK completed'
