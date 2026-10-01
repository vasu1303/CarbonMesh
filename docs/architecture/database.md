# CarbonMesh Database Architecture

Last updated: 2026-10-01

## Status and source of truth

The checked-in SQLAlchemy metadata implements the target relational contract:
46 tables across eight PostgreSQL schemas. The model registry under
`apps/api/app/db/models` is the code-level source of truth; this document
explains its ownership and integrity rules.

This does not mean the old shared Neon branch has been upgraded. The explicit
bootstrap creates or verifies a pristine/complete database and intentionally
rejects the earlier five-schema layout. A reviewed, data-preserving transition
for an existing database remains an operator task and must be rehearsed on a
disposable copy before it is applied anywhere shared.

## Table catalog

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

The registry test fails unless the names and distribution match this catalog
exactly.

## Implemented physical changes

The model transition makes these changes to the earlier database design:

- agent runs move from the carbon domain to `ai.agent_runs`, with ordered
  node/tool/provider telemetry in `ai.agent_run_steps`;
- Procurement-only approvals move to generic `core.approvals`;
- Procurement-only bindings move to shared `ledger.fact_bindings`;
- the physical recommendation table becomes
  `procurement.procurement_recommendations`;
- `semantic.policy_definitions` stores executable policy identity and content;
- `carbon.grid_intensity_points` stores timestamped grid observations instead
  of treating hourly electricity data only as date-ranged material factors;
- Assurance and Dispatch receive their own six-table and five-table domains.

Temporary Python import aliases keep the implemented Measurement, Procurement,
approval, audit, and agent services working while their imports are migrated.
New foreign keys always resolve to the new physical identities.

## Connection and lifecycle

The API reads one secret-safe `DATABASE_URL` and converts it to the asyncpg
dialect. The parser accepts two explicit modes:

| Mode | TLS | Pool behavior |
| --- | --- | --- |
| Hosted/remote PostgreSQL | `sslmode=require` is mandatory; certificate chain and hostname are verified | Neon uses `NullPool` and disables prepared-statement caches |
| Approved local/Docker host | `sslmode=disable` is allowed only for loopback or the allowlisted Compose service names | SQLAlchemy's bounded pool with pre-ping |

Malformed URLs, non-PostgreSQL protocols, unknown TLS modes, and remote
unencrypted URLs fail with credential-free errors. URLs, driver exceptions,
document bodies, and provider payloads are never returned to clients.

The engine and async session factory are lazy process-wide objects. Session
scopes roll back errors and never commit implicitly. The use-case service owns
each transaction and calls `commit()` explicitly.

FastAPI startup and shutdown never create, alter, reset, or seed database
objects. Shutdown only disposes runtime resources.

## Creation and verification

For a new disposable database:

```powershell
cd apps/api
python -m app.db.bootstrap
python -m app.db.bootstrap --check
```

Bootstrap:

1. loads all eight model modules and validates the exact 46-table registry;
2. acquires a transaction-scoped advisory lock;
3. refuses a partial, extra, or incompatible CarbonMesh schema;
4. enables pgvector in `public` and creates the eight application schemas;
5. creates tables from SQLAlchemy metadata;
6. installs three views and the immutable-ledger trigger;
7. verifies every table, column type/nullability, UUID default, constraint,
   index, view, vector contract, and trigger before commit.

`--check` is read-only. It performs the same structural verification without a
DDL transaction.

The application tables intentionally do not appear under PostgreSQL's `public`
schema. Verify the catalog with:

```sql
SELECT table_schema, table_name
FROM information_schema.tables
WHERE table_type = 'BASE TABLE'
  AND table_schema IN
      ('core', 'semantic', 'ai', 'carbon', 'ledger',
       'assurance', 'procurement', 'dispatch')
ORDER BY table_schema, table_name;
```

The result must contain 46 rows with the distribution above.

## Shared relational rules

- Entity identifiers are PostgreSQL UUIDs with a `gen_random_uuid()` server
  default. Stable fixture UUIDs may be supplied explicitly.
- Every tenant-owned table contains `company_id`.
- Foreign keys to tenant-owned data are composite `(company_id, id)` links so
  a row cannot reference another company's entity.
- Foreign keys use `ON DELETE RESTRICT`; history is not cascade-deleted.
- Timestamps are timezone-aware and stored in UTC.
- Quantity, money, carbon, percentage, confidence, and score values use exact
  `NUMERIC`/Python `Decimal`, never floating-point columns.
- Domain states use checked strings. Hashes use lowercase 64-character SHA-256
  checks where applicable.
- Mutable reference records carry `created_at` and `updated_at`; append-oriented
  facts/events normally carry only `created_at`.
- JSONB is reserved for bounded snapshots/configuration whose shape genuinely
  varies. Stable query fields remain relational columns.

## Schema responsibilities

### `core`

| Table | Responsibility and important identity |
| --- | --- |
| `companies` | Tenant root, stable code, synthetic/active flags. |
| `sites` | Company site, timezone, ISO country, coordinates, Electricity Maps zone. |
| `reporting_periods` | Closed date range and open/closed/locked lifecycle. |
| `actors` | Tenant actor and bounded CarbonMesh role. |
| `data_sources` | CSV/JSON/PDF/API/synthetic source configuration and state. |
| `source_documents` | Immutable import identity, SHA-256 checksum, storage reference, metadata, import time. |
| `evidence_items` | Located evidence text, checksum, metadata and optional `VECTOR(768)` embedding. |
| `approvals` | Generic target, exact preview payload/hash, analysis/context identity, requester/decider, expiry, idempotency, decision ledger link. |
| `audit_log` | Append-oriented action/entity/trace/run record with sanitized details. |

`core.approvals` temporarily retains a nullable Procurement recommendation
binding so the existing service remains operational. New Assurance and Dispatch
services must populate `target_type`, `target_id`, and `preview_payload` and must
revalidate the target in the decision transaction.

### `semantic`

| Table | Responsibility |
| --- | --- |
| `semantic_entities` | Canonical scoped terms for standards, materials, units, suppliers, metrics, sites, loads, zones, and requirements. |
| `semantic_aliases` | Normalized aliases resolving to one canonical entity. |
| `metric_definitions` | Canonical unit, dimensions, method key, formula/configuration, active dates. |
| `method_definitions` | Named calculation/scoring/validation/optimization method with code/configuration identity. |
| `policy_definitions` | Named executable policy, scope, rule set, priority, and effective dates. |

Methods and policies are append/revision oriented. Historical artifacts retain
the exact IDs and hashes used when they were created.

### `ai`

| Table | Responsibility |
| --- | --- |
| `agent_runs` | Frozen context, plan/result, workflow/stage/state, trace, parent run, counters, token/latency/energy telemetry, error and lifecycle timestamps. |
| `agent_run_steps` | Ordered context/planner/policy/graph/node/tool/provider/retrieval/validation/interrupt/approval/finalize events for replay. |

The physical run table accepts the largest approved golden-flow ceiling (six
model calls and 20 tools). Graph and Pydantic policy applies the smaller limit
for a particular workflow. This keeps persistence compatible with both a
single-module run and the four-module path.

### `carbon`

| Table | Responsibility |
| --- | --- |
| `raw_activity_records` | Exact imported row, row identity, checksum, payload and import disposition. |
| `activity_records` | Validated canonical activity scoped to site/period/metric/product. |
| `emission_factors` | Evidence-backed material/product/geography factor and effective dates. |
| `grid_intensity_points` | Site/zone/provider observation timestamp, gCO2e/kWh value, estimation/flow-traced flags, source/evidence/method identity and point hash. |
| `calculation_runs` | Deterministic method/code/rounding/input/output identity and run status. |
| `emission_calculations` | Per-input formula, factor or grid point, exact inputs/result, confidence components and output hash. |
| `carbon_measurements` | Aggregated verified metric fact, confidence, calculation run, ledger event and supersession state. |
| `data_quality_issues` | Typed source/entity issue, severity, field/details, open/resolved/waived lifecycle. |
| `carbon_baselines` | Context/metric baseline value, method and effective dates. |
| `variance_alerts` | Measurement-to-baseline variance, threshold and workflow state. |

The existing Electricity Maps adapter still writes an `emission_factors`
cache. Moving that service to `grid_intensity_points`, then using exact
timestamp alignment for Scope 2, is remaining application work.

### `ledger`

| Table | Responsibility |
| --- | --- |
| `ledger_events` | Append-only fact/event payload and hash, entity/context/run identity, optional supersession. |
| `lineage_edges` | Typed directed edge from a source ledger event to a derived event. |
| `ledger_event_evidence` | Many-to-many event/evidence link with role; composite primary key. |
| `fact_bindings` | Generic artifact placeholder bound to a ledger fact, value/display snapshot, evidence, context and binding hashes. |

PostgreSQL rejects UPDATE and DELETE on `ledger.ledger_events`. Corrections append
a new event and use supersession/lineage; they never rewrite numerical truth.

The implemented ledger APIs use tenant scope, bounded pagination, deterministic
ordering, safe evidence metadata, and at most 100 immediate parents/children.
Raw document/evidence bodies are not returned by those endpoints.

### `assurance`

| Table | Responsibility |
| --- | --- |
| `standards` | Evidence/document-backed standard code, revision, jurisdiction, template and effective dates. |
| `disclosure_requirements` | Ordered standard requirement, metric link, claim template, evidence rules and minimum confidence. |
| `disclosure_drafts` | Site/period/standard artifact, narrative/rendered text, hashes, validation summary, run/ledger identity and lifecycle. |
| `disclosure_claims` | Ordered atomic claim, requirement/binding/event links, support state, confidence and validation details. |
| `claim_citations` | Claim-to-ledger/evidence citation and deterministic validation state. |
| `evidence_gaps` | Requirement/claim gap with severity and open/resolved/waived lifecycle. |

These tables are implemented; repositories, validators, retrieval orchestration,
routes, and approval behavior are not yet implemented.

### `procurement`

| Table | Responsibility |
| --- | --- |
| `suppliers` | Tenant supplier master and active state. |
| `supplier_products` | Material/product identity, PCF and evidence, cost/currency, lead time, circularity and effective dates. |
| `procurement_scenarios` | Frozen measurement/current product/quantity/context and hard constraints plus scoring weights. |
| `supplier_scores` | Deterministic component/total scores, feasibility reasons and rank. |
| `procurement_recommendations` | Selected product, exact projected impact, bound narrative, analysis/payload hashes, ledger link and invalidation state. |

Only one active pending/approved recommendation is permitted per company and
scenario. Cost is a hard feasibility constraint and is not a weighted score.

### `dispatch`

| Table | Responsibility |
| --- | --- |
| `flexible_loads` | Site load, power/duration, operating timezone and active state. |
| `operating_constraints` | Load availability, delay/capacity/blackout rules and policy identity. |
| `grid_forecasts` | Immutable provider/model/zone forecast snapshot with interval points, source identity and hash. |
| `dispatch_scenarios` | Frozen load/constraint/forecast/baseline context and analysis identity. |
| `dispatch_recommendations` | Recommended advisory window, baseline/recommended emissions, avoided impact, hashes, ledger link and invalidation state. |

These tables are implemented. Forecast synchronization, window enumeration,
optimizer, recommendation service, routes, and approval behavior remain work.
No table or service authorizes physical actuation.

## Indexes, views, and immutable behavior

Important verified objects include:

- partial HNSW cosine index on non-null evidence embeddings with `m=16` and
  `ef_construction=64`;
- tenant/context indexes for sites, periods, sources, evidence, activity,
  factors, grid timestamps, runs/steps, claims/citations/gaps, supplier
  candidates, scenarios, forecasts, and ledger queries;
- unique pending approval per generic target and compatibility-safe unique
  pending approval per Procurement recommendation;
- unique active recommendation per Procurement scenario;
- `carbon.v_measurement_summary`;
- `procurement.v_supplier_comparison`;
- compatibility view `procurement.v_pending_approvals`, which now reads from
  `core.approvals`;
- `ledger.prevent_ledger_event_mutation()` and its BEFORE UPDATE OR DELETE
  trigger.

## Existing-database transition

SQLAlchemy `create_all()` does not rename, move, or alter existing tables. The
old shared structure therefore requires an explicit reviewed operation that:

1. confirms the exact source contract and backup/rollback target;
2. creates the new schemas;
3. moves or renames the four existing physical identities;
4. adds the generic columns/constraints without losing Procurement history;
5. creates the new policy, grid, run-step, Assurance, and Dispatch tables;
6. rebuilds affected foreign keys, indexes, views, and the ledger trigger;
7. backfills target/artifact identity and validates hashes where required;
8. runs the complete bootstrap verifier in read-only mode;
9. executes reset/import/calculate/recommend/approve/replay regression tests;
10. switches application traffic only after reconciliation counts match.

This repository does not yet contain that data-preserving operation. Until it
does, use a fresh disposable target database and retain the previous branch as
rollback. Never use normal bootstrap as an improvised migration command.

## Verification coverage

Unit tests verify:

- exact 46-table names and schema distribution;
- resolution of every foreign key inside registered metadata;
- tenant-aligned composite foreign keys and `ON DELETE RESTRICT`;
- absence of floating-point database columns;
- timezone-aware timestamp columns and UUID server defaults;
- generic approval/binding fields and moved-model import aliases;
- largest agent-run persistence budgets;
- vector dimensions/index options;
- partial unique indexes, views and trigger definitions;
- bootstrap pristine/complete/refusal and read-only check behavior.

The opt-in live Neon suite has been updated for the new physical identities but
must be run only against a confirmed disposable branch. It was not executed as
part of this documentation/model update to avoid mutating shared data.
