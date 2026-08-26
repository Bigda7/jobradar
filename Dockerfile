FROM ghcr.io/astral-sh/uv:0.12.5@sha256:e85be844203885286c60ffad8a858d48afb6c5a5c237ca0e67f12e74b8f174b1 AS uv

FROM python:3.14.7-alpine3.23@sha256:6b8f06d04d5305c1d1288435388df9165ab41e681fae6439d6349d8053cc3f83 AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

COPY --from=uv /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock README.md ./
COPY src ./src

RUN uv sync --frozen --no-dev --no-editable

FROM python:3.14.7-alpine3.23@sha256:6b8f06d04d5305c1d1288435388df9165ab41e681fae6439d6349d8053cc3f83 AS runtime

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
