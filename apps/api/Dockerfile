FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN addgroup --system carbonmesh \
    && adduser --system --ingroup carbonmesh carbonmesh

COPY apps/api/pyproject.toml ./
COPY apps/api/app ./app
COPY data/demo ./app/demo_fixtures

RUN python -m pip install . \
    && mkdir -p /app/.data/sources \
    && chown -R carbonmesh:carbonmesh /app/.data

USER carbonmesh

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
