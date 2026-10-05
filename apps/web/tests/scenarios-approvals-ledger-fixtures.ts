import type { Page, Route } from '@playwright/test'
import { id, measurement } from './dashboard-fixtures'
import { mockCatalog, products } from './procurement-fixtures'

// Synthetic browser-test data only. These contracts are never runtime fallbacks.
export const ids = {
  scenario: id(6001),
  recommendation: id(6002),
  approval: id(6003),
  actor: id(6004),
  method: id(6005),
  score: id(6006),
  disclosure: id(6007),
  dispatch: id(6008),
  parent: id(6009),
  event: id(6010),
  evidence: id(6011),
}
export const sha = 'a'.repeat(64)
const at = '2026-09-30T12:00:00Z'
const weights = {
  carbon: '0.40',
  evidence: '0.25',
  circularity: '0.20',
  operational_fit: '0.15',
}
const impact = {
  projected_footprint_kgco2e: '4123.123456789012',
  avoided_kgco2e: '876.123400',
  reduction_pct: '17.52',
  cost_delta_pct: '2.5000',
  lead_time_delta_days: -1,
}
export const assessment = {
  score_id: ids.score,
  product: products[1],
  scores: {
    carbon: '90.00',
    evidence: '80.00',
    circularity: '85.00',
    operational_fit: '95.00',
    total: '86.7500',
  },
  feasible: true,
  infeasibility_reasons: [],
  rank: 1,
  impact,
}
const evidence = {
  id: ids.evidence,
  evidence_type: 'product_pcf',
  locator: 'synthetic-product.csv:row:2',
  checksum: sha,
  metadata: { synthetic: true },
}
const fact = {
  id: id(6012),
  placeholder: 'projected_footprint',
  value: impact.projected_footprint_kgco2e,
  display_value: impact.projected_footprint_kgco2e,
  unit: 'kgCO2e',
  ledger_event_id: ids.event,
  evidence_item_id: ids.evidence,
}
const previewSummary = {
  id: ids.approval,
  status: 'pending',
  preview_hash: sha,
  analysis_signature: sha,
  expires_at: '2099-09-30T12:00:00Z',
}
const method = {
  id: ids.method,
  key: 'supplier_scoring',
  version: '1.0.0',
  code_version: 'synthetic-test-v1',
  weights,
}
export const scenario = {
  id: ids.scenario,
  company_id: id(1),
  site_id: id(2),
  reporting_period_id: id(3),
  current_product: products[0],
  carbon_measurement_id: measurement.id,
  agent_run_id: null,
  method,
  quantity: '10000.0000',
  quantity_unit: 'kg',
  current_unit_cost: '3.123456',
  currency: 'USD',
  constraints: {
    max_cost_increase_pct: '5.00',
    max_lead_time_days: 30,
    minimum_circularity_score: '50.00',
    material: {
      allowed_material_codes: ['aluminium'],
      excluded_risk_levels: ['high'],
    },
  },
  weights,
  analysis_signature: sha,
  frozen_context: { actor_id: ids.actor, synthetic: true },
  status: 'pending_approval',
  terminal_state: 'completed',
  alternatives: [
    assessment,
    {
      ...assessment,
      score_id: id(6013),
      product: products[2],
      feasible: false,
      rank: null,
      infeasibility_reasons: [
        {
          code: 'lead_time',
          message: 'Lead time exceeds the hard constraint.',
          actual: 40,
          required: 30,
        },
      ],
    },
  ],
  selected_recommendation: {
    id: ids.recommendation,
    status: 'pending_approval',
    recommended_product_id: products[1].id,
    payload_hash: sha,
    analysis_signature: sha,
    impact,
    approval: previewSummary,
  },
  created_at: at,
  updated_at: at,
}
export const procurementPayload = {
  company_id: id(1),
  scenario_id: ids.scenario,
  baseline_product_id: products[0].id,
  recommended_product_id: products[1].id,
  supplier_score_id: ids.score,
  analysis_signature: sha,
  ...impact,
  rationale_template: '{{projected_footprint}}',
  impact: {
    source_state_hash: sha,
    baseline_footprint_kgco2e: '4999.246856789012',
    quantity: '10000.0000',
    quantity_unit: 'kg',
    score: {
      id: ids.score,
      total: assessment.scores.total,
      method_id: ids.method,
      method_key: method.key,
      method_version: method.version,
    },
    review: {
      baseline_product: products[0],
      recommended_product: products[1],
      supplier_score: assessment,
      evidence: [evidence],
      fact_bindings: [fact],
    },
  },
}
export const recommendation = {
  id: ids.recommendation,
  company_id: id(1),
  scenario_id: ids.scenario,
  status: 'pending_approval',
  baseline_product: products[0],
  recommended_product: products[1],
  supplier_score: assessment,
  evidence: [evidence],
  ...impact,
  narrative: {
    template_id: 'synthetic-v1',
    template: '{{projected_footprint}}',
    resolved_text: 'Verified synthetic procurement recommendation.',
    fact_bindings: [fact],
    evidence_links: [ids.evidence],
    unsupported_fragments: [],
  },
  analysis_signature: sha,
  payload_hash: sha,
  impact_snapshot: procurementPayload.impact,
  ledger_event_id: ids.event,
  approval: previewSummary,
  invalidated_at: null,
  created_at: at,
}
const disclosurePayload = {
  target_type: 'disclosure_draft',
  target_id: ids.disclosure,
  company_id: id(1),
  analysis_signature: sha,
  context_hash: sha,
  standard: { id: id(6014), code: 'GHG-SCOPE2-SYNTHETIC', version: '1.0' },
  site_id: id(2),
  reporting_period_id: id(3),
  measurement_id: measurement.id,
  draft_version: 1,
  title: 'Synthetic disclosure summary',
  rendered_text: 'Supported synthetic disclosure.',
  claims: [
    {
      id: id(6015),
      sequence: 1,
      requirement_code: 'SCOPE2',
      claim_type: 'quantitative',
      rendered_text: 'Supported synthetic electricity claim.',
      claim_template: '{{scope2}}',
      support_status: 'supported',
      confidence: '0.9000',
      ledger_event_id: ids.event,
      citations: [
        {
          id: id(6016),
          ledger_event_id: ids.event,
          evidence_item_id: ids.evidence,
          locator: 'synthetic:row:1',
          validation_status: 'valid',
          evidence: null,
        },
      ],
    },
  ],
  gaps: [],
  fact_bindings: [fact],
  validation_method: 'synthetic-validator-v1',
  retrieval_method: 'synthetic-retrieval-v1',
  approval_eligible: true,
  disclaimer: 'Synthetic POC draft. Not an assurance opinion or filing.',
}
const dispatchPayload = {
  target_type: 'dispatch_recommendation',
  target_id: ids.dispatch,
  scenario_id: id(6017),
  recommended_start: '2099-09-30T10:00:00Z',
  recommended_end: '2099-09-30T12:00:00Z',
  baseline_start: '2099-09-30T08:00:00Z',
  baseline_end: '2099-09-30T10:00:00Z',
  expected_emissions_kgco2e: '100.1250',
  baseline_emissions_kgco2e: '150.1250',
  avoided_kgco2e: '50.0000',
  reduction_pct: '33.30',
  analysis_signature: sha,
  context_hash: sha,
  load_snapshot: {
    id: id(6018),
    name: 'Synthetic flexible load',
    power_kw: '500.0000',
  },
  method_snapshot: { id: ids.method, version: '1.0' },
  policy_snapshot: null,
  constraint_snapshot: { duration_hours: 2, maximum_delay_hours: 4 },
  frozen_constraints: { synthetic: true },
  forecast_source_document_id: id(6019),
  forecast_source_document_checksum: sha,
  forecast_points: [
    {
      id: id(6020),
      forecast_for: '2099-09-30T10:00:00Z',
      intensity_gco2e_per_kwh: '100.1250',
      point_hash: sha,
      evidence_item_id: ids.evidence,
      evidence_checksum: sha,
      source_document_id: id(6019),
      source_document_checksum: sha,
      forecast_ledger_event_id: ids.event,
      forecast_ledger_event_hash: sha,
    },
  ],
  method_id: ids.method,
  method_version: '1.0',
  method_code_version: 'synthetic-v1',
  input_hash: sha,
  output_hash: sha,
  advisory_only: true,
  actuation_authorized: false,
}
export function approvalFixture(kind = 'procurement_recommendation') {
  return {
    id: ids.approval,
    company_id: id(1),
    target_type: kind,
    target_id:
      kind === 'disclosure_draft'
        ? ids.disclosure
        : kind === 'dispatch_recommendation'
          ? ids.dispatch
          : ids.recommendation,
    recommendation_id:
      kind === 'procurement_recommendation' ? ids.recommendation : null,
    context_hash: sha,
    idempotency_key: 'synthetic-preview-idempotency-key',
    policy_definition_id: null,
    status: 'pending',
    preview_hash: sha,
    analysis_signature: sha,
    expires_at: previewSummary.expires_at,
    created_at: at,
    requested_by: id(6021),
    requester_name: 'Synthetic requester',
    decided_by: null as string | null,
    decider_name: null as string | null,
    decided_at: null as string | null,
    decision_note: null as string | null,
    ledger_event_id: null as string | null,
    expired: false,
    preview_current: true,
    preview_payload:
      kind === 'disclosure_draft'
        ? disclosurePayload
        : kind === 'dispatch_recommendation'
          ? dispatchPayload
          : procurementPayload,
  }
}
export const ledgerEvent = {
  id: ids.event,
  company_id: id(1),
  event_type: 'measurement.verified',
  entity_type: 'measurement',
  entity_id: measurement.id,
  payload_hash: sha,
  analysis_signature: sha,
  created_by: ids.actor,
  supersedes_event_id: null,
  created_at: at,
}
const parent = {
  ...ledgerEvent,
  id: ids.parent,
  event_type: 'calculation.completed',
}
export const ledgerDetail = {
  ...ledgerEvent,
  payload: {
    value_kgco2e: '12500.1250',
    unit: 'kgCO2e',
    source_document_id: id(6019),
    synthetic: true,
  },
  evidence: [
    {
      ...evidence,
      source_document_id: id(6019),
      source_filename: 'synthetic-electricity.csv',
      source_document_checksum: sha,
      data_source_id: id(6022),
      data_source_name: 'Synthetic source',
      is_synthetic: true,
      created_at: at,
      relevance: 'supports measurement',
    },
  ],
  parents: [
    {
      edge_id: id(6023),
      relationship_type: 'calculated_from',
      metadata: {},
      created_at: at,
      event: parent,
    },
  ],
  children: [],
  evidence_truncated: false,
  parents_truncated: false,
  children_truncated: false,
}
export const audit = {
  company_id: id(1),
  entity_type: 'measurement',
  entity_id: measurement.id,
  timeline: [
    {
      id: ids.event,
      timestamp: at,
      source: 'ledger',
      action: ledgerEvent.event_type,
      entity_type: 'measurement',
      entity_id: measurement.id,
      actor_id: ids.actor,
      trace_id: null,
      payload_hash: sha,
      details: ledgerDetail.payload,
      direct: true,
    },
  ],
  lineage: {
    events: [parent, ledgerEvent],
    edges: [
      {
        id: id(6023),
        parent_event_id: ids.parent,
        child_event_id: ids.event,
        relationship_type: 'calculated_from',
        metadata: {},
      },
    ],
    truncated: true,
  },
}

export async function mockScenariosApprovalsLedger(
  page: Page,
  override?: (route: Route, url: URL) => Promise<boolean | void>,
) {
  const stored = approvalFixture()
  return mockCatalog(page, async (route, url) => {
    if (override && (await override(route, url))) return true
    const path = url.pathname
    const paged = (items: unknown[]) => {
      const offset = Number(url.searchParams.get('offset') || 0)
      const limit = Number(url.searchParams.get('limit') || 10)
      return {
        items: items.slice(offset, offset + limit),
        total: items.length,
        offset,
        limit,
      }
    }
    let body: unknown
    if (path === '/api/measurements')
      body = paged([
        {
          ...measurement,
          metric_definition_id: id(102),
          site_name: 'Plant B (synthetic)',
          reporting_period_name: 'Q3 2026',
          verified_at: at,
        },
      ])
    else if (
      path === '/api/procurement/scenarios' ||
      path === `/api/procurement/scenarios/${ids.scenario}`
    )
      body = scenario
    else if (
      path === `/api/procurement/scenarios/${ids.scenario}/recommendation`
    )
      body = recommendation
    else if (path === `/api/procurement/scenarios/${ids.scenario}/score`)
      body = {
        scenario_id: ids.scenario,
        method,
        terminal_state: 'completed',
        assessments: scenario.alternatives,
        selected_product_id: products[1].id,
        recommendation_id: ids.recommendation,
      }
    else if (path === '/api/approvals')
      body = paged(
        !url.searchParams.get('status') ||
          url.searchParams.get('status') === stored.status
          ? [stored]
          : [],
      )
    else if (path === `/api/approvals/${ids.approval}`) body = stored
    else if (path === `/api/approvals/${ids.approval}/decision`) {
      const decision = route.request().postDataJSON()
      stored.status = decision.decision === 'approve' ? 'approved' : 'rejected'
      stored.decided_by = decision.actor_id
      stored.decider_name = 'Synthetic approver'
      stored.decided_at = at
      stored.decision_note = decision.decision_note
      stored.ledger_event_id = id(6024)
      body = {
        approval_id: stored.id,
        target_type: stored.target_type,
        target_id: stored.target_id,
        status: stored.status,
        preview_hash: stored.preview_hash,
        analysis_signature: stored.analysis_signature,
        decided_by: stored.decided_by,
        decided_at: at,
        decision_note: stored.decision_note,
        ledger_event_id: stored.ledger_event_id,
        idempotent_replay: false,
      }
    } else if (path === '/api/ledger/events')
      body = paged(
        Array.from({ length: 11 }, (_, i) =>
          i === 0 ? ledgerEvent : { ...ledgerEvent, id: id(6100 + i) },
        ),
      )
    else if (path === `/api/ledger/events/${ids.event}`) body = ledgerDetail
    else if (path === `/api/ledger/events/${ids.parent}`)
      body = { ...ledgerDetail, ...parent, parents: [], children: [] }
    else if (path === `/api/audit/measurement/${measurement.id}`) body = audit
    else if (path.startsWith('/api/audit/'))
      body = {
        ...audit,
        entity_type: path.split('/')[3],
        entity_id: path.split('/')[4],
      }
    else return
    await route.fulfill({ json: body })
    return true
  })
}
