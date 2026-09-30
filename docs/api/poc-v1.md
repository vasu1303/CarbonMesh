# CarbonMesh POC API v1

The FastAPI OpenAPI document at `/docs` is the authoritative field-level
contract. All tenant-owned reads require an explicit `company_id`, and command
payloads carry company and actor context. This explicit POC scoping does not
replace production authentication or authorization.

## Implemented routes

| Method | Route | Behavior |
| --- | --- | --- |
| GET | `/api/v1/measurements/{measurement_id}/lineage` | Bounded ledger/evidence node-edge graph. |
| GET | `/api/v1/suppliers` | Filtered, paginated supplier-product facts. |
| GET | `/api/v1/suppliers/{product_id}` | Product, supplier, and evidence detail. |
| POST | `/api/v1/procurement/assessments/run` | Idempotent deterministic assessment of a frozen scenario. |
| POST | `/api/v1/procurement/scenarios` | Freeze context, enforce hard constraints, score alternatives, and create a hash-bound recommendation and approval preview. |
| GET | `/api/v1/procurement/scenarios/{scenario_id}` | Frozen comparison matrix and selected recommendation. |
| GET | `/api/v1/procurement/recommendations/{recommendation_id}` | Reviewed facts, evidence, scores, narrative bindings, hashes, and status. |
| POST | `/api/v1/agent/query` | Commit a bounded run in `running` state, return its ID, and execute it asynchronously in process. |
| GET | `/api/v1/agent/runs/{run_id}` | Frozen context, plan, facts, judgments, terminal state, and telemetry. |
| GET | `/api/v1/agent/runs/{run_id}/events` | Replay committed events, follow new snapshots until terminal state, and resume with `Last-Event-ID`. |
| GET | `/api/v1/approvals` | Paginated pending or decided approvals. |
| POST | `/api/v1/approvals/{approval_id}/decision` | Approve/reject only the tenant-scoped, still-current preview hash; the body requires `company_id`, and repeat submissions are idempotent. |
| GET | `/api/v1/audit/{entity_type}/{entity_id}` | Chronological audit and bounded connected ledger lineage. |
| POST | `/api/v1/integrations/electricity-maps/test` | Validate the server-side V4 credential and list bounded accessible metadata. |
| POST | `/api/v1/sites/{site_id}/grid-intensity/sync` | Fetch at most 240 past hours, snapshot evidence, and cache normalized factors. |
| GET | `/api/v1/sites/{site_id}/grid-intensity/latest` | Latest company-zone cached factor with provider provenance. |

The legacy `/api/health` route remains available, with the versioned alias at
`/api/v1/health`.

## Determinism and safety

- Carbon, money, percentages, and scores use `Decimal`; no model produces a
  calculation, feasibility decision, or score.
- Cost, lead time, material compatibility, circularity, evidence, effective
  period, and PCF unit checks are hard constraints.
- Recommendation payloads and approval previews use canonical SHA-256 hashes.
- Approval decisions append a new immutable ledger event and audit record; they
  never create a purchase order.
- Unknown constraint fields are rejected. Ambiguous or incomplete agent context
  ends in a typed terminal state instead of guessed facts.
- Versioned route failures use one sanitized `detail` envelope containing
  `code`, `message`, `trace_id`, `retryable`, and `field_details`; database and
  unexpected failures never expose SQL, credentials, or implementation details.
- Agent execution uses a tracked in-process task and an independent database
  session. It is suitable for this single-process POC, not a durable distributed
  queue; a process crash can leave a run in `running` state.
- Electricity Maps credentials remain server-side. Tests replace the provider
  with a deterministic fake and make no external request.

## SSE events

Runs persist these named events as applicable:

`run.started`, `stage.started`, `stage.completed`, `tool.started`,
`tool.completed`, `validation.warning`, `fact.created`, `approval.required`,
`run.completed`, and `run.stopped`.

The SSE endpoint first replays events after the optional `Last-Event-ID`, then
polls fresh committed snapshots without holding a database session open. It
closes only after the persisted run becomes terminal, so clients can reconnect
without relying on an in-memory event buffer.

## Synthetic E2E journey

The isolated E2E test seed is visibly synthetic and reproduces the agreed demo
result. The application does not seed or reset production/demo databases during
startup or while serving these APIs; POST assertions use only prerequisite rows
created inside the disposable test database and verify persisted outputs through
the corresponding GET endpoints.

| Fact | Expected result |
| --- | ---: |
| Quantity | 12,000 kg |
| Verified baseline | 33,600 kgCO2e |
| Recommended projected footprint | 22,800 kgCO2e |
| Avoided emissions | 10,800 kgCO2e |
| Reduction | 32.1429% |
| Cost delta | +3.2% |

From `apps/api`, run:

```powershell
ruff check app tests
pytest -q
```

The API E2E fixture starts a temporary local PostgreSQL server when `initdb` and
`pg_ctl` are available. An external database is accepted only when both
`CARBONMESH_E2E_DATABASE_URL` and the exact destructive-test attestation
`CARBONMESH_RUN_API_E2E_TESTS=disposable-database` are set.
