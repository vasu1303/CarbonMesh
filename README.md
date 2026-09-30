# CarbonMesh

CarbonMesh is a React and FastAPI proof of concept that connects traceable carbon
measurement to deterministic supplier comparison and human approval.

## Prerequisites

- Node.js 22.12 or newer
- npm 10 or newer
- Python 3.12 or newer

## Install the frontend

From the repository root:

```bash
npm --prefix apps/web install
```

## Install the backend

```powershell
cd apps/api
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

On macOS or Linux, activate the environment with `source .venv/bin/activate`.

## Configure and bootstrap Neon PostgreSQL

The Neon connection URL belongs in the backend environment file, not in frontend
code or a committed source file:

```powershell
cd apps/api
Copy-Item .env.example .env
```

Open `apps/api/.env` and replace the example `DATABASE_URL` with a **rotated**
pooled connection string from a clean, disposable Neon branch. Keep
`sslmode=require` in the URL. The `.env` file is ignored by git; never commit,
print, or expose the value to the frontend. If a credential has appeared in a
prompt, issue, log, or commit, rotate it in Neon before using it here.
Paste the raw URL from the Neon console, not a Markdown link or a string with
brackets; reserved password characters must remain URL-encoded.

From `apps/api`, create the five application schemas, enable pgvector, and create
the 32 SQLAlchemy ORM tables plus the database views and ledger trigger:

```powershell
python -m app.db.bootstrap
python -m app.db.bootstrap --check
```

The first command is idempotent for a complete, compatible CarbonMesh schema.
The check form is read-only and verifies the schemas, tables, pgvector column and
index, views, and immutable-ledger trigger. Both commands reject partial or
unexpected CarbonMesh structures instead of dropping or rewriting them.
The repository supplies and locally tests this workflow; it does not imply that
the schema has already been applied to your Neon branch.

### Viewing the tables in Neon

CarbonMesh does not place application tables in PostgreSQL's `public` schema.
In the Neon **Tables** page, first select the same project, branch, and database
used by `apps/api/.env`, then use the **Schema** menu to select `core`, `carbon`,
`ledger`, `semantic`, or `procurement`. Neon shows one schema at a time, so the
default `public` selection can appear empty even after bootstrap succeeds.

The Neon SQL Editor can verify every application table independently of the
Tables-page filter:

```sql
SELECT table_schema, table_name
FROM information_schema.tables
WHERE table_type = 'BASE TABLE'
  AND table_schema IN ('core', 'carbon', 'ledger', 'semantic', 'procurement')
ORDER BY table_schema, table_name;
```

The query must return 32 rows. If it does not, compare the SQL Editor's selected
branch and database with the connection string in `apps/api/.env`, then rerun
`python -m app.db.bootstrap --check` from `apps/api`.

Start the API only after bootstrap succeeds:

```powershell
python -m uvicorn app.main:app --reload --reload-dir app --port 8000
```

Then open `http://localhost:8000/api/db/demo`. A successful response confirms
the async Neon connection and includes the database timestamp:

```json
{
  "connected": true,
  "database_time": "2026-01-01T12:00:00.000000+00:00",
  "message": "Connected to Neon PostgreSQL."
}
```

Connection failures return a fixed, sanitized `503` response. The endpoint never
returns the connection URL, driver exception, host, username, or password.

For the opt-in live contract suite, use only the rotated disposable branch. The
explicit attestation prevents an accidental run against an ordinary database:

```powershell
$env:CARBONMESH_RUN_NEON_INTEGRATION_TESTS = "rotated-disposable-branch"
python -m pytest tests/integration/test_neon_database.py -q
Remove-Item Env:CARBONMESH_RUN_NEON_INTEGRATION_TESTS
```

The suite bootstraps twice, runs `--check`, exercises vector search and integrity
constraints, and never drops or resets a schema. Without that exact opt-in value
and a `*.neon.tech` URL, every live test is skipped.

## SQLAlchemy code-first workflow

The backend uses SQLAlchemy 2.x async ORM, asyncpg, and pgvector. Define models
from `app.db.base.Base`; obtain an `AsyncSession` through the database session
context manager. Routes must not construct engines, open raw connections, or
commit implicitly.

Schema creation is explicit and never runs during FastAPI startup:

```powershell
python -m app.db.bootstrap
```

This repository intentionally does not use Alembic. SQLAlchemy `create_all()`
creates missing objects but does not alter existing tables. During the POC, a
structural model change therefore requires rebuilding a disposable Neon branch;
reintroduce a migration tool before persistent environments need in-place
evolution. Never point bootstrap at a database whose existing CarbonMesh data
must be preserved unless `python -m app.db.bootstrap --check` succeeds first.

Evidence embeddings are stored in `core.evidence_items.embedding` as
`VECTOR(768)` and indexed with a partial cosine HNSW index. The exact embedding
model identifier is stored alongside each vector. See
[`docs/architecture/database.md`](docs/architecture/database.md) for the full
database contract.

The API health endpoint is `http://localhost:8000/api/health`, and interactive
API documentation is at `http://localhost:8000/docs`. The base URL returns `404`
because no root route is defined. Limiting reloads to the `app` directory avoids
virtual-environment or OneDrive changes repeatedly restarting the server.

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
  api/        FastAPI, async SQLAlchemy models, bootstrap, and domain modules
data/demo/    Synthetic hackathon fixtures
docs/         Project context, database design, architecture, and API documentation
infra/        Deployment and infrastructure files
scripts/      Project automation
tests/e2e/    Cross-application end-to-end tests
```

The database foundation is implemented; business workflows, seed data, and most
feature screens remain staged work.
