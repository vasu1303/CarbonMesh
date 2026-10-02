# CarbonMesh Current API Reference

Last updated: 2026-10-02

## Scope

This document describes the 41 paths currently published in OpenAPI. Product
routes use one canonical `/api/...` path per implemented operation.

OpenAPI at `http://localhost:8000/docs` is authoritative for complete Pydantic
request/response schemas. The [target contract](contract.md) separately marks
the four-module operations that are still missing.

## Conventions

- Tenant-owned query endpoints require `company_id`; command payloads carry
  company and actor context.
- UUID path/query values use canonical UUID strings.
- Carbon, quantity, cost, percentages, confidence, and scores are exact decimal
  strings in API payloads where their Pydantic contracts require it.
- Timestamps are offset-aware and normalized to UTC by persistence services.
- Commands use explicit transactions. Session helpers never commit implicitly.
- Safe errors contain `code`, `message`, `trace_id`, `retryable`, and optional
  `field_details`; secrets, SQL, source bodies, and provider bodies are omitted.
- `X-Trace-ID` may be supplied by the caller and is echoed in safe error flows.
- No implemented endpoint performs purchasing or equipment actuation.

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

History sync accepts `mode: "fixture"` (default) or `mode: "live"`. Fixture
requests must specify dates present in the synthetic history (starting
`2026-07-01T00:00:00Z`), with at most 240 hours per request. Live mode requires
the server's configured credential and never silently falls back to fixtures.

For Scope 2, send the existing company/site/period context plus
`output_metric_key: "emissions.scope2.location_based"`; the request selects the
hourly activity/method keys and `ELECTRICITY` material by default. An explicit
`grid_method_version` resolves multiple immutable grid versions at one hour.

### Sources and Assurance

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/sources/upload` | Validate and persist one evidence-backed source document. |
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

Standards use `company_id`, `active_only`, `limit`, and `offset` query values
and return ordered requirements. Draft creation accepts company, standard,
site, period, verified measurement, requester, idempotency key, and optional
agent-run/title fields; its `201` response is the full draft view. Draft reads
and evidence packs require `company_id` in the query. Validation accepts
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

Forecast sync defaults to `source_mode: "fixture"`, so the golden path does not
need a provider credential. `source_mode: "live"` uses Electricity Maps v4 with
bounded responses and at most one retry for transient failures. Both modes pass
through the same normalized forecast contract and store points in
`dispatch.grid_forecasts`.

### Agent runs and SSE

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/agent/requests` | Start the existing bounded deterministic workflow. |
| GET | `/api/runs/{run_id}` | Read its state, plan, facts, judgments, and telemetry. |
| GET | `/api/runs/{run_id}/events` | Replay/follow ordered run events as SSE. |

The current workflow is a deterministic Measurement/Procurement router. It does
not yet invoke the configured model provider or LangGraph, and it does not
orchestrate Assurance or Dispatch. SSE is backed by committed run snapshots and
supports replay; ordered
`ai.agent_run_steps`, durable resume, provider events, and approval interrupts
remain work.

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
the complete synthetic four-module fixture and its deterministic golden values.
Hourly Scope 2 executes through the calculation endpoint. The 90-day fixture
must not be treated as complete coverage of the 92-day Q3 reporting period;
coverage and missing intervals remain explicit validation inputs.

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

Dispatch forecast sync requires exactly 24 contiguous hourly points for the
implemented endpoint. Scenario creation freezes the selected source-document
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

The response media type is `text/event-stream`. The existing stream replays
committed event snapshots, honors the supported last-event cursor, emits
heartbeats while waiting, and ends after a persisted terminal state. Clients
must treat terminal events as final and may fall back to bounded polling.

## Known contract gaps

The current OpenAPI intentionally does not claim success endpoints for durable
run resume, generalized recursive ledger traversal, or agent-sustainability
metrics. Their required paths and ownership are in [contract.md](contract.md)
and the [implementation plan](../planning/implementation-plan.md).

## Maintaining this document

When a route or schema changes:

1. update its Pydantic contract and tests;
2. regenerate/inspect OpenAPI;
3. update this reference and the target status table;
4. notify the frontend and agent owners if the shared contract changed;
5. keep one canonical path per operation and remove obsolete aliases.
