# CarbonMesh

CarbonMesh connects Measurement, Assurance, Procurement, and advisory Dispatch
through deterministic calculations, an append-only evidence ledger, bounded
agent workflows, and explicit approval decisions. The backend uses FastAPI,
SQLAlchemy, PostgreSQL with pgvector, and a configurable model provider.

## Using the workspace

Start with the [complete user walkthrough](docs/user-guide.md). The normal flow is
**Upload data -> Check data -> Emissions -> Plan -> Approvals**. Named selectors
replace manual identifier entry, and evidence is shown as a connected diagram.
The workspace has no sign-in screen, API-status badge, or System screen.

This no-login experience is for a trusted local/demo environment. Keep the API
private. Database records remain labeled synthetic where applicable; the frontend
does not invent data or silently substitute fixtures.

## Run the backend with Neon

Requires Python 3.12 or newer. From the repository root, on Windows:

```powershell
cd apps/api
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
if (!(Test-Path .env)) { Copy-Item .env.example .env }
```

On macOS/Linux, create the environment with `python3 -m venv .venv` and activate
it with `source .venv/bin/activate`.

Configure `apps/api/.env` using [.env.example](apps/api/.env.example):

- `DATABASE_URL`: Neon connection URL for the `carbonmesh` database, with
  `sslmode=require`.
- `AI_PROVIDER`, the selected provider's API key, and its model identifier.
  For OpenAI, use `AI_PROVIDER=openai`, `OPENAI_API_KEY`, and `OPENAI_MODEL`.
- `ELECTRICITY_MAPS_LIVE_ENABLED=false`: leave disabled for this workspace.
  Stored grid data and forecasts remain usable. A stored provider token alone
  does not permit live calls.
- `DEMO_RESET_TOKEN`: required only for the guarded demo reset endpoint.
- `AUTH_REQUIRED=false`: trusted local/demo mode with no frontend login.
  Named actors still identify commands and decisions; they are not authentication.
  Do not expose this mode publicly. Backend authentication remains available
  for separately secured API deployments using `AUTH_SIGNING_KEY` and `AUTH_ACCESS_KEYS`.
- `EMBEDDING_PROVIDER=openai` and `EMBEDDING_MODEL`: evidence embeddings use
  the existing OpenAI key and 768 dimensions. `hash` is explicit offline mode.

The application loads this file directly. Keep credentials local; `.env` is
ignored by Git and is excluded from the Docker build context.

The standard browser workflow uses no access key. Configure explicit
`CORS_ORIGINS` if the frontend and API run on different origins. For a separate
API deployment with `AUTH_REQUIRED=true`, the existing signed-session API and
its security tests remain in place; this simplified frontend does not supply a
login flow for that deployment.

Verify an existing database and start the API from `apps/api`:

```bash
python -m app.db.bootstrap --check
python -m uvicorn app.main:app --reload --reload-dir app --port 8000
```

For a **new, disposable database**, run `python -m app.db.bootstrap` once before
verification. It creates the eight-schema, 46-table contract with pgvector,
constraints, views, and the ledger trigger. Bootstrap does not seed data and
cannot upgrade an older schema in place. Application startup never performs DDL.

- API explorer: <http://localhost:8000/docs>
- OpenAPI schema: <http://localhost:8000/openapi.json>
- Health: <http://localhost:8000/api/health>
- Database readiness: <http://localhost:8000/api/health/ready>

## Run with local Docker PostgreSQL

Create `apps/api/.env` first, as above. Compose loads its provider settings and
overrides `DATABASE_URL` to use the local `db` service instead of Neon.

```bash
docker compose up -d db
docker compose build api
docker compose run --rm api python -m app.db.bootstrap
docker compose run --rm api python -m app.modules.demo.cli seed --endpoint db --database carbonmesh
docker compose up -d api
```

The root Compose file runs PostgreSQL and the API; `apps/api/Dockerfile` builds
only the API image. The build context is the repository root so the image can
include the single canonical `data/demo` bundle. The Docker ignore file excludes
frontend code, development environments, tests, and secrets.
Original uploaded documents persist in the `carbonmesh_source_documents` volume.
Back up that volume alongside the database; source hashes alone cannot recreate
original document bytes. Compose fixes the container's source storage path to
that volume and publishes its API/database ports on loopback only.

To build the API image directly:

```bash
docker build -f apps/api/Dockerfile -t carbonmesh-api .
```

## Run the frontend

Requires Node.js 22.12 or newer and npm 10 or newer. From the repository root,
in a separate terminal:

```bash
npm --prefix apps/web install
npm run dev
```

The frontend runs at <http://localhost:3000>. The Vite proxy forwards `/api`
requests to the backend on port 8000. Optional workspace configuration is in
`apps/web/.env.example`; internal references stay in configuration, not user forms.

Before pushing, run `npm run typecheck`, `npm run lint`, and `npm run build`.

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

To verify API data, use `GET /api/measurements` with the configured workspace
selectors, then inspect the result and its lineage. Internal references, exact
values, output hashes and ledger references must match the database. API-backed
synthetic data remains synthetic. Authentication is optional only in the trusted
local/demo configuration described above.

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

To verify API data, use `GET /api/measurements` with the configured workspace
selectors, then inspect the result and its lineage. Internal references, exact
values, output hashes and ledger references must match the database. API-backed
synthetic data remains synthetic. Authentication is optional only in the trusted
local/demo configuration described above.

Electricity Maps live calls are **disabled by default**, including agent calls.
The UI has no provider test or synchronization controls. Stored measurements,
history and forecast snapshots remain usable. An operator must deliberately
enable live calls outside the normal UI when needed. Fixture requests still
require explicit opt-in and retain synthetic provenance; disabled or failed
live requests never silently fall back to fixtures.

## Demo data and provider modes

[data/demo](data/demo) contains versioned synthetic Maverick Manufacturing,
Plant B, Q3 2026 inputs. CSV/JSON files here are application fixtures and test
inputs, not generated verification reports. The hourly file covers 90 days,
includes intentional quality issues, and cannot prove a complete 92-day Q3 total.
The separate `complete-q3-manifest-v1.json` bundle supplies a complete 2,208-hour
successful path. Its source files, factor snapshots, and calculated expectations
are independently versioned; the original quality-case bundle remains unchanged.

Seed a **verified empty target** from `apps/api`:

```bash
python -m app.modules.demo.cli seed --endpoint YOUR_EXACT_ENDPOINT --database carbonmesh
```

Use the configured hostname's first label without `-pooler`. For local Compose,
run the seed command inside the API container as shown above; it confirms `db`
and `carbonmesh` instead of using the host's Neon configuration. Seeding refuses
a target mismatch or existing data. For an explicitly
disposable, all-synthetic database, the same command supports `reset` instead of
`seed`. Reset is destructive and refuses non-synthetic data.

For an existing database, use the import and domain APIs in `/docs` to add data
without resetting it. Activity/supplier imports, source uploads, calculations,
scenarios, and approvals persist their own lineage and idempotency records.

Electricity Maps live calls are **disabled by default**, including agent calls.
The UI has no provider test or synchronization controls. Stored measurements,
history and forecast snapshots remain usable. An operator must deliberately
enable live calls outside the normal UI when needed. Fixture requests still
require explicit opt-in and retain synthetic provenance; disabled or failed
live requests never silently fall back to fixtures.

For a fresh agent run, supply `context.fresh_inputs` with source measurement
selectors, bounded history dates, and explicit procurement/dispatch inputs. The
runtime calculates measurements and creates drafts/scenarios through domain
services. Existing artifact-ID requests remain supported. `previous_run_id`
supports a bounded, tenant/actor-scoped follow-up; changed scope gets a new
signature and cannot reuse the earlier artifact IDs.

Source uploads retain original bytes and index text with the selected embedding
provider. Supplier-import evidence can be indexed explicitly through
`POST /api/sources/{document_id}/index`. Downloads use the tenant-scoped
`GET /api/sources/{document_id}/content` route. Model/provider failures are typed
errors; upload/indexing never silently substitutes hash embeddings.

The model selects bounded workflows; services calculate business values and
validate citations, hard constraints, hashes, and approvals. Dispatch is advisory
and has no equipment-control endpoint. Provider failures and exhausted budgets
are explicit terminal results. Remote database latency can exhaust the frozen
agent budget even when the same workflow completes locally.

Set `TELEMETRY_ENABLED=true` for correlated request, graph/tool, model/grid/
embedding, and database-operation spans and structured request logs. Configure
`OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` to export to a collector. SQL text, request
bodies, query strings, credentials, and exception messages are excluded from
these spans. Persisted run traces work without a collector. Sustainability
metrics expose approved **projected** benefits with ledger IDs and frozen token
proxy assumptions; the benefit ratio is absent when assumptions are incomplete,
incomparable, or the estimated footprint is zero.

Regenerate fixtures only when intentionally changing the synthetic data contract:

```bash
python scripts/generate_demo_fixtures.py
```

The generator writes the canonical bundle; Docker copies it into the image at
build time. Fixture and method versions, hashes, and deterministic replay tests
protect persisted results against silent changes.

## Tests and code quality

From `apps/api`, with the development environment activated:

```bash
python -m ruff check app tests
python -m pytest -q
```

Unit and API tests live under `apps/api/tests`. API E2E tests use a disposable
local PostgreSQL cluster; put `initdb` and `pg_ctl` on `PATH`. They never reset the
configured Neon database. CI uses a disposable PostgreSQL service and requires
the real pgvector extension. Local tests without that extension use test-only
array storage; the CI vector gate exercises cosine retrieval and HNSW. Live Neon
and paid-model tests require explicit opt-in; see the environment guards in their
test modules.

The GitHub workflow also checks frontend lint, types, and build. Keep it enabled
to catch regressions before merging. Its backend job bootstraps and verifies the
disposable database, runs lint/tests, and checks the built API image and packaged
fixtures. Optional Make targets wrap the existing commands: `test`, `lint`,
`db-bootstrap`, `db-check`, `seed-demo`, and `reset-demo`. Database Make targets
use Compose; pass `ENDPOINT=db DATABASE=carbonmesh` to seed/reset. Hosted Neon
operators use the explicit Python commands above.

Generated test output, coverage, local reports, editor settings, and credentials
are excluded by `.gitignore`. They are not application source and should not be
committed.

## Repository layout

```text
apps/api/                 API source, Dockerfile, configuration, and backend tests
apps/web/                 Frontend application
data/demo/               Canonical synthetic inputs and expected results
scripts/                  Deterministic demo fixture generator
docs/architecture/        System and database contracts
docs/api/                 API contract and usage notes
.github/workflows/        Automated quality checks
```

See [AGENTS.md](AGENTS.md) for project rules and ownership,
[system architecture](docs/architecture/system.md),
[database architecture](docs/architecture/database.md), and the
[API contract](docs/api/contract.md).
