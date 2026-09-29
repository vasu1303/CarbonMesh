# CarbonMesh

Lightweight hackathon foundation for the CarbonMesh React frontend and FastAPI backend.

## Prerequisites

- Node.js 22.12 or newer
- npm 10 or newer
- Python 3.12 or newer

## Install the frontend

From the repository root:

```bash
npm --prefix apps/web install
```

## Install and run the backend

```powershell
cd apps/api
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
uvicorn app.main:app --reload --port 8000
```

On macOS or Linux, activate the environment with `source .venv/bin/activate`.

The API health endpoint is available at `http://localhost:8000/api/health`, and interactive API documentation is available at `http://localhost:8000/docs`.

## Connect Neon PostgreSQL

The Neon connection URL belongs in the backend environment file, not in frontend
code or a committed source file:

```powershell
cd apps/api
Copy-Item .env.example .env
```

Open `apps/api/.env`, replace the example `DATABASE_URL` with the pooled
connection string from Neon, and keep `sslmode=require` in the URL. The `.env`
file is ignored by git.

Install the backend dependencies and start the API:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
uvicorn app.main:app --reload --port 8000
```

Then open `http://localhost:8000/api/db/demo`. A successful response contains
the timestamp returned by Neon:

```json
{
  "connected": true,
  "database_time": "2026-01-01T12:00:00.000000+00:00",
  "message": "Connected to Neon PostgreSQL."
}
```

The demo uses `SELECT CURRENT_TIMESTAMP` and is the starting point for adding
application queries. Never expose `DATABASE_URL` through the React app or commit
the real value.

## SQLAlchemy code-first workflow

The backend uses SQLAlchemy 2.x models and Alembic migrations. Define ORM models
using `app.db.base.Base`, then create and apply migrations from `apps/api`:

```powershell
alembic revision --autogenerate -m "describe schema change"
alembic upgrade head
```

The connection/session setup is in `app/db/session.py`. Use a SQLAlchemy
`Session` through `session_scope()` in API services and routes; do not create
raw database connections in route handlers.

## Run the frontend

In a second terminal, from the repository root:

```bash
npm run dev
```

Open `http://localhost:3000`. Vite proxies `/api` requests to the FastAPI server on port `8000`.

## Repository layout

```text
apps/
  web/        React, TypeScript, Vite, Tailwind CSS v4, shadcn/ui
  api/        FastAPI application and backend domain placeholders
data/demo/    Synthetic hackathon fixtures
docs/         Architecture and API documentation
infra/        Deployment and infrastructure files
scripts/      Project automation
tests/e2e/    Cross-application end-to-end tests
```

Feature and domain folders intentionally contain only `.gitkeep` files until the team starts implementation.
