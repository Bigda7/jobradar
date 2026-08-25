FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN addgroup --system jobradar && adduser --system --ingroup jobradar jobradar

COPY --chown=jobradar:jobradar pyproject.toml README.md ./
COPY --chown=jobradar:jobradar companies.yaml ./
COPY --chown=jobradar:jobradar src ./src
COPY --chown=jobradar:jobradar tests ./tests
COPY --chown=jobradar:jobradar alembic.ini ./
COPY --chown=jobradar:jobradar alembic ./alembic

RUN python -m pip install --no-cache-dir .

USER jobradar

CMD ["uvicorn", "jobradar.api.app:app", "--host", "0.0.0.0", "--port", "8000"]

FROM runtime AS test

USER root

RUN python -m pip install --no-cache-dir ".[dev]"

USER jobradar
