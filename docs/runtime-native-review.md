# Vendored Native Library Review

Reviewed on 2026-10-03 for the v1.2.12 Linux ARM64 runtime. This is a technical
source/license mapping, not legal certification. The exact 16 installed file hashes and
immutable source recipes are in `runtime-native-sources.json`.

| Installed files | Count | Source version | Preserved license evidence |
| --- | --- | --- | --- |
| Greenlet libgcc and libstdc++ | 2 | Alpine GCC 14.2.0-r6 | GPLv3 and GCC Runtime Library Exception 3.1 |
| Pydantic Core and Watchfiles libgcc | 2 | GCC 12.4.0, musl cross toolchain | GPLv3 and GCC Runtime Library Exception 3.1 |
| Psycopg libssl and libcrypto | 2 | OpenSSL 3.5.5-r0 | Apache 2.0 and upstream notices |
| Psycopg Kerberos libraries | 4 | krb5 1.21.3-r0 | MIT and component notices |
| Psycopg libsasl2 | 1 | Cyrus SASL 2.1.28-r8 | BSD attribution/advertising clauses and upstream notices |
| Psycopg libcom_err | 1 | e2fsprogs 1.47.2-r2 | MIT library notice and full origin license texts |
| Psycopg libldap and liblber | 2 | OpenLDAP 2.6.8-r0 | OpenLDAP Public License 2.8 |
| Psycopg libkeyutils | 1 | keyutils 1.6.3-r4 | LGPL 2.0-or-later library notice; full origin texts |
| Psycopg libpq | 1 | PostgreSQL 18.0, Psycopg wheel build | PostgreSQL COPYRIGHT and Psycopg build scripts |

## Identification Evidence

Eleven libraries were matched to original Alpine APK library `.text` hashes; their original
APK metadata, package build commits, and full library hashes are preserved in
`runtime-native-evidence.json`. Auditwheel changes ELF names/metadata, so the full installed
wheel-library hashes differ from the original APK files. Identical machine code supports
identification; it does not demonstrate reproducibility of the complete toolchain.

The two OpenSSL libraries are identified by the wheel's auditwheel SBOM as 3.5.5-r0. They are
not the system OpenSSL 3.5.9. Libpq reports version 180000 and the pinned Psycopg source
repository contains `tools/ci/build_libpq.sh`, including configuration flags and edits.
PostgreSQL sources are pinned to the official REL_18_0 commit.

The two remaining libgcc files have identical installed hashes and GCC 12.4.0 compiler
identification. Their `.text` SHA256 is
`459600c9e6eb69cc91580f2aad1d0b295d90d44504ad24eb6e25da451772a0b9`.
It matches the original libgcc in the immutable rust-musl-cross builder recorded in the
source mapping; the original whole-file hash is
`0bf60adc8e990861bcc846bdb16ce7e31ba0d82c01be2d6338e41ff14a6b6b52`.
The source bundle preserves GCC 12.4.0, a pinned musl-cross-make source/patch snapshot, and
the rust-musl-cross build configuration. The upstream builder cloned musl-cross-make without
a commit pin: its original checkout commit is not established. The preserved snapshot's
GCC patch set and recipe are review evidence, not proof of that original checkout or a
byte-for-byte reconstruction. No full native-toolchain rebuild was performed.

## File-Level License Treatment

Both GCC source sets include `COPYING3` and `COPYING.RUNTIME`. Their runtime exception is
not a reason to omit license texts or sources when redistributing the libraries themselves.
The [GCC exception FAQ](https://www.gnu.org/licenses/gcc-exception-3.1-faq.html) distinguishes
eligible compilation from independent library distribution. The source bundle retains both
texts rather than treating Alpine's broad origin license metadata as the final library license.

The keyutils library's `keyutils.c` header specifies Lesser GPL version 2 or later, while the
origin contains GPL tools too. The e2fsprogs origin likewise contains several licenses; the
libcom_err mapping does not relicense the entire source archive as MIT. Original headers and
all archived notices remain in the corresponding complete source archives.

The [Psycopg build recipe](https://github.com/psycopg/psycopg/blob/83f110367cdd249cc0a352e2246ecea9e878e5a0/tools/ci/build_libpq.sh)
and [rust-musl-cross configuration](https://github.com/rust-cross/rust-musl-cross/blob/80d14aebe943277b51552d65b289e49e922677d3/config.mak)
provide primary build evidence. The reviewed mapping does not waive separate license,
replacement/relinking, advertising, or attribution conditions applicable to distribution.

Any changed native file hash, package version/license, platform, or Alpine build commit must
stop source preparation until the mapping is reviewed again. This package changes neither
JobRadar's MIT license nor dependencies, ingestion, matching, or notification behavior.
