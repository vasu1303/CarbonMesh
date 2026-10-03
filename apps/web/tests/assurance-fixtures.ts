import type { Page, Route } from '@playwright/test'
import type { z } from 'zod'
import type {
  draftSchema,
  evidencePackSchema,
  standardsSchema,
  validationSchema,
} from '../src/features/assurance/schemas'
import measurements from './fixtures/measurements.synthetic.json' with { type: 'json' }
import { id, mockDashboard } from './dashboard-fixtures'

// Synthetic test contracts only. No feature code imports browser fixtures.
const time = '2026-09-30T12:00:00Z'
const hash = 'a'.repeat(64)
export const assuranceMeasurement = measurements.records[0].summary
export const assuranceStandards: z.infer<typeof standardsSchema> = {
  total: 1,
  limit: 25,
  offset: 0,
  items: [
    {
      id: id(6100),
      company_id: id(1),
      source_document_id: id(6101),
      code: 'SYNTHETIC-SCOPE2',
      version: '1.0',
      name: 'Synthetic disclosure standard',
      jurisdiction: null,
      description: 'Synthetic browser evidence mapping.',
      effective_from: '2026-01-01',
      effective_to: null,
      is_active: true,
      created_at: time,
      updated_at: time,
      template: {},
      requirements: [
        {
          id: id(6102),
          standard_id: id(6100),
          metric_definition_id: assuranceMeasurement.metric_definition_id,
          requirement_code: 'S2-TOTAL',
          title: 'Reported emissions',
          description: 'A verified ledger fact.',
          sequence: 1,
          claim_template: 'Emissions were {fact_emissions}.',
          evidence_rules: {},
          minimum_confidence: '0.8',
          is_required: true,
          is_active: true,
        },
        {
          id: id(6103),
          standard_id: id(6100),
          metric_definition_id: assuranceMeasurement.metric_definition_id,
          requirement_code: 'S2-REDUCTION',
          title: 'Prior-period reduction',
          description: 'A comparable prior-period fact.',
          sequence: 2,
          claim_template: 'Emissions decreased.',
          evidence_rules: { comparable_prior_period_required: true },
          minimum_confidence: '0.8',
          is_required: false,
          is_active: true,
        },
      ],
    },
  ],
}
const evidence = {
  id: id(6200),
  source_document_id: id(6201),
  data_source_id: id(6202),
  source_filename: 'assurance-synthetic-evidence.txt',
  source_document_checksum: hash,
  evidence_type: 'document_chunk',
  locator: 'paragraph:1',
  checksum: hash,
  metadata: { synthetic: true },
  embedding_model: null,
  embedded_at: null,
  similarity: null,
}
export const assuranceDraft: z.infer<typeof draftSchema> = {
  id: id(6300),
  company_id: id(1),
  standard: assuranceStandards.items[0],
  site_id: id(2),
  reporting_period_id: id(3),
  measurement_id: assuranceMeasurement.id,
  agent_run_id: null,
  ledger_event_id: id(6301),
  version: 1,
  title: 'Synthetic disclosure review',
  narrative_template: '{fact_emissions}',
  rendered_text: 'Recorded emissions: 123.456789012 kgCO2e.',
  context_hash: hash,
  payload_hash: hash,
  status: 'blocked',
  validation_summary: { terminal_state: 'unsupported', state: 'validated' },
  invalidated_at: null,
  created_at: time,
  updated_at: time,
  approval: null,
  claims: [
    {
      id: id(6302),
      disclosure_draft_id: id(6300),
      requirement_id: id(6102),
      requirement_code: 'S2-TOTAL',
      fact_binding_id: id(6303),
      ledger_event_id: id(6304),
      sequence: 1,
      claim_type: 'numeric',
      claim_template: 'Recorded emissions: {fact_emissions}.',
      rendered_text: 'Recorded emissions: 123.456789012 kgCO2e.',
      support_status: 'supported',
      confidence: '0.965',
      validation_details: {},
      validated_at: time,
      created_at: time,
      updated_at: time,
      citations: [
        {
          id: id(6305),
          disclosure_claim_id: id(6302),
          ledger_event_id: id(6304),
          evidence_item_id: evidence.id,
          locator: evidence.locator,
          validation_status: 'valid',
          validation_details: {},
          evidence,
          created_at: time,
        },
      ],
    },
    {
      id: id(6310),
      disclosure_draft_id: id(6300),
      requirement_id: id(6103),
      requirement_code: 'S2-REDUCTION',
      fact_binding_id: null,
      ledger_event_id: null,
      sequence: 2,
      claim_type: 'numeric',
      claim_template: 'Emissions decreased.',
      rendered_text: null,
      support_status: 'unsupported',
      confidence: '0',
      validation_details: { reason: 'comparable_prior_period_missing' },
      validated_at: time,
      created_at: time,
      updated_at: time,
      citations: [],
    },
  ],
  gaps: [
    {
      id: id(6320),
      disclosure_draft_id: id(6300),
      disclosure_claim_id: id(6310),
      requirement_id: id(6103),
      code: 'COMPARABLE_PRIOR_PERIOD_MISSING',
      severity: 'error',
      status: 'open',
      message: 'A comparable prior-period fact is unavailable.',
      details: {},
      resolved_at: null,
      created_at: time,
      updated_at: time,
    },
  ],
}
export const assuranceValidation: z.infer<typeof validationSchema> = {
  draft: assuranceDraft,
  terminal_state: 'unsupported',
  supported_claims: 1,
  partially_supported_claims: 0,
  unsupported_claims: 1,
  open_gaps: 1,
  idempotent: false,
}
export const assurancePack: z.infer<typeof evidencePackSchema> = {
  schema_version: '1.0',
  generated_at: time,
  company_id: id(1),
  draft_id: assuranceDraft.id,
  standard_code: assuranceStandards.items[0].code,
  standard_version: '1.0',
  context_hash: hash,
  payload_hash: hash,
  claims: assuranceDraft.claims,
  gaps: assuranceDraft.gaps,
  fact_bindings: [],
  evidence: [evidence],
  disclaimer: 'POC draft; not an assurance opinion or filing.',
}
export async function mockAssurance(
  page: Page,
  override?: (route: Route, url: URL) => Promise<boolean | void>,
) {
  return mockDashboard(page, async (route, url) => {
    if (override && (await override(route, url))) return true
    const path = url.pathname
    let json: unknown
    if (path === '/api/assurance/standards') json = assuranceStandards
    if (path === '/api/measurements')
      json = { items: [assuranceMeasurement], total: 1, limit: 25, offset: 0 }
    if (
      path === '/api/assurance/drafts' ||
      path === `/api/assurance/drafts/${assuranceDraft.id}`
    )
      json = assuranceDraft
    if (path === `/api/assurance/drafts/${assuranceDraft.id}/validate`)
      json = assuranceValidation
    if (path === `/api/assurance/drafts/${assuranceDraft.id}/evidence-pack`)
      json = assurancePack
    if (json === undefined) return
    await route.fulfill({ json })
    return true
  })
}
