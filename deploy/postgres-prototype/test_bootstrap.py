"""Run isolated PostgreSQL checks with a Windows-compatible asyncio policy."""

import asyncio
import os
import runpy
import sys
from pathlib import Path
from urllib.parse import urlsplit


def main() -> None:
    for name in ("DATABASE_URL", "TEST_DATABASE_URL"):
        parsed = urlsplit(os.environ.get(name, "").replace("postgresql+psycopg", "postgresql"))
        if parsed.hostname != "127.0.0.1" or parsed.path != "/jobradar_test":
            raise RuntimeError("Bootstrap requires the isolated loopback jobradar_test database")
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    # Match python -m behavior so repository-local test helpers remain importable.
    root = str(Path(__file__).resolve().parents[2])
    if root not in sys.path:
        sys.path.insert(0, root)
    target, *arguments = sys.argv[1:]
    sys.argv = [target, *arguments]
    if target in ("alembic", "pytest"):
        runpy.run_module(target, run_name="__main__", alter_sys=True)
    elif target == "reset":
        path = Path(__file__).resolve().parents[2] / "tests/reset_test_database.py"
        runpy.run_path(str(path), run_name="__main__")
    else:
        raise RuntimeError("Unsupported isolated test target")


if __name__ == "__main__":
    main()
