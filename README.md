# CarbonMesh

CarbonMesh is a four-module carbon-operations proof of concept covering
Measurement, Assurance, Procurement, and advisory Dispatch. The modules share
one deterministic control plane, evidence-backed ledger, bounded agent runtime,
and human Preview-Approve-Commit workflow.

The repository is in an active transition. The database contract and the
existing Measurement/Procurement backend are implemented; hourly Scope 2,
Assurance services, Dispatch optimization, and the full LangGraph runtime are
still assigned work. A table or route appearing in the target architecture is
not evidence that its business workflow already exists.

## Start locally with Docker

Prerequisites:

- Docker with Compose support;
- Node.js 22.12 or newer and npm 10 or newer for the frontend.

Create the database, install the explicit schema contract, then start the API:

```bash
docker compose up -d db
docker compose build api
docker compose run --rm api python -m app.db.bootstrap
docker compose up -d api
```

Bootstrap is deliberately separate from API startup. It creates a pristine
PostgreSQL/pgvector database with 46 tables across eight schemas and then
verifies columns, constraints, indexes, views, and the immutable-ledger trigger.
It never seeds application data.

Start the frontend from the repository root:

```bash
npm --prefix apps/web install
npm run dev
```

Open:

- frontend: `http://localhost:3000`;
- API health: `http://localhost:8000/api/health`;
- OpenAPI: `http://localhost:8000/docs`.

The optional Make targets wrap the same operations:

```bash
make db-bootstrap
make db-check
make test
make lint
```

Docker and GNU Make were not available in the environment that produced this
change, so the Compose YAML was parsed and reviewed but still needs one
clean-machine rehearsal.

## Run without Docker

Install the backend on Windows:

```powershell
cd apps/api
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

On macOS or Linux, activate the environment with
`source .venv/bin/activate`.

Set `DATABASE_URL` in the ignored `apps/api/.env` file. Local PostgreSQL may use
`sslmode=disable` only with an approved local or Docker hostname. Every remote
database must use `sslmode=require`. Never commit, print, or expose a real URL.

For a new disposable database only:

```powershell
python -m app.db.bootstrap
python -m app.db.bootstrap --check
```

Then run the API from `apps/api`:

```powershell
python -m uvicorn app.main:app --reload --reload-dir app --port 8000
```

Run the frontend in a second terminal from the repository root:

```bash
npm run dev
```

### Hosted database safety

The checked-in SQLAlchemy metadata now expects the complete eight-schema,
46-table contract. Do not point this branch at an older shared database and do
not assume `create_all()` can upgrade it. Use a fresh disposable branch, or run
a reviewed data-preserving migration after it has passed an empty-to-target and
baseline-to-target rehearsal. Keep the previous branch or a verified backup as
rollback.

FastAPI startup performs no DDL, migration, reset, or seed. `--check` is
read-only. The normal bootstrap refuses partial, extra, or structurally
incompatible CarbonMesh schemas rather than dropping or rewriting them.

## Database contract

PostgreSQL and pgvector are the only persistence services. The code-first model
contains exactly:

| Schema | Tables |
| --- | ---: |
| `core` | 9 |
| `semantic` | 5 |
| `ai` | 2 |
| `carbon` | 10 |
| `ledger` | 4 |
| `assurance` | 6 |
| `procurement` | 5 |
| `dispatch` | 5 |

Important physical changes include generic `core.approvals`, shared
`ledger.fact_bindings`, `ai.agent_runs` and `ai.agent_run_steps`, timestamped
`carbon.grid_intensity_points`, six Assurance tables, five Dispatch tables, and
`procurement.procurement_recommendations`.

The connection layer uses async SQLAlchemy and asyncpg. Hosted Neon endpoints
use authenticated TLS, disabled prepared-statement caches, and `NullPool`;
approved local hosts use the bounded SQLAlchemy pool with pre-ping. See the
[database architecture](docs/architecture/database.md) for the exact contract.

## API status

The canonical API is exposed under stable `/api/...` resource paths. OpenAPI is
the source of truth for implemented request and response shapes. Temporary
hidden aliases preserve the previous prefixed paths while frontend integration
is updated.

Implemented, substantive capabilities include:

- health and sanitized database diagnostics;
- synthetic reset for the existing Nova fixture;
- semantic context resolution;
- CSV/JSON activity and supplier imports with typed quality issues;
- deterministic purchased-material Measurement, evidence, lineage, and audit;
- supplier exploration, hard-constraint Procurement scoring, impact, and
  hash-bound approval;
- persisted bounded run state with SSE replay;
- Electricity Maps historical retrieval using the existing factor cache;
- tenant-scoped ledger event search and detail with safe evidence summaries and
  immediate lineage neighbors.

Still missing or incomplete:

- the Maverick four-module fixture and golden replay manifest;
- hourly Scope 2 calculation using `carbon.grid_intensity_points`;
- Assurance repositories/services, retrieval, citations, validation, and
  approval;
- Dispatch forecast, constraint, optimizer, recommendation, and approval;
- generic approval/fact-binding services beyond current Procurement use;
- five LangGraph graphs, 24 typed tools, durable resume, real provider calls,
  and persisted run-step telemetry;
- dependency-aware readiness and agent-sustainability metrics.

See the [current API reference](docs/api/reference.md),
[target API contract](docs/api/contract.md), and
[implementation plan](docs/planning/implementation-plan.md). The evidence for
the remaining-work assessment is in the
[current code audit](docs/planning/current-code-audit.md).

## Synthetic data reset

The current reset endpoint is destructive and is only for an empty or
all-synthetic disposable database. Set a random `DEMO_RESET_TOKEN` of at least
16 characters in `apps/api/.env`, start the API, and call:

```powershell
$headers = @{ "X-Demo-Reset-Token" = $env:DEMO_RESET_TOKEN }
Invoke-RestMethod -Method Post -Headers $headers http://localhost:8000/api/demo/reset
```

It returns `409 demo_reset_blocked` when any non-synthetic company exists. The
current fixture is the earlier Nova packaging scenario; replacing it with the
four-module Maverick fixture is explicitly assigned to the Data/Dispatch/QA
owner.

## Optional external providers

Set `ELECTRICITY_MAPS_API_TOKEN` only in the backend environment. Provider
responses are bounded and snapshotted as evidence; secrets and response bodies
must not enter logs or API errors.

The model layer has provider-neutral OpenAI, Gemini, Anthropic, and OpenRouter
HTTP adapters. Configure exactly one provider and an explicit model identifier,
for example:

```dotenv
AI_PROVIDER=gemini
GEMINI_API_KEY=replace-locally
GEMINI_MODEL=replace-with-a-current-model-id
```

The current deterministic orchestrator does not call these adapters yet. Model
identifiers remain configuration because provider catalogues change.

## Quality checks

Backend, from `apps/api`:

```powershell
python -m ruff check app tests
python -m pytest -q
```

Frontend, from the repository root:

```bash
npm run typecheck
npm run lint
npm run build
```

Live Neon tests are opt-in and must use only a disposable branch. API E2E tests
may drop and rebuild application schemas when explicitly configured; never aim
them at data that must be preserved.

## Repository map

```text
apps/web/                    React, TypeScript, Vite, shadcn/ui
apps/api/                    FastAPI, domain services, SQLAlchemy models
data/demo/                   Synthetic fixtures and expected results
docs/architecture/           System and database contracts
docs/api/                    Current and target HTTP contracts
docs/planning/               Code audit and five-person implementation plan
docker-compose.yml           Local PostgreSQL/pgvector and API
.github/workflows/quality.yml  Backend and frontend quality gates
```

The repository-wide engineering contract is [AGENTS.md](AGENTS.md), and the
short human summary is [docs/PROJECT_CONTEXT.md](docs/PROJECT_CONTEXT.md).
