#!/bin/sh
set -eu

mode=$1
before=$2
after=$3
tools=$(dirname "$0")
temp=$(mktemp -d)
trap 'rm -f "$temp/before" "$temp/after" "$temp/expected" "$temp/actual"; rmdir "$temp"' EXIT

for input in "$before" "$after"; do
    test -s "$input"
    sort -u "$input" > "$temp/actual"
    test "$(wc -l < "$input")" -eq "$(wc -l < "$temp/actual")"
done
test "$(wc -l < "$before")" -eq 45
case "$mode" in
    builder)
        test "$(wc -l < "$after")" -eq 73
        grep -Fx 'xz-libs-5.8.3-r0' "$before"
        tr '=' '-' < "$tools/builder-packages.txt" | sort > "$temp/expected"
        grep -Fxf "$temp/expected" "$after" | sort > "$temp/actual"
        cmp "$temp/expected" "$temp/actual"
        grep -v '^xz-libs-' "$before" | sort > "$temp/before"
        grep -Fvxf "$temp/expected" "$after" | sort > "$temp/after"
        ;;
    runtime)
        test "$(wc -l < "$after")" -eq 45
        grep -Fx 'libxml2-2.13.9-r2' "$before"
        grep -Fx 'libxml2-2.13.9-r200' "$after"
        grep -v '^libxml2-' "$before" | sort > "$temp/before"
        grep -v '^libxml2-' "$after" | sort > "$temp/after"
        ;;
    *) exit 1 ;;
esac
cmp "$temp/before" "$temp/after"
