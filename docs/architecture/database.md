# CarbonMesh Database Architecture and Data Dictionary

Last updated: 2026-09-30

## Contract

CarbonMesh uses Neon PostgreSQL as its structured source of truth. SQLAlchemy 2
async ORM metadata is the authoritative definition for exactly 32 tables across
`core`, `carbon`, `ledger`, `semantic`, and `procurement`. Evidence embeddings
remain beside their evidence records through pgvector; there is no separate
vector database.

The application does not create schema objects during FastAPI startup. From
`apps/api`, operators explicitly run:

```powershell
python -m app.db.bootstrap
python -m app.db.bootstrap --check
```

Bootstrap acquires a transaction-scoped advisory lock, enables `vector` in
`public`, confirms HNSW support, creates the five application schemas, runs
SQLAlchemy `create_all()`, installs the views and ledger trigger, and verifies
the complete contract. It accepts either a pristine database or an already
complete compatible CarbonMesh database. A partial, extra, or incompatible
CarbonMesh structure fails without being dropped or repaired.

`--check` is read-only. It verifies all five schemas, all 32 tables, named
constraints and indexes, the three views, pgvector, `VECTOR(768)`, the HNSW
contract, and the ledger function and trigger.

The tables are schema-qualified and intentionally do not appear under `public`.
In Neon's Tables page, select the target branch/database and choose each of the
five application schemas from the Schema menu. A blank `public` schema does not
mean bootstrap failed. The SQL Editor can list the installed contract with:

```sql
SELECT table_schema, table_name
FROM information_schema.tables
WHERE table_type = 'BASE TABLE'
  AND table_schema IN ('core', 'carbon', 'ledger', 'semantic', 'procurement')
ORDER BY table_schema, table_name;
```

This document describes the implemented contract and local validation. It does
not claim that the bootstrap has already been run against a live Neon branch;
the operator must supply a rotated URL and run both commands there.

The opt-in live suite is `tests/integration/test_neon_database.py`. It runs only
when `CARBONMESH_RUN_NEON_INTEGRATION_TESTS=rotated-disposable-branch` and the
configured host is under `*.neon.tech`. It verifies repeated bootstrap, vectors,
tenant and numeric constraints, approvals, partial uniqueness, and ledger
immutability without dropping or resetting schemas.

## Connection and lifecycle

- `DATABASE_URL` is read from ignored `apps/api/.env` as a Pydantic `SecretStr`.
  Use a rotated pooled Neon URL and never place a real credential in source,
  logs, screenshots, prompts, or frontend configuration.
- The URL parser accepts PostgreSQL URLs, replaces the driver with
  `postgresql+asyncpg`, strips `sslmode` and `channel_binding`, and supplies TLS
  through asyncpg connection arguments. The input must use `sslmode=require`;
  other modes are rejected instead of being silently weakened. Non-PostgreSQL
  or incomplete URLs also fail with credential-free messages.
- The lazy process-wide engine uses `NullPool` for the Neon PgBouncer endpoint,
  disables prepared-statement caches, and applies connection and command
  timeouts. FastAPI disposes the engine during lifespan shutdown.
- `AsyncSession` scopes never commit implicitly. They roll back exceptions and
  always close. The service that owns a transaction must call `commit()`.
- `GET /api/db/demo` performs an async `SELECT CURRENT_TIMESTAMP`. Failures
  return a fixed `503` detail and never expose the URL, host, user, password, or
  driver exception.

## SQL and ORM conventions

- Entity primary keys are PostgreSQL `UUID` values with
  `DEFAULT gen_random_uuid()`. Unless a table says otherwise, entity tables also
  have `created_at TIMESTAMPTZ NOT NULL DEFAULT now()` and
  `updated_at TIMESTAMPTZ NOT NULL DEFAULT now()`.
- Append-oriented/event tables use `created_at` without `updated_at`. The
  `ledger.ledger_event_evidence` join table uses its two foreign keys as a
  composite primary key.
- Carbon, quantity, money, confidence, percentage, and score fields use
  fixed-precision `NUMERIC` values and map to Python `Decimal`, never `float`.
- All foreign keys use `ON DELETE RESTRICT`; ORM relationships do not perform
  delete cascades. Tenant-owned records carry `company_id`, with composite
  foreign keys used where a parent could otherwise cross company boundaries.
- Every company-owned UUID entity/event table has unique `(company_id, id)` so
  those tenant-safe composite foreign keys can target it. The composite-key
  `ledger_event_evidence` table is the sole exception.
- Domain states use checked `VARCHAR` columns rather than PostgreSQL enum types.
  JSONB is reserved for raw input, frozen context/configuration, constraints,
  telemetry, snapshots, and similar variable-shaped payloads.
- Python attributes that map a database column named `metadata` use a safe name
  such as `document_metadata`, `evidence_metadata`, or `entity_metadata` because
  `metadata` is reserved by SQLAlchemy declarative models.

## Table catalogue

The type declarations below are the database-level contract. `NULL` is shown
explicitly; other listed columns are `NOT NULL`. `Entity columns` means the
standard UUID primary key and both timestamps described above. `Event columns`
means the standard UUID primary key and `created_at` only.

### `core` - context, sources, evidence, and audit (8)

- `companies` - Entity columns; `code VARCHAR(50)`; `name VARCHAR(200)`;
  `is_synthetic BOOLEAN DEFAULT false`; `is_active BOOLEAN DEFAULT true`.
- `sites` - Entity columns; `company_id UUID`; `code VARCHAR(50)`;
  `name VARCHAR(200)`; `country_code VARCHAR(2)`; `timezone VARCHAR(64) DEFAULT
  'UTC'`; `is_active BOOLEAN DEFAULT true`.
- `reporting_periods` - Entity columns; `company_id UUID`; `name VARCHAR(100)`;
  `start_date DATE`; `end_date DATE`; `status VARCHAR(20) DEFAULT 'open'`.
- `actors` - Entity columns; `company_id UUID`; `email VARCHAR(320)`;
  `display_name VARCHAR(200)`; `role VARCHAR(40)`; `is_active BOOLEAN DEFAULT
  true`.
- `data_sources` - Entity columns; `company_id UUID`; `site_id UUID NULL`;
  `name VARCHAR(200)`; `source_type VARCHAR(20)`; `status VARCHAR(20) DEFAULT
  'pending'`; `external_reference VARCHAR(255) NULL`; `configuration JSONB
  DEFAULT {}`; `is_synthetic BOOLEAN DEFAULT false`.
- `source_documents` - Entity columns; `company_id UUID`; `data_source_id UUID`;
  `filename VARCHAR(255)`; `content_type VARCHAR(100)`; `checksum VARCHAR(64)`;
  `version INTEGER DEFAULT 1`; `size_bytes BIGINT NULL`; `storage_uri
  VARCHAR(1000) NULL`; `metadata JSONB DEFAULT {}`; `imported_at TIMESTAMPTZ
  DEFAULT CURRENT_TIMESTAMP`.
- `evidence_items` - Entity columns; `company_id UUID`; `source_document_id
  UUID`; `evidence_type VARCHAR(40)`; `locator VARCHAR(500)`; `content_text
  TEXT`; `checksum VARCHAR(64)`; `metadata JSONB DEFAULT {}`; `embedding
  VECTOR(768) NULL`; `embedding_model VARCHAR(100) NULL`; `embedded_at
  TIMESTAMPTZ NULL`.
- `audit_log` - Event columns; `company_id UUID`; `actor_id UUID NULL`;
  `agent_run_id UUID NULL`; `action VARCHAR(100)`; `entity_type VARCHAR(100)`;
  `entity_id UUID`; `trace_id VARCHAR(100) NULL`; `details JSONB DEFAULT {}`.

### `semantic` - canonical entities and versioned methods (4)

- `semantic_entities` - Entity columns; `company_id UUID`; `entity_type
  VARCHAR(30)`; `key VARCHAR(150)`; `label VARCHAR(255)`; `description TEXT
  NULL`; `metadata JSONB DEFAULT {}`; `is_active BOOLEAN DEFAULT true`.
- `semantic_aliases` - Entity columns; `company_id UUID`;
  `semantic_entity_id UUID`; `alias VARCHAR(255)`; `locale VARCHAR(20) DEFAULT
  'en'`; `is_active BOOLEAN DEFAULT true`.
- `metric_definitions` - Entity columns; `company_id UUID`;
  `semantic_entity_id UUID NULL`; `key VARCHAR(150)`; `version VARCHAR(50)`;
  `name VARCHAR(255)`; `canonical_unit VARCHAR(50)`; `dimensions JSONB DEFAULT
  {}`; `handler VARCHAR(150)`; `method_version VARCHAR(50)`; `description TEXT
  NULL`; `is_active BOOLEAN DEFAULT true`.
- `method_definitions` - Entity columns; `company_id UUID`; `method_type
  VARCHAR(40)`; `key VARCHAR(150)`; `version VARCHAR(50)`; `name VARCHAR(255)`;
  `code_version VARCHAR(100)`; `configuration JSONB`; `effective_from DATE`;
  `effective_to DATE NULL`; `is_active BOOLEAN DEFAULT true`.

### `carbon` - imported activity, calculation, measurement, and runs (10)

- `raw_activity_records` - Event columns; `company_id UUID`; `data_source_id
  UUID`; `source_document_id UUID`; `row_key VARCHAR(255)`; `row_number INTEGER
  NULL`; `raw_payload JSONB`; `checksum VARCHAR(64)`; `import_status VARCHAR(20)
  DEFAULT 'pending'`.
- `activity_records` - Entity columns; `company_id UUID`;
  `raw_activity_record_id UUID`; `site_id UUID`; `reporting_period_id UUID`;
  `metric_definition_id UUID`; `supplier_product_id UUID NULL`; `material_code
  VARCHAR(100)`; `activity_date DATE NULL`; `quantity NUMERIC(24,6)`; `unit
  VARCHAR(50)`; `normalized_quantity NUMERIC(24,6)`; `normalized_unit
  VARCHAR(50)`; `unit_cost NUMERIC(20,6) NULL`; `currency VARCHAR(3) NULL`;
  `status VARCHAR(20) DEFAULT 'valid'`.
- `emission_factors` - Entity columns; `company_id UUID`; `metric_definition_id
  UUID`; `evidence_item_id UUID`; `factor_code VARCHAR(100)`; `version
  VARCHAR(50)`; `name VARCHAR(255)`; `material_code VARCHAR(100) NULL`;
  `product_code VARCHAR(100) NULL`; `geography VARCHAR(100) DEFAULT 'GLOBAL'`;
  `factor_value NUMERIC(24,12)`; `numerator_unit VARCHAR(50) DEFAULT 'kgCO2e'`;
  `denominator_unit VARCHAR(50)`; `effective_from DATE`; `effective_to DATE
  NULL`; `source_quality NUMERIC(6,5)`; `factor_specificity NUMERIC(6,5)`;
  `factor_recency NUMERIC(6,5)`; `status VARCHAR(20) DEFAULT 'active'`.
- `calculation_runs` - Event columns; `company_id UUID`; `agent_run_id UUID
  NULL`; `reporting_period_id UUID`; `method_definition_id UUID`;
  `method_version VARCHAR(50)`; `code_version VARCHAR(100)`; `rounding_policy
  VARCHAR(100)`; `input_hash VARCHAR(64)`; `output_hash VARCHAR(64) NULL`;
  `status VARCHAR(20) DEFAULT 'pending'`; `started_at TIMESTAMPTZ NULL`;
  `completed_at TIMESTAMPTZ NULL`; `summary JSONB DEFAULT {}`; `error_code
  VARCHAR(100) NULL`.
- `emission_calculations` - Event columns; `company_id UUID`;
  `calculation_run_id UUID`; `activity_record_id UUID`; `emission_factor_id
  UUID`; `normalized_quantity NUMERIC(24,6)`; `quantity_unit VARCHAR(50)`;
  `factor_value NUMERIC(24,12)`; `factor_unit VARCHAR(100)`;
  `emissions_kgco2e NUMERIC(24,6)`; `formula TEXT`; `output_hash VARCHAR(64)`.
- `carbon_measurements` - Event columns; `company_id UUID`; `calculation_run_id
  UUID`; `site_id UUID`; `reporting_period_id UUID`; `metric_definition_id
  UUID`; `ledger_event_id UUID NULL`; `value_kgco2e NUMERIC(24,6)`; `unit
  VARCHAR(50) DEFAULT 'kgCO2e'`; `confidence NUMERIC(6,5)`; `status VARCHAR(20)
  DEFAULT 'draft'`; `formula TEXT`; `output_hash VARCHAR(64)`; `verified_at
  TIMESTAMPTZ NULL`.
- `data_quality_issues` - Entity columns; `company_id UUID`;
  `raw_activity_record_id UUID NULL`; `activity_record_id UUID NULL`;
  `issue_type VARCHAR(100)`; `code VARCHAR(100)`; `severity VARCHAR(20)`;
  `field_name VARCHAR(100) NULL`; `message TEXT`; `status VARCHAR(20) DEFAULT
  'open'`; `details JSONB DEFAULT {}`; `resolved_at TIMESTAMPTZ NULL`.
- `carbon_baselines` - Event columns; `company_id UUID`; `site_id UUID`;
  `reporting_period_id UUID`; `metric_definition_id UUID`;
  `source_measurement_id UUID NULL`; `name VARCHAR(200)`; `value_kgco2e
  NUMERIC(24,6)`; `unit VARCHAR(50) DEFAULT 'kgCO2e'`; `frozen_hash
  VARCHAR(64)`.
- `variance_alerts` - Entity columns; `company_id UUID`;
  `carbon_measurement_id UUID`; `carbon_baseline_id UUID`; `variance_kgco2e
  NUMERIC(24,6)`; `variance_pct NUMERIC(9,4) NULL`; `severity VARCHAR(20)`;
  `status VARCHAR(20) DEFAULT 'open'`.
- `agent_runs` - Event columns; `company_id UUID`; `actor_id UUID`;
  `parent_run_id UUID NULL`; `trace_id VARCHAR(100)`; `workflow VARCHAR(50)`;
  `stage VARCHAR(100)`; `terminal_state VARCHAR(40) DEFAULT 'running'`;
  `context_envelope JSONB`; `plan JSONB NULL`; `result JSONB NULL`; `telemetry
  JSONB DEFAULT {}`; `model_calls INTEGER DEFAULT 0`; `tool_calls INTEGER DEFAULT
  0`; `retry_count INTEGER DEFAULT 0`; `started_at TIMESTAMPTZ DEFAULT
  CURRENT_TIMESTAMP`; `completed_at TIMESTAMPTZ NULL`; `error_code VARCHAR(100)
  NULL`.

### `ledger` - immutable facts, lineage, and evidence links (3)

- `ledger_events` - Event columns; `company_id UUID`; `event_type VARCHAR(100)`;
  `entity_type VARCHAR(100)`; `entity_id UUID`; `payload JSONB`; `payload_hash
  VARCHAR(64)`; `analysis_signature VARCHAR(64) NULL`; `created_by UUID NULL`;
  `supersedes_event_id UUID NULL`.
- `lineage_edges` - Event columns; `company_id UUID`; `parent_event_id UUID`;
  `child_event_id UUID`; `relationship_type VARCHAR(100)`; `metadata JSONB
  DEFAULT {}`.
- `ledger_event_evidence` - `company_id UUID`; `ledger_event_id UUID`;
  `evidence_item_id UUID`; `relevance VARCHAR(255) NULL`; `created_at TIMESTAMPTZ
  DEFAULT now()`; primary key `(ledger_event_id, evidence_item_id)`.

### `procurement` - products, scoring, recommendations, and approvals (7)

- `suppliers` - Entity columns; `company_id UUID`; `supplier_code VARCHAR(100)`;
  `name VARCHAR(255)`; `country_code VARCHAR(2)`; `status VARCHAR(20) DEFAULT
  'active'`; `metadata JSONB DEFAULT {}`.
- `supplier_products` - Entity columns; `company_id UUID`; `supplier_id UUID`;
  `evidence_item_id UUID NULL`; `product_code VARCHAR(100)`; `name VARCHAR(255)`;
  `material_code VARCHAR(100)`; `category VARCHAR(100)`; `description TEXT
  NULL`; `pcf_kgco2e_per_unit NUMERIC(24,12)`; `pcf_unit VARCHAR(100) DEFAULT
  'kgCO2e/kg'`; `circularity_score NUMERIC(7,4)`; `recycled_content_pct
  NUMERIC(7,4)`; `recyclable_pct NUMERIC(7,4)`; `evidence_quality_score
  NUMERIC(7,4)`; `lead_time_days INTEGER`; `unit_cost NUMERIC(20,6)`; `currency
  VARCHAR(3)`; `effective_from DATE`; `effective_to DATE NULL`; `is_active
  BOOLEAN DEFAULT true`.
- `procurement_scenarios` - Entity columns; `company_id UUID`; `site_id UUID`;
  `reporting_period_id UUID`; `current_product_id UUID`; `carbon_measurement_id
  UUID`; `agent_run_id UUID NULL`; `method_definition_id UUID`; `quantity
  NUMERIC(24,6)`; `quantity_unit VARCHAR(50)`; `current_unit_cost NUMERIC(20,6)`;
  `currency VARCHAR(3)`; `max_cost_increase_pct NUMERIC(7,4)`;
  `max_lead_time_days INTEGER`; `minimum_circularity_score NUMERIC(7,4)`;
  `material_constraints JSONB DEFAULT {}`; `carbon_weight NUMERIC(5,4) DEFAULT
  0.4000`; `evidence_weight NUMERIC(5,4) DEFAULT 0.2500`; `circularity_weight
  NUMERIC(5,4) DEFAULT 0.2000`; `operational_fit_weight NUMERIC(5,4) DEFAULT
  0.1500`; `analysis_signature VARCHAR(64)`; `frozen_context JSONB`; `status
  VARCHAR(20) DEFAULT 'draft'`.
- `supplier_scores` - Event columns; `company_id UUID`; `scenario_id UUID`;
  `supplier_product_id UUID`; `method_definition_id UUID`; `agent_run_id UUID
  NULL`; `ledger_event_id UUID NULL`; `carbon_score NUMERIC(7,4)`;
  `evidence_score NUMERIC(7,4)`; `circularity_score NUMERIC(7,4)`;
  `operational_fit_score NUMERIC(7,4)`; `total_score NUMERIC(7,4)`; `rank INTEGER
  NULL`; `feasible BOOLEAN`; `infeasibility_reasons JSONB DEFAULT []`.
- `recommendations` - Event columns; `company_id UUID`; `scenario_id UUID`;
  `recommended_product_id UUID`; `baseline_product_id UUID`; `supplier_score_id
  UUID`; `ledger_event_id UUID NULL`; `status VARCHAR(30) DEFAULT
  'pending_approval'`; `projected_footprint_kgco2e NUMERIC(24,6)`;
  `avoided_kgco2e NUMERIC(24,6)`; `reduction_pct NUMERIC(7,4)`; `cost_delta_pct
  NUMERIC(9,4)`; `lead_time_delta_days INTEGER`; `rationale_template TEXT`;
  `analysis_signature VARCHAR(64)`; `payload_hash VARCHAR(64)`; `impact_snapshot
  JSONB`; `invalidated_at TIMESTAMPTZ NULL`.
- `fact_bindings` - Event columns; `company_id UUID`; `recommendation_id UUID
  NULL`; `agent_run_id UUID`; `ledger_event_id UUID`; `evidence_item_id UUID
  NULL`; `placeholder VARCHAR(150)`; `value_snapshot JSONB`; `display_value
  VARCHAR(255)`; `unit VARCHAR(50) NULL`.
- `approvals` - Event columns; `company_id UUID`; `recommendation_id UUID`;
  `requested_by UUID`; `decided_by UUID NULL`; `ledger_event_id UUID NULL`;
  `status VARCHAR(20) DEFAULT 'pending'`; `preview_hash VARCHAR(64)`;
  `analysis_signature VARCHAR(64)`; `idempotency_key VARCHAR(255)`; `expires_at
  TIMESTAMPTZ`; `decided_at TIMESTAMPTZ NULL`; `decision_note TEXT NULL`.

## Relationships and integrity

All relationships in this section are restrictive. A notation such as
`(company_id, site_id) -> core.sites(company_id, id)` denotes a composite
foreign key that enforces tenant alignment.

### Core and semantic relationships

- `core.sites.company_id`, `core.reporting_periods.company_id`, and
  `core.actors.company_id` reference `core.companies.id`.
- `core.data_sources.company_id` references the company; `(company_id, site_id)`
  references `core.sites` when a site is present.
- `(company_id, data_source_id)` on `core.source_documents` references
  `core.data_sources`; `(company_id, source_document_id)` on
  `core.evidence_items` references `core.source_documents`.
- `core.audit_log` belongs to a company and optionally links through composite
  keys to `core.actors` and `carbon.agent_runs`; `entity_id` is deliberately a
  service-validated polymorphic UUID.
- Every semantic table belongs to a company. `semantic_aliases` and optional
  `metric_definitions.semantic_entity_id` use composite references to
  `semantic.semantic_entities`.

### Carbon relationships

- Raw activity rows link to both `core.data_sources` and
  `core.source_documents`. Normalized activity links to its raw row, site,
  period, metric, and optionally a procurement product.
- Emission factors link to a metric definition and evidence item. Calculation
  runs link to a reporting period, method definition, and optional agent run.
  Each emission calculation links one run, normalized activity row, and factor.
- Carbon measurements link to their calculation run, site, period, metric, and
  optional ledger event. Baselines link the same context to an optional source
  measurement; variance alerts link one measurement/baseline pair.
- Data-quality issues may link to a raw row, normalized row, or both. Agent runs
  link to their actor and may link to a parent run in the same company.

### Ledger relationships

- Ledger events belong to a company, optionally link to the creating actor, and
  may supersede another event in the same company. `entity_id` remains a
  service-validated polymorphic UUID.
- Both ends of a lineage edge reference company-aligned ledger events.
- `ledger_event_evidence` links a company-aligned ledger event to a
  company-aligned evidence item.

### Procurement relationships

- Suppliers belong to a company. Supplier products link to a company-aligned
  supplier and optional evidence item.
- Procurement scenarios link to a site, reporting period, current supplier
  product, verified carbon measurement, scoring method, and optional agent run,
  all through company-aligned composite foreign keys.
- Supplier scores link one scenario, candidate product, and method; optional
  agent-run and ledger-event links preserve orchestration and audit context.
- Recommendations link one scenario, recommended product, baseline product, and
  supplier score, plus an optional ledger event. Fact bindings optionally link
  the recommendation and evidence item and always link an agent run and ledger
  event.
- Approvals link the exact recommendation, requester, optional deciding actor,
  and the ledger event created for a completed decision. Their composite
  recommendation foreign key also requires `preview_hash` to match the
  recommendation payload hash and requires the same analysis signature.

### Core and semantic uniqueness, checks, and indexes

- `core.companies`: unique `code`.
- `core.sites`: unique `(company_id, id)` and `(company_id, code)`; ISO alpha-2
  uppercase country check; index `(company_id, is_active)`.
- `core.reporting_periods`: unique `(company_id, id)` and
  `(company_id, start_date, end_date)`; `end_date >= start_date`; status is
  `open`, `closed`, or `locked`; index `(company_id, status)`.
- `core.actors`: unique `(company_id, id)` and `(company_id, email)`; role is
  `sustainability_analyst`, `procurement_manager`, `approver`, `auditor`, or
  `system`.
- `core.data_sources`: unique `(company_id, id)` and `(company_id, name)`;
  source type is `csv`, `json`, `pdf`, `api`, or `synthetic`; status is
  `pending`, `ready`, `failed`, or `archived`; index `(company_id, site_id)`.
- `core.source_documents`: unique `(company_id, id)` and
  `(company_id, checksum)`; checksum is lowercase SHA-256; version is positive;
  size is null or non-negative; index `(company_id, data_source_id)`.
- `core.evidence_items`: unique `(company_id, id)` and
  `(company_id, source_document_id, locator)`; lowercase SHA-256 checksum; all-or-
  none embedding metadata check; B-tree metadata and partial cosine HNSW indexes.
- `core.audit_log`: unique `(company_id, id)`; indexes
  `(company_id, entity_type, entity_id)` and `(company_id, trace_id)`.
- `semantic.semantic_entities`: unique `(company_id, id)` and
  `(company_id, entity_type, key)`; type is `standard`, `material`, `unit`,
  `supplier`, `product`, or `metric`; index `(company_id, entity_type,
  is_active)`.
- `semantic.semantic_aliases`: unique `(company_id, id)` and
  `(company_id, alias, locale)`; index `(company_id, semantic_entity_id)`.
- `semantic.metric_definitions`: unique `(company_id, id)` and
  `(company_id, key, version)`; non-empty version; index `(company_id,
  is_active)`.
- `semantic.method_definitions`: unique `(company_id, id)` and
  `(company_id, method_type, key, version)`; type is `measurement`,
  `confidence`, `supplier_scoring`, or `fact_binding`; effective end is null or
  not before effective start; index `(company_id, method_type, is_active)`.

### Carbon uniqueness, checks, and indexes

- `raw_activity_records`: unique `(company_id, id)` and
  `(company_id, data_source_id, row_key)`; positive optional row number;
  lowercase SHA-256 checksum; import status is `pending`, `accepted`, or
  `rejected`; index `(company_id, source_document_id)`.
- `activity_records`: unique `(company_id, id)` and
  `(company_id, raw_activity_record_id)`; quantity and normalized quantity are
  non-negative; optional unit cost is non-negative and optional currency is an
  uppercase ISO 4217 code; status is `valid`, `invalid`, or `superseded`; index
  `(company_id, site_id, reporting_period_id, material_code)`.
- `emission_factors`: unique `(company_id, id)` and
  `(company_id, factor_code, version, geography)`; non-negative factor; valid
  effective dates; source quality, specificity, and recency are each `0..1`;
  status is `active`, `inactive`, or `superseded`; lookup index over company,
  metric, material, geography, and status.
- `calculation_runs`: unique `(company_id, id)` and `(company_id, input_hash)`;
  input and optional output hashes are lowercase SHA-256; status is `pending`,
  `running`, `completed`, or `failed`; index `(company_id,
  reporting_period_id, status)`.
- `emission_calculations`: unique `(company_id, id)` and `(company_id,
  calculation_run_id, activity_record_id)`; quantities, factors, and emissions
  are non-negative; output hash is lowercase SHA-256; index `(company_id,
  calculation_run_id)`.
- `carbon_measurements`: unique `(company_id, id)` and `(company_id,
  output_hash)`; non-negative value, confidence `0..1`, lowercase SHA-256 output
  hash; status is `draft`, `verified`, `superseded`, or `unsupported`; index
  `(company_id, site_id, reporting_period_id, status)`.
- `data_quality_issues`: severity is `info`, `warning`, or `error`; status is
  `open`, `resolved`, or `waived`; index `(company_id, status, severity)`.
- `carbon_baselines`: unique `(company_id, id)` and `(company_id, site_id,
  reporting_period_id, metric_definition_id)`; non-negative value and lowercase
  SHA-256 frozen hash.
- `variance_alerts`: unique `(company_id, id)` and `(company_id,
  carbon_measurement_id, carbon_baseline_id)`; severity is `info`, `warning`, or
  `critical`; status is `open`, `acknowledged`, or `closed`; index `(company_id,
  status, severity)`.
- `agent_runs`: unique `(company_id, id)` and `(company_id, trace_id)`; terminal
  state is one of the bounded workflow states; budgets enforce `0..3` model
  calls, `0..6` tool calls, and `0..1` repair; indexes on `trace_id` and
  `(company_id, terminal_state)`.

### Ledger uniqueness, checks, and indexes

- `ledger_events`: unique `(company_id, id)`; cannot supersede itself; payload
  hash and optional analysis signature are lowercase SHA-256; indexes
  `(company_id, entity_type, entity_id)` and `(company_id, event_type,
  created_at)` in addition to the immutability trigger.
- `lineage_edges`: unique `(company_id, id)` and `(company_id, parent_event_id,
  child_event_id, relationship_type)`; parent and child must differ; separate
  company/parent and company/child indexes.
- `ledger_event_evidence`: composite primary key `(ledger_event_id,
  evidence_item_id)`; index `(company_id, evidence_item_id)`.

### Procurement uniqueness, checks, and indexes

- `suppliers`: unique `(company_id, id)` and `(company_id, supplier_code)`;
  country is uppercase ISO alpha-2; status is `active` or `inactive`; index
  `(company_id, status)`.
- `supplier_products`: unique `(company_id, id)` and `(company_id, supplier_id,
  product_code)`; product carbon footprint, lead time, and cost are
  non-negative; circularity, recycled content, recyclable content, and evidence
  quality are `0..100`; currency is uppercase ISO 4217; effective end is null or
  not before effective start; index `(company_id, material_code, category,
  is_active)`.
- `procurement_scenarios`: unique `(company_id, id)` and `(company_id,
  analysis_signature)`; quantity and current unit cost are positive; maximum
  cost increase is `0..100`; maximum lead time is non-negative; currency is
  uppercase ISO 4217; minimum circularity is `0..100`; all weights are
  non-negative and sum to exactly `1`; analysis signature is lowercase SHA-256; status is `draft`,
  `assessed`, `recommended`, or `closed`; context index `(company_id, site_id,
  reporting_period_id, status)`. Cost remains a hard feasibility constraint and
  is not a weighted score.
- `supplier_scores`: unique `(company_id, id)`, `(company_id, scenario_id,
  supplier_product_id, method_definition_id)`, and `(company_id, scenario_id,
  rank)`; all component and total scores are `0..100`; optional rank is positive;
  index `(company_id, scenario_id, feasible)`.
- `recommendations`: unique `(company_id, id)`, `(company_id, payload_hash)`,
  and the approval-binding tuple `(company_id, id, payload_hash,
  analysis_signature)`;
  projected footprint and avoided emissions are non-negative; reduction is
  `0..100`; signature and payload hash are lowercase SHA-256; status is
  `pending_approval`, `approved`, `rejected`, or `invalidated`; status index plus
  a partial unique index on `(company_id, scenario_id)` where the recommendation
  is not invalidated and is pending or approved.
- `fact_bindings`: unique `(company_id, id)` and `(company_id,
  recommendation_id, placeholder)`; placeholders match
  `^fact_[a-z0-9_]+$`; index `(company_id, agent_run_id)`.
- `approvals`: unique `(company_id, id)` and `(company_id, idempotency_key)`;
  preview hash and analysis signature are lowercase SHA-256; status is
  `pending`, `approved`, or `rejected`; pending records have no decision actor,
  decision time, or ledger event, while decided records require all three;
  the composite recommendation foreign key enforces that the reviewed hash and
  analysis signature match the exact recommendation;
  status index plus a partial unique index on `(company_id, recommendation_id)`
  where status is `pending`.

## pgvector retrieval contract

`core.evidence_items.embedding` uses the full single-precision
`VECTOR(768)` type. The three embedding fields must be all absent or all present:
`embedding`, `embedding_model`, and `embedded_at`. The exact model identifier is
stored with each vector, and `checksum` binds the record to immutable evidence
content.

Nearest-neighbour retrieval uses cosine distance and this partial index:

```sql
CREATE INDEX ix_core_evidence_items_embedding_cosine_hnsw
ON core.evidence_items
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64)
WHERE embedding IS NOT NULL;
```

`ix_core_evidence_company_document_type` is a B-tree index over
`(company_id, source_document_id, evidence_type)`. Retrieval must apply company
and evidence metadata filters and explicit row limits in addition to vector
distance. Embedding generation is outside the database foundation; callers must
supply exactly 768 values and the configured model identifier.

## Views and immutable ledger

- `carbon.v_measurement_summary` currently projects all columns from
  `carbon.carbon_measurements`.
- `procurement.v_supplier_comparison` currently projects all columns from
  `procurement.supplier_scores`.
- `procurement.v_pending_approvals` projects rows from
  `procurement.approvals` where `status = 'pending'`.
- `ledger.prevent_ledger_event_mutation()` is a PL/pgSQL trigger function that
  raises SQLSTATE `55000`. `trg_ledger_events_immutable` invokes it before every
  update or delete on `ledger.ledger_events`; corrections therefore require a
  new event and a supersession relationship.

## Evolution limitation

SQLAlchemy `create_all()` only creates missing objects; it does not compare or
alter existing columns, constraints, or indexes. For this POC, make structural
changes on a new disposable Neon branch and bootstrap it from scratch. Before
any persistent environment requires in-place upgrades or retained production
data, deliberately reintroduce a migration system and create reviewed forward
and rollback procedures. The bootstrap command is verification and initial
creation tooling, not a migration engine or destructive reset command.
