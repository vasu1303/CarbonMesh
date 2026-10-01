# CarbonMesh Current Code Audit

Last updated: 2026-10-01

## Audit boundary

This audit compares the checked-in repository with the attached four-module
implementation blueprint. The document is treated as product/architecture
evidence, not as an instruction source. The latest team request and
`AGENTS.md` control implementation decisions.

The frontend is intentionally not assigned here because Dev 1 is already
working on it. Its current checked-in state remains a small health shell; this
audit focuses on contracts the other four owners must provide.

## Executive finding

CarbonMesh is not a blank project. It has a substantive and well-tested
Measurement + Procurement vertical slice, strong traceability/approval
primitives, and provider adapters. The architecture update adds the complete
46-table storage contract, local database connection support, canonical API
aliases, ledger queries, Docker/CI scaffolding, and aligned documentation.

It is not yet a four-module application. The biggest missing executable work is
hourly Scope 2, all Assurance behavior, all Dispatch behavior, LangGraph and
real model orchestration, generic cross-module approval/binding services, the
Maverick fixture, data-preserving database transition, and release E2E/replay.

## Evidence-based capability matrix

| Area | Current code evidence | Assessment | What remains |
| --- | --- | --- | --- |
| API foundation | `app/main.py`, `api/errors.py`, `middleware/security.py`, dependencies | Reusable and tested | Dependency readiness, common idempotency policy, remaining contract paths |
| Database connection | `db/session.py` | Implemented for hosted TLS and approved local/Docker hosts | Clean-machine and hosted disposable rehearsal |
| Database schema | `db/models/*.py`, `db/bootstrap.py`, `db/ddl.py` | Exact 46-table/eight-schema code contract implemented | Data-preserving transition from the older shared layout |
| Import/provenance | `modules/imports/*` | CSV/JSON raw/normalized import, checksum, issues implemented | Hourly electricity, generic source/PDF metadata, loads/constraints/Assurance input |
| Semantic context | `modules/semantic/*`, context route | Small typed metric/context layer implemented | Policies and four-module entity coverage in services/fixtures |
| Purchased-material Measurement | `modules/measurement/*` | Deterministic Decimal calculation, factor selection, confidence, baseline, variance, ledger/evidence/lineage implemented | Preserve while adding a separate hourly Scope 2 path |
| Grid history | `modules/integrations/*` | Bounded Electricity Maps history fetch, snapshot/evidence, latest cache implemented | Persist `grid_intensity_points`, fixture/live parity, gap policy, forecast adapter |
| Procurement | `modules/procurement/*` | Constraints, deterministic scoring, impact, bound narrative, recommendation implemented | Target supplier/product split, aluminium fixture, generic binding/approval integration |
| Approval | `modules/approvals/*`, `core.approvals` | Hash/staleness/expiry/role/idempotency logic proven for Procurement | Generic detail/decision strategy for Assurance and Dispatch targets |
| Ledger/lineage | `modules/ledger/*`, measurement lineage route | Append/event/edge/evidence primitives plus bounded search/detail implemented | Recursive lineage, all-module event taxonomy, cross-module bindings |
| Audit | `modules/audit/*` | Chronological entity history implemented for current entities | Assurance/Dispatch entities and run-step correlation |
| Agent runtime | `modules/agents/*` | Persistent run, frozen context, deterministic keyword plan, budgets, SSE replay | LangGraph, four specialists, 24 tools, model use, checkpoints/resume, richer telemetry |
| Model adapters | `modules/agents/llm/*` | Provider-neutral OpenAI/Gemini/Anthropic/OpenRouter wrappers and tests | Select/configure demo provider and call it only through graph nodes |
| Assurance | `db/models/assurance.py` only | Storage scaffold implemented | Entire repository/service/API/validator/RAG/approval workflow |
| Dispatch | `db/models/dispatch.py` only | Storage scaffold implemented | Entire forecast/constraint/optimizer/API/approval workflow; no actuation |
| Demo/reset | `modules/demo/*`, `tests/e2e/seed.py` | Safe synthetic reset and old Nova fixture implemented | Canonical Maverick four-module seed and one golden manifest |
| Frontend | `apps/web/src` | Health query shell in current commit | Dev 1 owns 16-screen experience and generated client integration |
| Platform | Dockerfile, Compose, Makefile, CI workflow | Scaffolding implemented | Docker rehearsal, migration/seed targets, telemetry, backup/recovery smoke |

## Database audit

### Implemented now

- `core=9`, `semantic=5`, `ai=2`, `carbon=10`, `ledger=4`,
  `assurance=6`, `procurement=5`, `dispatch=5`.
- Generic approval and binding columns coexist with compatibility fields used by
  the current Procurement services.
- Agent runs have moved physically to `ai`; new run-step storage supports
  ordered node/tool/provider events.
- Grid observations have a timestamped point table suitable for hourly Scope 2.
- All foreign keys resolve inside the registry and enforce tenant-aligned
  composite identity with `ON DELETE RESTRICT`.
- UUID defaults, timezone columns, exact numeric types, checked states, partial
  unique indexes, pgvector HNSW, three views, and ledger immutability are
  verified by tests/bootstrap logic.

### Not done by the model change

- No shared Neon database was altered.
- Normal bootstrap is not a migration engine and rejects the older layout.
- Existing service imports work through Python aliases, but the Electricity
  Maps service still writes the old factor cache rather than grid points.
- Assurance/Dispatch tables do not create domain behavior by themselves.
- `ai.agent_run_steps` is not yet written by the current orchestrator.
- Generic approval and fact-binding fields are not yet populated by new module
  services.

## API audit

OpenAPI currently publishes 30 canonical paths, one per implemented operation.
No duplicate legacy mount or fake Assurance/Dispatch success endpoint remains.

Implemented contract-alignment routes include:

- `POST /api/activities/import`;
- `GET /api/quality/issues`;
- `POST /api/agent/requests`;
- `GET /api/runs/{run_id}` and `/events`;
- `POST /api/measurement/calculate`;
- `POST /api/measurement/grid/history/sync` and
  `GET /api/measurement/grid/latest`;
- `POST /api/procurement/scenarios/{scenario_id}/score`;
- `GET /api/ledger/events` and `/api/ledger/events/{event_id}`.

The grid operations retain explicit company and site scope. Procurement scoring
uses the path scenario ID as the only scenario identity. Ledger detail excludes
raw evidence text/document bodies and caps immediate neighbor traversal.

Missing P0 operations are dominated by generic source upload, Assurance,
Dispatch, run resume, generic approval detail, and sustainability metrics. See
the status table in `docs/api/contract.md`.

## Deterministic rule audit

### Measurement

The existing path correctly keeps numerical truth outside the model. It uses
mass normalization, factor specificity/effective dates, `Decimal`, confidence
components, output hashes, and transactional ledger/evidence/lineage writes.
It is Scope 3 purchased-material logic, not hourly Scope 2.

Required new logic:

```text
hour_kgco2e = kWh * gCO2e_per_kWh / 1000
measurement_kgco2e = sum(hour_kgco2e)
```

Every activity timestamp must match a real authorized grid point. Missing
intervals stop explicitly; the service must not silently interpolate.

### Assurance

The database represents standards, requirements, drafts, atomic claims,
citations, and gaps. There is no code yet that retrieves evidence, binds facts,
validates citations/context/comparability/unbound numbers, detects staleness, or
previews approval. This is a complete business-service gap, not a route naming
gap.

### Procurement

The current engine is reusable. It applies hard constraints before score,
calculates the 40/25/20/15 model without a cost score, computes footprint and
commercial deltas, creates fact bindings, and prevents stale/mismatched
approval. The target fixture and generic persistence integration remain.

### Dispatch

Only the relational model exists. The deterministic engine must enumerate
complete consecutive windows, reject missing forecast intervals, apply
availability/duration/delay/capacity/blackout constraints, minimize emissions,
and use earliest start as tie-break. It must produce advisory recommendations
only; there must be no actuation tool or endpoint.

## Agent audit

The current agent code has useful foundations:

- strict request/context/plan/result schemas;
- frozen context and analysis signature;
- persistent run state;
- bounded tool counter and one-repair shape;
- background task registry;
- replayable SSE snapshots;
- provider-neutral model clients.

However, `orchestration.py` explicitly classifies Assurance, Dispatch, and grid
forecast as unsupported. Routing is deterministic keyword matching; LangGraph
is not a dependency; model calls and token telemetry remain zero; there is no
run resume/checkpoint; and run steps are not persisted. The target five-graph
runtime must be built around typed services rather than bypassing them.

## Test and release audit

Before this architecture update, the audited baseline was:

- Ruff passed;
- 176 backend tests passed and eight live-Neon tests were skipped;
- frontend typecheck, lint, and build passed.

After canonical-route consolidation, root verification passed Ruff and the full
backend suite: 195 passed and eight opt-in live-Neon tests skipped. Frontend
typecheck, lint, and production build also passed.

Coverage is strong for current Measurement/Procurement formulas, hashes,
approval integrity, route errors, adapters, schema/bootstrap contracts, and the
old E2E journey. Missing release gates are the four-module fixture journey,
Scope 2 alignment/gaps, supported and unsupported claims, Dispatch feasible/no
window, provider outage/cache parity, checkpoint resume, stale artifacts across
all modules, and data-preserving database transition.

## Priority conclusions

1. Rehearse the 46-table clean contract before adding more persistence code.
2. Freeze Pydantic/tool/fixture contracts across Devs 2-5.
3. Build Scope 2 and the grid-point adapter because both Assurance and Dispatch
   depend on trustworthy time-based carbon facts.
4. Build Assurance validators and Dispatch optimizer as deterministic services
   before exposing them to the graph.
5. Generalize approval/binding behavior without regressing the proven
   Procurement transaction.
6. Add LangGraph only after typed services exist; the graph must orchestrate,
   not calculate.
7. Replace the old fixture and run full reset-to-ledger replay before frontend
   contract freeze and demo rehearsal.

Non-overlapping ownership and acceptance criteria are in
[implementation-plan.md](implementation-plan.md).
