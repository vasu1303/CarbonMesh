import { z } from 'zod'
import { productSchema } from './schemas'

export const decimal = z.string().regex(/^-?\d+(\.\d+)?([eE][+-]?\d+)?$/)
export const timestamp = z.iso.datetime({ offset: true })
export const hash = z.string().regex(/^[0-9a-f]{64}$/)
export const record = z.record(z.string(), z.unknown())
export const weightsSchema = z.object({
  carbon: decimal,
  evidence: decimal,
  circularity: decimal,
  operational_fit: decimal,
})
export const impactSchema = z.object({
  projected_footprint_kgco2e: decimal,
  avoided_kgco2e: decimal,
  reduction_pct: decimal,
  cost_delta_pct: decimal,
  lead_time_delta_days: z.number().int(),
})
export const assessmentSchema = z.object({
  score_id: z.uuid(),
  product: productSchema,
  scores: weightsSchema.extend({ total: decimal }),
  feasible: z.boolean(),
  infeasibility_reasons: z.array(
    z.object({
      code: z.string(),
      message: z.string(),
      actual: z
        .union([z.string(), z.number(), z.boolean()])
        .nullable()
        .optional(),
      required: z
        .union([z.string(), z.number(), z.boolean()])
        .nullable()
        .optional(),
    }),
  ),
  rank: z.number().int().nullable(),
  impact: impactSchema,
})
export const methodSchema = z.object({
  id: z.uuid(),
  key: z.string(),
  version: z.string(),
  code_version: z.string(),
  weights: weightsSchema,
})
export const previewSummarySchema = z.object({
  id: z.uuid(),
  status: z.string(),
  preview_hash: hash,
  analysis_signature: hash,
  expires_at: timestamp,
})
export const factSchema = z.object({
  id: z.uuid().nullable().optional(),
  placeholder: z.string(),
  display_value: z.string(),
  unit: z.string().nullable(),
  ledger_event_id: z.uuid().nullable().optional(),
  evidence_item_id: z.uuid().nullable().optional(),
})
export const evidenceSchema = z.object({
  id: z.uuid(),
  evidence_type: z.string(),
  locator: z.string(),
  checksum: z.string(),
  metadata: record,
})
export const scenarioSchema = z.object({
  id: z.uuid(),
  company_id: z.uuid(),
  site_id: z.uuid(),
  reporting_period_id: z.uuid(),
  current_product: productSchema,
  carbon_measurement_id: z.uuid(),
  agent_run_id: z.uuid().nullable(),
  method: methodSchema,
  quantity: decimal,
  quantity_unit: z.string(),
  current_unit_cost: decimal,
  currency: z.string(),
  constraints: z.object({
    max_cost_increase_pct: decimal,
    max_lead_time_days: z.number().int(),
    minimum_circularity_score: decimal,
    material: z.object({
      allowed_material_codes: z.array(z.string()),
      excluded_risk_levels: z.array(z.string()),
    }),
  }),
  weights: weightsSchema,
  analysis_signature: hash,
  frozen_context: record,
  status: z.string(),
  terminal_state: z.enum(['completed', 'no_feasible_option']),
  alternatives: z.array(assessmentSchema),
  selected_recommendation: z
    .object({
      id: z.uuid(),
      status: z.string(),
      recommended_product_id: z.uuid(),
      payload_hash: hash,
      analysis_signature: hash,
      impact: impactSchema,
      approval: previewSummarySchema.nullable().optional(),
    })
    .nullable(),
  created_at: timestamp,
  updated_at: timestamp,
})
export const recommendationSchema = z.object({
  id: z.uuid(),
  company_id: z.uuid(),
  scenario_id: z.uuid(),
  status: z.string(),
  baseline_product: productSchema,
  recommended_product: productSchema,
  supplier_score: assessmentSchema,
  evidence: z.array(evidenceSchema),
  ...impactSchema.shape,
  narrative: z.object({
    template_id: z.string(),
    template: z.string(),
    resolved_text: z.string(),
    fact_bindings: z.array(factSchema),
    evidence_links: z.array(z.uuid()),
    unsupported_fragments: z.array(z.string()),
  }),
  analysis_signature: hash,
  payload_hash: hash,
  impact_snapshot: record,
  ledger_event_id: z.uuid().nullable(),
  approval: previewSummarySchema.nullable(),
  invalidated_at: timestamp.nullable(),
  created_at: timestamp,
})
export const scoreResultSchema = z.object({
  scenario_id: z.uuid(),
  method: methodSchema,
  terminal_state: z.enum(['completed', 'no_feasible_option']),
  assessments: z.array(assessmentSchema),
  selected_product_id: z.uuid().nullable(),
  recommendation_id: z.uuid().nullable(),
})

const positive = z
  .string()
  .trim()
  .regex(/^\d+(\.\d+)?$/, 'Enter a positive decimal.')
  .refine((v) => /[1-9]/.test(v), 'Must be greater than zero.')
const percentage = z
  .string()
  .trim()
  .regex(/^\d+(\.\d+)?$/, 'Enter a decimal from 0 to 100.')
  .refine((v) => {
    const [whole, fraction = ''] = v.split('.')
    const integer = whole.replace(/^0+/, '') || '0'
    return integer.length < 3 || (integer === '100' && !/[1-9]/.test(fraction))
  }, 'Must be between 0 and 100.')
const constraintList = z
  .string()
  .trim()
  .transform((v) => (v ? v.split(',').map((s) => s.trim()) : []))
  .pipe(z.array(z.string().min(1).max(100)).max(100))
  .refine((v) => new Set(v).size === v.length, 'Remove duplicate entries.')
export const scenarioFormSchema = z.object({
  current_product_id: z.uuid('Select a current product.'),
  carbon_measurement_id: z.uuid('Select a verified measurement.'),
  method_definition_id: z.uuid('Select a scoring method.'),
  requested_by: z.uuid('Select a requester.'),
  quantity: positive,
  quantity_unit: z.string().trim().min(1).max(50),
  current_unit_cost: z.union([positive, z.literal('')]),
  currency: z
    .string()
    .trim()
    .regex(/^([A-Z]{3})?$/, 'Use an ISO currency code.'),
  max_cost_increase_pct: percentage,
  max_lead_time_days: z
    .string()
    .regex(/^\d+$/, 'Enter whole days.')
    .transform(Number)
    .pipe(z.number().int().nonnegative().max(Number.MAX_SAFE_INTEGER)),
  minimum_circularity_score: percentage,
  allowed_material_codes: constraintList,
  excluded_risk_levels: constraintList
    .transform((v) => v.map((s) => s.toLowerCase()))
    .refine(
      (v) => new Set(v).size === v.length,
      'Remove duplicate risk levels.',
    ),
  approval_expires_at: z
    .string()
    .refine(
      (v) => !v || Number.isFinite(Date.parse(v)),
      'Enter a valid expiry.',
    ),
})
export type Scenario = z.infer<typeof scenarioSchema>
export type Assessment = z.infer<typeof assessmentSchema>
export type Fact = z.infer<typeof factSchema>
