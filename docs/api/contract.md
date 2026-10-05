# CarbonMesh API Contract

Last updated: 2026-10-05

## Scope

The backend exposes implemented operations on canonical `/api/...` paths.
The endpoint index below describes the application catalog.
The running API's `/docs` and `/openapi.json` are authoritative for exact
Pydantic request/response schemas. This document explains client behavior;
[AGENTS.md](../../AGENTS.md) defines product scope and team ownership.

## Conventions

- Tenant-owned query endpoints require `company_id`; command payloads carry
  company and actor context. Company scoping and domain actor/role validation
  remain; actor selection provides attribution without authenticating the caller.
- UUID path/query values use canonical UUID strings.
- Carbon, quantity, cost, percentages, confidence, and scores are exact decimal
  strings in API payloads where their Pydantic contracts require it.
- Timestamps are offset-aware and normalized to UTC by persistence services.
- Commands use explicit transactions. Session helpers never commit implicitly.
- Safe errors contain `code`, `message`, `trace_id`, `retryable`, and optional
  `field_details`; secrets, SQL, source bodies, and provider bodies are omitted.
- `X-Trace-ID` may be supplied by the caller and is echoed in safe error flows.
- No implemented endpoint performs purchasing or equipment actuation.

Live Electricity Maps calls are disabled by default. History/forecast requests
may default to a `live` source mode, but execution, including agent calls,
returns `integration_live_disabled` unless `ELECTRICITY_MAPS_LIVE_ENABLED=true`.
Stored data remains available, and fixture requests require explicit opt-in.

### Workspace access and attribution

REST and SSE requests require no application credentials. Authentication routes,
signed sessions, cookies, and access-key configuration are removed. This API is
for the trusted local/demo workspace. Named actor IDs bind commands and decisions
to existing company records. Approval decisions still require the domain's
approver role, exact preview, expiry, staleness, and idempotency checks. Destructive
demo reset retains its separately configured reset token.

## Endpoint index

### Health and administration

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/health` | Process liveness response. |
| GET | `/api/health/ready` | Read-only verification of the complete database contract; 503 when unavailable or incompatible. |
| GET | `/api/db/demo` | Sanitized asynchronous database connectivity diagnostic. |
| POST | `/api/demo/reset` | Guarded destructive reset of the current synthetic fixture. |

### Context, imports, and quality

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/semantic/metrics` | List active metric definitions for a company. |
| POST | `/api/context/resolve` | Validate context IDs and create a frozen analysis signature. |
| POST | `/api/imports/suppliers` | Import supplier products and evidence. |
| GET | `/api/imports/{import_id}` | Read bounded import status and issues. |
| POST | `/api/activities/import` | Import CSV/JSON activity through raw and normalized records. |
| GET | `/api/quality/issues` | Filter/paginate typed quality findings. |
| PATCH | `/api/quality/issues/{issue_id}` | Audited, tenant/actor-scoped resolve or waive decision. |

Activity and supplier import bodies accept an optional `actor_id` for audit
attribution. When supplied, it must identify an active actor in the selected
company. Replays retain the original audit event and actor; changing an explicit
actor with the same idempotency key is a conflict.

### Measurement and grid history

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/measurements` | Filter/paginate measurement summaries. |
| GET | `/api/measurements/{measurement_id}` | Read inputs, factor, formula, confidence, facts, and evidence. |
| GET | `/api/measurements/{measurement_id}/lineage` | Read bounded source-to-measurement lineage. |
| GET | `/api/measurements/{measurement_id}/breakdown` | Read calculation-backed chart values and lineage identities. |
| POST | `/api/measurement/calculate` | Deterministic purchased-material or exact-hour Scope 2 calculation. |
| POST | `/api/integrations/electricity-maps/test` | Test the server-side provider credential/access safely. |
| POST | `/api/measurement/grid/history/sync` | Synchronize history with explicit company and site query scope. |
| GET | `/api/measurement/grid/latest` | Read the latest cached grid-intensity point with explicit company and site query scope. |
| POST | `/api/emission-factors` | Register an immutable evidence-backed material factor version. |
| GET | `/api/emission-factors` | Read a bounded tenant-scoped factor catalog. |

The implemented calculation handles purchased-material mass and hourly Scope 2
with exact UTC timestamp alignment and `kWh * gCO2e_per_kWh / 1000` using
Decimal. Missing, duplicate, ambiguous, and structurally invalid intervals fail
closed. Scope 2 uses method version `2.0.0` and confidence weights 35/25/20/20;
historical measurements retain their original methods. Coverage is explicit;
an incomplete reporting period cannot support an Assurance period-total claim.
Historical sync stores versioned hourly points
in `carbon.grid_intensity_points`; the latest-point query reads that store and
returns both canonical kgCO2e/kWh and the original provider gCO2e/kWh value with
source-document and evidence provenance, including explicit fixture/live and
synthetic markers.

History sync defaults to `mode: "live"`; offline demos explicitly set `mode: "fixture"`. Fixture
requests must specify dates present in the synthetic history (starting
`2026-07-01T00:00:00Z`), with at most 240 hours per request. Live mode requires
the server's configured credential and never silently falls back to fixtures.
Explicit fixture requests can select `fixture_variant: "complete_q3_v1"` for
the separately versioned 2,208-hour successful path. The default
`quality_cases_v1` preserves the original 90-day quality examples.

For Scope 2, send the existing company/site/period context plus
`output_metric_key: "emissions.scope2.location_based"`; the request selects the
hourly activity/method keys and `ELECTRICITY` material by default. An explicit
`grid_method_version` resolves multiple immutable grid versions at one hour.

### Sources and Assurance

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/sources/upload` | Validate and persist one evidence-backed source document. |
| GET | `/api/sources/{document_id}/content` | Download retained original bytes after tenant and checksum verification. |
| POST | `/api/sources/{document_id}/index` | Idempotently index/reindex existing evidence using the configured embedding model. |
| GET | `/api/assurance/standards` | List tenant-scoped standards and their ordered requirements. |
| POST | `/api/assurance/drafts` | Create an idempotent disclosure draft bound to immutable context. |
| GET | `/api/assurance/drafts/{draft_id}` | Read claims, citations, gaps, binding identities, lineage identity, and approval state. |
| POST | `/api/assurance/drafts/{draft_id}/validate` | Validate facts and evidence, detect gaps/staleness, and create an eligible exact approval preview. |
| GET | `/api/assurance/drafts/{draft_id}/evidence-pack` | Return a structured traceability manifest with safe evidence metadata. |

Source upload accepts company scope; optional site, period, and actor scope;
source/file identity; bounded UTF-8 or base64 content; an optional SHA-256; an
evidence type; metadata; and the synthetic marker. Its `201` response reports
replay state, deterministic method IDs, decoded size, and safe source,
document, and evidence metadata. Matching tenant/checksum uploads replay the
stored result; response payloads do not expose document or evidence bodies.
Original bytes are content-addressed in the configured persistent source store;
download responses force attachment delivery. Evidence indexing uses OpenAI
768-dimensional vectors by default. Explicit `EMBEDDING_PROVIDER=hash` is for
offline tests/demos. Provider failure never changes the configured model.
Supplier imports mark their evidence as requiring explicit indexing. Changing
vector/model metadata changes the evidence fingerprint and invalidates dependent
Assurance previews. Retrieval applies tenant, context, trust, and source-version
filters before bounding candidate similarity search.

Source uploads are limited to 1 MiB decoded content and 128 evidence chunks.
Embedding requests are bounded to 8,000 UTF-8 bytes per input and 250,000 bytes
per batch, with a response size cap and an overall timeout. Reindex requests
require an active actor in the document's company; repeated indexing with
the same model is a no-op. Original downloads verify both byte length and SHA-256,
use `private, no-store`, and never expose local storage paths. Checksum replays
cannot relabel the stored source's site, period, or synthetic status.

Standards use `company_id`, `active_only`, `limit`, and `offset` query values
and return ordered requirements. Draft creation accepts company, standard,
site, period, verified measurement, requester, idempotency key, and optional
agent-run/title/requirement-scope fields. An explicit requirement scope must
belong to the standard and cannot omit a required requirement; the immutable
selection participates in context, source, request, and staleness hashes. Its
`201` response is the full draft view. Draft reads and evidence packs require
`company_id` in the query. Validation accepts
company, requester, idempotency key, and an optional expected context hash; it
returns the full draft, a typed terminal state, support/gap counts, and replay
state. Required unsupported claims or error gaps produce `unsupported` and no
preview. An eligible draft stores an exact `disclosure_draft` preview in
`core.approvals`; numerical facts persist bindings for both direct requests and
agent runs. Evidence packs include ordered claims, gaps,
fact bindings, safe evidence summaries, hashes, and the POC disclaimer.

### Suppliers and Procurement

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/procurement/suppliers` | Filter/paginate tenant-scoped suppliers with risk and product counts. |
| GET | `/api/procurement/products` | Filter/paginate supplier products with commercial/carbon fields. |
| POST | `/api/procurement/scenarios` | Freeze constraints, assess alternatives, and create a preview. |
| POST | `/api/procurement/scenarios/{scenario_id}/score` | Re-run deterministic scoring for a frozen scenario. |
| GET | `/api/procurement/scenarios/{scenario_id}` | Read frozen scenario and comparison matrix. |
| GET | `/api/procurement/scenarios/{scenario_id}/recommendation` | Read a scenario's recommendation facts, scores, bindings, evidence, and hashes. |

Scoring applies hard constraints before the weighted 40/25/20/15 carbon,
evidence, circularity, and operational-fit model. Cost is never a compensating
score. If no product is feasible, the service returns an explicit terminal
result without weakening constraints.

The scenario score operation treats the path `scenario_id` as authoritative;
its body contains only `company_id`, preventing conflicting scenario IDs.
Recommendation review payloads retain deterministic binding snapshots and
persist matching `ledger.fact_bindings` rows, including direct non-agent calls.

### Advisory Dispatch

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/dispatch/loads` | Filter/paginate tenant-scoped flexible loads and their active operating constraints. |
| POST | `/api/dispatch/forecasts/sync` | Normalize and persist one immutable 24-hour hourly forecast from the packaged fixture or live Electricity Maps v4. |
| POST | `/api/dispatch/scenarios` | Freeze the load, method, optional policy, forecast, window, and hard constraints. |
| POST | `/api/dispatch/scenarios/{scenario_id}/optimize` | Enumerate consecutive windows and persist either a recommendation preview or `no_feasible_option`. |
| GET | `/api/dispatch/scenarios/{scenario_id}/recommendation` | Read the persisted advisory result and exact approval preview. |

The deterministic optimizer requires whole-hour alignment and a complete
forecast for every candidate interval. It checks the exact baseline and applies
availability, duration, maximum-delay, capacity, and blackout constraints
without interpolation or relaxation. It minimizes calculated emissions and
uses the earliest feasible start as the tie-break. A recommendation persists
impact, hashes, evidence, lineage, a ledger event, and a generic approval row;
the response always has `actuation_authorized: false`. If no window is feasible,
the typed outcome and rejected-window reasons are persisted and replayed.
Recommendation reads and optimization replays reflect the current decision as
`approved` or `rejected`; invalidated or expired previews return `stale`.

Forecast sync defaults to `source_mode: "live"`. Offline demos explicitly set
`source_mode: "fixture"`. Live mode uses Electricity Maps v4 with
bounded responses and at most one retry for transient failures. Both modes pass
through the same normalized forecast contract and store points in
`dispatch.grid_forecasts`.

### Agent runs and SSE

Agent context defaults to `grid_source_mode: "live"`. Offline demos must explicitly
set `grid_source_mode: "fixture"`. This choice is bound into the analysis signature;
tools and resume cannot switch modes or silently substitute fixtures after a live failure.

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/agent/requests` | Start a policy-gated, bounded agent workflow. |
| GET | `/api/runs/{run_id}` | Read its state, plan, facts, judgments, and telemetry. |
| POST | `/api/runs/{run_id}/resume` | Resume clarification or observe a real generic approval decision. |
| GET | `/api/runs/{run_id}/events` | Replay/follow ordered run events as SSE. |
| GET | `/api/metrics/agent-sustainability` | Read bounded tenant/provider agent resource telemetry and documented footprint proxies. |

The runtime invokes the configured provider-neutral model client for strict
structured planning, then executes a LangGraph orchestrator and bounded
Measurement, Assurance, Procurement, and Dispatch specialist graphs. Its 24
typed tools call application-service ports; an unavailable domain handler
returns explicit `unsupported` rather than placeholder success. Model, tool,
repair, and latency budgets are enforced in application code.

`context.fresh_inputs` selects creation from uploaded source records: at most two
measurements (hourly Scope 2 and purchased material), an optional explicit history
interval of at most 93 days split into bounded provider requests, procurement
quantity/scoring method, and Dispatch method/baseline. Business constraints remain
in the frozen context. Services create calculations, drafts, scenarios and
recommendations; the model cannot supply computed values or choose missing IDs.
Prepared artifact-ID requests retain their replay path.
Fresh requests cannot also select a prepared measurement or override its method.
Explicit outer activity selectors must agree with the nested source selections;
conflicts stop before calculations are written.

`previous_run_id` imports one bounded previous context from the same tenant/actor.
The new request gets a new signature; changed scope clears incompatible artifact
references. Unlimited chat history and source bodies are never projected into the
planner. Semantic definitions and budget limits constrain planning.
Fresh Assurance/Procurement follow-ups include their deterministic Measurement
dependency so previously uploaded sources are revalidated before new artifacts
are created.

Node, tool, retrieval, provider, validation, interrupt, checkpoint, and terminal
activity is appended to `ai.agent_run_steps`. Clarification resumes may fill
only missing frozen context. The production approval observer reads and
revalidates the exact human decision made through the generic approval service.
The agent never decides or commits approval. Agent requests default
`context.grid_source_mode` to `live`; this choice is frozen into signed context,
retained across clarification, and cannot be overridden by tools or earlier
results. Fixture mode requires explicit selection.
Execution leases prevent simultaneous workers from claiming the same run, and
startup recovery resumes persisted plans/checkpoints. Each completed tool result
is journaled exactly under a size cap, so recovery can continue inside a
specialist without re-running that handler; a started tool with no durable
outcome fails closed because its side effect is indeterminate. Deadline
cancellation drains child execution and rolls back interrupted SQL before a
typed terminal outcome is persisted. Budget exhaustion is not completion.

All 24 handlers have production service adapters. Generated plans invoke
`sync_grid_history` only for an explicit bounded interval. `write_ledger_event`
verifies and replays the exact event already written by its domain transaction;
it cannot write arbitrary model-authored events or duplicate a domain commit.

### Approvals, audit, and ledger

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/approvals` | List pending/decided Procurement, Assurance, and Dispatch approvals. |
| GET | `/api/approvals/{approval_id}` | Read the exact frozen payload, hashes, context, expiry, and freshness. |
| POST | `/api/approvals/{approval_id}/decision` | Approve/reject after preview/hash/staleness revalidation. |
| GET | `/api/audit/{entity_type}/{entity_id}` | Read a chronological audit/lineage summary. |
| GET | `/api/ledger/events` | Tenant-scoped bounded ledger search. |
| GET | `/api/ledger/events/{event_id}` | Ledger payload, safe evidence metadata, and immediate lineage. |

Generic `core.approvals` supports `procurement_recommendation`,
`disclosure_draft`, and `dispatch_recommendation`. Decisions revalidate the
frozen payload and current domain dependencies, serialize competing decisions,
and append one ledger decision event. Exact repeats are idempotent; mismatched
decisions fail closed. Dispatch approval records an advisory decision only.
All three modules persist `ledger.fact_bindings` without requiring an agent run.

## Important operation behavior

### Demo reset

`POST /api/demo/reset` requires `X-Demo-Reset-Token` to match the configured
server secret. It is disabled when the secret is absent and returns
`demo_reset_blocked` if any non-synthetic company or data source exists. It
replaces synthetic rows atomically and rolls back the entire reset on seed
failure. Truncation is restricted to the model catalogue and cannot cascade to
unrelated tables. Run only on a disposable rehearsal database.

The current seed is Maverick Manufacturing (synthetic), Plant B (synthetic), Q3
2026. It includes the recycled-aluminium Procurement inputs, one Assurance
template with supporting evidence, and Batch Process 7 with its hard Dispatch
constraints. Grid history and forecasts are synchronized separately so their
source snapshots, checksums, and evidence lineage are produced by the normal
integration paths.

The companion `data/demo/manifest-v1.json` and `expected-results.json` describe
the original synthetic quality-case fixture and its deterministic golden values.
Hourly Scope 2 executes through the calculation endpoint. The 90-day fixture
must not be treated as complete coverage of the 92-day Q3 reporting period;
coverage and missing intervals remain explicit validation inputs.
The separately versioned `complete-q3-manifest-v1.json` bundle covers all 2,208
hours of Q3 for the connected success journey while preserving the original
fixture's missing, duplicate, and estimated-factor cases.

### Imports

Import commands accept a strict JSON envelope carrying either bounded CSV text
or JSON rows. They preserve source/document/raw-row checksums even when
normalization rejects a row. The result reports accepted/rejected counts and
typed issue summaries. Supplier import creates evidence-backed product records;
it does not contact a supplier.

Activity and supplier imports accept an optional `idempotency_key`. Concurrent
identical requests return the original import ID; a changed payload with the
same tenant/kind/key returns a conflict. Hourly electricity rows accept an
offset-aware exact-hour `timestamp` and `kwh`, or `quantity` and an allowed
energy unit. Optional `interval_start`/`interval_end` declare the expected
coverage so missing leading and trailing hours become explicit issues.

Quality PATCH accepts `company_id`, `actor_id`, `status` (`resolved` or
`waived`), and `decision_note`. It records one audit entry for an exact repeated
decision. Error issues cannot be waived, and closing a review does not convert
invalid source rows into valid activity.

Correction imports with explicit replacement/supersession lineage are not yet
implemented. Structural issues therefore remain blocking after review; a
synthetic rehearsal can reset or use a clean context to import corrected data.

### Emission factor registration

`POST /api/emission-factors` requires company and actor identity, an active
`emissions.scope3.category1` metric, and same-company evidence with valid content
integrity and trusted provenance. Only an active sustainability analyst or system
actor may register a factor. The current deterministic method accepts
`kgCO2e` per `kg`; quantity and quality values use bounded, finite `Decimal`s.

The company/code/version/geography key is immutable. An exact normalized request
replays with `200`; new registration returns `201`; changed content under the
same key returns `409`. Factor, `factor.registered` ledger event, evidence link,
and audit entry commit together. A new version preserves earlier factors and
their measurements. Overlapping equally specific versions require clarification
during calculation rather than an implicit latest-version choice. Registration
events are included in the measurement lineage through factor-use events.

`GET /api/emission-factors` supports company, material/product, active-state,
limit (1-100), and offset filters. It returns typed catalog values and the total
matching count. Evidence IDs are available for trace-back through the ledger.

### Measurement transaction

The purchased-material calculation:

1. validates tenant/site/period/activity context;
2. resolves the most specific valid factor;
3. calculates with `Decimal`;
4. calculates confidence and optional baseline variance;
5. stores run, row calculation, aggregate measurement, evidence links, ledger,
   lineage, and audit in one transaction;
6. returns typed facts rather than parsing generated prose.

Ambiguous/missing factors and invalid data stop explicitly.

### Procurement preview and decision

Scenario creation freezes the current product, quantity, site/period,
measurement, method, hard constraints, and analysis signature. The service
persists deterministic scores/impact, a fact-bound narrative, recommendation,
ledger event, and exact approval preview/hash.

Decision rechecks actor role, expiry, current payload hash, analysis signature,
upstream facts/method/scenario, idempotency, and stale state inside the commit
transaction. It never creates a purchase order.

### Grid history and Dispatch forecast provenance

Historical Electricity Maps points retain their provider timestamp and update
timestamp, estimation flag, lifecycle/flow-traced method, native intensity,
method version, point hash, response checksum, source document, and evidence
item. Repeating an identical provider point version is idempotent; reusing a
version with different content fails closed.

Dispatch forecast sync persists 24 contiguous hourly points. The normalizer
validates all provider points and accepts either 24 points or 25 with an
inclusive trailing boundary; only that valid final boundary is excluded from
the normalized interval, while the complete raw snapshot is preserved. Scenario creation freezes the selected source-document
checksum and per-point hashes/evidence before optimization. Missing or changed
forecast inputs, or changed frozen load/method/policy inputs, fail closed rather
than silently changing a recommendation.

### Ledger search

`GET /api/ledger/events` requires `company_id` and supports exact event/entity,
entity ID, correlated agent run, inclusive offset-aware time bounds, `limit`
1-100, and `offset` 0-10,000. Results are newest first with a deterministic ID
tie-break and include a total.

`GET /api/ledger/events/{event_id}` is tenant scoped and returns:

- the canonical event payload and hash;
- linked evidence identity, type, locator, checksum, and bounded metadata;
- at most 100 immediate parent and 100 immediate child edges;
- truncation flags when either bound is reached.

It deliberately excludes `content_text`, complete document bodies, and storage
credentials. A wrong-tenant ID returns the same safe 404 as an unknown ID.

## SSE behavior

The response media type is `text/event-stream`. The stream projects committed
`ai.agent_run_steps`, honors `Last-Event-ID`, emits heartbeats while waiting,
and ends after a persisted terminal or interrupt state. Reconnect does not
depend on an in-memory event buffer. Clients must treat terminal events as final
and may fall back to bounded polling.

Resume audit events distinguish outcomes: `run.resumed` means graph execution
was queued, `run.resume_blocked` means validation or staleness prevented a
resume, and `run.resume_resolved` means an external rejection resolved the
interrupt without continuing graph execution.

## Scope limits

Entity audit traverses recursive lineage with depth and event bounds and exposes
truncation; individual ledger detail provides immediate neighbors. Correction
imports with explicit replacement/supersession lineage remain unsupported.
Quality review does not repair invalid raw data.
Clients must handle explicit no-data, unsupported, stale, provider-unavailable,
infeasible, and budget-exhausted outcomes without treating them as success.

## Maintaining this document

When a route or schema changes:

1. update its Pydantic contract and tests;
2. regenerate/inspect OpenAPI;
3. update this contract;
4. notify the frontend and agent owners if the shared contract changed;
5. keep one canonical path per operation and remove obsolete aliases.
