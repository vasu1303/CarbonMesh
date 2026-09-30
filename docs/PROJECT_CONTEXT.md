# CarbonMesh Project Context

Last updated: 2026-09-30

## Purpose of this file

This is the shared implementation context for the CarbonMesh hackathon team. Give this file to every teammate and to any coding agent working on the repository. It records the agreed product scope, architecture, stack, engineering rules, current repository state, and four-day delivery plan.

This file is context, not an instruction to implement everything at once. Every coding task must still follow the latest explicit request from the developer assigning that task and must first inspect the current repository state.

## Source precedence

When sources disagree, use this order:

1. The latest explicit instruction from the team/user.
2. This `PROJECT_CONTEXT.md` file.
3. `CarbonMesh_4_Day_Hackathon_POC_Plan_v3.docx` for the four-day implementation scope.
4. `CarbonMesh_Technology_Stack_and_Architecture.docx` for technology decisions.
5. `CarbonMesh_Problem_and_Solution.pdf` for the broader product vision and problem framing.
6. The frontend and backend architecture Markdown files as structural references only.
7. `frontend-design-system.md` as a design-principles reference, subject to the shadcn/ui rule below.

Important conflict resolutions:

- The broad vision describes four agents. The four-day POC builds only Measurement and Procurement, joined through one ledger and one human approval flow.
- Assurance and Dispatch are Phase 2. Do not create placeholder screens, tables, or agents for them during the hackathon.
- Backend examples using Flask and MongoDB are patterns only. CarbonMesh uses FastAPI, Pydantic, SQLAlchemy, asyncpg, PostgreSQL, and pgvector.
- The approved five-schema, 32-table code-first model in `docs/architecture/database.md` supersedes older database catalogues. The POC uses explicit SQLAlchemy bootstrap commands and no Alembic migrations.
- The frontend reference describes a custom component catalogue. The current team decision is to use shadcn/ui components and not build a replacement custom design-system component library.
- The POC plan is more specific and newer than the broad solution brief. It is authoritative for what must ship.

## One-sentence product definition

CarbonMesh is an agent-assisted carbon operations application that turns purchased-material activity into a traceable emissions measurement, uses that verified measurement to compare lower-carbon supplier products, and requires a human to approve or reject the resulting procurement recommendation.

## Problem and users

Sustainability and procurement work is fragmented across spreadsheets, carbon tools, supplier documents, and sourcing workflows. Emissions are often calculated after decisions are made, evidence is difficult to trace, and the same facts are repeatedly entered or re-estimated.

Primary users:

- Sustainability analyst: imports activity data, validates factors, calculates emissions, and investigates data-quality issues.
- Procurement manager: compares supplier products under cost, lead-time, material, and circularity constraints.
- Approver: reviews exact facts, evidence, scores, hashes, and trade-offs before approving or rejecting a recommendation.
- Judge/auditor: traces any displayed value back to its source, method, evidence, ledger event, and decision history.

The key differentiation is not another carbon dashboard. It is the handoff between measurement and procurement through one lineage-tracked ledger.

## Four-day POC objective

Build one connected vertical slice:

1. Upload synthetic purchased-material activity and supplier/product evidence.
2. Validate and normalize the activity data.
3. Select a versioned emissions factor.
4. Calculate emissions deterministically.
5. Persist calculations, evidence links, facts, lineage, and audit information.
6. Compare feasible supplier alternatives with a versioned deterministic scoring model.
7. Calculate projected footprint, avoided emissions, cost delta, and lead-time delta.
8. Generate an evidence-bound explanation.
9. Require a human approval or rejection.
10. Show the complete run trace, including model calls, tool calls, retries, tokens, latency, and terminal state.

The judge-facing question is:

> What is the verified emissions baseline for Plant B's packaging purchase, and which feasible supplier/product would reduce it the most without increasing unit cost by more than 5%?

## Success criterion

A judge can select any displayed carbon number and follow it back to:

- the uploaded source row;
- normalized activity;
- factor and evidence source;
- deterministic formula and calculation run;
- ledger event and lineage edges;
- recommendation and component scores;
- approval record and audit history.

The result must be reproducible after a database reset and seed import. Important values must never be hard-coded in the frontend.

## Demo dataset and expected result

All demo data is synthetic and must be visibly labelled as synthetic.

| Item | Expected value |
| --- | --- |
| Company | Nova Components Ltd |
| Site | Plant B |
| Reporting period | Q3 2026 |
| Purchased material | 12,000 kg packaging tray |
| Current product factor | 2.8 kgCO2e/kg |
| Current measured footprint | 33,600 kgCO2e |
| Alternative product factor | 1.9 kgCO2e/kg |
| Projected alternative footprint | 22,800 kgCO2e |
| Projected avoided emissions | 10,800 kgCO2e, or 32.1% |
| Maximum cost increase | 5% |
| Expected alternative cost delta | +3.2% |

These are expected fixture results, not UI constants. The application must calculate them from imported records.

## Scope

### Build now

- React application with the 12 POC routes described below.
- FastAPI modular monolith.
- PostgreSQL as the structured source of truth.
- pgvector for small, metadata-filtered evidence retrieval when needed.
- CSV/JSON activity and supplier/product import.
- Synthetic evidence text or PDF content represented as evidence items.
- Deterministic Measurement workflow.
- Deterministic Procurement workflow.
- Shared append-oriented ledger and lineage.
- Bounded LangGraph orchestration.
- REST commands/queries and SSE progress events.
- Human approval/rejection with preview hash validation.
- Audit trail and agent-run telemetry.
- Reproducible seed/reset path.

### Explicitly out of scope for the four-day build

- Assurance/disclosure workflows.
- Dispatch, grid forecasting, scheduling, or equipment actuation.
- Generic natural-language-to-SQL.
- Arbitrary dashboard generation.
- Autonomous purchasing or supplier contact.
- Enterprise SSO, full RBAC, or production multi-tenancy.
- Live ERP, IoT, or customer integrations.
- Redis, Celery, Kafka, or a distributed task queue.
- A separate vector database.
- Microservices.
- Next.js or server-side frontend rendering.
- Redux.
- CrewAI or AutoGen as the core orchestrator.
- Model-generated calculations, scores, filters, or raw SQL.

If delivery is behind, cut PDF extraction automation, generic charts, multi-period forecasting, complex circularity taxonomies, live external APIs, custom authentication, and polished animations before cutting traceability or deterministic calculations.

## Product principles

1. The model proposes; software validates, authorizes, executes, and verifies.
2. Generative output is never the source of numerical truth.
3. Structured facts and generated judgments are visually and structurally separate.
4. Every displayed number is bound to a verified fact.
5. Constraints are never silently relaxed to force a recommendation.
6. Ledger history is append-oriented. Corrections create new events and supersession links.
7. Approval binds the exact reviewed payload, facts, context, method versions, and hashes.
8. A failed or unsupported state is an acceptable result and must be explicit.
9. Keep infrastructure minimal enough to finish the four-day POC.
10. Every day ends with a runnable vertical slice.

## Target architecture

```text
React 19 + TypeScript + Vite
        |
        | REST commands/queries + SSE progress
        v
FastAPI modular monolith
        |
        +-- API routes and Pydantic contracts
        +-- Measurement services
        +-- Procurement services
        +-- Ledger, evidence, approval, and audit services
        +-- LangGraph orchestrator and bounded subgraphs
        |
        v
SQLAlchemy 2.0 + asyncpg
        |
        v
PostgreSQL + pgvector
```

The model provider is called only through the agent layer. Agent tools call typed application services, not SQLAlchemy sessions directly. FastAPI is the only application boundary that talks to the frontend.

## Technology stack

### Frontend

- React 19 and TypeScript.
- Vite.
- Tailwind CSS v4 through the Vite plugin.
- shadcn/ui for UI components.
- Lucide icons through the shadcn ecosystem.
- TanStack Query for server state, caching, loading/error states, and mutations.
- Zustand only for small client-only state.
- React Router for SPA routing.
- React Hook Form for form state.
- Zod for form, API, and runtime payload validation.
- Recharts for deterministic charts when the dashboard requires them.
- React Flow for lineage visualization when the measurement detail screen requires it.
- Native `EventSource` for SSE, with terminal-state handling and fallback polling if needed.

Do not add Recharts, React Flow, or another dependency until a real screen uses it.

### Backend

- Python 3.12 or newer.
- FastAPI.
- Pydantic v2.
- SQLAlchemy 2.0 async ORM/SQL toolkit.
- asyncpg PostgreSQL driver.
- Pydantic v2 settings with secret-safe `DATABASE_URL` validation.
- Neon PostgreSQL with pgvector and `VECTOR(768)` evidence embeddings.
- LangChain for model/tool abstractions only where useful.
- LangGraph for explicit bounded workflow state.
- Gemini API model for planning, tool selection, structured output, and explanation.
- Gemini embeddings for evidence retrieval if the P1 retrieval feature is implemented.
- Python `Decimal` for money, quantity, carbon, scoring, and percentage calculations.
- pytest, httpx, and Ruff for backend quality checks.

The source documents name `Gemini 3.8 Flash` and `Gemini Embedding 2`. Treat those as the current intended choices, but confirm exact provider model identifiers before integration because model catalog names can change. Keep model identifiers in configuration, not business code.

### Deployment direction

- Docker and Docker Compose for a reproducible local multi-service run once the database is introduced.
- Vercel is the intended frontend hosting option.
- A Python container host is the intended backend deployment option.
- Managed PostgreSQL or a PostgreSQL container is the intended database.

Do not add deployment infrastructure until it supports a runnable vertical slice.

## Current repository state

The repository contains the lightweight frontend/API foundation and the implemented database foundation. Measurement, procurement, agent business logic, seed data, and feature screens are still staged work.

Current working functionality:

- The React/Vite app runs on `http://localhost:3000`.
- The FastAPI app runs on `http://localhost:8000`.
- Vite proxies `/api` to FastAPI.
- `GET /api/health` returns a typed health response.
- The frontend calls the health endpoint through TanStack Query and validates it with Zod.
- The page displays `CarbonMesh` and the API connection state.
- Tailwind CSS v4 and shadcn/ui configuration are initialized.
- SQLAlchemy defines the authoritative 32-table model across five PostgreSQL schemas.
- The backend uses one lazy asyncpg engine and async SQLAlchemy session factory for Neon.
- Explicit bootstrap commands enable pgvector, create tables, views, and the immutable-ledger trigger, and verify compatibility.
- The repository implements and locally tests the bootstrap workflow; applying it to a live Neon branch still requires the operator's rotated `DATABASE_URL`.
- `GET /api/db/demo` checks connectivity asynchronously and sanitizes failures.
- Placeholder feature folders are retained where business workflows have not yet been built.

Current repository shape:

```text
carbonmesh/
  apps/
    web/
      src/
        app/providers/
        components/ui/
        features/
          agent/
          approvals/
          audit/
          dashboard/
          data/
          measurements/
          procurement/
          runs/
          suppliers/
        hooks/
        lib/
        pages/
        routes/
        schemas/
        services/
        stores/
        types/
    api/
      app/
        api/routes/
        core/
        db/
          bootstrap.py
          ddl.py
          session.py
          models/
        dependencies/
        middleware/
        modules/
          agents/
          approvals/
          evidence/
          ledger/
          measurement/
          procurement/
          semantic/
        schemas/
        services/
      tests/integration/
      tests/unit/
  data/demo/
  docs/api/
  docs/architecture/
  infra/
  scripts/
  tests/e2e/
```

`data/demo` is reserved for synthetic fixtures and their expected deterministic results. `infra` is reserved for Docker/deployment files when they become necessary. Both currently contain only placeholders.

Frontend dependencies and `package-lock.json` live under `apps/web`. There should be no root `node_modules`. Root npm scripts delegate to `apps/web`.

Backend dependencies are declared in `apps/api/pyproject.toml`. A `requirements.txt` is not currently used because `pyproject.toml` is the single dependency source.

Database schema details, including the table catalogue and vector index, are documented in `docs/architecture/database.md`.

## Local setup

Prerequisites:

- Node.js 22.12 or newer.
- npm 10 or newer.
- Python 3.12 or newer.

Install frontend dependencies from the repository root:

```bash
npm --prefix apps/web install
```

Create and install the backend environment on Windows:

```powershell
cd apps/api
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

Copy `apps/api/.env.example` to `apps/api/.env`, replace its placeholder with a rotated pooled Neon URL, then explicitly bootstrap and verify the database from `apps/api`:

```powershell
Copy-Item .env.example .env
python -m app.db.bootstrap
python -m app.db.bootstrap --check
```

Never commit or log a real database URL. FastAPI startup does not create or alter database objects.

Run the backend from `apps/api`:

```powershell
uvicorn app.main:app --reload --port 8000
```

Run the frontend from the repository root:

```bash
npm run dev
```

Useful URLs:

- Frontend: `http://localhost:3000`
- API health: `http://localhost:8000/api/health`
- OpenAPI UI: `http://localhost:8000/docs`

Root frontend checks:

```bash
npm run typecheck
npm run lint
npm run build
```

Backend checks from `apps/api`:

```powershell
ruff check app tests
pytest
```

## Frontend architecture rules

Use feature-oriented organization without unnecessary abstractions.

- Pages/routes own screen orchestration.
- TanStack Query owns remote/server state.
- Zustand owns only small client-only state that must be shared.
- React Hook Form owns form state.
- Zod owns runtime validation of external data and forms.
- Services own HTTP details. Pages and UI components do not call `fetch` directly.
- Schemas and types define API contracts at the frontend boundary.
- Prefer generated OpenAPI types when the backend contracts stabilize.
- Route paths and query keys should be centralized when repetition appears.
- Do not duplicate server data into Zustand.
- Do not add React Context for state already handled by TanStack Query or Zustand.
- Do not create speculative hooks, stores, components, or abstractions.

### UI component rule

Use shadcn/ui registry components as the base UI primitives. Do not build a parallel custom component library containing replacement buttons, inputs, cards, dialogs, tables, badges, or similar primitives.

Feature-level composition is allowed when it represents CarbonMesh behavior, such as a measurement lineage view or approval workflow, but it should compose shadcn primitives rather than replace them. Add a shared abstraction only after real reuse appears.

UI expectations:

- Desktop-first responsive application shell.
- Left navigation rail, top context bar, and right-side evidence/run drawer when those screens are built.
- Green for verified facts/success, purple for generated judgment, amber for warnings, and red for failed/unsupported states.
- Every numeric value comes from structured API facts, not prose parsing or UI constants.
- Loading, empty, error, no-data, unsupported, and terminal agent states are first-class UI states.
- Use accessible semantic controls and labels.
- Keep the application operational and information-dense, not marketing-oriented.

## Backend architecture rules

Use a modular monolith. Each domain should have a clear public service boundary, typed schemas, database access, and tests, but do not reproduce a deep enterprise template before it is needed.

Request flow:

```text
FastAPI route
  -> Pydantic request validation
  -> domain/application service
  -> deterministic domain logic and repository calls
  -> SQLAlchemy transaction
  -> Pydantic response
```

Rules:

- Routes handle HTTP concerns only.
- Services orchestrate use cases and transaction boundaries.
- Repositories contain persistence operations, not business decisions.
- Database models do not leak into API responses.
- Pydantic schemas are the API/tool boundary.
- The LLM and LangGraph nodes call typed services/tools, never raw database sessions.
- Use `AsyncSession` and async database access; transaction owners commit explicitly and session helpers roll back on failure.
- Do not run DDL during application startup. Use `python -m app.db.bootstrap` explicitly.
- SQLAlchemy `create_all()` does not alter existing tables. Rebuild a disposable branch for POC schema changes or deliberately introduce migrations before persistent environments require in-place evolution.
- Use `Decimal`, never float, for carbon, quantity, percentage, score, and money arithmetic.
- Store timestamps in UTC.
- Use ISO currency codes.
- Use UUID or ULID strings generated server-side; fixtures may use stable IDs.
- Return typed safe errors with code, message, trace ID, retryability, and field details where applicable.
- Logs are structured and must redact secrets and document bodies.
- Keep imports directed inward. Domain code must not import application entrypoints.
- Prefer straightforward functions/classes over speculative framework abstractions.

## Semantic layer and context

The semantic layer is intentionally small. It defines only the POC metrics, units, aliases, standards, and scoring methods required to keep the model from guessing terms such as footprint, PCF, avoided emissions, or best supplier.

Initial metric definitions:

| Metric key | Canonical unit | Main context |
| --- | --- | --- |
| `activity.purchased_material_mass` | kg | company, site, period, material, supplier product |
| `emissions.scope3.category1` | kgCO2e | company, site, period, material |
| `supplier.product_carbon_footprint` | kgCO2e/kg | supplier, product, effective period |
| `supplier.circularity_score` | score 0-100 | supplier product, scoring model |
| `procurement.projected_avoided_emissions` | kgCO2e | scenario, recommended product |
| `procurement.cost_delta_pct` | percent | scenario, recommended product |

Every agent run receives one frozen context envelope containing:

- company ID;
- site ID;
- reporting-period ID;
- workflow;
- metric keys;
- supplier/material scope;
- actor and role;
- explicit cost, lead-time, and circularity constraints;
- analysis signature.

Only the contextualizer may inspect limited conversation history. Tools receive the frozen structured context, not an unbounded transcript.

## Deterministic fact binding

The model may produce a narrative template containing placeholders, for example:

```text
Switching from {fact_current_product} to {fact_recommended_product}
could avoid {fact_avoided_kgco2e} while changing unit cost by
{fact_cost_delta_pct}.
```

Application code resolves placeholders from verified facts, ledger events, calculations, and evidence.

Validation rules:

- Unknown placeholders fail validation.
- Unbound numbers fail validation.
- Stale facts or context mismatches fail validation.
- The binder owns units, percentages, display formatting, and rounding.
- Every displayed number has a fact binding.
- Recommendation previews store an analysis signature and SHA-256 payload/preview hash.
- A changed scenario, measurement, factor, scoring model, or recommendation invalidates the approval preview.

## Measurement workflow

Objective: convert purchased-material activity and factor evidence into a verified Scope 3 Category 1 measurement with confidence, baseline comparison, data-quality issues, evidence, and lineage.

Steps:

1. Import CSV/JSON activity and factor/evidence data.
2. Validate required fields, signs, units, period, site, supplier/product references, duplicates, and checksums.
3. Normalize activity to the metric's canonical unit.
4. Resolve the most specific valid factor for product, geography, unit, and period.
5. Stop for clarification or return unsupported when factor selection is ambiguous or impossible.
6. Calculate emissions with `Decimal`.
7. Calculate transparent confidence components.
8. Persist calculation, measurement, evidence links, ledger event, lineage, and audit records transactionally.
9. Bind a concise explanation to verified fact IDs.

Core formulas:

```text
normalized_quantity = convert(activity.quantity, activity.unit, canonical_unit)
measurement_kgco2e = normalized_quantity * emission_factor.value
variance_pct = ((measurement_kgco2e - baseline_kgco2e) / baseline_kgco2e) * 100
confidence =
  0.40 * source_quality +
  0.30 * factor_specificity +
  0.20 * factor_recency +
  0.10 * record_completeness
```

The calculation run records method version, code version, input IDs, factor ID, rounding policy, and output hash.

## Procurement workflow

Objective: use the verified measurement to compare feasible supplier products, calculate carbon and commercial impact, explain trade-offs, and require human approval.

Steps:

1. Freeze the current product, quantity, site, period, cost ceiling, lead-time ceiling, material compatibility, and circularity requirement.
2. Load active alternatives with valid compatible evidence.
3. Normalize carbon, evidence quality, circularity, and operational-fit criteria to deterministic 0-100 scores.
4. Apply a versioned scoring model.
5. Remove options violating cost or any other hard constraint. Never soften constraints or score cost as a compensating benefit.
6. Calculate projected footprint, avoided emissions, reduction percentage, cost delta, and lead-time delta.
7. Select the highest-scoring feasible option.
8. Create an explanation template and bind verified facts.
9. Create an approval preview with analysis signature and hash.
10. Record approve/reject. Never create a purchase order.

Initial scoring model:

| Criterion | Weight |
| --- | ---: |
| Carbon performance | 40% |
| Evidence quality | 25% |
| Circularity | 20% |
| Operational fit | 15% |

Cost is a hard feasibility constraint, not a weighted scoring component.

Core formulas:

```text
total_score =
  0.40 * carbon_score +
  0.25 * evidence_score +
  0.20 * circularity_score +
  0.15 * operational_fit_score

avoided_kgco2e = quantity * (current_pcf - alternative_pcf)

cost_delta_pct =
  ((alternative_unit_cost - current_unit_cost) / current_unit_cost) * 100
```

If no option is feasible, return `no_feasible_option` without changing the constraints.

## Agent design

Use three bounded graphs:

- Orchestrator: resolve context, classify intent, create a structured plan, choose Measurement, Procurement, or the cross-module sequence, enforce budgets, and assemble the response.
- Measurement subgraph: validate/normalize, resolve factors, call deterministic calculations, write ledger/lineage, and bind facts.
- Procurement subgraph: freeze the scenario, retrieve feasible alternatives, score deterministically, calculate impact, create a preview, request approval, and bind facts.

Allowlisted tools:

| ID | Tool | Responsibility |
| --- | --- | --- |
| T01 | `resolve_context` | Resolve company, site, period, metric, scope, and constraints. |
| T02 | `get_metric_definition` | Return canonical unit, dimensions, handler, and method version. |
| T03 | `find_activity_records` | Retrieve authorized activity records for frozen context. |
| T04 | `validate_normalize_activity` | Return normalized records and typed issues. |
| T05 | `lookup_emission_factor` | Select the most specific valid factor and evidence. |
| T06 | `calculate_emissions` | Perform Decimal calculation and confidence scoring. |
| T07 | `write_ledger_event` | Persist event, evidence, and lineage transactionally. |
| T08 | `trace_lineage` | Return output-to-source lineage graph. |
| T09 | `list_supplier_alternatives` | Retrieve compatible active products and constraints. |
| T10 | `score_supplier_products` | Calculate versioned component and total scores. |
| T11 | `calculate_procurement_impact` | Calculate feasibility and projected impact. |
| T12 | `create_approval_preview` | Freeze payload, signature, hash, and approval record. |

Budgets per user request:

- Maximum 3 model calls.
- Maximum 6 tool calls.
- Maximum 1 structured-output or transient-tool repair.
- Maximum 6 recent turns or 4,000 context tokens for the contextualizer.
- Target 15 seconds end-to-end.
- Explicit row and retrieval limits.

Typed terminal states:

- `needs_clarification`
- `no_data`
- `validation_error`
- `unsupported`
- `no_feasible_option`
- `failed_validation`
- `budget_exhausted`
- `approval_invalidated`
- `completed`

No autonomous retry loop is allowed.

## Core API contracts

- `ContextEnvelope`: frozen tenant/site/period/metric/constraints plus analysis signature.
- `ImportResult`: source IDs, accepted/rejected counts, and data-quality issues.
- `MeasurementResult`: value, unit, formula, factor, confidence, fact/ledger IDs, baseline, and variance.
- `ProcurementScenarioResult`: current product, constraints, alternatives, scores, feasibility, and selected recommendation.
- `BoundNarrative`: template ID, resolved text, fact bindings, evidence links, and unsupported fragments.
- `AgentRunResult`: plan, stage, terminal state, facts, judgments, telemetry, and approval requirement.
- `ApprovalPreview`: entity, payload hash, analysis signature, expiration, facts, and evidence.

## Planned API surface

The POC plan defines versioned routes under `/api/v1`. The current bootstrap route `/api/health` is temporary. New domain APIs should use `/api/v1`, and health should move or be aliased to `/api/v1/health` when API versioning is introduced.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/v1/health` | Readiness check. |
| POST | `/api/v1/demo/reset` | Reset and reseed the demo. P1. |
| GET | `/api/v1/semantic/metrics` | List metric definitions and versions. |
| POST | `/api/v1/context/resolve` | Preview resolved context. P1. |
| POST | `/api/v1/imports/activity` | Import activity CSV/JSON. |
| POST | `/api/v1/imports/suppliers` | Import supplier products and evidence. |
| GET | `/api/v1/imports/{id}` | Read import status and issues. P1. |
| GET | `/api/v1/data-quality/issues` | List/filter data-quality issues. |
| POST | `/api/v1/measurements/calculate` | Run deterministic measurement. |
| GET | `/api/v1/measurements` | List measurements. |
| GET | `/api/v1/measurements/{id}` | Read measurement facts and formula. |
| GET | `/api/v1/measurements/{id}/lineage` | Read lineage graph. |
| GET | `/api/v1/suppliers` | List supplier products. |
| GET | `/api/v1/suppliers/{id}` | Read supplier/product evidence. |
| POST | `/api/v1/procurement/supplier-scores/run` | Calculate deterministic supplier scores. |
| POST | `/api/v1/procurement/scenarios` | Create a frozen scenario. |
| GET | `/api/v1/procurement/scenarios/{id}` | Read scenario and comparison. |
| GET | `/api/v1/procurement/recommendations/{id}` | Read recommendation facts, scores, and hashes. |
| POST | `/api/v1/agent/query` | Start bounded agent workflow. |
| GET | `/api/v1/agent/runs/{id}` | Read run state and telemetry. |
| GET | `/api/v1/agent/runs/{id}/events` | Stream SSE run events. |
| GET | `/api/v1/approvals` | List pending/decided approvals. |
| POST | `/api/v1/approvals/{id}/decision` | Approve/reject with hash validation. |
| GET | `/api/v1/audit/{entity_type}/{entity_id}` | Read chronological audit/lineage summary. |

SSE event names:

- `run.started`
- `stage.started`
- `stage.completed`
- `tool.started`
- `tool.completed`
- `validation.warning`
- `fact.created`
- `approval.required`
- `run.completed`
- `run.stopped`

## Planned frontend routes

| ID | Route | Screen | Main responsibility |
| --- | --- | --- | --- |
| S01 | `/` | POC Dashboard | Outcome, quality, approvals, recent runs, and efficiency summary. |
| S02 | `/ask` | Agent Workspace | Request, structured plan, live steps, verified answer, evidence, telemetry. |
| S03 | `/data/import` | Data Import | Upload and preview activity/supplier data. |
| S04 | `/data/quality` | Data Quality Review | Filter and resolve typed issues. |
| S05 | `/measurements` | Measurement Overview | Totals, confidence, variance, status, drill-down. |
| S06 | `/measurements/:id` | Measurement Detail | Formula, inputs, factor, confidence, evidence, lineage, supersession. |
| S07 | `/suppliers` | Supplier Explorer | Product PCF, circularity, cost, lead time, and evidence quality. |
| S08 | `/procurement/scenario` | Scenario Builder | Constraints, scoring version, comparison, and impact. |
| S09 | `/procurement/recommendations/:id` | Recommendation Detail | Bound facts, scores, impact, evidence, explanation, preview hash. |
| S10 | `/approvals` | Approval Queue | Review and approve/reject exact recommendation payloads. |
| S11 | `/runs/:id` | Agent Run Trace | Graph stages, tools, tokens, retries, latency, and terminal state. |
| S12 | `/audit/:type/:id` | Audit Explorer | Timeline of imports, calculations, events, recommendation, and approval. |

Build these as the POC requires them. Do not create all screens as empty placeholders just to satisfy the route count.

## Implemented database model

The SQLAlchemy metadata contains exactly 32 relational tables across five PostgreSQL schemas. This five-schema model is authoritative over older unqualified table catalogues.

| Schema | Tables |
| --- | --- |
| `core` | `companies`, `sites`, `reporting_periods`, `actors`, `data_sources`, `source_documents`, `evidence_items`, `audit_log` |
| `carbon` | `raw_activity_records`, `activity_records`, `emission_factors`, `calculation_runs`, `emission_calculations`, `carbon_measurements`, `data_quality_issues`, `carbon_baselines`, `variance_alerts`, `agent_runs` |
| `ledger` | `ledger_events`, `lineage_edges`, `ledger_event_evidence` |
| `semantic` | `semantic_entities`, `semantic_aliases`, `metric_definitions`, `method_definitions` |
| `procurement` | `suppliers`, `supplier_products`, `procurement_scenarios`, `supplier_scores`, `recommendations`, `fact_bindings`, `approvals` |

Database rules:

- PostgreSQL UUID primary keys are server-generated; timestamps are UTC-aware; carbon, quantity, money, percentages, confidence, and scores use fixed-precision numerics and Python `Decimal`.
- Tenant-owned rows carry `company_id`, and composite foreign keys prevent cross-company references through sites, sources, products, ledger events, recommendations, and approvals.
- Core relationships remain relational. JSONB is limited to raw/frozen/configuration payloads, constraints, weights, trace summaries, and snapshots.
- Domain statuses use named `VARCHAR` checks instead of PostgreSQL enum types. Foreign keys use `ON DELETE RESTRICT`, and ORM relationships do not cascade deletes.
- Checks enforce non-negative quantities and factors, confidence `0..1`, percentages and scores `0..100`, lowercase SHA-256 hashes, and valid evidence-embedding metadata.
- Important facts are append-oriented. `ledger.prevent_ledger_event_mutation()` and `trg_ledger_events_immutable` reject update/delete operations on `ledger.ledger_events` with SQLSTATE `55000`.
- `core.evidence_items.embedding` is nullable `VECTOR(768)`. Embedding, model identifier, and timestamp must all be present or all absent. A partial cosine HNSW index uses `m = 16` and `ef_construction = 64`; a B-tree metadata index supports company/document/type filtering.
- Uniqueness and partial indexes enforce source and row idempotency, versioned definitions, one active recommendation per scenario, one pending approval per recommendation, fact-placeholder uniqueness, and approval decision idempotency.
- Approval composite foreign keys bind each reviewed preview hash and analysis signature to the exact recommendation payload.
- Read models are `carbon.v_measurement_summary`, `procurement.v_supplier_comparison`, and `procurement.v_pending_approvals`.

Run `python -m app.db.bootstrap` explicitly to enable pgvector and create compatible missing objects, then `python -m app.db.bootstrap --check` to verify them. API startup never performs DDL. `create_all()` does not migrate existing tables; rebuild a disposable branch for POC structural changes or reintroduce migrations for persistent environments. The full data dictionary is in `docs/architecture/database.md`.

## Team ownership

| Developer | Primary ownership |
| --- | --- |
| Dev 1 - Frontend | React shell, routes, POC screens, queries/forms, lineage/charts, SSE client, approval UX, frontend tests. |
| Dev 2 - Backend | FastAPI contracts, imports, measurement engine, procurement engine, REST/SSE services, deterministic unit tests. |
| Dev 3 - Agentic AI | LangGraph state, context/intent planner, tool schemas, budgets, fact-template synthesis, grounding validation, evals. |
| Dev 4 - Database/Platform | Neon/PostgreSQL code-first schema, async sessions, ledger/lineage, approvals/audit, pgvector, Docker, deployment, logging/tracing. |
| Dev 5 - Data/QA | Synthetic fixtures/evidence, seed/reset, expected values, integration/e2e tests, README, deck, and video coordination. |

Critical handoffs:

- Hour 4: Dev 2 and Dev 4 freeze table names, IDs, core Pydantic contracts, and transaction boundaries.
- Hour 6: Dev 3 freezes graph state, tool signatures, terminal states, and SSE event names.
- End of Day 1: Dev 5 publishes seed files, expected calculations, expected recommendation, and stable IDs.
- Day 2 morning: Dev 2 publishes OpenAPI and working Measurement endpoints/tools.
- Day 3 morning: Dev 2 and Dev 4 publish Procurement, approval, and audit endpoints/tools.
- Day 4 noon: code freeze; only fixes, documentation, demo, and packaging.

## Four-day build sequence

### Day 1 - Foundation

- Freeze contracts and identifiers.
- Bootstrap Neon PostgreSQL, pgvector, the code-first tables, and the seed/reset path.
- Build frontend shell and data import entry point.
- Build agent graph skeleton and terminal states.
- Exit gate: bootstrap verification and seed run, services boot, frontend reaches health, and the agent can return a structured plan.

### Day 2 - Measurement

- Import and normalize activity.
- Resolve factors.
- Implement Decimal calculation, confidence, ledger write, evidence links, and lineage.
- Build measurement list/detail and data-quality screens.
- Exit gate: source CSV produces 33,600 kgCO2e and trace-back reaches source row and factor.

### Day 3 - Procurement

- Import supplier products/evidence.
- Implement scoring, hard constraints, impact calculation, recommendation, fact binding, and approval.
- Build supplier, scenario, recommendation, and approval screens.
- Exit gate: Alternative B is selected with 10,800 kgCO2e avoided and +3.2% cost, then approved/rejected and audited.

### Day 4 - Integration and submission

- Complete cross-module agent flow, SSE, telemetry, e2e checks, polish, README, deployment, deck, and video.
- Exit gate: fresh clone/reset works and the main demo succeeds twice without manual database edits.

## Release-blocking journeys

1. Fresh Neon setup, explicit code-first bootstrap verification, and seed complete without manual database changes.
2. Happy measurement returns 33,600 kgCO2e with expected factor, confidence, fact, and ledger IDs.
3. Measurement trace-back resolves every graph node to real source/evidence records.
4. Wrong units and missing supplier data create typed issues and no verified measurement.
5. Happy procurement recommends Alternative B with 10,800 kgCO2e avoided and +3.2% cost.
6. Tight cost constraints return `no_feasible_option` without relaxation.
7. Unknown fact placeholders fail grounding validation.
8. Changed scenarios invalidate approval previews; repeated decisions are idempotent.

## Definition of done

- A fresh clone can be installed, reset, seeded, run, and demonstrated from documented commands.
- The connected Measurement to Procurement to Approval flow works end to end.
- The frontend contains no hard-coded result values.
- Deterministic formulas, scoring, constraints, and hashes pass unit tests.
- Every final number is traceable through facts, ledger events, evidence, and source records.
- Agent runs have bounded plans, tools, retries, and typed stopping states.
- Run telemetry displays model, tokens, tools, retries, latency, and impact metrics.
- The recommendation requires human approval and cannot autonomously purchase.
- No confidential, customer, or personal data appears in fixtures, prompts, logs, or traces.
- README, architecture explanation, limitations, pitch deck, and demo video are complete.

## Privacy, safety, and responsible AI

- Use only public, open, or synthetic data.
- Label synthetic data in files and screens.
- Do not use customer code, branding, invoices, supplier contracts, personal data, or secrets.
- Send only the minimum context and short evidence chunks to the model.
- Never send entire uploaded documents to the model by default.
- Redact secrets and document bodies from logs/traces.
- The agent may recommend and draft but may not place orders or create legal commitments.
- Token count is usage telemetry, not an exact carbon footprint.
- Only publish an SCI-style compute estimate if every energy/carbon-intensity assumption is documented and reproducible.

## Agent sustainability telemetry

Capture:

- measured baseline and projected impact;
- approved/rejected impact;
- baseline manual-time assumption and actual elapsed time;
- model/provider identifier;
- calls and input/output/context tokens;
- structured-output repairs;
- tool calls, retries, failures, rows processed, and evidence chunks retrieved;
- total and per-stage latency;
- trace ID, analysis signature, code/method/scoring versions, hashes, and approval actor/time.

Useful transparent ratios:

```text
impact_per_1k_tokens = projected_kgco2e_avoided / (total_tokens / 1000)
minutes_saved_per_run = baseline_manual_minutes - actual_elapsed_minutes
verified_value_ratio = approved_kgco2e_avoided / max(1, model_call_count)
```

Do not overstate these ratios as direct model-carbon measurements.

## Working rules for coding agents

When an AI coding agent receives this file:

1. Read the latest user task first. Do not implement the whole roadmap unless explicitly requested.
2. Inspect current files and `git status` before changing anything.
3. Treat existing user/team changes as intentional and preserve them.
4. Keep changes scoped to the requested vertical slice.
5. Follow existing project patterns before introducing abstractions.
6. Do not overengineer. The delivery window is four days.
7. Do not add infrastructure, dependencies, folders, or generic utilities without an immediate use.
8. Prefer typed contracts and deterministic functions at system boundaries.
9. Never let an LLM calculate values, issue SQL, relax constraints, or approve actions.
10. Add tests in proportion to risk, prioritizing formulas, constraints, hashes, transaction integrity, and end-to-end journeys.
11. Run relevant type checks, linting, tests, and builds before reporting completion.
12. Update this file only when the team intentionally changes a shared project decision.

## Reference documents used to create this context

- `CarbonMesh_4_Day_Hackathon_POC_Plan_v3.docx`
- `CarbonMesh_Problem_and_Solution.pdf`
- `CarbonMesh_Technology_Stack_and_Architecture.docx`
- `frontend-architecture (3).md`
- `backend-architecture (2).md`
- `frontend-design-system.md`
- The current CarbonMesh repository and its README, package manifests, configuration, and bootstrap code.

