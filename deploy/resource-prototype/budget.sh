#!/bin/sh

set -eu

owner=${1-}
operation=${2-}
case "$owner" in ''|*[!0-9a-f]*) exit 2 ;; esac
[ "${#owner}" -eq 12 ] || exit 2
group="/sys/fs/cgroup/jobradar-memory-$owner"
[ "$(readlink -f /sys/fs/cgroup)" = /sys/fs/cgroup ] || exit 2

case "$operation" in
  create)
    [ ! -e "$group" ] || exit 2
    mkdir "$group"
    trap 'rmdir "$group" 2>/dev/null || true' EXIT
    printf '+memory +cpu +pids' > "$group/cgroup.subtree_control"
    printf '1476395008' > "$group/memory.max"
    printf '0' > "$group/memory.swap.max"
    printf '200000 100000' > "$group/cpu.max"
    printf '512' > "$group/pids.max"
    trap - EXIT
    ;;
  read)
    [ "$(cat "$group/memory.max")" = 1476395008 ] || exit 2
    for field in memory.current memory.peak memory.max memory.events.local memory.swap.current memory.swap.max pids.current cpu.max; do
      printf '%s=' "$field"
      tr '\n' ',' < "$group/$field"
      printf '\n'
    done
    ;;
  remove)
    [ "$(cat "$group/memory.max")" = 1476395008 ] || exit 2
    [ -z "$(cat "$group/cgroup.procs")" ] || exit 2
    # rmdir refuses an occupied group; never remove descendants recursively.
    rmdir "$group"
    ;;
  *) exit 2 ;;
esac
