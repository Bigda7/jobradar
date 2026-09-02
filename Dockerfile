FROM ghcr.io/astral-sh/uv:0.12.9@sha256:8b940d3a9d65bed080436972241af2e21c84b5e8c9193f7014ed71479ee795ff AS uv

FROM python:3.13.15-alpine3.23@sha256:7ea3f82de8ea6d4fb7e5d2bbe3fe3c9d931700b7a529f1fe5769e42abe514ca1 AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

COPY --from=uv /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock README.md ./
COPY src ./src

RUN uv sync --frozen --no-dev --no-editable

FROM python:3.13.15-alpine3.23@sha256:7ea3f82de8ea6d4fb7e5d2bbe3fe3c9d931700b7a529f1fe5769e42abe514ca1 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

RUN apk add --no-cache openssl=3.5.8-r0 sqlite-libs=3.53.4-r0 \
    && addgroup -S jobradar \
    && adduser -S -D -H -G jobradar jobradar

COPY --from=builder --chown=jobradar:jobradar /app/.venv /app/.venv
COPY --chown=jobradar:jobradar companies.yaml ./
COPY --chown=jobradar:jobradar alembic.ini ./
COPY --chown=jobradar:jobradar alembic ./alembic

USER jobradar

CMD ["uvicorn", "jobradar.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header"]

FROM builder AS test-builder

RUN uv sync --frozen --extra dev --no-editable

FROM runtime AS test

COPY --from=test-builder --chown=jobradar:jobradar /app/.venv /app/.venv
COPY --chown=jobradar:jobradar pyproject.toml ./
COPY --chown=jobradar:jobradar tests ./tests
