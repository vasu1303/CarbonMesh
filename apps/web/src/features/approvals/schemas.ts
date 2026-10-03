import { z } from 'zod'
import {
  assessmentSchema,
  decimal,
  evidenceSchema,
  factSchema,
  hash,
  impactSchema,
  record,
  timestamp,
} from '@/features/procurement/scenario-schemas'
import { productSchema } from '@/features/procurement/schemas'

export const approvalStatus = z.enum([
  'pending',
  'approved',
  'rejected',
  'invalidated',
  'expired',
])
export const approvalSchema = z.object({
  id: z.uuid(),
  company_id: z.uuid(),
  target_type: z.string(),
  target_id: z.uuid(),
  recommendation_id: z.uuid().nullable().optional(),
  context_hash: hash.nullable().optional(),
  idempotency_key: z.string().min(1),
  policy_definition_id: z.uuid().nullable().optional(),
  status: approvalStatus,
  preview_hash: hash,
  analysis_signature: hash,
  expires_at: timestamp,
  created_at: timestamp,
  requested_by: z.uuid(),
  requester_name: z.string(),
  decided_by: z.uuid().nullable().optional(),
  decider_name: z.string().nullable().optional(),
  decided_at: timestamp.nullable().optional(),
  decision_note: z.string().nullable().optional(),
  ledger_event_id: z.uuid().nullable().optional(),
  recommended_product_id: z.uuid().nullable().optional(),
  recommended_product_name: z.string().nullable().optional(),
  supplier_name: z.string().nullable().optional(),
  projected_footprint_kgco2e: decimal.nullable().optional(),
  avoided_kgco2e: decimal.nullable().optional(),
  reduction_pct: decimal.nullable().optional(),
  cost_delta_pct: decimal.nullable().optional(),
  lead_time_delta_days: z.number().int().nullable().optional(),
  expired: z.boolean(),
  preview_current: z.boolean(),
})
export const approvalDetailSchema = approvalSchema.extend({
  preview_payload: record,
})
export const approvalListSchema = z.object({
  items: z.array(approvalSchema),
  total: z.number().int().nonnegative(),
  limit: z.number().int().min(1).max(100),
  offset: z.number().int().nonnegative(),
})
export const decisionSchema = z.object({
  approval_id: z.uuid(),
  target_type: z.string(),
  target_id: z.uuid(),
  status: z.enum(['approved', 'rejected']),
  preview_hash: hash,
  analysis_signature: hash,
  decided_by: z.uuid(),
  decided_at: timestamp,
  decision_note: z.string().nullable(),
  ledger_event_id: z.uuid(),
  idempotent_replay: z.boolean(),
})
export const approvalFiltersSchema = z.object({
  status: z.union([approvalStatus, z.literal('all')]).default('pending'),
  offset: z.coerce
    .number()
    .int()
    .nonnegative()
    .max(Number.MAX_SAFE_INTEGER)
    .default(0),
  limit: z.coerce.number().int().min(1).max(100).default(10),
  approval: z.uuid().optional(),
})

export const procurementPreviewSchema = z.object({
  company_id: z.uuid(),
  scenario_id: z.uuid(),
  baseline_product_id: z.uuid(),
  recommended_product_id: z.uuid(),
  supplier_score_id: z.uuid(),
  analysis_signature: hash,
  ...impactSchema.shape,
  rationale_template: z.string(),
  impact: z.object({
    source_state_hash: hash,
    baseline_footprint_kgco2e: decimal,
    quantity: decimal,
    quantity_unit: z.string(),
    score: z.object({
      id: z.uuid(),
      total: decimal,
      method_id: z.uuid(),
      method_key: z.string(),
      method_version: z.string(),
    }),
    review: z.object({
      baseline_product: productSchema,
      recommended_product: productSchema,
      supplier_score: assessmentSchema,
      evidence: z.array(evidenceSchema),
      fact_bindings: z.array(factSchema),
    }),
  }),
})
const citationSchema = z.object({
  id: z.uuid(),
  ledger_event_id: z.uuid().nullable(),
  evidence_item_id: z.uuid().nullable(),
  locator: z.string().nullable(),
  validation_status: z.string(),
  evidence: record.nullable().optional(),
})
export const disclosurePreviewSchema = z.object({
  target_type: z.literal('disclosure_draft'),
  target_id: z.uuid(),
  company_id: z.uuid(),
  analysis_signature: hash,
  context_hash: hash,
  standard: z.object({ id: z.uuid(), code: z.string(), version: z.string() }),
  site_id: z.uuid(),
  reporting_period_id: z.uuid(),
  measurement_id: z.uuid(),
  draft_version: z.number().int(),
  title: z.string(),
  rendered_text: z.string().nullable(),
  claims: z.array(
    z.object({
      id: z.uuid(),
      sequence: z.number().int(),
      requirement_code: z.string().nullable().optional(),
      claim_type: z.string(),
      rendered_text: z.string().nullable(),
      claim_template: z.string(),
      support_status: z.string(),
      confidence: decimal,
      ledger_event_id: z.uuid().nullable(),
      citations: z.array(citationSchema),
    }),
  ),
  gaps: z.array(
    z.object({
      id: z.uuid(),
      code: z.string(),
      severity: z.string(),
      status: z.string(),
      message: z.string(),
    }),
  ),
  fact_bindings: z.array(factSchema),
  validation_method: z.string(),
  retrieval_method: z.string(),
  approval_eligible: z.boolean(),
  disclaimer: z.string(),
})
export const dispatchPreviewSchema = z.object({
  target_type: z.literal('dispatch_recommendation'),
  target_id: z.uuid(),
  scenario_id: z.uuid(),
  recommended_start: timestamp,
  recommended_end: timestamp,
  baseline_start: timestamp,
  baseline_end: timestamp,
  expected_emissions_kgco2e: decimal,
  baseline_emissions_kgco2e: decimal,
  avoided_kgco2e: decimal,
  reduction_pct: decimal,
  analysis_signature: hash,
  context_hash: hash,
  load_snapshot: record,
  method_snapshot: record,
  policy_snapshot: record.nullable(),
  constraint_snapshot: record,
  frozen_constraints: record,
  forecast_source_document_id: z.uuid(),
  forecast_source_document_checksum: hash,
  forecast_points: z.array(
    z.object({
      id: z.uuid(),
      forecast_for: timestamp,
      intensity_gco2e_per_kwh: decimal,
      point_hash: hash,
      evidence_item_id: z.uuid(),
      evidence_checksum: hash,
      source_document_id: z.uuid(),
      source_document_checksum: hash,
      forecast_ledger_event_id: z.uuid(),
      forecast_ledger_event_hash: hash,
    }),
  ),
  method_id: z.uuid(),
  method_version: z.string(),
  method_code_version: z.string(),
  input_hash: hash,
  output_hash: hash,
  advisory_only: z.literal(true),
  actuation_authorized: z.literal(false),
})
export type Approval = z.infer<typeof approvalSchema>
export type ApprovalDetail = z.infer<typeof approvalDetailSchema>

export function supportedPreview(item: ApprovalDetail) {
  if (item.target_type === 'procurement_recommendation') {
    const result = procurementPreviewSchema.safeParse(item.preview_payload)
    return (
      result.success &&
      result.data.company_id === item.company_id &&
      result.data.analysis_signature === item.analysis_signature
    )
  }
  if (item.target_type === 'disclosure_draft') {
    const result = disclosurePreviewSchema.safeParse(item.preview_payload)
    return (
      result.success &&
      result.data.target_id === item.target_id &&
      result.data.company_id === item.company_id &&
      result.data.analysis_signature === item.analysis_signature &&
      result.data.context_hash === item.context_hash &&
      result.data.approval_eligible
    )
  }
  if (item.target_type === 'dispatch_recommendation') {
    const result = dispatchPreviewSchema.safeParse(item.preview_payload)
    return (
      result.success &&
      result.data.target_id === item.target_id &&
      result.data.analysis_signature === item.analysis_signature &&
      result.data.context_hash === item.context_hash
    )
  }
  return false
}
