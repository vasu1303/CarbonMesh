# CarbonMesh System Architecture

Last updated: 2026-10-05

## Scope and implementation

CarbonMesh connects Measurement, Assurance, Procurement, and advisory Dispatch
through one ledger, generic human approvals, and a bounded agent runtime.
Deterministic domain services, hourly Scope 2, generic approval decisions,
non-agent fact bindings, database readiness, and production approval resume
are implemented. The React workspace connects the four module workflows with
named record selection, approval review, run traces, and evidence diagrams.

The model plans; application services calculate and validate. Grid workflows use
stored data by default; live provider calls require explicit configuration,
including calls from agents. Explicit synthetic fixture mode never substitutes
for a disabled or failed live provider. A typed stop such as
`provider_unavailable` or `budget_exhausted` is not successful completion.
Product rules and team ownership remain in [AGENTS.md](../../AGENTS.md).

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
    Web[React 19 and TypeScript<br/>Prepare, Plan, Review]
    HTTP[REST commands and queries<br/>SSE progress]
    Web <--> HTTP
  end

  subgraph Runtime[Application and bounded agent runtime]
    API[FastAPI and Pydantic v2<br/>context, validation, idempotency]
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
    Telemetry[Persisted run steps and audit<br/>tokens, calls, retries, cache, latency, CO2e proxy]
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

## Persistence and lifecycle

SQLAlchemy 2 async/asyncpg stores the shared contract in PostgreSQL: 46 tables
across eight schemas, including pgvector evidence embeddings. The
[database architecture](database.md) owns the table catalog, TLS/pooling rules,
bootstrap commands, and integrity constraints.

Services own explicit commits. A bounded background agent execution segment
retains one connection across checkpoint commits, then closes it; request and
SSE sessions keep their ordinary lifecycle. Cancellation drains child work and
rolls back interrupted transactions before a typed terminal result is saved.
FastAPI startup never creates, migrates, resets, or seeds database objects.

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

## API and agent boundaries

Each product operation has one canonical `/api/...` route backed by a real
application service. The [API contract](../api/contract.md) documents client
behavior; the running OpenAPI defines exact request/response schemas.

One orchestrator and four specialist graphs share frozen context, signed
constraints, durable tool journals, execution leases, and restart recovery.
Clarification can fill only missing context. The production approval observer
revalidates a decision committed through the generic approval service before
continuation; the agent cannot decide approvals itself.

All 24 frozen tool IDs have typed production handlers. Fresh-input plans can
synchronize an explicit bounded history interval, calculate Scope 2 and material
emissions, and create the downstream drafts and scenarios. Domain transactions
own authoritative ledger writes; `write_ledger_event` validates and replays their
persisted event without allowing a model to append arbitrary ledger content.
Budgets bound model/tool calls, one repair per stage, and active latency; see
AGENTS.md for the approved single-module, four-module, and resume limits.

## Observability

Runs and ordered steps persist trace/context identities, graph/node/tool and
provider outcomes, token/call/retry/cache counters, elapsed time, typed errors,
approval interrupts, and bounded result snapshots. SSE projects committed state
and supports replay. Tool results preserve fact and lineage identifiers instead
of deriving numbers from generated prose.

Energy and CO2e telemetry uses a versioned token proxy with assumptions frozen
across run continuations. Approved projected procurement/dispatch benefits expose
their ledger identities; unknown/mixed assumptions or zero footprint suppress the
benefit ratio. These estimates do not measure realized savings or hardware energy.

OpenTelemetry records correlated HTTP, graph/tool, model, grid, embedding, and
SQL-operation spans. An optional OTLP HTTP exporter sends them to a configured
collector. Structured request logs and spans exclude source bodies, query strings,
SQL text/parameters, credentials, and raw exception messages. Persisted run traces
remain available without an external collector.

## Security and failure behavior

- All demo data is synthetic and visibly marked.
- Retrieved documents are untrusted evidence; their text cannot change policy or
  tool access.
- Tenant filters scope data reads; domain actor/role checks validate commands and
  approval decisions.
- Application authentication is removed. The trusted local/demo API uses no
  access keys, signed sessions, or authentication cookies. Named actors provide
  attribution, and approval validation and the destructive reset token remain.
- Source bodies, prompts, credentials, and provider responses are redacted from
  normal logs.
- Unknown placeholders, invented citations, stale previews, missing forecast
  intervals, ambiguous factors/entities, and exhausted budgets stop in typed
  terminal states.
- Corrections append new ledger events and supersession edges. They do not edit
  prior ledger truth.
