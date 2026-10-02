# CarbonMesh API Contract

Last updated: 2026-10-02

## Purpose

This is the target four-module HTTP contract. Canonical product routes use
stable `/api/...` resource paths. The generated OpenAPI document is
authoritative for implemented operations, and the
[current API reference](reference.md) records their actual behavior.

Each implemented operation has one canonical `/api/...` path. Do not add
duplicate legacy aliases. A missing workflow must never return placeholder
success.

Status meanings:

- **Implemented**: the canonical path and substantive behavior exist.
- **Implemented, limited**: the path is real but only the stated existing use
  case is supported.
- **Partial**: useful code/storage exists but required behavior is incomplete.
- **Missing**: a new domain implementation is required.

## Operation catalog

| # | Method | Route | Priority | Current status | Owner |
| ---: | --- | --- | --- | --- | --- |
| 1 | GET | `/api/health` | P0 | Implemented process health; `/api/health/ready` verifies the database contract read-only | Dev 2 |
| 2 | POST | `/api/demo/reset` | P0 | Implemented for the Maverick synthetic seed | Dev 2 |
| 3 | POST | `/api/sources/upload` | P0 | Implemented checksum-verified document/evidence upload with deterministic extraction, chunking, embedding, and replay | Dev 3 |
| 4 | POST | `/api/activities/import` | P0 | Implemented material and hourly electricity import with exact UTC-hour validation | Dev 2 |
| 5 | GET | `/api/quality/issues` | P0 | Implemented | Dev 2 |
| 6 | PATCH | `/api/quality/issues/{issue_id}` | P1 | Implemented tenant/actor-scoped, audited resolve/waive command | Dev 2 |
| 7 | POST | `/api/agent/requests` | P0 | Implemented bounded run start; target orchestration missing | Dev 5 |
| 8 | GET | `/api/runs/{run_id}` | P0 | Implemented | Dev 5 |
| 9 | GET | `/api/runs/{run_id}/events` | P0 | Implemented persisted SSE; node/interrupt events partial | Dev 5 |
| 10 | POST | `/api/runs/{run_id}/resume` | P0 | Missing durable resume/revalidation | Dev 5 |
| 11 | POST | `/api/measurement/grid/history/sync` | P0 | Implemented with timestamped `carbon.grid_intensity_points` and evidence provenance | Dev 4 |
| 12 | GET | `/api/measurement/grid/latest` | P1 | Implemented timestamped-point read with provenance | Dev 4 |
| 13 | POST | `/api/measurement/calculate` | P0 | Implemented purchased-material and exact-hour Scope 2 calculation, confidence v2 | Dev 2 |
| 14 | GET | `/api/measurements` | P0 | Implemented | Dev 2 |
| 15 | GET | `/api/measurements/{measurement_id}` | P0 | Implemented | Dev 2 |
| 16 | GET | `/api/measurements/{measurement_id}/lineage` | P0 | Implemented | Dev 2 |
| 17 | GET | `/api/measurements/{measurement_id}/breakdown` | P1 | Implemented calculation-backed breakdown with lineage | Dev 2 |
| 18 | GET | `/api/assurance/standards` | P0 | Implemented tenant-scoped standards with ordered requirements | Dev 3 |
| 19 | POST | `/api/assurance/drafts` | P0 | Implemented immutable-context, idempotent draft creation | Dev 3 |
| 20 | GET | `/api/assurance/drafts/{draft_id}` | P0 | Implemented atomic claims, citations, gaps, binding identities, and approval state | Dev 3 |
| 21 | POST | `/api/assurance/drafts/{draft_id}/validate` | P0 | Implemented deterministic fact/evidence validation, gaps, staleness, and exact approval preview creation | Dev 3 |
| 22 | GET | `/api/assurance/drafts/{draft_id}/evidence-pack` | P1 | Implemented structured traceability manifest with safe evidence metadata | Dev 3 |
| 23 | GET | `/api/procurement/suppliers` | P0 | Implemented | Dev 4 |
| 24 | GET | `/api/procurement/products` | P0 | Implemented | Dev 4 |
| 25 | POST | `/api/procurement/scenarios` | P0 | Implemented | Dev 4 |
| 26 | POST | `/api/procurement/scenarios/{scenario_id}/score` | P0 | Implemented | Dev 4 |
| 27 | GET | `/api/procurement/scenarios/{scenario_id}/recommendation` | P0 | Implemented | Dev 4 |
| 28 | GET | `/api/dispatch/loads` | P0 | Implemented advisory load/constraint query | Dev 4 |
| 29 | POST | `/api/dispatch/forecasts/sync` | P0 | Implemented 24-hour fixture/live sync and immutable forecast persistence | Dev 4 |
| 30 | POST | `/api/dispatch/scenarios` | P0 | Implemented frozen advisory scenario creation | Dev 4 |
| 31 | POST | `/api/dispatch/scenarios/{scenario_id}/optimize` | P0 | Implemented deterministic optimization, preview, and no-feasible result | Dev 4 |
| 32 | GET | `/api/dispatch/scenarios/{scenario_id}/recommendation` | P0 | Implemented advisory result and embedded preview read | Dev 4 |
| 33 | GET | `/api/approvals` | P0 | Implemented for Procurement, Assurance, and Dispatch | Dev 2 |
| 34 | GET | `/api/approvals/{approval_id}` | P0 | Implemented exact payload, preview hash, and freshness detail | Dev 2 |
| 35 | POST | `/api/approvals/{approval_id}/decision` | P0 | Implemented idempotent, revalidated decisions for all three targets | Dev 2 |
| 36 | GET | `/api/ledger/events` | P0 | Implemented tenant-scoped bounded search | Dev 2 |
| 37 | GET | `/api/ledger/events/{event_id}` | P0 | Implemented safe evidence and immediate-neighbor detail | Dev 2 |
| 38 | GET | `/api/metrics/agent-sustainability` | P0 | Missing | Dev 5 |

The implemented Dispatch routes are advisory only. Optimization creates an
exact `dispatch_recommendation` preview in `core.approvals`; the shared queue,
detail, and decision endpoints handle it alongside `disclosure_draft` and
`procurement_recommendation`. Shared `ledger.fact_bindings` persist with an
optional agent-run association for direct API and agent commands. No Dispatch
route purchases, schedules, controls, or actuates equipment.

## Commands and idempotency

Every mutating operation accepts or derives a stable idempotency key. Repeating
the same key with the same canonical payload returns the original result;
reusing it with a different payload returns a typed conflict. One command owns
one transaction and appends its audit/ledger records within that transaction.

Approval decisions require the exact preview hash shown to the reviewer. The
commit transaction revalidates target payload, context/analysis signatures,
facts, evidence, method or scoring/optimization identity, relevant forecast,
and constraints.

## Decimal and time representation

- Carbon, quantity, money, percentage, confidence, and scores are JSON decimal
  strings.
- Timestamps include an offset and are stored in UTC.
- Hourly grid/forecast points retain provider timestamp, retrieval time,
  estimation flag, source snapshot/checksum, provider/model identity, and zone.

## Safe error envelope

Operations return a sanitized body with a stable code, message, trace ID,
retryability, optional field details, and an applicable terminal state. SQL,
credentials, raw provider responses, document bodies, prompts, and model
response bodies never appear in errors.

## SSE contract

The stream replays committed events after `Last-Event-ID` and follows new steps
until a typed terminal or interrupt state. Event categories cover:

- run and node start/completion;
- tool and provider start/completion/failure/cache hit;
- validation warnings and fact creation;
- clarification and approval interrupts;
- unsupported/stale/budget stops;
- final completion.

Dev 1 and Dev 5 must freeze the exact event-name enum before the frontend client
is generated.
