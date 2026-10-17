"""Run an offline profile comparison from an explicitly supplied JSON snapshot."""

import argparse
import json
from pathlib import Path
from time import perf_counter

from jobradar.matching.preview import compare_snapshot


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.snapshot.resolve() == args.output.resolve():
        parser.error("Output must not overwrite the input snapshot")
    started = perf_counter()
    result = compare_snapshot(json.loads(args.snapshot.read_text(encoding="utf-8")))
    result["elapsed_seconds"] = round(perf_counter() - started, 3)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=True), encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "transitions"}))


if __name__ == "__main__":
    main()
