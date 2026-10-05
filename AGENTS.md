# CarbonMesh Project Context

Last updated: 2026-10-05

## Purpose

This is the shared implementation contract for developers and coding agents
working in this repository. It records the approved four-module direction,
the actual transition state of the code, ownership boundaries, and engineering
rules. It is context, not permission to implement the whole roadmap in one task.
Always follow the latest explicit user/developer request and inspect the current
worktree before editing.

## Source precedence

When sources disagree, use this order:

1. The latest explicit instruction from the team/user.
2. This `AGENTS.md` file.
3. The attached four-module hackathon implementation blueprint.
4. [docs/architecture/system.md](docs/architecture/system.md).
5. [docs/architecture/database.md](docs/architecture/database.md).
6. [docs/api/contract.md](docs/api/contract.md).
7. Earlier POC plans and architecture documents as historical references only.

Repository code and tests determine what is currently implemented. Planning
documents determine the target; they do not prove that a feature exists.

## Decisions that supersede the previous POC contract

- The October 5 usability direction groups the interface into Prepare, Plan,
  and Review. Users select named database records; UUIDs remain internal keys
  and URL/payload references, never visible labels or fields to type manually.
- Application authentication is removed from the frontend and API: no sign-in,
  access keys, signed sessions, or authentication configuration. Named actors
  still identify commands and approval decisions; company scoping and domain
  approval checks remain. This is a trusted local/demo experience with a private
  API. Database/provider credentials and the destructive reset token remain.
- Electricity Maps controls are removed from the interface. Live provider calls
  are disabled by default, including calls from agents. Existing stored grid
  data, measurements and forecasts remain available; missing data must never
  be replaced by silent fixture fallback.
- Lineage is a directed evidence diagram using real API nodes and relationships.
  The API-status badge and System navigation are removed; destructive reset
  remains an explicitly authorized operator operation, not a normal user step.

- P0 now includes Measurement, Assurance, Procurement, and Dispatch in one
  connected golden path. Assurance and Dispatch are no longer Phase 2.
- The implemented database contract is eight schemas and 46 tables. The prior
  five-schema, 32-table layout is historical and matters only if an operator
  must preserve data from an older live database.
- The target agent runtime is one orchestrator plus four bounded specialist
  graphs and 24 allowlisted tools.
- The target experience contains 16 role-oriented screens.
- The target API catalog contains 38 operations. Each implemented operation has
  one canonical `/api/...` route; do not add duplicate legacy aliases.
- The target demo is Maverick Manufacturing, Plant B, Q3 2026, with hourly
  electricity, a 10,000 kg recycled-aluminium decision, one Assurance template,
  and one flexible load.
- Keep the existing `apps/api` repository layout. The blueprint's `services/api`
  path is illustrative and does not justify a churn-only move.
- The connection layer, SQLAlchemy model, bootstrap verifier, and clean-database
  creation path are complete. Do not allocate a feature owner to database
  migration work. If old live data must be retained, treat its transition as a
  separate reviewed operator activity. FastAPI startup still must never run
  DDL, migration, reset, or seed.
- Local Docker PostgreSQL and hosted Neon are both supported. Remote database
  connections require authenticated TLS; non-TLS is allowed only for approved
  loopback/Docker hosts.
- The canonical database name is `carbonmesh` for local Docker and the current
  hosted Neon environment. A login role such as `neondb_owner` is independent
  of the selected database and does not change this name.
- Confidence changes must use a new method version. Never silently reinterpret
  previously persisted measurements.

## Product definition

CarbonMesh is an agent-assisted carbon operations application that converts
activity and grid data into traceable emissions, turns verified facts into
evidence-supported disclosure claims and procurement/dispatch recommendations,
and requires a human to approve or reject consequential outputs.

The differentiator is one lineage-tracked ledger reused across four workflows,
not four unrelated dashboards.

## Primary users

- Sustainability analyst: imports activity, resolves quality issues, calculates
  emissions, and investigates confidence and lineage.
- ESG/compliance reviewer: reviews atomic claims, citations, gaps, and stale
  disclosure artifacts.
- Procurement manager: compares supplier products under hard commercial and
  material constraints.
- Operations planner: reviews an advisory lower-carbon operating window under
  unchanged hard constraints.
- Approver: reviews the exact payload, facts, evidence, hashes, expiry, and
  trade-offs before deciding.
- Judge/auditor: traces every numerical value to source, method, calculation,
  ledger event, and decision history.

## Golden path

The connected golden flow is:

1. Upload synthetic hourly electricity, purchased-material, supplier/evidence,
   standard, and flexible-load data.
2. Validate and normalize records, preserving raw rows and typed issues.
3. Resolve Plant B to a grid zone and cache historical grid-intensity points.
4. Calculate hourly Scope 2 and purchased-material emissions deterministically.
5. Persist calculations, measurements, evidence links, ledger facts, and
   lineage.
6. Draft atomic disclosure claims, bind facts, validate citations, and visibly
   block at least one unsupported claim.
7. Apply hard procurement constraints, score feasible recycled-aluminium
   products, calculate impact, and create a fact-bound recommendation.
8. Cache a 24-hour forecast, enumerate feasible windows, optimize a two-hour
   flexible load, and create an advisory recommendation.
9. Create exact approval previews for disclosure, procurement, and dispatch.
10. Approve/reject, append decision events, and invalidate stale artifacts.
11. Show one run trace with model/tool/provider/cache/token/retry/latency and
    documented footprint-proxy telemetry.

## Synthetic demo contract

All data is synthetic unless a public/open source is explicitly declared. Every
file and screen must say so.

| Element | Demo value |
| --- | --- |
| Company | Maverick Manufacturing (synthetic) |
| Site | Plant B (synthetic) |
| Period | Q3 2026 |
| Electricity | 90 days of hourly kWh with missing, duplicate, and estimated-factor cases |
| Supplier need | 10,000 kg recycled aluminium; 3-5 products |
| Flexible load | Batch Process 7, 500 kW, two hours |
| Dispatch bounds | 12-hour allowed interval; four-hour maximum delay |
| Assurance | GHG Protocol Scope 2 summary plus limited ESRS-style evidence mapping |
| Grid source | Stored data only by default per the October 5 usability decision; live provider calls disabled and fixtures require explicit opt-in |

Do not hard-code a winning supplier, carbon total, or dispatch window. Golden
results are generated by deterministic services and versioned with fixture and
method identifiers.

## P0/P1/P2 scope

### P0

- One synthetic tenant/site/period and one connected four-module run.
- CSV/JSON ingestion plus evidence documents needed by the demo.
- Scope 2 and purchased-material Measurement.
- One Assurance template with supported and unsupported claims.
- Three to five supplier products and one deterministic recommendation.
- One flexible load, 24-hour forecast, deterministic advisory optimizer.
- Generic Preview-Approve-Commit for all consequential artifacts.
- Live lineage, run trace, reset/seed, fixture mode, and clean local setup.

### P1

- Evidence-pack export.
- Measurement chart breakdown endpoint.
- Latest live-grid card.
- Resolve/waive quality issues.
- Richer ledger/audit search.

### P2

- Full standards library and formal assurance opinion.
- Market-based Scope 2 instruments.
- Enterprise SSO/RBAC and production multi-tenancy.
- Generic onboarding and live ERP/IoT integrations.
- Distributed task queues and scale hardening.
- Any automated purchasing or equipment actuation.

## Non-negotiable product rules

1. The model proposes; software validates, authorizes, executes, and verifies.
2. The model never calculates business values, writes raw SQL, invents a source
   ID, relaxes a hard constraint, or commits an approval.
3. Every displayed numerical value is bound to a verified fact and lineage.
4. Structured facts and generated judgments are visibly separate.
5. Unknown placeholders, unbound numbers, context mismatch, incompatible units,
   stale facts, and unsupported citations fail closed.
6. Ledger history is append-only. Corrections create new events and
   supersession/staleness links.
7. Approval binds exact payload, facts, context, methods, hashes, actor, expiry,
   and idempotency key.
8. A `no_data`, `unsupported`, `provider_unavailable`, `no_feasible_option`, or
   `stale` result is valid and must not trigger silent fallback.
9. Dispatch is advisory only. The repository contains no actuation endpoint,
   equipment credential, or control tool.
10. Traceability and deterministic replay take priority over visual polish.

## Target architecture

```text
React 19 + TypeScript + Vite
        |
        | REST commands/queries + SSE
        v
FastAPI + Pydantic v2 modular monolith
        |
        +-- context, policy, idempotency, safe errors
        +-- five bounded LangGraph graphs
        +-- typed tools -> deterministic application services
        +-- Measurement / Assurance / Procurement / Dispatch
        +-- generic approvals, ledger, evidence, audit, telemetry
        |
        v
SQLAlchemy 2 async + asyncpg
        |
        v
PostgreSQL 16+ + pgvector (8 schemas / 46 tables)
```

The detailed diagram and boundaries are in
[system.md](docs/architecture/system.md).

## Technology choices

### Frontend

- React 19, TypeScript, Vite, Tailwind CSS v4.
- shadcn/ui primitives and Lucide icons; do not create a parallel component
  library.
- React Router, TanStack Query, React Hook Form, Zod.
- Zustand only for small client-only shared state; never duplicate server data.
- Recharts and React Flow only where real screens use them.
- Native `EventSource` with terminal-state handling and fallback polling.

### Backend

- Python 3.12+, FastAPI, Pydantic v2.
- SQLAlchemy 2.0 async ORM/toolkit and asyncpg.
- PostgreSQL and pgvector `VECTOR(768)`.
- `Decimal` for quantity, money, carbon, percentage, score, and optimization.
- LangGraph for bounded orchestration; provider abstractions stay behind the
  agent layer.
- Gemini is the intended demo provider, but exact model identifiers remain
  configuration and must be verified before integration.
- pytest/httpx/Ruff; add type, contract, replay, and browser gates as they become
  executable.

## Historical transition baseline (audited 2026-10-01)

The repository has a working old-scope backend, not an empty skeleton:

- The pre-transition audit found 29 OpenAPI paths. The current branch exposes
  30 canonical `/api/...` paths with no duplicate legacy mounts. Implemented
  behavior includes imports, data quality,
  deterministic purchased-material Measurement, supplier exploration,
  Procurement, approvals, ledger lineage/audit, persistent SSE runs, demo
  reset, and Electricity Maps historical sync.
- The purchased-material Measurement engine uses `Decimal`, factor specificity,
  confidence, baselines/variance, transactionally persisted calculations,
  ledger events, evidence, and lineage.
- Procurement applies hard constraints, deterministic weighted scoring,
  projected impact, fact-bound narrative, preview hashes, staleness, and
  idempotent approval decisions.
- The provider-neutral OpenAI/Gemini/Anthropic/OpenRouter HTTP wrappers exist,
  but the active agent runtime does not invoke them.
- The active agent is a deterministic keyword router for Measurement and
  Procurement. It returns an existing verified Measurement and explicitly
  rejects Assurance and Dispatch. LangGraph, RAG, interrupts/resume, and live
  model telemetry are not implemented yet.
- Historical Electricity Maps data is cached as emission factors; hourly Scope
  2 calculation and forecast/Dispatch are not implemented.
- SQLAlchemy and bootstrap now implement the exact 46-table/eight-schema
  contract. This is storage scaffolding, not completed Assurance or Dispatch
  business behavior.
- Tenant-scoped, bounded ledger event search and detail endpoints are
  implemented with safe evidence summaries and immediate lineage neighbors.
- The previous demo is Nova Components / 12,000 kg packaging and must be replaced
  by the approved fixture contract.
- The frontend in the audited baseline is a health shell; Dev 1 is actively
  building the four-module experience.

Quality baseline before the architecture transition:

- `ruff check app tests`: passed.
- backend: 176 passed, 8 opt-in Neon tests skipped.
- frontend typecheck, lint, and production build: passed.

Preserve these tests as regression coverage while changing the contract.
After canonical-route consolidation, the combined backend result is 195 passed
with eight opt-in live-Neon tests skipped; Ruff also passes.

## Database foundation and optional legacy transition

The implemented table distribution is:

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

The physical table catalog and lifecycle rules are in
[database.md](docs/architecture/database.md). The implemented changes are:

- `carbon.agent_runs` -> `ai.agent_runs`, plus `ai.agent_run_steps`.
- `procurement.approvals` -> generic `core.approvals`.
- `procurement.fact_bindings` -> generic `ledger.fact_bindings`.
- `procurement.recommendations` ->
  `procurement.procurement_recommendations`.
- Add timestamped `carbon.grid_intensity_points`, six Assurance tables, and
  five Dispatch tables.

Database rules:

- FastAPI startup performs no DDL, migration, reset, or seed.
- New development and demo environments use the completed clean bootstrap on a
  fresh or disposable target database.
- Database migration is not part of the remaining feature backlog. Only when
  old live data must be preserved should an operator design and rehearse a
  reviewed 32-to-46-table transition with explicit target confirmation and a
  rollback branch or backup.
- `create_all()` cannot update existing tables and must never be presented as an
  in-place upgrade mechanism.
- Keep UUID server defaults, UTC timestamps, exact NUMERIC types, tenant-aligned
  foreign keys, `ON DELETE RESTRICT`, checked states, and stable indexes.
- `ledger.ledger_events` rejects UPDATE/DELETE. Corrections append events.
- Evidence embeddings, model ID, and embedded timestamp are all present or all
  absent; HNSW cosine index and metadata filtering remain required.
- Local non-TLS database URLs are accepted only for approved loopback/Docker
  hosts. Remote URLs require verified TLS.

## Module engineering contracts

### Measurement

Support two deterministic paths:

```text
Scope 2 row kgCO2e = kWh * gCO2e_per_kWh / 1000
purchased material kgCO2e = kg * kgCO2e_per_kg
```

Hourly Scope 2 requires timestamp alignment and cannot interpolate a missing
grid interval. Confidence v2 uses source quality 35%, method fit 25%, temporal
match 20%, and completeness 20%. Persist method/code/input/output hashes and
complete source-to-measurement lineage.

### Assurance

Implement one GHG Protocol Scope 2 summary with limited ESRS evidence mapping.
Claims are atomic. Numerical support comes from ledger facts; unstructured
support comes from metadata-filtered evidence retrieval. Runtime code selects
citation IDs and validates existence, tenant, context, requirement relevance,
comparability, and support threshold. One unsupported reduction claim must be
blocked. Outputs are POC drafts, not assurance opinions or filings.

### Procurement

Hard constraints run before scoring and are never softened. Initial weights:

| Criterion | Weight |
| --- | ---: |
| Carbon performance | 40% |
| Evidence quality | 25% |
| Circularity | 20% |
| Operational fit | 15% |

Cost is a hard feasibility constraint, not a score. All scenarios, including
non-agent calls, persist required fact bindings.

### Dispatch

Use immutable 24-hour forecast points, enumerate all consecutive two-hour
windows, apply earliest start/latest finish/duration/delay/capacity/blackout
constraints, and minimize emissions. Earliest feasible start is the primary
tie-break. Missing forecast intervals invalidate a candidate; do not
interpolate. Persist baseline/recommended windows and impact. Approval remains
advisory and cannot actuate equipment.

## Shared semantic, fact, and approval contracts

The semantic layer contains only required entities, aliases, metric definitions,
method definitions, and policy definitions. Each run receives one immutable
`ContextEnvelope` with company, site, period, entities, metric/method scope,
actor/role, hard constraints, request hash, and analysis signature.

Generated prose uses templates with verified placeholders. The deterministic
binder owns unit conversion, display formatting, rounding, citation IDs, and
unknown-placeholder rejection.

Generic Preview-Approve-Commit stores target type/ID, exact payload and preview
hash, analysis signature, expiry, requester, decider, decision note,
idempotency key, and ledger decision event. Revalidate upstream facts,
evidence, method, forecast, and constraints immediately before commit.

## Agent design

Use five bounded graphs:

- Orchestrator: contextualize, plan module order, enforce policy/budget, manage
  handoffs, and synthesize a facts/judgments/unsupported-items response.
- Measurement: validate, normalize, sync/select factors, calculate, confidence,
  ledger.
- Assurance: map requirements, decompose atomic claims, retrieve, bind, cite,
  detect gaps, preview approval.
- Procurement: freeze constraints, feasibility, score, impact, bind, preview.
- Dispatch: sync forecast, apply constraints, optimize, impact, bind, preview.

The 24 tool IDs are frozen as:

`resolve_context`, `resolve_entity`, `retrieve_ledger_facts`,
`retrieve_evidence`, `write_ledger_event`, `create_approval_preview`,
`validate_activity`, `normalize_unit`, `sync_grid_history`,
`select_emission_factor`, `calculate_emissions`, `calculate_confidence`,
`map_standard_requirement`, `decompose_claim`, `bind_claim_facts`,
`validate_citations`, `detect_evidence_gaps`, `load_supplier_candidates`,
`score_supplier`, `calculate_procurement_impact`,
`build_procurement_recommendation`, `sync_grid_forecast`,
`optimize_dispatch_window`, and `calculate_dispatch_impact`.

Budgets:

- Single module: at most 3 model calls, 8 tool calls, one repair per stage,
  target 15 seconds.
- Four-module golden path: at most 6 model calls, 20 tool calls, one repair per
  stage, target 45 seconds.
- Approval resume: 0-1 model call, at most 3 tools, no repair, target 10 seconds.

Terminal states include `success`, `needs_clarification`, `no_data`,
`unsupported`, `policy_blocked`, `provider_unavailable`, `budget_exhausted`,
`validation_failed`, `approval_required`, `no_feasible_option`, and `stale`.
Every graph path must stop in a typed state; no autonomous retry loop exists.

## API and frontend contracts

The target API is documented in [docs/api/contract.md](docs/api/contract.md).
Use only the canonical stable `/api/...` routes. New Assurance, Dispatch,
generic approval, run-resume, recursive lineage, and sustainability behavior
requires real services, not placeholder 200 responses. Bounded ledger search
and immediate-neighbor detail are already implemented.

Target frontend routes:

1. `/dashboard`
2. `/ask`
3. `/data`
4. `/quality`
5. `/measurement`
6. `/measurement/:id`
7. `/assurance`
8. `/assurance/:draftId`
9. `/procurement/suppliers`
10. `/procurement/scenarios/:id`
11. `/dispatch`
12. `/dispatch/:scenarioId`
13. `/approvals`
14. `/runs/:runId`
15. `/ledger`
16. `/demo`

Every screen has loading, empty, error, unsupported, stale, and terminal states
as applicable. Numbers come from typed API facts, never parsed narrative or UI
constants.

## Team ownership

| Person | Primary ownership |
| --- | --- |
| Dev 1 - Frontend | React shell and 16 screens, generated client, charts/lineage, SSE and approval UX, Playwright. Already in progress. |
| Dev 2 - Backend foundation and Measurement | Sole ownership of shared routing/contracts, activity/hourly import, data quality, Scope 2 timestamp alignment, ledger/audit, generic approval lifecycle, and the reset transaction. |
| Dev 3 - Assurance backend | Generic document/evidence upload plus Assurance standards, requirements, drafts, atomic claims, retrieval, citation/gap validation, staleness, domain handlers under Dev 2's shared API contract, and approval-preview integration. |
| Dev 4 - Procurement, Dispatch, integrations, and QA | Electricity Maps/grid-point and forecast sync, Procurement contract gaps, deterministic Dispatch, canonical fixture builders, golden replay, cross-module E2E, and demo readiness. |
| Dev 5 - Agentic runtime | Context/planning, five LangGraph graphs, 24 typed tools, model-provider execution, persisted run steps, interrupts/resume, SSE, evaluations, and agent telemetry. |

The ownership table above and release-blocking journeys below define the work
boundaries and acceptance gates. Current implementation is established by code,
tests, and the generated OpenAPI schema.

## Four-day delivery sequence

### Day 1 - contract and platform

Freeze IDs, API and tool contracts, terminal states, and the fixture manifest.
Verify the completed 46-table clean bootstrap, seed a skeleton, and prove upload
-> run record -> persisted SSE. No feature developer is assigned an old-database
migration.

### Day 2 - Measurement and Assurance

Complete hourly Scope 2, history fixture/live parity, confidence v2, lineage,
atomic claims, citations/gaps, and a blocked unsupported claim. End with live
source-to-claim trace-back and deterministic replay.

### Day 3 - Procurement and Dispatch

Complete aluminium fixtures, constraints/scoring/impact, forecast fixture/live
parity, optimizer, generic previews, stale checks, and all-four orchestration.
Feature-freeze after P0 E2E passes.

### Day 4 - hardening and submission

Bug fixes only, provider failure paths, telemetry, clean-machine rehearsal,
three dry runs, documentation, architecture/deck, video, tag, and early upload.

## Release-blocking journeys

1. Clean local setup creates/verifies 46 tables and seeds without manual SQL.
2. Reset is safe, reproducible, and rejects non-synthetic data.
3. Hourly Scope 2 and purchased-material outputs reproduce golden Decimal values
   and hashes.
4. Every displayed numerical fact traces to a real raw row/provider snapshot,
   calculation, evidence, and ledger event.
5. Missing/duplicate data creates typed issues and blocks invalid verification.
6. Assurance supports valid claims and blocks an unsupported reduction claim.
7. Procurement preserves hard constraints and returns no feasible option rather
   than relaxing them.
8. Dispatch selects a complete feasible lower-carbon window and never actuates.
9. Generic previews cover disclosure, procurement, and dispatch.
10. Changed fact/evidence/forecast/method/constraint invalidates a preview.
11. Repeated decisions are idempotent and create one commit event.
12. The four-module run respects budgets and exposes a complete replayable trace.

## Definition of done

- All four modules execute in one connected golden-path run.
- Deterministic outputs and hashes replay from declared fixtures.
- Every numerical fact is clickable to live lineage and evidence.
- Unsupported, stale, no-data, provider-failure, and infeasible states are
  explicit.
- Disclosure, procurement, and dispatch use Preview-Approve-Commit.
- Agent tools, retries, context, and model calls remain bounded.
- Run trace shows node/tool/API/cache/token/retry/latency and footprint
  assumptions.
- No customer, personal, confidential, or secret data exists in fixtures,
  prompts, logs, screenshots, or traces.
- Docker/database bootstrap and verification, seed/reset, tests, and E2E work on
  a clean machine. A legacy-data transition is required only if the team chooses
  to preserve an older live database.
- README, architecture/design document, limitations, data/source register,
  8-10 slide deck, and 3-5 minute running-agent video are complete.

## Working rules for coding agents

1. Read the latest request and this file; do not implement unrelated roadmap
   items.
2. Inspect `git status` and current files before editing; shared changes belong
   to the team.
3. Keep routes thin, services transactional, repositories persistence-only, and
   Pydantic schemas at API/tool boundaries.
4. Database models never leak into API responses.
5. Use `AsyncSession`; transaction owners commit explicitly and helpers roll
   back failures.
6. Use `Decimal`, UTC timestamps, ISO currency codes, server-generated UUIDs,
   and stable fixture IDs.
7. Do not add an endpoint that pretends an unimplemented workflow succeeded.
8. Do not perform DDL on application startup or mutate the shared database while
   developing features.
9. Do not run destructive reset, bootstrap, or optional transition commands
   without resolving and verifying the exact disposable target.
10. Add tests in proportion to risk, prioritizing formulas, time alignment,
    constraints, citations, hashes, transactions, idempotency, lineage, and
    replay.
11. Run relevant Ruff/pytest/frontend checks before reporting completion.
12. Update this file only when the team intentionally changes a shared decision.
13. Keep generated verification reports, test output, and local editor settings
    out of source control. Use the existing test suite and CI rather than adding
    parallel one-off verification frameworks.

## References

- Attached four-module hackathon implementation blueprint (external source).
- [System architecture](docs/architecture/system.md)
- [Database architecture](docs/architecture/database.md)
- [API contract](docs/api/contract.md)
- The current repository, tests, OpenAPI schema, and package manifests.
