# CarbonMesh Remaining Work and Five-Person Delivery Plan

Last updated: 2026-10-01

## Executive assessment

CarbonMesh is expanding a working Measurement and Procurement backend into one
connected four-module demonstration. The platform foundation is no longer the
main delivery risk. The remaining work is business behavior: hourly Scope 2,
Assurance, the final Procurement contract, Dispatch, the bounded agent runtime,
and one reproducible four-module fixture and test path.

Verified current branch state:

- 30 canonical `/api/...` paths are published in OpenAPI with no duplicate
  legacy route mount.
- Purchased-material Measurement, Procurement scoring/recommendations,
  procurement approval decisions, audit/lineage, persistent run snapshots/SSE,
  and Electricity Maps history sync are substantive implementations.
- The exact 46-table/eight-schema SQLAlchemy contract, clean bootstrap/check,
  local/hosted connection policy, pgvector objects, immutable-ledger trigger,
  Docker stack, and quality CI are implemented.
- Bounded ledger event search and event detail with safe evidence summaries and
  immediate lineage neighbors are implemented.
- Assurance and Dispatch have storage models but not their P0 services or APIs.
- The active agent remains a deterministic Measurement/Procurement router. It
  does not yet execute LangGraph, a model provider, Assurance, or Dispatch.
- The active demo remains the earlier Nova packaging scenario. The approved
  Maverick four-module fixture is still required.
- After canonical-route consolidation, Ruff passed; 195 backend tests passed
  and eight opt-in live-Neon tests were skipped. Frontend typecheck, lint, and
  production build also passed.

No shared database was altered by the architecture update.

## Database work is not a feature assignment

The clean 46-table database path is complete. No developer is assigned a
database migration workstream in this plan. Feature branches should use a fresh
or disposable database created and verified by the existing operator commands.

An older live database needs a separate data-preserving transition only if the
team explicitly decides that its existing rows must survive. In that case, an
operator must confirm the exact target, rehearse against a disposable clone,
keep a rollback branch or backup, and review the move/rename steps before any
shared-branch change. That optional operation is outside the four-module feature
backlog and must not delay a clean hackathon environment.

FastAPI startup never creates, changes, resets, or seeds database objects.

## Remaining capability gaps

| Area | Remaining P0 work | Primary owner |
| --- | --- | --- |
| Frontend | 16 real screens, canonical generated client, terminal/stale states, lineage, approvals, SSE/resume, browser journeys | Dev 1 |
| Backend foundation | Hourly activity/data quality, strict shared contracts, readiness, reset transaction, idempotency, ledger/audit, shared fact/approval services | Dev 2 |
| Measurement | Timestamped grid-point use, hourly Scope 2, confidence method, breakdown, complete lineage | Dev 2 |
| Assurance | Generic document/evidence upload, standards/requirements, drafts, atomic claims, retrieval, citations, gaps, staleness, approval preview, APIs | Dev 3 |
| Procurement | Canonical supplier/product paths, unconditional bindings, aluminium fixture integration, generic approvals | Dev 4 |
| Dispatch | Forecast sync, loads/constraints, deterministic optimizer, impact, advisory recommendation, stale approval | Dev 4 |
| Data and QA | Maverick fixture builders, reset-driven tests, golden values/hashes, provider parity, twelve release journeys, demo assets | Dev 4 |
| Agent runtime | Context/planner, five graphs, 24 tools, providers, persisted run steps, interrupts/resume, SSE, budgets, evals, telemetry | Dev 5 |

## Ownership rules

The five developers own one connected vertical slice, not independent demos.

| Person | Accountable area | Must not own |
| --- | --- | --- |
| Dev 1 | Frontend experience and browser tests | Business formulas, persistence transactions, graph policy |
| Dev 2 | Shared router/contracts, activity ingestion, Measurement, reset, ledger/audit, shared facts/approvals | Document retrieval judgment, Procurement/Dispatch selection, prompt reasoning |
| Dev 3 | Document/evidence upload and Assurance backend | Agent graph control, carbon/procurement/dispatch arithmetic, shared approval commit rules |
| Dev 4 | Electricity Maps/grid sync, Procurement, Dispatch, fixture builders, E2E, demo readiness | Shared API conventions, Assurance validity, agent orchestration |
| Dev 5 | Agentic runtime, model providers, tools, run steps, SSE/resume, telemetry | Deterministic domain formulas, direct SQL/session use, fixture truth |

Dev 2 is the sole owner of shared routers, cross-domain HTTP/Pydantic conventions,
safe errors, and shared transactions. Domain owners implement their services and
domain routes against those conventions. Dev 5 owns graph/tool/event contracts;
the affected domain owner reviews every business result.

## Dev 1 - Frontend experience (already in progress)

### Deliverables

- Build the 16 approved screens using React, TypeScript, shadcn/ui, and the
  canonical `/api/...` OpenAPI client.
- Implement loading, empty, error, unsupported, infeasible, stale, approval,
  provider-unavailable, and terminal states without parsing numerical facts from
  generated prose.
- Show Measurement formulas/evidence/lineage, Assurance claims/citations/gaps,
  Procurement feasibility/scores/impact, Dispatch windows/impact, approval
  hashes/expiry, and full run telemetry.
- Reconnect persisted SSE with `Last-Event-ID`, stop on typed terminal states,
  and use bounded polling only as a documented fallback.
- Add Playwright journeys and stable selectors for the release-blocking paths.

### Required handoffs

- Dev 2: frozen shared API/error/approval contracts and Measurement payloads.
- Dev 3: Assurance draft/claim/citation/gap payloads.
- Dev 4: Procurement/Dispatch payloads, stable fixture IDs, and golden results.
- Dev 5: graph stages, event names, interrupt/resume payloads, and telemetry.

### Acceptance

- No important value is a frontend constant or extracted from prose.
- Every displayed number links to a structured fact, evidence, or lineage view.
- Frontend typecheck, lint, build, and agreed browser journeys pass.

## Dev 2 - Backend foundation, data, Measurement, and shared services

### P0-A: shared routing, HTTP, and application contracts

- Freeze the 38-operation product catalog with Dev 1 and keep one canonical
  path for every implemented operation.
- Standardize strict Pydantic command/query/tool envelopes, decimal strings,
  offset-aware UTC timestamps, trace IDs, terminal states, and safe errors.
- Apply mutation idempotency consistently: same key and same payload replays the
  result; the same key with a different payload returns a typed conflict.
- Make health report dependency readiness without leaking connection details.
- Own inclusion of all domain routers and prevent duplicate or semantically
  divergent routes. Devs 3-5 provide domain handlers and schemas for review.
- Own the atomic demo reset transaction. Dev 4 supplies canonical fixture
  builders and expected manifests; reset safety and transaction boundaries stay
  in the shared backend layer.

### P0-B: ingestion and data quality

- Extend activity import for hourly electricity records with timestamp, kWh,
  site, period, source-row identity, checksum, estimation state, and timezone.
- Preserve purchased-material ingestion and rejected raw rows.
- Detect required-field, range, sign, unit, duplicate, period/site/timezone, and
  missing-provider-interval issues with stable codes and severity.
- Keep issue resolve/waive as P1 until invalid hourly data reliably blocks a
  verified Measurement.

### P0-C: hourly Scope 2 Measurement

- Consume Dev 4's typed history adapter and persisted
  `carbon.grid_intensity_points`; do not call a provider from calculation code.
- Match each kWh interval to an exact grid point. Missing intervals are explicit
  and are never interpolated.
- Calculate `kWh * gCO2e_per_kWh / 1000` with `Decimal`, while keeping the
  purchased-material path independent and regression-safe.
- Add the approved confidence method as a new method definition rather than
  reinterpreting persisted measurements.
- Persist row calculations, aggregate Measurement, evidence, ledger events,
  lineage, audit, and method/code/input/output hashes in one transaction.
- Add the P1 breakdown query only after the verified P0 result is stable.

### P0-D: ledger, audit, shared facts, and generic approvals

- Generalize fact binding for Assurance, Procurement, Dispatch, and final agent
  responses using the implemented `ledger.fact_bindings` table.
- Implement generic approval preview/detail/decision services for disclosure,
  procurement recommendation, and dispatch recommendation targets.
- Revalidate preview hash, analysis signature, context, expiry, facts, evidence,
  method, forecast, and constraints in the commit transaction.
- Preserve idempotent decisions and exactly one decision ledger event.
- Keep audit projections tenant-scoped and chronologically consistent with
  ledger events and decisions.
- Keep the implemented bounded ledger search/detail endpoints stable; recursive
  lineage remains separate behavior and must not be faked by immediate-neighbor
  data.
- Expose typed application-service ports to Devs 3, 4, and 5. Never expose an
  `AsyncSession` outside the application/repository boundary.

### Acceptance

- Invalid or incomplete hourly data cannot produce a verified Measurement.
- The same source/context/method reproduces Decimal output and hashes.
- Each Scope 2 result traces to the exact meter row and grid snapshot point.
- Disclosure, Procurement, and Dispatch previews share one fail-closed approval
  contract and reject stale inputs.
- Relevant unit, route, transaction, idempotency, and lineage tests pass.

## Dev 3 - Assurance backend

### P0-A: standards, requirements, and draft services

- Implement generic `/sources/upload` for bounded CSV/JSON/text/PDF document and
  evidence input, immutable checksums, safe storage references, and typed import
  outcomes using Dev 2's shared HTTP conventions.
- Implement repositories and services over the existing Assurance tables for
  standards, disclosure requirements, drafts, claims, citations, and gaps.
- Seed one GHG Protocol Scope 2 summary with limited ESRS-style mapping through
  Dev 4's canonical fixture package.
- Expose standards list, draft create/read, and validate operations with strict
  tenant/site/period/measurement context checks.

### P0-B: atomic claims and evidence retrieval

- Decompose the supported template into atomic claim requests and store each
  claim independently; Dev 5 may invoke model-assisted decomposition only
  through this typed contract.
- Retrieve candidate evidence with tenant and metadata filters, requirement
  scope, comparability, model ID, checksum, and explicit row limits.
- Bind all numerical content through Dev 2's fact service. A model may never
  choose an invented fact or citation ID.
- Validate citation existence, tenant/context equality, requirement relevance,
  comparability, evidence threshold, unsupported numbers, and staleness.
- Store supported, partial, and unsupported states plus explicit evidence gaps.
  The deliberate unsupported reduction claim remains visible and blocked.

### P0-C: Assurance review and approval integration

- Render a bounded draft from deterministic claims and verified bindings.
- Create disclosure approval previews through Dev 2's generic service and mark
  drafts stale when measurements, evidence, requirements, or methods change.
- Implement evidence-pack export only after the P0 claim trace is complete.
- Provide Dev 5 typed Assurance tools; do not embed graph control or provider
  calls inside the deterministic validation service.

### Acceptance

- Every supported numerical claim resolves to live fact and evidence IDs.
- The known unsupported claim is stored, explained, and cannot be approved.
- Changed upstream context invalidates the draft and its preview.
- Assurance unit, repository, API, retrieval, citation, and stale-state tests
  pass without a live model provider.

## Dev 4 - Procurement, Dispatch, integrations, fixtures, and E2E

### P0-A: complete the Procurement contract

- Preserve hard constraints, 40/25/20/15 scoring, deterministic impact, and
  `no_feasible_option`; never relax a constraint to force a result.
- Add canonical supplier and product queries and scenario-scoped recommendation
  read behavior without adding duplicate route aliases.
- Integrate the 10,000 kg recycled-aluminium fixture and persist fact bindings
  for direct and agent-created scenarios through Dev 2's shared service.
- Move approval preview/decision handling to Dev 2's generic contract without
  regressing current hash, staleness, or idempotency behavior.

### P0-B: Electricity Maps, grid-point, and forecast integrations

- Provide fixture and optional live Electricity Maps-shaped history/forecast
  adapters behind one typed interface.
- Implement cache-first reads, bounded ranges, chunking, timeout, one transient
  retry, retrieval/freshness/estimation metadata, immutable source snapshots,
  and credential-safe errors.
- Persist history as timestamped grid-intensity points and forecast snapshots as
  immutable Dispatch inputs.
- Give Dev 2 the typed history interface and Dev 5 tool-safe sync/read ports;
  provider-specific payloads must not leak into business calculations.

### P0-C: deterministic advisory Dispatch

- Implement flexible-load and operating-constraint reads plus forecast sync,
  scenario create, optimize, and recommendation read operations.
- Enumerate every complete consecutive two-hour window inside earliest start,
  latest finish, maximum delay, duration, capacity, and blackout constraints.
- Reject candidates with missing forecast intervals; never interpolate.
- Minimize Decimal emissions and use earliest feasible start as the primary
  tie-break. Persist baseline/recommended windows, emissions, avoided impact,
  method/input/output hashes, evidence, ledger, and lineage.
- Create a generic approval preview through Dev 2. Dispatch remains advisory;
  add no actuation service, endpoint, tool, credential, or control message.

### P0-D: canonical fixtures, golden replay, and E2E

- Replace the active Nova seed with Maverick Manufacturing / Plant B / Q3 2026.
- Add 90 days of hourly kWh with declared missing, duplicate, and estimated
  cases; 10,000 kg recycled aluminium and 3-5 products; standard/evidence data;
  and Batch Process 7 with its availability, delay, blackout, and capacity rules.
- Add history plus 6/24/48/72-hour provider-shaped forecast fixtures. Fixture
  and live payloads must validate through the same schema.
- Maintain one golden manifest produced from real deterministic services for
  Measurement values/issues, claims/citations/gaps, supplier scores/impact,
  dispatch windows/impact, hashes, signatures, and terminal states.
- Supply deterministic fixture builders and expected manifests to Dev 2's atomic
  reset transaction. Own provider contract tests, reset-driven replay, the
  twelve release-blocking API journeys, cross-module E2E, data/source register,
  limitations, demo run sheet, and rehearsal evidence.

### Acceptance

- Procurement returns infeasible rather than weakening constraints.
- Dispatch selects a complete feasible window deterministically and never
  actuates equipment.
- Fixture and fake-provider runs require no network credentials and reproduce
  all declared hashes.
- The golden run starts from reset/import, not hand-inserted calculated rows.
- All twelve release journeys pass on a clean disposable database.

## Dev 5 - Agentic runtime, providers, SSE/resume, and telemetry

### P0-A: graph contracts and context isolation

- Add the approved LangGraph dependency and define strict state for immutable
  `ContextEnvelope`, typed `ExecutionPlan`, facts, judgments, unsupported items,
  pending approval, budgets, telemetry, and terminal state.
- Implement the contextualizer with bounded recent history, entity resolution,
  request hash, analysis signature, actor/role, and explicit hard constraints.
- Implement one orchestrator plus Measurement, Assurance, Procurement, and
  Dispatch specialist graphs. Every branch reaches a typed stop; no autonomous
  retry loop exists.

### P0-B: 24 typed tools and provider execution

- Implement exactly the 24 approved tool IDs as wrappers around Dev 2 shared and
  Measurement services, Dev 3 Assurance services, and Dev 4
  Procurement/Dispatch/integration services.
- Tools accept frozen typed context and never receive `AsyncSession`, raw SQL,
  unbounded transcript, provider credentials, or permission to alter hard
  constraints.
- Activate the provider-neutral model interface for configured Gemini or other
  supported providers with strict structured output, bounded time/size, one
  repair per stage, and explicit `provider_unavailable` behavior.
- Keep calculations, scores, filters, citations, hashes, and approvals in
  deterministic application code. Generated narratives use verified
  placeholders resolved by Dev 2's binder.

### P0-C: budgets, interrupts, resume, and persisted SSE

- Enforce separate single-module, four-module, and approval-resume budgets in
  runtime code while remaining within the database's maximum persisted limits.
- Persist ordered context/planner/node/tool/provider/validation/approval/final
  steps in `ai.agent_run_steps` with safe input/output summaries.
- Implement clarification and approval interrupts plus durable resume that
  reloads context, revalidates current hashes/expiry/staleness, and never trusts
  an in-memory-only checkpoint.
- Replay SSE after `Last-Event-ID`, follow new committed steps, and close on a
  typed terminal or interrupt state.

### P0-D: telemetry, sustainability metrics, and evaluations

- Record graph/node/tool/provider/cache names and versions, calls, tokens,
  retries, rows/chunks, latency, errors, hashes, and projected/approved impact.
- Implement the agent-sustainability metrics query. Clearly label energy/CO2e
  as a documented proxy; tokens are not direct carbon measurements.
- Add graph-path, budget, provider-failure, malformed-output, prompt-injection,
  unsupported, stale-resume, and replay evaluations using deterministic service
  fakes and Dev 4's golden fixtures.

### Acceptance

- A single request can traverse all four modules with bounded calls and a full
  replayable trace.
- Provider absence/failure is explicit and never changes vendor silently.
- Reconnect/resume survives process-local state loss and revalidates the exact
  pending artifact.
- Every tool call is allowlisted, typed, tenant-scoped, and attributable to a
  persisted run step.

## Critical dependency order

```text
Completed 46-table/database/API foundation
  -> freeze shared API, domain result, tool, event, and fixture contracts
    -> Dev 2 hourly data/Measurement/shared approvals
    -> Dev 3 Assurance backend
    -> Dev 4 Procurement/Dispatch/integrations/fixtures
      -> Dev 5 real tool bindings and all-four graph
        -> Dev 1 generated client and final interaction states
          -> cross-module replay, browser E2E, demo, submission
```

Safe parallel work after contract freeze:

- Dev 1 builds screens against reviewed OpenAPI examples and fixture IDs.
- Dev 2 builds activity ingestion, Scope 2, reset, ledger/audit, fact binding,
  and generic approval services.
- Dev 3 builds document/evidence upload plus Assurance validators and APIs
  against typed fact ports.
- Dev 4 builds Electricity Maps/grid adapters, Procurement/Dispatch, fixture
  builders, and E2E.
- Dev 5 builds graph state, tools against typed fakes, provider handling, and
  persisted event/resume behavior.

## Four-day integration schedule

### Day 1 - freeze interfaces and fixtures

- All: freeze IDs, P0 operations, terminal states, tool names, event names,
  method identifiers, and fixture manifest.
- Dev 1: shell and generated-client integration points.
- Dev 2: shared routers/contracts, reset transaction, and hourly
  import/Measurement ports.
- Dev 3: document/evidence upload plus Assurance result/validation contracts.
- Dev 4: Electricity Maps/grid schemas, canonical fixture builders, and
  Procurement/Dispatch ports.
- Dev 5: graph state, planner, tools, events, and resume contracts.
- Gate: clean bootstrap -> skeleton seed -> import -> run/step -> SSE.

### Day 2 - Measurement and Assurance

- Devs 2 and 4 complete fixture/history parity and hourly Scope 2 lineage.
- Dev 3 completes supported/unsupported claims, citations, gaps, staleness, and
  disclosure preview using Dev 2's shared services.
- Dev 5 integrates Measurement and Assurance tools/graphs.
- Gate: source row and grid/evidence snapshot trace through a blocked or
  supported claim with deterministic replay.

### Day 3 - Procurement, Dispatch, and all-four orchestration

- Dev 4 completes Procurement, Dispatch, golden fixtures, and domain E2E.
- Dev 2 completes generic approvals/fact bindings for all three consequential
  artifacts.
- Dev 5 completes Procurement/Dispatch tools, all-four handoffs, resume, and the
  unified trace.
- Dev 1 switches mocks to live APIs and completes approval/run/ledger states.
- Gate: all four modules create real artifacts and the P0 API journey passes.

### Day 4 - hardening and submission

- Fix regressions only; exercise provider failure, stale state, no data,
  infeasible options/windows, reconnect/resume, and clean setup.
- Devs 1 and 4 complete browser journeys, three dry runs, data/source register,
  limitations, demo script/video, and submission evidence.
- No schema or contract redesign after feature freeze.

## Merge gates

- The implemented database contract is frozen. An unavoidable model correction
  requires Dev 2 plus the affected domain owner; it is not a standing workstream.
- Shared router/API convention/Pydantic error/approval change: Dev 2 solely
  accountable; Dev 1 and the affected domain owner review.
- Assurance behavior: Dev 3 accountable; Devs 2 and 5 review shared/tool impact.
- Procurement/Dispatch/provider/fixture/E2E behavior: Dev 4 accountable; Devs 2
  and 5 review service/tool impact.
- Agent state/tool/event/provider/resume/telemetry change: Dev 5 accountable;
  Devs 1, 2, and the affected domain owner review.
- Frontend contract consumption and browser tests: Dev 1 accountable; API owner
  reviews.
- Shared approval/hash change: Dev 2 accountable; Dev 3 or Dev 4 reviews the
  relevant artifact.
- Every dry-run failure becomes an automated regression test before the next
  rehearsal.
