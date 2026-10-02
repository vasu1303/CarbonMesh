# CarbonMesh

CarbonMesh is a four-module carbon-operations proof of concept covering
Measurement, Assurance, Procurement, and advisory Dispatch. The modules share
one deterministic control plane, evidence-backed ledger, bounded agent runtime,
and human approval records.

The repository is in an active transition. The database contract,
purchased-material Measurement, Assurance draft/evidence workflow, Procurement,
grid integrations, and advisory Dispatch backend are implemented. Hourly Scope
2, generic approval decisions for every target type, and the full LangGraph
runtime are still assigned work. A table, fixture, or expected result appearing
in the target architecture is not evidence that its business workflow already
exists.

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

Dashboard and Measurements use authenticated API data only, including during
development. Sign in using an operator-provisioned key from `AUTH_ACCESS_KEYS`
in the ignored `apps/api/.env` (use the analyst grant for these screens).
In the seeded local setup, the analyst actor UUID ends with `000004`.
The company must match `VITE_COMPANY_ID`; site/period IDs must already exist.
The browser uses the API's HttpOnly session cookie, not a token in localStorage.
Never place access keys in `VITE_*` variables or commit them. Session expiry
returns to sign-in and clears cached workspace data. Seeded database records
remain labeled synthetic; no frontend preview or fallback dataset exists.

Dashboard and Measurements use authenticated API data only, including during
development. Sign in using an operator-provisioned key from `AUTH_ACCESS_KEYS`
in the ignored `apps/api/.env` (use the analyst grant for these screens).
In the seeded local setup, the analyst actor UUID ends with `000004`.
The company must match `VITE_COMPANY_ID`; site/period IDs must already exist.
The browser uses the API's HttpOnly session cookie, not a token in localStorage.
Never place access keys in `VITE_*` variables or commit them. Session expiry
returns to sign-in and clears cached workspace data. Seeded database records
remain labeled synthetic; no frontend preview or fallback dataset exists.

## Demo data and provider modes

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

For a small additive API test dataset on an existing synthetic Maverick tenant,
run the following from `apps/api` after configuring its database connection:

```bash
python -m app.modules.demo.populate --endpoint YOUR_EXACT_ENDPOINT --database carbonmesh
python -m app.modules.demo.populate --endpoint YOUR_EXACT_ENDPOINT --database carbonmesh --apply
```

The first command only verifies the schema, tenant, and row counts. `--apply`
imports `data/demo/api-smoke-material-v1.csv`: three synthetic material receipts
and one deliberately invalid quantity. The existing import and Measurement
services create quality issues, calculated values, confidence, hashes, and ledger
lineage. No carbon result is hard-coded in the script. Repeating the same command
reuses the import and calculation results. Each service owns its transaction, so
an interrupted run can be resumed; the whole script is not one atomic transaction.
Changing the fixture requires a new filename and idempotency key.

This command refuses a different target or a missing/inactive/non-synthetic
Maverick tenant. It never bootstraps, resets, deletes, overwrites reference data,
approves recommendations, or calls an external model/grid provider. Existing
suppliers, standards, loads, recommendations, and approvals remain untouched.
Scope 2 and Assurance results still require suitable timestamp-aligned grid and
activity data; empty tables are not filled with fabricated artifacts.

To verify the API, exchange a locally provisioned access key at
`POST /api/auth/session`, then use the returned session for `GET /api/measurements`
with the company/site/period selectors. Inspect each result at
`GET /api/measurements/{id}` and `GET /api/measurements/{id}/lineage`.
The response IDs, exact values, output hashes, and ledger IDs should match Neon.
API-backed synthetic test data is still synthetic; its labels must remain even
after frontend-only preview data is removed. Local HTTP development uses
`AUTH_COOKIE_SECURE=false`; deployed HTTPS environments must keep it true.

For a small additive API test dataset on an existing synthetic Maverick tenant,
run the following from `apps/api` after configuring its database connection:

```bash
python -m app.modules.demo.populate --endpoint YOUR_EXACT_ENDPOINT --database carbonmesh
python -m app.modules.demo.populate --endpoint YOUR_EXACT_ENDPOINT --database carbonmesh --apply
```

The first command only verifies the schema, tenant, and row counts. `--apply`
imports `data/demo/api-smoke-material-v1.csv`: three synthetic material receipts
and one deliberately invalid quantity. The existing import and Measurement
services create quality issues, calculated values, confidence, hashes, and ledger
lineage. No carbon result is hard-coded in the script. Repeating the same command
reuses the import and calculation results. Each service owns its transaction, so
an interrupted run can be resumed; the whole script is not one atomic transaction.
Changing the fixture requires a new filename and idempotency key.

This command refuses a different target or a missing/inactive/non-synthetic
Maverick tenant. It never bootstraps, resets, deletes, overwrites reference data,
approves recommendations, or calls an external model/grid provider. Existing
suppliers, standards, loads, recommendations, and approvals remain untouched.
Scope 2 and Assurance results still require suitable timestamp-aligned grid and
activity data; empty tables are not filled with fabricated artifacts.

To verify the API, exchange a locally provisioned access key at
`POST /api/auth/session`, then use the returned session for `GET /api/measurements`
with the company/site/period selectors. Inspect each result at
`GET /api/measurements/{id}` and `GET /api/measurements/{id}/lineage`.
The response IDs, exact values, output hashes, and ledger IDs should match Neon.
API-backed synthetic test data is still synthetic; its labels must remain even
after frontend-only preview data is removed. Local HTTP development uses
`AUTH_COOKIE_SECURE=false`; deployed HTTPS environments must keep it true.

Grid calls default to **live** data. Offline demos must explicitly choose
`mode: "fixture"` for history, `source_mode: "fixture"` for forecast sync, and
`context.grid_source_mode: "fixture"` for an agent request. Agent source mode is
bound into the run signature. Live failures never silently switch to fixtures.

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
the source of truth for implemented request and response shapes. Each
implemented operation has one canonical path; obsolete supplier,
recommendation, and module-prefixed aliases are not mounted.

Implemented, substantive capabilities include:

- health and sanitized database diagnostics;
- guarded synthetic reset for Maverick Manufacturing, Plant B, Q3 2026;
- semantic context resolution;
- CSV/JSON activity and supplier imports with typed quality issues;
- bounded text/CSV/JSON/PDF source upload with checksums, extraction, chunking,
  deterministic embeddings, and tenant/context-filtered evidence retrieval;
- deterministic purchased-material Measurement, evidence, lineage, and audit;
- Assurance standards and requirements, immutable-context disclosure drafts,
  atomic claims, fact bindings, citations, evidence gaps, staleness checks,
  structured evidence packs, and eligible generic approval previews;
- supplier exploration, hard-constraint Procurement scoring, impact, and
  hash-bound approval;
- Electricity Maps historical sync persisted as timestamped
  `carbon.grid_intensity_points`, plus a provenance-aware latest-point read;
- fixture and live Electricity Maps forecast adapters with immutable Dispatch
  forecast persistence;
- deterministic advisory Dispatch load discovery, frozen scenarios, complete
  consecutive-window enumeration, hard constraints, impact, evidence, lineage,
  and exact approval previews;
- persisted bounded run state with SSE replay;
- tenant-scoped ledger event search and detail with safe evidence summaries and
  immediate lineage neighbors.

The fully synthetic `data/demo` assets cover the four-module Maverick target:
90 days of hourly electricity, recycled-aluminium Procurement inputs,
Assurance source material, history and forecast snapshots, and Batch Process 7.
The versioned manifest and expected-results file are generated deterministically
and include fixture, method, input, output, context, and analysis hashes.

Still missing or incomplete:

- hourly Scope 2 calculation and persistence using
  `carbon.grid_intensity_points`; its golden fixture result does not substitute
  for the missing service;
- generic approval list/detail/decision handling beyond the current
  Procurement-specific queue and commit service; Assurance and Dispatch create
  exact previews but cannot be decided through that shared endpoint;
- persisted fact bindings for direct non-agent Procurement and Dispatch calls;
- five LangGraph graphs, 24 typed tools, durable resume, model-provider
  execution from the agent runtime, and persisted run-step telemetry;
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
reset installs the stable Maverick Manufacturing / Plant B / Q3 2026 seed,
including recycled-aluminium products, Assurance definitions, and the advisory
Batch Process 7 load and hard constraints. History and forecast points are
loaded through their sync endpoints so their provider-snapshot provenance is
preserved.

## Optional external providers

Set `ELECTRICITY_MAPS_API_TOKEN` only in the backend environment. Provider
responses are bounded and snapshotted as evidence; secrets and response bodies
must not enter logs or API errors. Dispatch forecast sync defaults to the
credential-free packaged fixture; request `source_mode: "live"` to use the
configured Electricity Maps v4 provider.

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
