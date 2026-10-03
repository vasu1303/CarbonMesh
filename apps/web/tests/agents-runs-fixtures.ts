import type { Page, Route } from '@playwright/test'
import { fixtureFor, id } from './dashboard-fixtures'

// Synthetic browser-test responses only. No application fallback data.
export const runId = id(1701)
export const actorId = id(4)
export const signature = 'b'.repeat(64)
export const recordedAt = '2026-10-02T12:00:00Z'
export const runFixture = {
  run_id: runId,
  trace_id: 'synthetic-agent-trace',
  workflow: 'measurement',
  stage: 'completed',
  terminal_state: 'success',
  context: {
    company_id: id(1),
    site_id: id(2),
    reporting_period_id: id(3),
    actor_id: actorId,
    actor_role: 'sustainability_analyst',
    workflow: 'measurement',
    grid_source_mode: 'live',
    fresh_inputs: null,
    carbon_measurement_id: id(201) as string | null,
    current_product_id: null,
    method_definition_id: null,
    activity_record_ids: [],
    standard_id: null,
    disclosure_draft_id: null,
    requirement_ids: [],
    evidence_item_ids: [],
    procurement_scenario_id: null,
    flexible_load_id: null,
    dispatch_scenario_id: null,
    forecast_id: null,
    policy_definition_id: null,
    metric_keys: [],
    material_scope: [],
    supplier_product_ids: [],
    constraints: {
      max_cost_increase_pct: null,
      max_lead_time_days: null,
      minimum_circularity_score: null,
    },
    dispatch_constraints: {
      window_start: null,
      window_end: null,
      duration_minutes: null,
      max_delay_minutes: null,
      maximum_power_kw: null,
      blackout_constraint_ids: [],
    },
    request_hash: signature,
    analysis_signature: signature,
  },
  plan: {
    version: 'carbonmesh.execution-plan.v1',
    profile: 'single',
    context_tools: ['resolve_context'],
    expected_model_calls: 1,
    stages: [
      {
        stage_id: 'measurement',
        module: 'measurement',
        depends_on: [],
        tool_names: ['calculate_emissions'],
        requires_human_approval: false,
      },
    ],
  },
  facts: [
    {
      fact_id: id(1702),
      metric_key: 'emissions.scope2.location_based',
      display_value: '123.000000000000001 kgCO2e',
      ledger_event_id: id(501),
    },
  ],
  judgments: [
    {
      kind: 'workflow_classification',
      value: 'measurement',
      basis: 'structured_model_plan',
      matched_terms: ['electricity'],
    },
  ],
  telemetry: {
    trace_id: 'synthetic-agent-trace',
    analysis_signature: signature,
    orchestrator_version: 'carbonmesh.orchestrator.v1',
    provider: 'gemini',
    provider_status: 'completed',
    model_id: 'synthetic-test-model',
    model_calls: 1,
    input_tokens: 180,
    output_tokens: 24,
    cached_input_tokens: 0,
    context_tokens: 100,
    tool_calls: 3,
    retry_count: 0,
    repairs: 0,
    api_calls: 1,
    cache_hits: 1,
    external_cache_hits: 0,
    rows_processed: 48,
    evidence_chunks_retrieved: 1,
    elapsed_ms: 600,
    estimated_energy_wh: '0.1234567890001',
    estimated_co2e_g: '0.01234567890001',
    sustainability_method: 'synthetic.token_proxy.v1',
    sustainability_assumptions: {
      caveat: 'Synthetic token proxy; provider energy is not measured.',
    },
    execution_attempts: 1,
    stage_timings: [{ stage: 'measurement', elapsed_ms: 400 }],
    events: [],
  },
  approval_requirement: {
    required: false,
    approval_id: null,
    recommendation_id: null,
    preview_hash: null,
  },
  recommendation: null,
  pending_interrupt: null as null | {
    interrupt_id: string
    sequence: number
    kind: string
    status: string
    analysis_signature: string
    missing_fields: string[]
    approval_id: string | null
    target_type: string | null
    target_id: string | null
    preview_hash: string | null
    expires_at: string | null
  },
  message: 'Synthetic run completed with bound facts.',
  missing_fields: [] as string[],
  unsupported_reason: null as string | null,
  unsupported_items: [] as string[],
  error_code: null as string | null,
  started_at: recordedAt,
  completed_at: recordedAt as string | null,
}
export function interruptedRun(kind: 'clarification' | 'approval') {
  const run = structuredClone(runFixture)
  run.terminal_state =
    kind === 'approval' ? 'approval_required' : 'needs_clarification'
  run.stage = 'interrupted'
  run.completed_at = null
  run.pending_interrupt = {
    interrupt_id: id(1801),
    sequence: 4,
    kind,
    status: 'pending',
    analysis_signature: signature,
    missing_fields:
      kind === 'clarification'
        ? [
            'context.carbon_measurement_id_or_material_scope_or_activity_record_ids',
          ]
        : [],
    approval_id: kind === 'approval' ? id(301) : null,
    target_type: kind === 'approval' ? 'procurement_recommendation' : null,
    target_id: kind === 'approval' ? id(401) : null,
    preview_hash: kind === 'approval' ? signature : null,
    expires_at: kind === 'approval' ? '2099-10-02T12:00:00Z' : null,
  }
  if (kind === 'clarification') run.context.carbon_measurement_id = null
  return run
}
export const assumptionsFixture = {
  method: 'synthetic.token_proxy',
  version: '1',
  energy_wh_per_1k_tokens: '0.1',
  grid_intensity_gco2e_per_kwh: '400',
  caveat: 'Test-only uncalibrated token proxy.',
  functional_unit: 'Selected agent executions, excluding human review time',
  region_basis: 'Provider region is not measured',
  omitted_footprint:
    'Embodied hardware, network, storage, and non-model compute',
  uncertainty: 'No measured provider-energy uncertainty interval',
  benefit_basis:
    'Approved projected recommendations; not measured realized savings',
}
export const sustainabilityFixture = {
  company_id: id(1),
  from_time: null,
  to_time: null,
  run_count: 1,
  completed_run_count: 1,
  interrupted_run_count: 0,
  provider_call_count: 1,
  tool_call_count: 3,
  input_tokens: 180,
  output_tokens: 24,
  cached_input_tokens: 0,
  retry_count: 0,
  cache_hits: 1,
  latency_ms: 600,
  estimated_energy_wh: '0.1234567890001',
  estimated_co2e_g: '0.01234567890001',
  proxy_coverage_runs: 1,
  assumptions: assumptionsFixture,
  external_api_calls: 1,
  business_benefit_kgco2e: '0',
  benefit_to_footprint_ratio: null,
  benefit_facts: [],
  assumption_coverage_runs: 1,
  assumption_sets: [assumptionsFixture],
}
export const approvalFixture = {
  id: id(301),
  company_id: id(1),
  status: 'pending',
  target_type: 'procurement_recommendation',
  target_id: id(401),
  preview_hash: signature,
  analysis_signature: signature,
  expired: false,
  preview_current: true,
  decided_by: null as string | null,
  decided_at: null as string | null,
  ledger_event_id: null as string | null,
}
export function streamFixture(name = 'run.completed') {
  const payload = {
    run_id: runId,
    sequence: 4,
    occurred_at: recordedAt,
    step_type: 'finalize',
    status: 'completed',
    graph_name: 'measurement',
    node_name: 'calculate',
    tool_name: 'calculate_emissions',
    input_tokens: 180,
    output_tokens: 24,
    retry_count: 0,
    latency_ms: 400,
    provider: 'gemini',
    cache_hit: true,
  }
  return `id: 4\nevent: ${name}\ndata: ${JSON.stringify(payload)}\n\n`
}
export async function mockAgents(
  page: Page,
  override?: (route: Route, url: URL) => Promise<boolean | void>,
) {
  const calls: { path: string; method: string; body: unknown; url: URL }[] = []
  await page.route('**/api/**', async (route) => {
    const request = route.request(),
      url = new URL(request.url()),
      path = url.pathname
    calls.push({
      path,
      method: request.method(),
      body: request.postDataJSON(),
      url,
    })
    if (override && (await override(route, url))) return
    if (path === '/api/auth/session') {
      await route.fulfill({
        json: {
          company_id: id(1),
          actor_id: actorId,
          role: 'sustainability_analyst',
        },
      })
      return
    }
    if (path === `/api/runs/${runId}`) {
      await route.fulfill({ json: runFixture })
      return
    }
    if (path === `/api/runs/${runId}/events`) {
      await route.fulfill({
        contentType: 'text/event-stream',
        body: streamFixture(),
      })
      return
    }
    if (path === '/api/metrics/agent-sustainability') {
      await route.fulfill({ json: sustainabilityFixture })
      return
    }
    if (path === '/api/agent/requests') {
      await route.fulfill({
        status: 202,
        json: {
          run_id: runId,
          trace_id: 'synthetic-agent-trace',
          terminal_state: 'running',
        },
      })
      return
    }
    if (path === `/api/approvals/${id(301)}`) {
      await route.fulfill({ json: approvalFixture })
      return
    }
    if (path === '/api/procurement/products') {
      await route.fulfill({
        json: {
          items: [
            {
              id: id(1501),
              name: 'Recycled aluminium (synthetic)',
              material_code: 'AL-REC',
              supplier_name: 'Synthetic supplier',
            },
          ],
          total: 1,
          limit: 25,
          offset: 0,
        },
      })
      return
    }
    const body = fixtureFor(url)
    if (body === undefined)
      throw new Error(`Unexpected API request: ${request.method()} ${path}`)
    await route.fulfill({ json: body })
  })
  return calls
}
