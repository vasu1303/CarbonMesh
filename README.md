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

## Configure the existing Neon PostgreSQL database

The Neon connection URL belongs in the backend environment file, not in frontend
code or a committed source file:

```powershell
cd apps/api
Copy-Item .env.example .env
```

Open `apps/api/.env` and replace the example `DATABASE_URL` with the pooled
connection string for the already-provisioned shared development database,
obtained through the team's approved secret-sharing channel. Keep
`sslmode=require` in the URL. The `.env` file is ignored by git; never commit,
print, or expose the value to the frontend. If a credential has appeared in a
prompt, issue, log, or commit, rotate it in Neon before using it here.
Paste the raw URL from the Neon console, not a Markdown link or a string with
brackets; reserved password characters must remain URL-encoded.

The shared development database is already provisioned. Normal contributors do
not run bootstrap: after installing dependencies and configuring `.env`, start
the API directly.

### Provisioning a new database (operator only)

Only an operator creating a new or reset disposable database should run the
bootstrap commands that enable pgvector and install the five schemas, 32 tables,
views, and immutable-ledger trigger:

```powershell
python -m app.db.bootstrap
python -m app.db.bootstrap --check
```

The first command is idempotent for a complete, compatible CarbonMesh schema.
The check form is read-only and verifies the schemas, tables, pgvector column and
index, views, and immutable-ledger trigger. Both commands reject partial or
unexpected CarbonMesh structures instead of dropping or rewriting them.
Do not run the provisioning command against the shared development database as
part of routine startup. Use `--check` only when an operator needs read-only
contract verification or when diagnosing a database mismatch.

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

With the existing shared database configured, start the API directly:

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

Schema creation is explicit, operator-owned, and never runs during FastAPI
startup. The following command is for a new or reset disposable database, not
normal development against the shared database:

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

The API health endpoints are `http://localhost:8000/api/health` and
`http://localhost:8000/api/v1/health`. Interactive API documentation is at
`http://localhost:8000/docs`. The base URL returns `404` because no root route is
defined. Limiting reloads to the `app` directory avoids virtual-environment or
OneDrive changes repeatedly restarting the server.

## POC API workflows

The `/api/v1` surface implements measurement lineage, supplier exploration,
deterministic procurement assessment and scenarios, bounded agent runs with SSE,
hash-bound approvals, audit history, and Electricity Maps grid-intensity caching.
Tenant-owned query routes require `company_id`; command payloads carry their tenant
and actor context. OpenAPI documents the exact request and response contracts; the
route overview is in [`docs/api/poc-v1.md`](docs/api/poc-v1.md).

Set `ELECTRICITY_MAPS_API_TOKEN` only in `apps/api/.env` to use the live integration.
The API never returns that credential. Grid-intensity syncs are bounded to ten days,
snapshot the provider response, normalize gCO2eq/kWh to kgCO2e/kWh, and persist
provenance in the existing evidence and factor tables.

### Configure an AI model provider

The agent layer exposes one provider-neutral model interface for OpenAI, Gemini,
Anthropic, and OpenRouter. Put one provider's key and model identifier in
`apps/api/.env`; model identifiers have no source-code defaults because provider
catalogues change over time. For example:

```dotenv
AI_PROVIDER=openai
OPENAI_API_KEY=replace-locally
OPENAI_MODEL=replace-with-a-current-model-id
```

When `AI_PROVIDER=auto` (or blank), the factory selects a provider only if exactly
one of `OPENAI_API_KEY`, `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, or
`OPENROUTER_API_KEY` is configured. If multiple credentials are present, set
`AI_PROVIDER` explicitly. The factory never silently fails over to another vendor.

Agent code calls the same interface regardless of provider:

```python
from app.modules.agents.llm import AIMessage, AIRequest, build_ai_model

model = build_ai_model()
result = await model.generate(
    AIRequest(messages=(AIMessage(role="user", content="Summarize these facts."),))
)
```

Construction is lazy and makes no network call, so deterministic workflows and API
startup do not require an AI credential. Provider calls have a bounded timeout and
response size, return normalized token/latency metadata, and expose only sanitized
errors. Prompts, provider response bodies, and credentials are not retained in the
normalized result.

The API E2E suite arranges the minimum synthetic Nova Components / Plant B / Q3
2026 prerequisite records only inside its disposable database, then exercises all
requested endpoints through HTTP. Command responses are persisted and read back
through their corresponding query endpoints. The application never seeds data at
startup or while serving these APIs. By default, the suite starts a temporary local
PostgreSQL server and never reads `DATABASE_URL`:

```powershell
cd apps/api
python -m pytest tests/e2e -q
```

The local path requires `initdb` and `pg_ctl` on `PATH`. To use an explicitly
disposable PostgreSQL database instead, set `CARBONMESH_E2E_DATABASE_URL` and also
set `CARBONMESH_RUN_API_E2E_TESTS=disposable-database`. The suite drops and rebuilds
the five CarbonMesh schemas, so never opt in with a database that contains data to
preserve. Electricity Maps is replaced by a deterministic fake provider in E2E
tests; no external credential or network call is required.

Run all backend quality checks from `apps/api`:

```powershell
ruff check app tests
pytest -q
```

## Reset and use the synthetic POC API

The versioned readiness endpoint is `GET /api/v1/health`; the legacy
`GET /api/health` path remains available to the existing frontend. On an empty
or all-synthetic disposable database, seed the documented Nova Components demo:

```powershell
$headers = @{ "X-Demo-Reset-Token" = $env:DEMO_RESET_TOKEN }
Invoke-RestMethod -Method Post -Headers $headers http://localhost:8000/api/v1/demo/reset
```

Set a random `DEMO_RESET_TOKEN` of at least 16 characters in `apps/api/.env`
before starting the API; without it the endpoint is disabled. The reset is also
blocked with `409 demo_reset_blocked` if any non-synthetic company exists. It
atomically replaces all demo rows, so use it only on a disposable rehearsal
database. The response returns the stable company, Plant B, and Q3 2026
identifiers needed by the other endpoints.

Implemented POC endpoints include:

- `GET /api/v1/semantic/metrics` and `POST /api/v1/context/resolve`
- `POST /api/v1/imports/activity` and `POST /api/v1/imports/suppliers`
- `GET /api/v1/imports/{import_id}` and `GET /api/v1/data-quality/issues`
- `POST /api/v1/measurements/calculate`
- `GET /api/v1/measurements` and `GET /api/v1/measurements/{measurement_id}`

Import commands use a typed JSON envelope containing either CSV text or JSON
rows. The exact request and response contracts, filters, status codes, and
examples are available in the OpenAPI UI at `http://localhost:8000/docs`.

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

The database foundation, synthetic reset/seed path, import/data-quality APIs,
semantic context APIs, and deterministic measurement APIs are implemented.
Procurement, approvals, agents, and most feature screens remain staged work.
The database and P0 backend workflows are implemented. Measurement calculation and
import APIs beyond this route batch, production model execution, and most frontend
feature screens remain staged work.
