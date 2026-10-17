"""Fail closed before using the opt-in resource profile on an undersized host.

This is an explicit operator preflight, not a scheduled monitor or a deployment.
The shared application/backup ceiling is 1408 MiB, with 384 MiB reserved for the
host. Individual ceilings are not summed as reservations. Verify the actual
systemd slice with --check-slice before an explicitly authorized rollout.
"""

import argparse
import json
import subprocess
from pathlib import Path

GROUP_MEMORY_MIB = 1408
HOST_RESERVE_MIB = 384
MINIMUM_MEMORY_MIB = GROUP_MEMORY_MIB + HOST_RESERVE_MIB


def capacity_result(memory_bytes: int) -> dict[str, int | bool]:
    if memory_bytes <= 0:
        raise ValueError("Memory must be positive.")
    return {
        "verified": memory_bytes >= MINIMUM_MEMORY_MIB * 1024 * 1024,
        "memory_bytes": memory_bytes,
        "required_memory_bytes": MINIMUM_MEMORY_MIB * 1024 * 1024,
        "group_memory_bytes": GROUP_MEMORY_MIB * 1024**2,
        "host_reserve_bytes": HOST_RESERVE_MIB * 1024**2,
    }


def slice_is_verified() -> bool:
    try:
        result = subprocess.run(
            [
                "systemctl",
                "show",
                "jobradar.slice",
                "--property=LoadState,ActiveState,MemoryMax,MemorySwapMax,TasksMax",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    except (OSError, subprocess.SubprocessError):
        return False
    return all(
        values.get(key) == value
        for key, value in {
            "LoadState": "loaded",
            "ActiveState": "active",
            "MemoryMax": str(GROUP_MEMORY_MIB * 1024**2),
            "MemorySwapMax": "0",
            "TasksMax": "512",
        }.items()
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--memory-bytes", type=int, help="Explicit measured MemTotal for offline review"
    )
    parser.add_argument(
        "--check-slice",
        action="store_true",
        help="Also require the active reviewed systemd resource group",
    )
    arguments = parser.parse_args()
    try:
        memory = arguments.memory_bytes
        if memory is None:
            lines = Path("/proc/meminfo").read_text().splitlines()
            memory = next(
                int(line.split()[1]) * 1024 for line in lines if line.startswith("MemTotal:")
            )
        result = capacity_result(memory)
        if arguments.check_slice:
            result["slice_verified"] = slice_is_verified()
            result["verified"] = bool(result["verified"] and result["slice_verified"])
    except (ValueError, OSError, StopIteration):
        print(json.dumps({"verified": False, "reason": "host_memory_unavailable"}))
        return 1
    print(json.dumps(result))
    if not result["verified"]:
        print("Resource profile refused: insufficient RAM or unverified resource group.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
