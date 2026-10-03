import type { Page, Route } from '@playwright/test'
import type { z } from 'zod'
import type {
  Forecast,
  Load,
  Recommendation,
  Scenario,
  optimizationSchema,
} from '../src/features/dispatch/schemas'
import { id, mockDashboard } from './dashboard-fixtures'

// Synthetic browser fixtures only; never imported by the production application.
const hash = 'b'.repeat(64)
const time = '2026-10-01T00:00:00Z'
export const dispatchLoad: Load = {
  id: id(7100),
  company_id: id(1),
  site_id: id(2),
  semantic_entity_id: null,
  code: 'SYNTHETIC-BATCH',
  name: 'Synthetic batch load',
  description: 'Synthetic browser-test load.',
  power_kw: '500.125000',
  duration_minutes: 120,
  energy_kwh: '1000.250000',
  minimum_power_kw: null,
  maximum_power_kw: null,
  is_interruptible: false,
  metadata: { synthetic: true },
  is_active: true,
  constraints: [
    {
      id: id(7101),
      code: 'SYNTHETIC-DELAY',
      name: 'Synthetic hard delay',
      constraint_type: 'deadline',
      is_hard: true,
      valid_from: null,
      valid_to: null,
      configuration: { maximum_delay_minutes: 240 },
      is_active: true,
    },
  ],
  created_at: time,
  updated_at: time,
}
export const dispatchForecast: Forecast = {
  site_id: id(2),
  zone: 'IN-SO',
  provider: 'electricity_maps',
  source_mode: 'live',
  synthetic: false,
  issued_at: time,
  temporal_granularity: 'hourly',
  source_document_id: id(7200),
  snapshot_hash: hash,
  received_points: 24,
  inserted_points: 24,
  existing_points: 0,
  estimated_points: 0,
  forecast_start: time,
  forecast_end: '2026-10-02T00:00:00Z',
  points: Array.from({ length: 24 }, (_, index) => ({
    id: id(7300 + index),
    forecast_for: `2026-10-01T${String(index).padStart(2, '0')}:00:00Z`,
    intensity_gco2e_per_kwh: index === 2 ? '200.123456789' : '400.987654321',
    is_estimated: false,
    point_hash: hash,
    evidence_item_id: id(7400 + index),
    evidence_checksum: hash,
    source_document_id: id(7200),
    source_document_checksum: hash,
    forecast_ledger_event_id: id(7201),
    forecast_ledger_event_hash: hash,
  })),
}
const loadSnapshot = {
  id: dispatchLoad.id,
  code: dispatchLoad.code,
  name: dispatchLoad.name,
  power_kw: dispatchLoad.power_kw,
  duration_minutes: dispatchLoad.duration_minutes,
  energy_kwh: dispatchLoad.energy_kwh,
  minimum_power_kw: null,
  maximum_power_kw: null,
  is_interruptible: false,
  is_active: true,
  snapshot_hash: hash,
}
const methodSnapshot = {
  id: id(7500),
  key: 'synthetic_dispatch_method',
  version: '1.0',
  code_version: 'synthetic-test',
  configuration: {},
  is_active: true,
  snapshot_hash: hash,
}
export const dispatchScenario: Scenario = {
  id: id(7600),
  company_id: id(1),
  site_id: id(2),
  flexible_load: dispatchLoad,
  agent_run_id: null,
  method: methodSnapshot,
  policy_definition_id: null,
  forecast_source_document_id: dispatchForecast.source_document_id,
  window_start: time,
  window_end: '2026-10-01T12:00:00Z',
  objective: 'minimum_carbon',
  constraints: {
    earliest_start: time,
    latest_finish: '2026-10-01T12:00:00Z',
    baseline_start: time,
    maximum_delay_minutes: 240,
    blackouts: [],
    capacity_windows: [
      { available_capacity_kw: '600.125000', start: null, end: null },
    ],
    source_constraints: dispatchLoad.constraints,
    load_snapshot: loadSnapshot,
    method_snapshot: methodSnapshot,
    policy_snapshot: null,
    requested_by: id(4),
    approval_expires_at: '2099-01-01T00:00:00Z',
    approval_expiry_is_default: true,
    approval_idempotency_key: 'synthetic-scenario-key',
  },
  forecast_snapshot: dispatchForecast.points,
  analysis_signature: hash,
  context_hash: hash,
  status: 'draft',
  created_at: time,
  updated_at: time,
}
const emissions = {
  recommended_start: '2026-10-01T02:00:00Z',
  recommended_end: '2026-10-01T04:00:00Z',
  baseline_start: time,
  baseline_end: '2026-10-01T02:00:00Z',
  expected_emissions_kgco2e: '300.555555555',
  baseline_emissions_kgco2e: '400.987654321',
  avoided_kgco2e: '100.432098766',
  reduction_pct: '25.045',
}
export const dispatchRecommendation: Recommendation = {
  id: id(7700),
  company_id: id(1),
  scenario_id: dispatchScenario.id,
  status: 'pending_approval',
  ...emissions,
  rationale:
    'Synthetic advisory recommendation; exact facts are bound separately.',
  impact_snapshot: {
    input_hash: hash,
    output_hash: hash,
    method_id: methodSnapshot.id,
    method_key: methodSnapshot.key,
    method_version: methodSnapshot.version,
    method_code_version: methodSnapshot.code_version,
    evaluated_windows: 11,
    feasible_windows: 4,
    rejected_windows: [
      {
        start: '2026-10-01T06:00:00Z',
        end: '2026-10-01T08:00:00Z',
        reasons: ['maximum_delay_exceeded'],
      },
    ],
  },
  analysis_signature: hash,
  payload_hash: hash,
  ledger_event_id: id(7701),
  evidence_item_ids: [id(7400)],
  approval: {
    id: id(7800),
    target_type: 'dispatch_recommendation',
    target_id: id(7700),
    status: 'pending',
    preview_hash: hash,
    analysis_signature: hash,
    context_hash: hash,
    idempotency_key: 'synthetic-scenario-key',
    expires_at: '2099-01-01T00:00:00Z',
    preview_payload: {
      target_type: 'dispatch_recommendation',
      scenario_id: dispatchScenario.id,
      ...emissions,
      load_snapshot: loadSnapshot,
      method_snapshot: methodSnapshot,
      policy_snapshot: null,
      frozen_constraints: dispatchScenario.constraints,
      forecast_points: dispatchForecast.points,
      forecast_source_document_id: dispatchForecast.source_document_id,
      forecast_source_document_checksum: hash,
      input_hash: hash,
      output_hash: hash,
      advisory_only: true,
      actuation_authorized: false,
    },
  },
  actuation_authorized: false,
  invalidated_at: null,
  created_at: time,
}
export const dispatchOptimization: z.infer<typeof optimizationSchema> = {
  scenario_id: dispatchScenario.id,
  terminal_state: 'approval_required',
  evaluated_windows: 11,
  feasible_windows: 4,
  rejected_windows: dispatchRecommendation.impact_snapshot.rejected_windows,
  recommendation: dispatchRecommendation,
}
export async function mockDispatch(
  page: Page,
  override?: (route: Route, url: URL) => Promise<boolean | void>,
) {
  return mockDashboard(page, async (route, url) => {
    if (override && (await override(route, url))) return true
    const path = url.pathname
    let json: unknown
    if (path === '/api/dispatch/loads')
      json = { items: [dispatchLoad], total: 1, offset: 0, limit: 25 }
    if (path === '/api/dispatch/forecasts/sync') {
      const fixture = route.request().postDataJSON().source_mode === 'fixture'
      json = {
        ...dispatchForecast,
        source_mode: fixture ? 'fixture' : 'live',
        synthetic: fixture,
      }
    }
    if (path === '/api/dispatch/scenarios') json = dispatchScenario
    if (
      path === `/api/dispatch/scenarios/${dispatchScenario.id}/recommendation`
    )
      json = {
        scenario_id: dispatchScenario.id,
        terminal_state: 'approval_required',
        recommendation: dispatchRecommendation,
      }
    if (path === `/api/dispatch/scenarios/${dispatchScenario.id}/optimize`)
      json = dispatchOptimization
    if (json === undefined) return
    await route.fulfill({ json })
    return true
  })
}
