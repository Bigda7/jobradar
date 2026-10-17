#!/bin/sh
set -eu
test "$#" -eq 2
test -s "$1"
test -s "$2"

awk '
BEGIN {
    approved["libcrypto3-3.5.7-r0"] = "libcrypto3-3.5.9-r0"
    approved["libssl3-3.5.7-r0"] = "libssl3-3.5.9-r0"
    approved["libuuid-2.42.1-r0"] = "libuuid-2.42.3-r1"
    for (old in approved) updated[approved[old]] = 1
}
FNR == NR { before[$0]++; next }
{ after[$0]++ }
END {
    for (old in approved) {
        new = approved[old]
        if (before[old] != 1 || after[new] != 1 || before[new] != 0 || after[old] != 0)
            failed = 1
    }
    for (line in before) {
        if (!(line in approved) && !(line in updated) && before[line] != after[line]) failed = 1
        if (before[line] > 1 || line == "") failed = 1
    }
    for (line in after) {
        if (!(line in updated) && !(line in approved) && after[line] != before[line]) failed = 1
        if (after[line] > 1 || line == "") failed = 1
    }
    if (failed) {
        print "Unapproved APK package transaction" > "/dev/stderr"
        exit 1
    }
    print "Verified exactly three approved APK upgrades and no other package changes"
}' "$1" "$2"
