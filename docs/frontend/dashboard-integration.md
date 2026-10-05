# Dashboard integration

The `/dashboard` screen is connected to canonical backend routes. `/` redirects
there. The shared navigation connects the four module workflows, approvals,
assistant runs, and evidence trail. See the [user guide](../user-guide.md) for
the current workflow and [Measurements integration](measurements-integration.md)
for result details.

## Setup

Run the API using the repository README, then `npm run dev` from the repository
root. The dashboard is at `http://localhost:3000/dashboard`. The Vite development
proxy forwards `/api` to port 8000.

The frontend and API use no sign-in, access key, or session. Queries use the
configured company context. Named actor selection identifies commands and
approval decisions in the trusted local/demo workspace.

`apps/web/.env.example` documents the existing synthetic seed's company, site,
reporting-period and metric UUIDs. These are configurable identifiers, not demo
results. Set all identifiers for another existing context and restart Vite.
Names, provenance, values and counts always come from the API. An invalid context
does not trigger seeding, a reset, or a fallback dataset.

## Endpoint mapping

| Dashboard surface | Canonical endpoint | Scope / meaning |
| --- | --- | --- |
| Context labels | `POST /api/context/resolve` | Read-only context resolution; no workflow execution |
| Latest verified result, chart and result table | `GET /api/measurements` | Verified records for configured company/site/period; paginated |
| Measurement source inspector | `GET /api/measurements/{id}` | On demand, includes method, hashes, raw row and factor evidence references |
| Measurement lineage tab | `GET /api/measurements/{id}/lineage` | On demand; truncation explicitly shown |
| Open issue count and severity filter | `GET /api/quality/issues` | Company-wide, open only; bounded preview with server total |
| Generic approval queue and inspector | `GET /api/approvals` | Company-wide, pending only; includes expired/stale records |
| Active suppliers | `GET /api/procurement/suppliers` | Company-wide active catalog count |
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
- Approvals display the target type and named records for every workflow. Optional
  procurement fields are shown only when present, never replaced with zero.
  Queue inspection does not authorize a decision.
- Refresh errors retain previously fetched data with a visible warning. Empty
  and unavailable states never substitute zero emissions or synthetic values.
- The dashboard does not request provider data, synchronize grid history, or
  display an API-status card.
- Source inspection and the chart are separate lazy-loaded bundles. Interactive
  primitives come from shadcn/ui; charts use Recharts. No alternate UI library,
  global server-data store, polling loop per panel or business engine was added.

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

Opening the dashboard performs company-scoped reads and context resolution;
it never requests provider data or mutates the database.
