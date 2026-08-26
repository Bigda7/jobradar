# JobRadar

JobRadar is a single-user, self-hosted backend that collects remote employment and freelance
opportunities, normalizes source-specific data, applies deterministic matching rules, and sends
selected results to Telegram. It is a modular monolith: the API, worker, bot, and maintenance
commands share one Python package and one PostgreSQL database while running as separate
processes.

The project does not use a language model, browser automation, or unapproved private APIs.

## Architecture

```text
Public APIs, RSS, JSON-LD, HTML sources, ATS APIs
                         |
                    BaseSource adapters
                         |
                 ingestion and reconciliation
                         |
                     PostgreSQL
                  /       |       \
          FastAPI API   matcher   Telegram bot
                             \
                         notifications
```

Production deployments are designed to place Caddy or another TLS reverse proxy in front of the
FastAPI container. PostgreSQL is not published to the host network. A PostgreSQL advisory lock
prevents a scheduled worker and a manual one-shot worker from mutating the database concurrently.

## Technology stack

- Python 3.13
- FastAPI and Uvicorn
- SQLAlchemy 2 asynchronous ORM with psycopg 3
- PostgreSQL 17
- Alembic migrations
- HTTPX source clients
- Pydantic Settings
- structlog JSON logging with secret redaction
- Docker Compose
- pytest, Ruff, and mypy

## Supported sources

All adapters implement `BaseSource` and remain independent of API and notification code.

| Source | Transport | Data type |
| --- | --- | --- |
| Djinni | public JSON-LD | employment |
| Freelancer.com | official API | freelance |
| Work.ua | public pages through a read-only text reader | employment |
| Jobs.cz | public HTML and JSON-LD | employment |
| StartupJobs.cz | public JSON endpoints | employment |
| Prace.cz | public HTML and JSON-LD | employment |
| Freelance.cz | public JSON endpoints | freelance |
| Startup.jobs | official API | employment |
| Jobicy | public API | employment |
| We Work Remotely | RSS | employment |
| DOU Jobs | RSS | employment |
| Himalayas | public API | employment |
| The Muse | official API | employment |
| Greenhouse | public job board API | employment |
| Lever | public postings API | employment |
| Ashby | public job board API | employment |
| Arbeitnow | public API | employment |
| Remotive | public API | employment |

Upwork is intentionally unsupported because custom job-search RSS feeds were discontinued. The
project does not replace them with scraping or browser automation.

## Security model

- Runtime secrets are loaded from `.env`, which is excluded from Git and Docker build contexts.
- `.env.example` contains placeholders only.
- PostgreSQL credentials are mandatory in Docker Compose.
- API pagination and numeric filters have explicit upper and lower bounds.
- SQL statements use SQLAlchemy expressions or parameterized static SQL.
- CORS and trusted hosts are exact allowlists; wildcards are rejected.
- API responses include defensive browser headers. HSTS is enabled when `APP_ENV=production`.
- Database connections have connect, pool, statement, and readiness timeouts.
- Source failures are isolated, recorded, and redacted before logging or database storage.
- Containers run as an unprivileged user with a read-only filesystem, no Linux capabilities, and
  `no-new-privileges`.

The current HTTP API is read-only and does not implement authentication. Deploy it behind a TLS
reverse proxy and add authentication before introducing mutation endpoints or multi-user access.
CORS is a browser policy and must not be treated as authentication.

## Prerequisites

- Docker Desktop or Docker Engine with Compose v2
- Git
- Python 3.13 and `uv` for development outside Docker

## Configuration

Create a private local environment file:

```powershell
Copy-Item .env.example .env
```

Set a unique PostgreSQL password and only enable sources whose credentials are configured. For a
production API, configure exact host and browser-origin allowlists:

```dotenv
APP_ENV=production
POSTGRES_PASSWORD=your_secure_password
API_ALLOWED_HOSTS=api.yourdomain.com
CORS_ALLOWED_ORIGINS=https://app.yourdomain.com
```

Do not commit `.env`, database dumps, private keys, certificates, or handoff documents.

## Run with Docker Compose

Build and start PostgreSQL, the API, worker, and Telegram bot:

```powershell
docker compose up --build -d
docker compose ps
```

The API binds to `127.0.0.1:8000` by default:

```text
http://localhost:8000/docs
```

Inspect service logs:

```powershell
docker compose logs --tail=100 api worker bot
```

Stop the stack without deleting PostgreSQL data:

```powershell
docker compose down
```

## Database migrations

The API container applies committed migrations before Uvicorn starts. Apply them manually with:

```powershell
docker compose run --rm api alembic upgrade head
```

Create and review a migration after changing ORM models:

```powershell
uv run alembic revision --autogenerate -m "describe the schema change"
uv run alembic upgrade head
```

Never use `Base.metadata.create_all()` as a production migration mechanism.

## Worker and maintenance commands

Run one forced source cycle:

```powershell
docker compose run --rm worker python -m jobradar.worker --once
```

Run maintenance operations:

```powershell
docker compose run --rm worker python -m jobradar.maintenance rescore-all
docker compose run --rm worker python -m jobradar.maintenance expire-stale
docker compose run --rm worker python -m jobradar.maintenance deduplicate-opportunities
docker compose run --rm worker python -m jobradar.maintenance reset-hidden
```

Employment listings expire after 30 days and freelance listings after 7 days unless the
opportunity is a favorite. Successful complete snapshots reconcile removed source records. Empty
or unexpectedly reduced snapshots do not deactivate the existing inventory.

## HTTP API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | process liveness |
| `GET` | `/ready` | PostgreSQL readiness |
| `GET` | `/jobs` | active opportunities with filters and pagination |
| `GET` | `/matches` | active deterministic matches with scores and canonical links |
| `GET` | `/sources` | source health and collection timestamps |

`GET /jobs` supports `q`, `work_mode`, `employment_type`, `min_salary`, `limit`, and `offset`.
`GET /matches` supports `min_score`, `limit`, and `offset`. OpenAPI documentation is available at
`/docs` and `/openapi.json`.

## Telegram bot

When Telegram polling is enabled, the bot supports `/latest`, `/all`, `/favorites`, `/stats`,
`/clear`, `/pause`, and `/resume`. Inline actions support favorite, hide, restore, and source-link
operations. User state and sent message identifiers are persisted in PostgreSQL.

Validate configured Telegram credentials with one test message:

```powershell
docker compose run --rm worker python -m jobradar.worker --test-telegram
```

## Development and quality checks

Install the locked development environment:

```powershell
uv sync --extra dev
```

Run unit tests and static checks:

```powershell
uv run pytest -m "not integration"
uv run ruff format --check .
uv run ruff check .
uv run mypy src
```

Run PostgreSQL integration tests in containers:

```powershell
docker compose --profile test run --rm test
```

## Project layout

```text
alembic/                 database migrations
deploy/systemd/          backup timer examples
scripts/                 operational scripts
src/jobradar/api/        FastAPI application and schemas
src/jobradar/db/         SQLAlchemy models, sessions, and advisory locks
src/jobradar/ingestion/  normalization, idempotency, and deduplication
src/jobradar/matching/   deterministic profile scoring
src/jobradar/notifications/ Telegram delivery and currency conversion
src/jobradar/sources/    isolated BaseSource adapters
tests/                   unit and PostgreSQL integration tests
```

## Adding a source

1. Implement `BaseSource.fetch()` and `BaseSource.normalize()` in `src/jobradar/sources`.
2. Enforce remote-only evidence before ingestion.
3. Set complete-snapshot reconciliation behavior explicitly.
4. Add bounded timeouts, pagination, rate-limit behavior, and a conservative poll interval.
5. Register the adapter in `sources/registry.py` and add validated settings.
6. Add deterministic fixture-based tests for valid, malformed, partial, and rate-limited data.

Keep source-specific network and mapping logic out of the API, matching, and notification layers.
