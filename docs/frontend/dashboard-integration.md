# Dashboard integration: phase 1

Follow-up: the [Measurements screen](measurements-integration.md) is now wired
into the shared shell as the second reviewable screen.

The `/dashboard` screen is connected to canonical backend routes. `/` redirects
there. Other screens are intentionally not implemented in this phase; their
navigation items remain disabled. No backend code or database content is changed.

## Setup

Run the API using the repository README, then `npm run dev` from the repository
root. The dashboard is at `http://localhost:3000/dashboard`. The Vite development
proxy forwards `/api` to port 8000.

Sign in with the backend's operator-provisioned analyst access key. The shared
session boundary uses an HttpOnly cookie, gates workspace queries, checks the
company, and clears cached workspace data on expiry/sign-out. Never place access
keys in Vite environment variables. See README for local authentication setup.

`apps/web/.env.example` documents the existing synthetic seed's company, site,
reporting-period and metric UUIDs. These are configurable identifiers, not demo
results. Set all identifiers for another existing context and restart Vite.
Names, provenance, values and counts always come from the API. An invalid context
does not trigger seeding, a reset, or a fallback dataset.

## Endpoint mapping

| Dashboard surface | Canonical endpoint | Scope / meaning |
| --- | --- | --- |
| API connectivity | `GET /api/health` | Process health only, not database health |
| Context labels | `POST /api/context/resolve` | Read-only context resolution; no workflow execution |
| Latest verified result, chart and result table | `GET /api/measurements` | Verified records for configured company/site/period; paginated |
| Measurement source inspector | `GET /api/measurements/{id}` | On demand, includes method, hashes, raw row and factor evidence references |
| Measurement lineage tab | `GET /api/measurements/{id}/lineage` | On demand; truncation explicitly shown |
| Open issue count and severity filter | `GET /api/quality/issues` | Company-wide, open only; bounded preview with server total |
| Generic approval queue and inspector | `GET /api/approvals` | Company-wide, pending only; includes expired/stale records |
| Cached historical grid point | `GET /api/measurement/grid/latest` | Configured site; fixture/live-source provenance and timestamps |
| Active suppliers | `GET /api/procurement/suppliers` | Company-wide active catalog count |
| Active supplier products | `GET /api/procurement/products` | Company-wide active catalog count |
| Active Assurance standards | `GET /api/assurance/standards` | Company-wide catalog count, not completed claims |
| Active flexible loads | `GET /api/dispatch/loads` | Site catalog count, not optimized dispatch results |
| Recent ledger activity | `GET /api/ledger/events` | Company-wide, latest bounded events |
| Ledger source inspector | `GET /api/ledger/events/{id}` | On demand; payload, evidence and immediate lineage neighbors |

## Data rules

- Queries load independently with TanStack Query, tenant/context-aware keys,
  cancellation, a 20-second timeout and at most one retry for retryable errors.
  Shared panels reuse query results rather than duplicating server state.
- Zod validates the response projections used by this screen. Measurement and
  context identifiers are checked against the requested scope. Contract mismatches
  fail closed. No generated prose is parsed for numerical values.
- Decimal strings are retained in labels and source views. Only chart geometry
  uses floating point. No client-side emissions, confidence, scoring or impact
  calculation is performed.
- Individual measurements can overlap. Neither the chart nor the latest-result
  card claims to be a company total or emissions trend.
- Counts come from API `total`, not the length of a bounded result page.
- Quality, generic approvals and ledger lists do not support site/period
  filters; their company-wide scope is visible.
- Approvals display the API target type and ID for every workflow. Optional
  procurement fields are shown only when present, never replaced with zero.
  Queue inspection does not authorize a decision.
- Refresh errors retain previously fetched data with a visible warning. Empty
  and unavailable states never substitute zero emissions or synthetic values.
- Missing cached grid data is an empty state. Opening the dashboard never calls
  a provider sync endpoint. Cached history is not described as a live forecast.
- Source inspection and the chart are separate lazy-loaded bundles. Interactive
  primitives come from shadcn/ui; charts use Recharts. No alternate UI library,
  global server-data store, polling loop per panel or business engine was added.

## Subsequent reviewable screen phases

Implement one screen per team review/push, not all phases together:

1. Data and quality: supplier/activity imports, generic document upload, import
   status and quality exploration.
2. Measurement: semantic metrics, deterministic calculation, historical grid
   sync and full measurement detail.
3. Procurement: supplier/product exploration, scenario creation, scoring,
   scenario details and recommendations.
4. Assurance: standards, draft creation/detail, validation and evidence packs.
5. Dispatch: loads, forecast sync, scenario creation, optimization and advisory
   recommendations.
6. Approvals: exact Preview-Approve-Commit using implemented decision contracts.
7. Ask / run trace: agent requests, run status and terminal-aware SSE.
8. Ledger / audit: search, detail, entity audit and source traversal.
9. Demo / diagnostics: database diagnostics, provider connection test and
   explicitly confirmed synthetic reset, never automatic dashboard requests.

Routes absent from the backend must not be invented. In particular there is no
dashboard aggregate, emissions-trend endpoint, or recent-run listing in this
integration. Fake avoided-total, agent-usage and recent-run cards were removed.

## Verification

From `apps/web`:

```sh
npm run typecheck
npm run lint
npm run build
npx playwright install chromium
npm run test:e2e
```

Browser tests run an isolated frontend on port 3100 with synthetic API response
projections. These fixtures exist only in `tests/`, never in application code.
Tests cover canonical routes, tenant parameters, independent loading, response
validation, pagination, source inspection, empty/error/stale states, severity
filtering, precision and responsive light/dark layouts. Screenshots and traces
are ignored test output.

The previous database-name mismatch has been corrected to `carbonmesh` and
additive test records were populated in a separate task. Both screens now use
authenticated API reads. Missing grid history remains an explicit empty state;
opening the dashboard never requests provider data or mutates the database.
