# CarbonMesh System Architecture

Last updated: 2026-10-01

## Status

This is the approved target architecture for the four-module hackathon build.
It distinguishes the target from the repository's transition state. A component
is not considered implemented merely because it appears in this document.

- Target: Measurement, Assurance, Procurement, and Dispatch joined by one
  append-only ledger, one bounded agent runtime, and one generic
  Preview-Approve-Commit flow.
- Implemented foundation: the repository has Measurement, Assurance,
  Procurement, and advisory Dispatch domain services plus the
  46-table/eight-schema SQLAlchemy contract, historical grid-intensity sync,
  exact approval previews, audit/lineage, bounded ledger queries, and persisted
  SSE run events.
- Missing feature depth: hourly Scope 2, cross-target approval decisions, five
  LangGraph graphs, durable resume, and agent-sustainability reporting.

## Logical architecture

```mermaid
flowchart TB
  subgraph Sources[Public, open, and synthetic sources]
    PDFs[Invoices and supplier PDFs]
    CSV[Meter and activity CSV]
    Factors[Public emission factors]
    EM[Electricity Maps history and forecast]
  end

  subgraph Experience[Experience]
    Web[React 19 and TypeScript<br/>16 role-oriented screens]
    HTTP[REST commands and queries<br/>SSE progress]
    Web <--> HTTP
  end

  subgraph Runtime[Application and bounded agent runtime]
    API[FastAPI and Pydantic v2<br/>authorization, validation, idempotency]
    Context[Stage 0 contextualizer<br/>context isolation and ContextEnvelope]
    Planner[Intent planner and policy gate<br/>typed ExecutionPlan and budgets]
    Graph[LangGraph orchestrator<br/>five bounded graphs and interrupts]
    Modules[Measurement | Assurance | Procurement | Dispatch]
    API --> Context --> Planner --> Graph --> Modules
  end

  subgraph Control[Deterministic control plane]
    Engines[Deterministic engines<br/>calculation, validation, scoring, optimization]
    Semantic[Semantic catalog<br/>entities, metrics, methods, policies]
    Binder[Fact binding and citation validation<br/>no unbound numerical claims]
    Approval[Preview -> Approve -> Commit<br/>SHA-256, expiry, idempotency]
    Engines --> Binder --> Approval
    Semantic --> Engines
  end

  subgraph Data[Shared data and observability]
    PG[PostgreSQL and pgvector<br/>46 tables, eight schemas]
    Ledger[Ledger, lineage, evidence<br/>source of numerical truth]
    Telemetry[OpenTelemetry and audit<br/>tokens, calls, retries, cache, latency, CO2e proxy]
    PG <--> Ledger
  end

  PDFs --> API
  CSV --> API
  Factors --> API
  EM --> API
  HTTP <--> API
  Modules <--> Engines
  Modules <--> Semantic
  Approval --> PG
  Binder --> Ledger
  API -.-> Telemetry
  Graph -.-> Telemetry
  Engines -.-> Telemetry
```

## Runtime boundaries

1. FastAPI is the only frontend-facing application boundary.
2. Pydantic contracts reject unknown fields at command, tool, and structured
   model-output boundaries.
3. The contextualizer freezes company, site, period, entity scope, methods,
   constraints, actor, and an analysis signature before any specialist runs.
4. The model may plan, decompose a claim, or propose a narrative template. It
   may not calculate business values, issue SQL, invent source IDs, relax a hard
   constraint, approve a decision, or actuate equipment.
5. Typed tools call application services. They do not receive an SQLAlchemy
   session and do not bypass authorization, validation, or transaction rules.
6. Measurement, scoring, claim validation, scheduling, hashes, staleness, and
   approval commits are deterministic Python/PostgreSQL behavior.
7. SSE is a projection of persisted run/step state. Reconnect must not depend
   on an in-memory event buffer.

## Database connection and lifecycle

The API supports two explicit PostgreSQL deployment modes through one
secret-safe `DATABASE_URL` parser:

| Mode | Example host | TLS and pooling |
| --- | --- | --- |
| Shared/hosted | Neon pooled endpoint | `sslmode=require`, verified TLS context, SQLAlchemy `NullPool`, asyncpg statement caches disabled. |
| Local Docker/test | `db`, `localhost`, or loopback | `sslmode=disable` is allowed only for approved local hosts; SQLAlchemy uses its bounded pool with pre-ping. |

Remote non-TLS URLs are rejected. Credentials remain Pydantic `SecretStr`
values and must never be returned, logged, or exposed to the frontend.

FastAPI startup never creates, migrates, resets, or seeds database objects.
Provisioning and migration are explicit operator commands. No migration or
bootstrap command may be run against the shared Neon branch until its target is
confirmed and a backup/branch rollback exists.

## Implemented database contract

The SQLAlchemy registry and clean bootstrap contain exactly 46 relational
tables across eight schemas:

| Schema | Count | Tables |
| --- | ---: | --- |
| `core` | 9 | `companies`, `sites`, `reporting_periods`, `actors`, `data_sources`, `source_documents`, `evidence_items`, `approvals`, `audit_log` |
| `semantic` | 5 | `semantic_entities`, `semantic_aliases`, `metric_definitions`, `method_definitions`, `policy_definitions` |
| `ai` | 2 | `agent_runs`, `agent_run_steps` |
| `carbon` | 10 | `raw_activity_records`, `activity_records`, `emission_factors`, `grid_intensity_points`, `calculation_runs`, `emission_calculations`, `carbon_measurements`, `data_quality_issues`, `carbon_baselines`, `variance_alerts` |
| `ledger` | 4 | `ledger_events`, `lineage_edges`, `ledger_event_evidence`, `fact_bindings` |
| `assurance` | 6 | `standards`, `disclosure_requirements`, `disclosure_drafts`, `disclosure_claims`, `claim_citations`, `evidence_gaps` |
| `procurement` | 5 | `suppliers`, `supplier_products`, `procurement_scenarios`, `supplier_scores`, `procurement_recommendations` |
| `dispatch` | 5 | `flexible_loads`, `operating_constraints`, `grid_forecasts`, `dispatch_scenarios`, `dispatch_recommendations` |

Four existing identities move or change:

- `carbon.agent_runs` becomes `ai.agent_runs` and gains
  `ai.agent_run_steps`.
- `procurement.approvals` becomes generic `core.approvals`.
- `procurement.fact_bindings` becomes generic `ledger.fact_bindings`.
- `procurement.recommendations` becomes
  `procurement.procurement_recommendations`.

`carbon.grid_intensity_points` is the timestamped truth for hourly Scope 2.
Historical grid observations must no longer be represented only as date-ranged
material emission factors.

See [database.md](database.md) for the transition contract, current detailed
dictionary, integrity rules, and migration safety requirements.

## Module data flow

### Measurement

Source document -> raw row -> normalized activity -> factor or timestamped grid
point -> row calculation -> aggregated measurement -> ledger fact -> lineage and
evidence.

### Assurance

Supported requirement -> atomic claim template -> authorized ledger facts and
evidence -> fact binding -> citation validation -> supported/partial/unsupported
state -> approval preview -> immutable decision.

### Procurement

Verified measurement -> frozen constraints -> feasible supplier products ->
deterministic component scores -> impact calculation -> recommendation facts ->
approval preview -> immutable decision.

### Dispatch

Flexible load and operating constraints -> immutable forecast snapshot ->
enumerated feasible windows -> deterministic minimum-emissions choice -> impact
facts -> advisory recommendation -> approval preview -> immutable decision.
There is no actuation tool, equipment endpoint, or control credential.

## API routing strategy

Product operations use stable `/api/...` resource paths with exactly one route
per implemented operation. There is no duplicate legacy mount. Missing features
return no fake successful response and are implemented through their real domain
service.

Canonical routes cover activity import, quality listing, agent run
start/read/SSE, Measurement calculation, grid history/latest, and Procurement
scoring. Tenant-scoped ledger search/detail is implemented as new behavior.
Generic source upload, Assurance, and Dispatch now have real domain services.
Run resume, generic approval detail/decision handling across every target, and
agent-sustainability metrics remain contract gaps.

See [contract.md](../api/contract.md) for the full 38-operation catalog and
implementation status.

## Observability

Every run uses one trace/correlation ID and persists:

- graph/node/tool/provider names and versions;
- model calls, input/output/context tokens, and one-repair counters;
- rows processed, evidence chunks, API/cache behavior, and retries;
- stage and total latency;
- method, code, prompt, policy, and scoring versions;
- source, payload, preview, analysis, and output hashes;
- projected and approved business impact;
- documented compute-energy/CO2e assumptions when a footprint proxy is shown.

Token usage is telemetry, not a direct carbon-footprint measurement.

## Security and failure behavior

- All demo data is synthetic and visibly marked.
- Retrieved documents are untrusted evidence; their text cannot change policy or
  tool access.
- Tenant filters and actor/role checks apply before data leaves a repository.
- Source bodies, prompts, credentials, and provider responses are redacted from
  normal logs.
- Unknown placeholders, invented citations, stale previews, missing forecast
  intervals, ambiguous factors/entities, and exhausted budgets stop in typed
  terminal states.
- Corrections append new ledger events and supersession edges. They do not edit
  prior ledger truth.
