# Workspace Options

`GET /api/workspace/options` is a read-only named-picker query. It uses the
ordinary API authentication, tenant checks, and security middleware.

| Query parameter | Contract |
| --- | --- |
| `company_id` | Required UUID, checked against the authenticated company when authentication is enabled. |
| `kind` | Required enum: `actors`, `metrics`, `methods`, `policies`, `imports`, `documents`, `activity`, `evidence`, `measurements`, `suppliers`, `products`, `standards`, `loads`, `forecasts`, `assurance`, `procurement`, `dispatch`, `runs`, `ledger`. |
| `site_id` | Optional UUID; must belong to the company. |
| `reporting_period_id` | Optional UUID; must belong to the company. |
| `id` | Optional exact record UUID; resolves the selection without search or pagination. Company, site, period, and role checks still apply. Returned `offset` is zero. |
| `role` | Actors only: `sustainability_analyst`, `procurement_manager`, `approver`, `auditor`, `system`. Other kinds reject this parameter with 422. |
| `search` | Optional text, maximum 200 characters. Trimmed, case-insensitive literal substring of label or description; `%` and `_` are literal characters. Ignored for an `id` lookup. |
| `limit` | Integer 1-100, default 50. |
| `offset` | Integer 0-10000, default 0. Ignored for an `id` lookup. |

Response shape:

```json
{
  "items": [
    {
      "id": "00000000-0000-4000-8000-000000000004",
      "label": "Synthetic Sustainability Analyst",
      "description": null,
      "status": "active",
      "role": "sustainability_analyst"
    }
  ],
  "total": 1,
  "limit": 50,
  "offset": 0
}
```

This example is synthetic. Labels use persisted names, versions, and UTC dates,
never appended UUIDs; UUIDs embedded in legacy names are removed. Descriptions
are bounded summaries, not document bodies, credentials, or provider payloads.
`role` is null except for actors. Unavailable statuses are null. Existing inactive
or stale records remain selectable for inspection and carry their stored status.
Sorting is case-insensitive label followed by UUID for stable pagination. `total`
counts all matching records before pagination. An unknown company, foreign or
unknown site/period, unknown selection, or no matches returns 200 with an empty
list and `total: 0`. An offset beyond the matches returns an empty list with the
matching total. Malformed or out-of-range input returns 422. Database failures
return a safe 503 `workspace_options_unavailable` response.

## IDs and Scope

- Actors, metrics, methods, policies, suppliers, products, and standards are
  company-wide catalogs. Valid optional site/period scopes do not narrow them.
- Imports return `core.data_sources.id`, compatible with `/api/imports/{id}`;
  only recognized activity/supplier imports are included. Documents return
  `core.source_documents.id`; evidence returns `core.evidence_items.id`.
  These use data-source site and persisted period metadata. Unscoped shared
  sources/documents are included alongside exact scope matches.
- Activity, measurements, Assurance drafts, and Procurement scenarios filter by
  their stored site and reporting-period foreign keys.
- Loads and Dispatch scenarios filter by site. They have no reporting-period
  foreign key, so a valid period does not narrow them.
- Forecasts return one snapshot document ID, not one option per hourly point.
  This ID is the `forecast_source_document_id` accepted by Dispatch. Site applies;
  reporting period does not narrow future forecast snapshots.
- Runs use their persisted context's exact site/period. Ledger events use exact
  site/period values in their stored payload. Events without the requested scope
  are excluded. Omit site/period to inspect all company events.
- `assurance`, `procurement`, `dispatch`, and `runs` IDs identify disclosure
  drafts, procurement scenarios, dispatch scenarios, and agent runs respectively.

## Offline Grid and No-Login Demo

`ELECTRICITY_MAPS_LIVE_ENABLED=false` is the configuration default and example.
The HTTP client checks it before request execution, including retries, even when
a token is configured or a client was constructed earlier. Disabled requests
return `integration_live_disabled`, non-retryable, with HTTP 503 from sync/test
routes. No automatic fixture fallback occurs: history needs `mode: "fixture"`;
forecast sync needs `source_mode: "fixture"`. Stored data and deterministic
calculation paths remain available.

`.env.example` explicitly selects the approved hackathon `AUTH_REQUIRED=false`
mode. Runtime authentication still defaults to true if unconfigured, and
`AUTH_REQUIRED=true` retains session, actor, and company enforcement. Destructive
demo reset still requires the separately configured reset token. Existing local
`.env` files are not rewritten; a running API must be restarted to load code
changes and use the intended process configuration.
