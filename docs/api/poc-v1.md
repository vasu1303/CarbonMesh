# CarbonMesh POC API v1 Reference

This consolidated document explains every currently implemented HTTP API, what
it does, the parameters or request body it accepts, and the success body it
returns. It is a human-readable companion to the generated OpenAPI document at
`/openapi.json` and Swagger UI at `/docs`. The live OpenAPI schema remains
authoritative for validation constraints.

## API conventions

- Versioned business APIs use `/api/v1`. `/api/health` and `/api/db/demo` are
  legacy bootstrap/diagnostic routes.
- Tenant-owned reads require `company_id`; commands carry company and actor
  context in their body where relevant. This is explicit POC scoping, not a
  substitute for production authentication/authorization.
- UUIDs are JSON strings in standard UUID form. Dates use `YYYY-MM-DD` and
  timestamps use ISO 8601 with an offset, normally UTC.
- Exact decimal values—carbon, quantities, money, percentages, and scores—are
  returned as JSON strings to preserve precision. Requests generally accept a
  JSON number or decimal string where OpenAPI says `Decimal`.
- Optional `X-Trace-ID` values must start with an alphanumeric character and
  may contain letters, digits, `.`, `_`, `:`, and `-` (maximum 100 characters).
  The backend generates one when absent.
- Paginated responses use `items`, `total`, `limit`, and `offset`.
- `POST /api/v1/demo/reset` is disabled unless the server has a reset token
  configured and the request supplies the same `X-Demo-Reset-Token`. The token
  is never logged.

## Endpoint index

| Method | Path | Success | What it does or retrieves |
| --- | --- | ---: | --- |
| GET | `/api/health` | 200 | Lightweight process health. |
| GET | `/api/v1/health` | 200 | Versioned alias of process health. |
| GET | `/api/db/demo` | 200 | Read-only database connectivity/time check. |
| POST | `/api/v1/demo/reset` | 200 | Destructively reset and seed the synthetic demo dataset. |
| GET | `/api/v1/semantic/metrics` | 200 | Active/versioned metric definitions for a company. |
| POST | `/api/v1/context/resolve` | 200 | Validate IDs and freeze a signed workflow context. |
| POST | `/api/v1/imports/activity` | 201 | Import and normalize activity CSV/JSON. |
| POST | `/api/v1/imports/suppliers` | 201 | Import suppliers, products, and evidence. |
| GET | `/api/v1/imports/{import_id}` | 200 | Import status, counts, and bounded issues. |
| GET | `/api/v1/data-quality/issues` | 200 | Filtered/paginated validation findings. |
| POST | `/api/v1/measurements/calculate` | 200 | Deterministically calculate a carbon measurement. |
| GET | `/api/v1/measurements` | 200 | Filtered/paginated measurement summaries. |
| GET | `/api/v1/measurements/{measurement_id}` | 200 | Complete measurement inputs, factor, formula, and facts. |
| GET | `/api/v1/measurements/{measurement_id}/lineage` | 200 | Bounded ledger/evidence graph for a measurement. |
| GET | `/api/v1/suppliers` | 200 | Filtered/paginated supplier products. |
| GET | `/api/v1/suppliers/{product_id}` | 200 | One supplier product with evidence detail. |
| POST | `/api/v1/procurement/assessments/run` | 200 | Re-run deterministic assessment for a frozen scenario. |
| POST | `/api/v1/procurement/scenarios` | 201 | Freeze constraints, assess alternatives, and create approval preview. |
| GET | `/api/v1/procurement/scenarios/{scenario_id}` | 200 | Frozen scenario and comparison matrix. |
| GET | `/api/v1/procurement/recommendations/{recommendation_id}` | 200 | Recommendation facts, evidence, scores, narrative, and hashes. |
| POST | `/api/v1/agent/query` | 202 | Start a bounded in-process workflow run. |
| GET | `/api/v1/agent/runs/{run_id}` | 200 | Run state, plan, facts, judgments, and telemetry. |
| GET | `/api/v1/agent/runs/{run_id}/events` | 200 SSE | Replay/follow ordered run events. |
| GET | `/api/v1/approvals` | 200 | Pending or decided approval previews. |
| POST | `/api/v1/approvals/{approval_id}/decision` | 200 | Approve/reject the exact current preview. |
| GET | `/api/v1/audit/{entity_type}/{entity_id}` | 200 | Audit timeline and connected ledger lineage. |
| POST | `/api/v1/integrations/electricity-maps/test` | 200 | Test server-side provider credentials/access. |
| POST | `/api/v1/sites/{site_id}/grid-intensity/sync` | 200 | Fetch, evidence-snapshot, and cache recent grid intensity. |
| GET | `/api/v1/sites/{site_id}/grid-intensity/latest` | 200 | Latest cached site/company-zone intensity and provenance. |

## Health, diagnostics, and demo administration

### `GET /api/health` and `GET /api/v1/health`

Returns process health without querying PostgreSQL. There are no parameters or
request body.

| Response field | Type | Meaning |
| --- | --- | --- |
| `status` | literal `"ok"` | Process accepted the request. |
| `service` | string | Service identifier. |

### `GET /api/db/demo`

Runs a small read-only SQL query to prove that the configured database is
reachable. It accepts optional `X-Trace-ID` and has no body.

| Response field | Type | Meaning |
| --- | --- | --- |
| `connected` | boolean | Whether the database check succeeded. |
| `database_time` | timestamp | Time returned by PostgreSQL. |
| `message` | string | Sanitized diagnostic summary. |

### `POST /api/v1/demo/reset`

Resets only the demo-owned dataset and recreates the agreed synthetic company,
site, period, metrics, activity, factors, and products. It is destructive and
must not be treated as an application-startup operation. There is no body.

Headers: optional `X-Trace-ID`; `X-Demo-Reset-Token` (16–256 characters) is
required. The endpoint returns 503 when no server token is configured and 403
when the supplied token is absent or incorrect.

| Response field | Type | Meaning |
| --- | --- | --- |
| `status` | literal `"reset"` | Confirms reset completion. |
| `synthetic` | literal `true` | Makes the nature of the data explicit. |
| `company_id` | UUID | Seeded Nova Components company. |
| `site_id` | UUID | Seeded Plant B site. |
| `reporting_period_id` | UUID | Seeded Q3 2026 period. |
| `seeded.metrics` | integer | Number of metric definitions created. |
| `seeded.activity_records` | integer | Number of activity records created. |
| `seeded.supplier_products` | integer | Number of supplier products created. |
| `seeded.emission_factors` | integer | Number of factors created. |

## Semantic context APIs

### `GET /api/v1/semantic/metrics`

Retrieves metric definitions belonging to one company.

Query parameters:

| Parameter | Required | Meaning |
| --- | --- | --- |
| `company_id` | Yes | Tenant company UUID. |
| `active_only` | No, default `true` | Exclude inactive metric versions when true. |

Response:

| Field | Type | Meaning |
| --- | --- | --- |
| `company_id` | UUID | Resolved tenant. |
| `items` | array of metric definitions | Returned metric versions. |
| `count` | non-negative integer | Number of items. |

Each metric item contains `id`, stable `key`, `version`, display `name`,
`canonical_unit`, variable `dimensions`, typed `handler`, `method_version`, and
optional `description`.

### `POST /api/v1/context/resolve`

Validates the requested company/site/period/metrics/actor/supplier scope,
normalizes constraints, and produces a frozen SHA-256 `analysis_signature`.
Nothing is guessed when a referenced row is missing or belongs to another
tenant.

Request body:

| Field | Required | Type and meaning |
| --- | --- | --- |
| `company_id` | Yes | Tenant UUID. |
| `site_id` | Yes | Site UUID that must belong to the company. |
| `reporting_period_id` | Yes | Reporting-period UUID in the company. |
| `metric_definition_ids` | Yes | Array of 1–16 metric UUIDs. |
| `workflow` | Yes | `measurement`, `procurement`, `measurement_procurement`, or `cross_module`. |
| `actor_id` | No | Actor UUID to resolve. |
| `supplier_scope` | No | Up to 100 supplier UUIDs to authorize/freeze. |
| `material_scope` | No | Up to 100 material codes. |
| `constraints.max_cost_increase_pct` | No | Decimal percentage from 0 through 100. |
| `constraints.max_lead_time_days` | No | Non-negative integer. |
| `constraints.minimum_circularity_score` | No | Decimal score from 0 through 100. |

Response body:

| Field | Type | Meaning |
| --- | --- | --- |
| `company` | object | Resolved `id`, `code`, `name`, and `is_synthetic`. |
| `site` | object | Resolved `id`, `code`, `name`, `country_code`, and `timezone`. |
| `reporting_period` | object | Resolved `id`, `name`, `start_date`, `end_date`, and `status`. |
| `metrics` | metric array | Full resolved metric definitions. |
| `workflow` | enum | Frozen workflow selection. |
| `actor` | object or null | `id`, `display_name`, and `role`. |
| `supplier_scope` | array | Resolved supplier `id`, `supplier_code`, and `name`. |
| `material_scope` | string array | Frozen material codes. |
| `constraints` | object | Normalized optional hard constraints; decimal values are strings. |
| `analysis_signature` | 64-char hex string | Hash binding the resolved context. |

## Import and data-quality APIs

### `POST /api/v1/imports/activity`

Accepts CSV or JSON purchased-material activity, stores source provenance and
raw rows, validates and normalizes accepted rows, and creates typed quality
issues for rejected/problematic rows.

Request body:

| Field | Required | Type and meaning |
| --- | --- | --- |
| `company_id` | Yes | Tenant UUID. |
| `site_id` | Yes | Site receiving the activity. |
| `reporting_period_id` | Yes | Period to which rows belong. |
| `metric_definition_id` | Yes | Activity metric/canonical-unit definition. |
| `source_name` | Yes | Source label, 1–160 characters. |
| `filename` | Yes | Original filename, 1–255 characters. |
| `content_type` | Yes | `text/csv` or `application/json`. |
| `content` | Yes | CSV string, JSON array, or JSON object accepted by the importer. |
| `checksum` | No | Caller-computed lowercase SHA-256; mismatch is rejected. |
| `external_reference` | No | Upstream correlation ID, maximum 255 characters. |
| `is_synthetic` | No, default `false` | Marks demo/test input. |

Returns `ImportResult` with HTTP 201, defined below.

### `POST /api/v1/imports/suppliers`

Accepts CSV or JSON supplier/product facts, creates/updates supplier catalogue
rows, and stores supporting evidence and quality issues.

Its request body is the same as the activity import except it omits `site_id`,
`reporting_period_id`, and `metric_definition_id`. It requires `company_id`,
`source_name`, `filename`, `content_type`, and `content`; `checksum`,
`external_reference`, and `is_synthetic` are optional. Returns `ImportResult`
with HTTP 201.

### `GET /api/v1/imports/{import_id}`

Retrieves the current status and bounded issue list for an import. `import_id`
is the path UUID; required query `company_id` enforces tenant ownership. There
is no body. Returns `ImportResult`.

### `ImportResult`

| Field | Type | Meaning |
| --- | --- | --- |
| `import_id` | UUID | Import identifier (the backing data-source ID). |
| `data_source_id` | UUID | Provenance source; currently the same logical import resource. |
| `source_document_id` | UUID or null | Stored document/payload snapshot. |
| `import_type` | `activity` or `suppliers` | Import workflow. |
| `status` | enum | `processing`, `completed`, `completed_with_errors`, or `failed`. |
| `accepted_count` | integer | Rows persisted as usable records. |
| `rejected_count` | integer | Rows not accepted. |
| `issue_count` | integer | Total issues persisted. |
| `returned_issue_count` | integer | Issues included in this response. |
| `issues_truncated` | boolean | True when more issues exist than were returned. |
| `is_synthetic` | boolean | Whether the source was explicitly synthetic. |
| `issues` | issue array | Bounded `DataQualityIssueRead` objects. |
| `created_at` | timestamp | Import creation time. |
| `updated_at` | timestamp | Most recent import status update. |

### `GET /api/v1/data-quality/issues`

Retrieves quality issues without returning document bodies.

Query parameters: required `company_id`; optional `status` (`open` by default,
or `resolved`/`waived`), `severity` (`info`/`warning`/`error`), `code`,
`issue_type`, `import_id`, `limit` (1–200, default 100), and `offset` (default
0). Returns `{items, total, limit, offset}`.

Each item contains `id`, `company_id`, optional `raw_activity_record_id`,
optional `activity_record_id`, optional `import_id`, optional `row_number`,
`issue_type`, stable `code`, `severity`, optional `field_name`, safe `message`,
`status`, structured `details`, `created_at`, and `updated_at`.

## Measurement APIs

### `POST /api/v1/measurements/calculate`

Selects normalized activity and the most specific valid evidence-backed factor,
calculates emissions with `Decimal`, computes confidence and optional baseline
variance, and transactionally persists calculation details, the measurement,
ledger/evidence links, lineage, and audit data. The same canonical input may
return an idempotently reused completed result.

Request body:

| Field | Required | Type and meaning |
| --- | --- | --- |
| `company_id` | Yes | Tenant UUID. |
| `site_id` | Yes | Site whose activity will be measured. |
| `reporting_period_id` | Yes | Measurement period. |
| `material_code` | Yes | Material selector, 1–100 characters. |
| `activity_metric_key` | No | Input metric; defaults to `activity.purchased_material_mass`. |
| `output_metric_key` | No | Output metric; defaults to `emissions.scope3.category1`. |
| `method_key` | No | Deterministic method; defaults to `measurement.scope3.category1.mass_factor`. |
| `geography` | No | Preferred factor geography, 2–100 characters. |
| `activity_record_ids` | No | Restrict the calculation to 1–500 explicit activity UUIDs. |
| `actor_id` | No | Actor to write into audit/ledger context. |
| `agent_run_id` | No | Initiating agent run. |
| `trace_id` | No | Caller correlation ID; generated when absent. |

Success response (`MeasurementResult`):

| Field | Type | Meaning |
| --- | --- | --- |
| `id` | UUID | Measurement/fact identifier. |
| `company_id` | UUID | Tenant. |
| `site_id`, `site_name` | UUID, string | Resolved site. |
| `reporting_period_id`, `reporting_period_name` | UUID, string | Resolved period. |
| `metric_definition_id` | UUID | Output metric version. |
| `metric_key`, `metric_version`, `category` | strings | Semantic classification. |
| `value_kgco2e` | decimal string | Total calculated emissions. |
| `unit` | string | Normally `kgCO2e`. |
| `confidence` | decimal string | Overall weighted confidence, 0–1. |
| `confidence_breakdown` | object | Components, weights, and overall confidence; detailed below. |
| `status` | enum | `draft`, `verified`, `superseded`, or `unsupported`. |
| `formula` | string | Deterministic aggregation formula. |
| `output_hash` | string | Canonical result hash. |
| `verified_at` | timestamp or null | Verification time. |
| `created_at` | timestamp | Persistence time. |
| `calculation_run` | object | Method/code versions, hashes, state, and timings. |
| `inputs` | activity input array | Exact normalized inputs and source-row provenance. |
| `factors` | factor array | Selected factor versions and evidence. |
| `calculations` | calculation array | Row-level quantities, factors, emissions, formulas, and hashes. |
| `baseline` | object or null | Baseline value and variance comparison. |
| `facts` | object | Fact/ledger/output-hash/audit identifiers. |
| `terminal_state` | literal `completed` | Typed successful terminal state. |
| `trace_id` | string | Correlation ID. |
| `idempotent` | boolean | True when an earlier completed run was safely reused. |

### `GET /api/v1/measurements`

Retrieves paginated measurement summaries. Required `company_id`; optional
`site_id`, `reporting_period_id`, `category`, `status`, `limit` (1–100, default
25), and `offset`.

Response is `{items, total, limit, offset}`. Every summary item contains `id`,
`company_id`, site ID/name, period ID/name, metric ID/key/version, `category`,
decimal-string `value_kgco2e` and `confidence`, `unit`, `status`, optional
`ledger_event_id`, `output_hash`, optional `verified_at`, and `created_at`.

### `GET /api/v1/measurements/{measurement_id}`

Retrieves the complete persisted measurement for required path
`measurement_id` and query `company_id`. There is no body. The response has the
same fields as `MeasurementResult` except it omits the command-only
`terminal_state`, `trace_id`, and `idempotent` fields.

### Measurement nested response objects

`confidence_breakdown`:

| Field | Meaning |
| --- | --- |
| `source_quality`, `factor_specificity`, `factor_recency`, `record_completeness` | Decimal-string 0–1 component values. |
| `source_quality_weight`, `factor_specificity_weight`, `factor_recency_weight`, `record_completeness_weight` | Decimal-string weights used in the formula. |
| `overall` | Final weighted decimal-string confidence. |

Each `inputs` item contains:

| Field | Meaning |
| --- | --- |
| `activity_record_id`, `raw_activity_record_id` | Normalized and original row IDs. |
| `data_source_id`, `source_document_id` | Import provenance. |
| `source_row_key`, `source_row_number`, `raw_checksum` | Exact row locator and integrity hash. |
| `supplier_product_id`, `product_code` | Optional resolved product references. |
| `material_code`, `activity_date` | Material and optional activity date. |
| `source_quantity`, `source_unit` | Interpreted source value. |
| `normalized_quantity_kg` | Quantity used in the mass calculation. |
| `record_completeness` | Decimal-string 0–1 completeness score. |

Each `factors` item contains `id`, `factor_code`, `version`, `name`, optional
`material_code`/`product_code`, `geography`, `factor_value`, `numerator_unit`,
`denominator_unit`, normalized `normalized_factor_kgco2e_per_kg`,
`effective_from`, optional `effective_to`, and `evidence`. Evidence contains
`id`, `source_document_id`, `data_source_id`, source filename/document checksum,
`evidence_type`, `locator`, and evidence `checksum`.

Each `calculations` item contains `id`, `activity_record_id`,
`emission_factor_id`, decimal-string `normalized_quantity_kg`,
`factor_kgco2e_per_kg`, `emissions_kgco2e`, human-readable `formula`, and
`output_hash`.

`calculation_run` contains `id`, `method_definition_id`, `method_key`,
`method_version`, `code_version`, `rounding_policy`, `input_hash`, `output_hash`,
`status` (`pending`/`running`/`completed`/`failed`), and optional `started_at` and
`completed_at`.

`baseline`, when present, contains `baseline_id`, `name`, decimal-string
`value_kgco2e`, `unit`, decimal-string `variance_kgco2e`, nullable
`variance_pct`, and optional `variance_alert_id`. `facts` contains `fact_id`,
optional `ledger_event_id`, `output_hash`, and optional `audit_log_id`.

### `GET /api/v1/measurements/{measurement_id}/lineage`

Retrieves a bounded graph showing how a measurement relates to ledger events
and evidence. Required path `measurement_id`, query `company_id`; optional
`X-Trace-ID`. There is no body.

Response:

| Field | Type | Meaning |
| --- | --- | --- |
| `measurement_id` | UUID | Requested measurement. |
| `root_event_id` | UUID or null | Measurement's ledger root when present. |
| `nodes` | array | Ledger-event and evidence graph nodes. |
| `edges` | array | Directed relationships. |
| `truncated` | boolean | Whether service safety bounds omitted connected data. |

Each node contains string `id`, `node_type` (`ledger_event` or `evidence`),
`label`, optional `entity_type`, `entity_id`, `event_type`, structured `payload`,
optional `payload_hash`, optional `created_at`, and `metadata`. Each edge contains
string `id`, source node ID, target node ID, `relationship_type`, and `metadata`.

## Supplier and procurement APIs

### `GET /api/v1/suppliers`

Retrieves supplier products and their commercial/environmental facts. Required
query `company_id`; optional `material_code`, `category`, `active_only` (default
true), case-insensitive `search`, `limit` (1–100, default 50), `offset`, and
`X-Trace-ID` header.

Response is `{items, total, limit, offset}`. Every product summary contains:

| Fields | Meaning |
| --- | --- |
| `id`, `product_code`, `name`, `description` | Product identity and description. |
| `supplier_id`, `supplier_code`, `supplier_name`, `supplier_country_code`, `supplier_status`, `risk` | Supplier facts and derived risk label. |
| `material_code`, `category` | Compatibility/search classification. |
| `pcf_kgco2e_per_unit`, `pcf_unit` | Exact product-carbon factor and unit. |
| `circularity_score`, `recycled_content_pct`, `recyclable_pct`, `evidence_quality_score` | Decimal-string 0–100 sustainability/evidence values. |
| `lead_time_days`, `unit_cost`, `currency` | Commercial/operational facts. |
| `effective_from`, `effective_to`, `is_active` | Validity and lifecycle. |
| `evidence_item_id`, `evidence_available` | Evidence reference/presence indicator. |

### `GET /api/v1/suppliers/{product_id}`

Retrieves one product and its supplier/evidence detail. Requires path
`product_id`, query `company_id`; optional `X-Trace-ID`. There is no body.

The response includes every product summary field plus `company_id`, structured
`supplier_metadata`, optional `evidence`, `created_at`, and `updated_at`.
Evidence contains `id`, `evidence_type`, `locator`, `checksum`, `metadata`,
`source_document_id`, `content_text`, `created_at`, and `updated_at`.

### `POST /api/v1/procurement/scenarios`

Freezes the current product, verified measurement, quantity, method, constraints,
and actor; filters infeasible products; computes deterministic scores and impact;
and, when feasible, creates a recommendation and hash-bound approval preview.
Constraints are never relaxed.

Request body:

| Field | Required | Type and meaning |
| --- | --- | --- |
| `company_id` | Yes | Tenant UUID. |
| `site_id` | Yes | Site under analysis. |
| `reporting_period_id` | Yes | Reporting period. |
| `current_product_id` | Yes | Baseline supplier-product UUID. |
| `carbon_measurement_id` | Yes | Verified baseline measurement UUID. |
| `method_definition_id` | Yes | Procurement scoring method UUID. |
| `requested_by` | Yes | Actor requesting the scenario/approval. |
| `agent_run_id` | No | Initiating agent run. |
| `quantity` | Yes | Positive decimal quantity. |
| `quantity_unit` | Yes | Unit, 1–50 characters. |
| `current_unit_cost` | No | Baseline cost; service may resolve it from current product. |
| `currency` | No | Three-uppercase-letter ISO currency. |
| `max_cost_increase_pct` | No, default `5` | Hard limit from 0 through 100. |
| `max_lead_time_days` | No, default `30` | Hard non-negative lead-time limit. |
| `minimum_circularity_score` | No, default `0` | Hard score floor from 0 through 100. |
| `material_constraints.allowed_material_codes` | No | Up to 100 acceptable material codes. |
| `material_constraints.excluded_risk_levels` | No | Up to 100 supplier risk levels to reject. |
| `approval_expires_at` | No | Explicit preview expiration timestamp. |
| `idempotency_key` | No | 1–255-character repeat-request key. |

Success response (`ProcurementScenarioResult`):

| Field | Meaning |
| --- | --- |
| `id`, `company_id`, `site_id`, `reporting_period_id` | Scenario identity/scope. |
| `current_product` | Full supplier-product summary. |
| `carbon_measurement_id`, `agent_run_id` | Source measurement and optional orchestrator. |
| `method` | Method ID/key/version/code version and scoring weights. |
| `quantity`, `quantity_unit`, `current_unit_cost`, `currency` | Frozen commercial inputs. |
| `constraints` | Cost, lead-time, circularity, and material/risk hard constraints. |
| `weights` | Carbon/evidence/circularity/operational-fit weights as decimal strings. |
| `analysis_signature` | Hash of the frozen analysis context. |
| `frozen_context` | Exact structured context snapshot. |
| `status` | Scenario lifecycle status. |
| `terminal_state` | `completed` or `no_feasible_option`. |
| `alternatives` | Product assessments described below. |
| `selected_recommendation` | Recommendation/approval summary, or null when none is feasible. |
| `created_at`, `updated_at` | Persistence timestamps. |

### `POST /api/v1/procurement/assessments/run`

Idempotently performs or retrieves deterministic product assessments for an
already frozen scenario. Request body contains required `company_id` and
`scenario_id`; optional `X-Trace-ID` header.

Response contains `scenario_id`, the resolved scoring `method`, `terminal_state`
(`completed` or `no_feasible_option`), the `assessments` array, and nullable
`selected_product_id` and `recommendation_id`.

### `GET /api/v1/procurement/scenarios/{scenario_id}`

Retrieves the frozen scenario and comparison matrix. Requires path
`scenario_id`, query `company_id`, and accepts optional `X-Trace-ID`. There is no
body. Returns the same `ProcurementScenarioResult` shape as scenario creation.

### Procurement nested response objects

`method` contains `id`, stable `key`, business `version`, implementation
`code_version`, and `weights`. `weights` contains decimal-string `carbon`,
`evidence`, `circularity`, and `operational_fit` fractions.

Each product assessment contains:

| Field | Meaning |
| --- | --- |
| `score_id` | Persisted supplier-score UUID. |
| `product` | Supplier-product summary. |
| `scores` | Decimal-string `carbon`, `evidence`, `circularity`, `operational_fit`, and weighted `total`, all 0–100. |
| `feasible` | Whether every hard constraint passed. |
| `infeasibility_reasons` | Typed failures containing `code`, `message`, optional `actual`, and optional `required`. |
| `rank` | Rank among feasible alternatives, or null. |
| `impact` | Deterministic projected impact. |

`impact` contains decimal-string `projected_footprint_kgco2e`,
`avoided_kgco2e`, `reduction_pct`, `cost_delta_pct`, and integer
`lead_time_delta_days`.

`selected_recommendation`, when present, contains recommendation `id`, `status`,
`recommended_product_id`, `payload_hash`, `analysis_signature`, `impact`, and
optional approval preview (`id`, `status`, `preview_hash`, `analysis_signature`,
`expires_at`).

### `GET /api/v1/procurement/recommendations/{recommendation_id}`

Retrieves exactly what an approver/auditor needs to review a recommendation.
Requires path `recommendation_id`, query `company_id`, and accepts optional
`X-Trace-ID`.

Response body:

| Field | Meaning |
| --- | --- |
| `id`, `company_id`, `scenario_id`, `status` | Recommendation identity and lifecycle. |
| `baseline_product`, `recommended_product` | Full product summaries. |
| `supplier_score` | Winning assessment, component scores, feasibility, rank, and impact. |
| `evidence` | Supporting evidence summaries (`id`, type, locator, checksum, metadata). |
| `projected_footprint_kgco2e`, `avoided_kgco2e`, `reduction_pct`, `cost_delta_pct`, `lead_time_delta_days` | Verified deterministic impact. |
| `narrative` | Bound explanation object described below. |
| `analysis_signature`, `payload_hash` | Context/method and exact-payload integrity hashes. |
| `impact_snapshot` | Frozen structured values reviewed by the approver. |
| `ledger_event_id` | Immutable recommendation event, or null. |
| `approval` | Current approval preview summary, or null. |
| `invalidated_at` | Time changed inputs invalidated the recommendation, or null. |
| `created_at` | Recommendation creation time. |

`narrative` contains `template_id`, original `template`, resolved
`resolved_text`, `fact_bindings`, `evidence_links`, and
`unsupported_fragments`. Every fact binding contains optional `id`,
`placeholder`, raw `value`, backend-formatted `display_value`, optional `unit`,
optional `ledger_event_id`, and optional `evidence_item_id`. Unknown or unbound
numeric fragments do not become trusted output.

## Agent APIs

### `POST /api/v1/agent/query`

Creates a persistent run in `running` state and starts bounded in-process
execution. The orchestrator deterministically classifies the query, freezes
context, invokes typed measurement/procurement services, and records events and
telemetry. HTTP 202 means accepted, not completed.

Request body:

| Field | Required | Type and meaning |
| --- | --- | --- |
| `query` | Yes | User request, 3–4,000 characters. |
| `context.company_id` | Yes | Tenant UUID. |
| `context.actor_id` | Yes | Initiating actor UUID. |
| `context.site_id` | No | Site scope. |
| `context.reporting_period_id` | No | Period scope. |
| `context.carbon_measurement_id` | No | Existing verified measurement for procurement. |
| `context.current_product_id` | No | Baseline supplier-product. |
| `context.method_definition_id` | No | Explicit versioned method. |
| `context.metric_keys` | No | Up to 20 dotted canonical metric keys. |
| `context.material_scope` | No | Up to 20 material identifiers. |
| `context.supplier_product_ids` | No | Up to 100 product UUIDs. |
| `context.constraints.max_cost_increase_pct` | No | Decimal 0–100. |
| `context.constraints.max_lead_time_days` | No | Integer 0–3,650. |
| `context.constraints.minimum_circularity_score` | No | Decimal 0–100. |

Accepted response:

| Field | Meaning |
| --- | --- |
| `run_id` | UUID used by run and SSE endpoints. |
| `trace_id` | Correlation ID. |
| `terminal_state` | Literal `running`. |

### `GET /api/v1/agent/runs/{run_id}`

Retrieves the latest committed snapshot for path `run_id` within required query
`company_id`; optional `X-Trace-ID`. There is no body.

Response body:

| Field | Meaning |
| --- | --- |
| `run_id`, `trace_id` | Run and correlation identifiers. |
| `workflow` | `measurement`, `procurement`, `cross_module`, or `unsupported`. |
| `stage` | Current/final stage name. |
| `terminal_state` | `running`, `needs_clarification`, `no_data`, `validation_error`, `unsupported`, `no_feasible_option`, `failed_validation`, `budget_exhausted`, `approval_invalidated`, `completed`, or `failed`. |
| `context` | Frozen context envelope described below. |
| `plan` | Structured plan, or null before planning. |
| `facts` | Verified facts bound to ledger events. |
| `judgments` | Explicit non-factual workflow classification judgments. |
| `telemetry` | Budgets, usage, timings, and ordered events. |
| `approval_requirement` | Whether approval is required and relevant IDs/hash. |
| `recommendation` | Procurement recommendation fact summary, or null. |
| `message` | Safe terminal/current message, or null. |
| `missing_fields` | Fields needed for a clarification terminal state. |
| `unsupported_reason` | Reason a request is outside supported scope. |
| `error_code` | Safe failure code, or null. |
| `started_at`, `completed_at` | Run timing; completion is null while running. |

The frozen `context` contains company/site/period/measurement/current-product/
method identifiers, workflow, metric and material scopes, supplier product IDs,
actor ID/role, normalized constraints, optional resolved workflow values,
`request_hash`, and `analysis_signature`. Resolved values include measurement,
activity, product and optional method IDs, quantity/unit, optional unit cost, and
optional currency.

The `plan` contains constant version `orchestrator.v1`, execution mode
`deterministic_services`, workflow, up to six steps, a budget, and
`requires_human_approval`. Each step has `id`, `title`, `responsibility`, status
(`completed`, `planned`, `blocked`, `not_applicable`), and up to six tool IDs.
Budget fields are `max_model_calls` (≤3), `max_tool_calls` (≤6), `max_repairs`
(≤1), `max_context_turns` (≤6), `max_context_tokens` (≤4,000), and positive
`target_latency_ms`.

Each fact contains `fact_id`, canonical `metric_key`, `display_value`, and
`ledger_event_id`. Each judgment contains constant kind
`workflow_classification`, classified `value`, constant basis
`deterministic_keyword_rules`, and bounded `matched_terms`.

Telemetry contains `trace_id`, `analysis_signature`, orchestrator/provider/model
metadata, model/input/output/context token counts, tool/retry/repair counts,
rows processed, evidence chunks retrieved, total `elapsed_ms`, stage timings
(`stage`, `elapsed_ms`), and ordered event snapshots (`sequence`, `name`,
`occurred_at`, `data`).

An agent recommendation contains scenario/recommendation/product IDs, status,
the five impact values, `payload_hash`, `analysis_signature`, and
`ledger_event_id`. Approval requirement contains `required`, optional
`approval_id`, optional `recommendation_id`, and optional `preview_hash`.

### `GET /api/v1/agent/runs/{run_id}/events`

Returns `text/event-stream`. It replays committed events after optional integer
header `Last-Event-ID`, then follows fresh snapshots until the persisted run is
terminal. Required path `run_id` and query `company_id`; optional `X-Trace-ID`.

Each frame uses the standard SSE format:

```text
id: 4
event: fact.created
data: {"sequence":4,"name":"fact.created","occurred_at":"...","data":{...}}
```

Possible event names are `run.started`, `stage.started`, `stage.completed`,
`tool.started`, `tool.completed`, `validation.warning`, `fact.created`,
`approval.required`, `run.completed`, and `run.stopped`. Reconnect with the last
received sequence in `Last-Event-ID` to avoid depending on an in-memory buffer.

## Approval and audit APIs

### `GET /api/v1/approvals`

Retrieves approval queue/history for required `company_id`. Optional `status`
is `pending`, `approved`, or `rejected`; `limit` is 1–100 (default 50), and
`offset` defaults to 0. There is no body. Returns `{items, total, limit, offset}`.

Each item contains:

| Fields | Meaning |
| --- | --- |
| `id`, `company_id`, `recommendation_id`, `status` | Approval identity/scope/state. |
| `preview_hash`, `analysis_signature` | Exact reviewed payload and context hashes. |
| `expires_at`, `created_at` | Preview lifecycle times. |
| `requested_by`, `requester_name` | Requesting actor. |
| `decided_by`, `decider_name`, `decided_at`, `decision_note` | Nullable decision information. |
| `ledger_event_id` | Immutable decision event, or null. |
| `recommended_product_id`, `recommended_product_name`, `supplier_name` | Proposed choice. |
| `projected_footprint_kgco2e`, `avoided_kgco2e`, `reduction_pct`, `cost_delta_pct`, `lead_time_delta_days` | Reviewed impact. |
| `expired` | Whether the decision deadline passed. |
| `preview_current` | Whether hashes still match the live recommendation. |

### `POST /api/v1/approvals/{approval_id}/decision`

Approves or rejects only a pending, unexpired, tenant-owned approval whose
current preview matches the supplied hash. A successful decision appends ledger
and audit records; it never places an order. Repeating the same valid decision
is idempotent.

Request body:

| Field | Required | Meaning |
| --- | --- | --- |
| `company_id` | Yes | Tenant UUID. |
| `decision` | Yes | `approve` or `reject`. |
| `preview_hash` | Yes | 64-character lowercase SHA-256 shown during review. |
| `actor_id` | Yes | Deciding actor UUID. |
| `decision_note` | No | Optional explanation, maximum 2,000 characters. |

Response contains `approval_id`, `recommendation_id`, resulting `status`
(`approved`/`rejected`), `preview_hash`, `analysis_signature`, `decided_by`,
`decided_at`, optional `decision_note`, `ledger_event_id`, and boolean
`idempotent_replay`.

### `GET /api/v1/audit/{entity_type}/{entity_id}`

Retrieves a chronological combination of direct/connected audit and ledger
history for an entity. Required `entity_type` must match
`^[a-z][a-z0-9_]{0,99}$`; `entity_id` and query `company_id` are UUIDs; optional
`X-Trace-ID`. There is no body.

Response contains requested `entity_type`, `entity_id`, `company_id`, a
`timeline`, and `lineage`. Each timeline item contains `id`, `timestamp`, source
(`audit` or `ledger`), action, entity type/ID, optional actor/trace/payload hash,
structured `details`, and `direct` (whether the item directly targets the
requested entity). `lineage` contains event summaries, edge summaries, and a
`truncated` safety flag. Event summaries contain ID/type/entity/payload hash/time;
edge summaries contain ID, parent event ID, child event ID, relationship type,
and metadata.

## Electricity Maps integration APIs

These endpoints use a server-side credential. Provider credentials and raw
document bodies are not returned.

### `POST /api/v1/integrations/electricity-maps/test`

Tests authentication and reports bounded accessible-zone metadata. Optional
query `max_zones` is 1–100 (default 25); optional `X-Trace-ID`; no body.

Response contains constant `provider` (`electricity_maps`), `api_version`
(`v4`), `authenticated`, total `accessible_zone_count`, bounded `zones`, and
`zones_truncated`. Each zone contains `zone`, `zone_name`, optional
`country_code`, and `accessible_endpoints`.

### `POST /api/v1/sites/{site_id}/grid-intensity/sync`

Fetches historical grid-carbon-intensity points, stores a checksummed source
document/evidence snapshot, and inserts normalized emission factors while
counting already cached factors. Requires path `site_id`, query `company_id`,
and accepts optional `X-Trace-ID`.

The JSON body is optional:

| Field | Required | Meaning |
| --- | --- | --- |
| `zone` | No | Explicit provider zone; otherwise the service resolves one. |
| `start`, `end` | No | Explicit time range. |
| `lookback_hours` | No, default 24 | Past hours to request, 1–240. |
| `disable_estimations` | No, default false | Ask the provider to exclude estimated points. |

Response contains `site_id`, resolved `zone`, `zone_resolution` (`request`,
`cached`, `configured`, `country_exact`, or `country_unique`), requested time
range, counts `received_points`, `inserted_factors`, `existing_factors`,
`estimated_points`, provenance `data_source_id`/`source_document_id`, and
`response_checksum`.

### `GET /api/v1/sites/{site_id}/grid-intensity/latest`

Retrieves the latest cached grid-intensity factor for path `site_id` within
required query `company_id`; optional `X-Trace-ID`. It does not call the provider.

Response contains `site_id`, `factor_id`, provider `zone`, normalized decimal
`value` and `unit`, original `provider_value_gco2eq_per_kwh`, provider timestamp
and optional update timestamp, `is_estimated`, `emission_factor_type`,
`temporal_granularity`, and `provenance`. Provenance contains provider/API/cache
scope, endpoint, optional source site ID, source document/evidence IDs, response
checksum, and retrieval timestamp.

## Errors and status behavior

Most versioned service errors use this safe envelope:

| Field | Meaning |
| --- | --- |
| `detail.code` | Stable machine-readable error code. |
| `detail.message` | Sanitized human-readable message. |
| `detail.trace_id` | Correlation ID for logs/support. |
| `detail.retryable` | Whether retrying without changing input may succeed. |
| `detail.field_details` | Bounded field-specific details; never SQL or secrets. |

Measurement failures additionally return typed `detail.terminal_state`:
`completed`, `no_data`, `needs_clarification`, `unsupported`,
`validation_error`, or `failed_validation`. FastAPI request-shape validation
uses HTTP 422 and an array of errors containing `loc`, `msg`, `type`, and
optional sanitized input/context.

Common status meanings:

| Status | Meaning |
| ---: | --- |
| 200 | Successful read or synchronous command. |
| 201 | Resource/import/scenario created. |
| 202 | Agent run accepted and still executing. |
| 404 | Tenant-scoped resource or required context not found. |
| 409 | Current state conflicts with the command, for example stale approval/ambiguous measurement selection. |
| 422 | Invalid request or deterministic domain validation failure. |
| 500 | Sanitized unexpected calculation/service failure. |
| 503 | Database or configured external dependency unavailable. |

## Maintaining this document

When a route or Pydantic model changes, regenerate or inspect `/openapi.json`
and update this document in the same change. Database persistence fields are
documented in [database.md](../architecture/database.md).
