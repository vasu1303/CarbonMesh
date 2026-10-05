# Measurements: API-only integration

`/measurement` lists persisted results and opens an explicit calculation form.
`/measurement/:id` provides facts, calculation breakdown, evidence and lineage.
All workspace screens use the same API-only navigation and workspace context.

## Setup

Start the backend and frontend using the README, then open
`http://localhost:3000/measurement`. The frontend and API use no sign-in, access
key, or session. Queries use the configured workspace context. Commands use an
existing named actor selected from the API; the UI does not invent actors.

Configure existing company, site, period and metric UUIDs in `apps/web/.env` as
documented in `.env.example`. Names and provenance come from the backend.
There is no development preview switch, runtime fixture import or error fallback.
Old `source=preview` URLs still use the API. Seeded synthetic data keeps its label.

## Endpoint mapping

| Endpoint | Purpose |
| --- | --- |
| `GET /api/measurements` | Records, filtered counts and server pagination |
| `GET /api/measurements/{id}` | Persisted facts, method, confidence and evidence |
| `GET /api/measurements/{id}/lineage` | Persisted source-to-result relationships |
| `GET /api/measurements/{id}/breakdown` | Exact calculation values and source identities |
| `POST /api/measurement/calculate` | Explicit material or hourly Scope 2 calculation |
| `GET /api/semantic/metrics` | Category names and exact metric keys |
| `POST /api/context/resolve` | Read-only reporting context resolution |

Queries are independent, cancelable and bounded. Lists include company, site,
period, status and category filters. Category is an exact metric key. Detail and
lineage are fetched only when opening a record; neither blocks the other.
Zod checks consumed fields and rejects context mismatches. Decimal values stay
strings except chart geometry. No emissions or confidence formula runs in React.

## Interactions and states

- URL-backed filters, pagination, record selection, and table/chart views.
- Counts use API totals. Charts show individual page records, not an additive
  footprint or an inferred time trend.
- Recorded confidence v1 and v2 are displayed with their original components
  and weights. Unknown versions fail validation rather than being reinterpreted.
- Material inputs/factors and hourly Scope 2 inputs/grid evidence are supported.
  Coverage, provider timestamps, estimated flags, method versions and hashes are
  displayed only when supplied by the API.
- Loading, empty, unsupported, superseded, invalid ID, contract mismatch, partial
  lineage, error and failed-refresh states are explicit. Retry and refresh are
  independent; cached data is marked when refresh fails.
- Visiting a page never starts a calculation or any other mutation. The Calculate
  dialog submits only after explicit confirmation, keeps Decimal business values
  on the backend, and opens the returned persisted measurement.
- Calculation charts display at most 20 recorded calculations per page; source
  documents and ledger events link to their corresponding API-backed screens.

## Verification

From `apps/web`: `npm run typecheck`, `npm run lint`, `npm run build`, and
`npm run test:e2e`. Browser fixtures live only under `tests/`; they are not part
of the application bundle and are not database seeds. Browser tests cover
direct workspace access, independent API loading, precision, filters, inspection,
confidence versions, Scope 2 evidence, failures and responsive light/dark views.

Live acceptance uses the already-populated Neon `carbonmesh` database through
FastAPI, without additional data writes. Missing grid history remains an empty
state until a separate explicit sync populates it.

Earlier baseline verified on 2026-10-03: all 60 browser tests passed, as did typecheck, lint and
the production build (Vite reports a non-blocking main-chunk size advisory).
A browser loaded four actual API records: one verified and three
superseded. The latest record displayed 34,400 kgCO2e with confidence 0.9325,
confidence v2 components and persisted lineage. Dashboard queries validated
successfully; grid history returned its expected no-data state. Desktop/mobile
checks found no browser errors or page-level horizontal overflow.
