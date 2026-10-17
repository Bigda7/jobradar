"""Profile API SQL on an owned, synthetic PostgreSQL snapshot without external calls."""

import argparse
import asyncio
import importlib.util
import json
import os
import tempfile
import time
import uuid
from pathlib import Path

import psycopg
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.engine import make_url

from jobradar.api.app import create_app
from jobradar.config import Settings
from jobradar.db.access import provision_access
from jobradar.db.session import create_engine, create_session_factory

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("memory_profile_fixture", HERE / "check_2gb.py")
memory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(memory)


def plan_summary(node):
    result = [
        {
            key: node[key]
            for key in (
                "Node Type",
                "Relation Name",
                "Subplan Name",
                "Actual Total Time",
                "Actual Rows",
                "Actual Loops",
                "Plan Rows",
                "Shared Read Blocks",
                "Temp Written Blocks",
            )
            if key in node
        }
    ]
    for child in node.get("Plans", []):
        result.extend(plan_summary(child))
    return result


async def profile(fixture, *, analyze, baseline_path=None):
    url = make_url(fixture.environment()["DATABASE_URL"])
    with psycopg.connect(
        str(url.set(drivername="postgresql").render_as_string(hide_password=False))
    ) as connection:
        provision_access(connection, "jobradar_test", memory.resources.PASSWORDS)
    url = url.set(username="jobradar_api", password=memory.resources.PASSWORDS["api"])
    engine = create_engine(url.render_as_string(hide_password=False))
    queries = []

    def before(connection, cursor, statement, parameters, context, executemany):
        context.profile_started = time.perf_counter()

    def after(connection, cursor, statement, parameters, context, executemany):
        queries.append((statement, parameters, time.perf_counter() - context.profile_started))

    event.listen(engine.sync_engine, "before_cursor_execute", before)
    event.listen(engine.sync_engine, "after_cursor_execute", after)
    settings = Settings(
        _env_file=None,
        app_env="test",
        service_role="api",
        api_bearer_token=None,
        api_allowed_hosts="localhost;127.0.0.1;test",
    )
    application = create_app(create_session_factory(engine), application_settings=settings)
    try:
        if baseline_path is not None:
            baseline_spec = importlib.util.spec_from_file_location("baseline_api", baseline_path)
            baseline = importlib.util.module_from_spec(baseline_spec)
            baseline_spec.loader.exec_module(baseline)
            old_application = baseline.create_app(
                create_session_factory(engine), application_settings=settings
            )
            paths = (
                "/jobs?limit=200",
                "/jobs?limit=1&offset=1",
                "/jobs?limit=200&offset=100000",
                "/jobs?q=Python&limit=200",
                "/jobs?min_salary=1000",
                "/jobs?work_mode=onsite",
                "/jobs?employment_type=full_time",
                "/matches?min_score=0&limit=200",
                "/matches?min_score=0&limit=1&offset=1",
                "/matches?min_score=0&source=djinni&sort=newest&limit=200",
                "/matches?min_score=0&sort=company&limit=200",
                "/matches?min_score=0&max_score=54&limit=200",
                "/matches?source=missing",
                "/sources",
            )
            async with (
                AsyncClient(
                    transport=ASGITransport(app=old_application), base_url="http://test"
                ) as old,
                AsyncClient(
                    transport=ASGITransport(app=application), base_url="http://test"
                ) as new,
            ):
                for path in paths:
                    before_response, after_response = await old.get(path), await new.get(path)
                    assert before_response.status_code == after_response.status_code == 200
                    assert before_response.json() == after_response.json(), path
            print(json.dumps({"baseline_contract_equivalence": len(paths)}), flush=True)
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://test"
        ) as client:
            for phase in ("restored", "analyzed") if analyze else ("restored",):
                if phase == "analyzed":
                    fixture.sql("ANALYZE;")
                for path in (
                    "/jobs?limit=50",
                    "/jobs?limit=200",
                    "/jobs?q=Python&limit=200",
                    "/matches?min_score=0&limit=200",
                ):
                    queries.clear()
                    wall, cpu = time.perf_counter(), time.process_time()
                    response = await client.get(path)
                    wall, cpu = time.perf_counter() - wall, time.process_time() - cpu
                    assert response.status_code == 200
                    captured = list(queries)
                    plans = []
                    with psycopg.connect(
                        url.set(drivername="postgresql").render_as_string(hide_password=False)
                    ) as connection:
                        for statement, parameters, elapsed in captured:
                            result = connection.execute(
                                "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + statement, parameters
                            ).fetchone()[0][0]
                            plans.append(
                                {
                                    "query_seconds": elapsed,
                                    "execution_ms": result["Execution Time"],
                                    "nodes": plan_summary(result["Plan"]),
                                }
                            )
                    print(
                        json.dumps(
                            {
                                "phase": phase,
                                "path": path,
                                "wall_seconds": wall,
                                "python_cpu_seconds": cpu,
                                "bytes": len(response.content),
                                "queries": plans,
                            }
                        ),
                        flush=True,
                    )
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True)
    parser.add_argument("--analyze", action="store_true")
    parser.add_argument("--compare-image", type=memory.local_image)
    options = parser.parse_args()
    data = memory.load_archive(options.archive)
    owner = uuid.uuid4().hex[:12]
    budget = memory.Budget(owner)
    fixture = memory.resources.Fixture(
        "jobradar-postgres-clean:amd64-20261016",
        "amd64",
        "api-profile",
        owner,
        cgroup_parent=budget.parent,
    )
    baseline_container = None
    try:
        budget.create()
        fixture.start()
        memory.run(
            [
                "docker",
                "update",
                "--memory",
                "640m",
                "--memory-swap",
                "640m",
                "--cpus",
                "1",
                fixture.name,
            ]
        )
        memory.resources.restore_completed_fixture(fixture, data)
        with tempfile.TemporaryDirectory(prefix="jobradar-api-baseline-") as directory:
            baseline_path = None
            if options.compare_image:
                name = f"jobradar-api-baseline-{owner}"
                memory.run(
                    [
                        "docker",
                        "create",
                        "--pull",
                        "never",
                        "--name",
                        name,
                        "--network",
                        "none",
                        "--read-only",
                        "--label",
                        f"{memory.resources.LABEL}={owner}",
                        options.compare_image,
                    ]
                )
                baseline_container = name
                memory.resources.owned(baseline_container, owner)
                baseline_path = Path(directory) / "baseline_app.py"
                memory.run(
                    [
                        "docker",
                        "cp",
                        f"{baseline_container}:/app/.venv/lib/python3.13/site-packages/jobradar/api/app.py",
                        str(baseline_path),
                    ]
                )
            asyncio.run(
                profile(fixture, analyze=options.analyze, baseline_path=baseline_path),
                loop_factory=asyncio.SelectorEventLoop if os.name == "nt" else None,
            )
    finally:
        try:
            if baseline_container is not None:
                memory.resources.owned(baseline_container, owner)
                memory.run(["docker", "rm", baseline_container])
        finally:
            try:
                fixture.close()
            finally:
                budget.close()


if __name__ == "__main__":
    main()
