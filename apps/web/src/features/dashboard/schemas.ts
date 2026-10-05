import { z } from 'zod'

const id = z.uuid()
const timestamp = z.iso.datetime({ offset: true })
// Keep API Decimal values as strings; floating point is used only for chart geometry.
const decimal = z.string().regex(/^-?\d+(\.\d+)?([eE][+-]?\d+)?$/)
const count = z.number().int().nonnegative()
export const pageSchema = <T extends z.ZodType>(item: T) =>
  z.object({
    items: z.array(item),
    total: count,
    limit: z.number().int().positive(),
    offset: count,
  })

export const contextSchema = z.object({
  company: z.object({ id, name: z.string(), is_synthetic: z.boolean() }),
  site: z.object({ id, name: z.string(), timezone: z.string() }),
  reporting_period: z.object({
    id,
    name: z.string(),
    start_date: z.iso.date(),
    end_date: z.iso.date(),
  }),
  analysis_signature: z.string(),
})

export const measurementSchema = z.object({
  id,
  company_id: id,
  site_id: id,
  reporting_period_id: id,
  metric_key: z.string(),
  metric_version: z.string(),
  category: z.string(),
  value_kgco2e: decimal,
  unit: z.string(),
  confidence: decimal,
  status: z.enum(['draft', 'verified', 'superseded', 'unsupported']),
  ledger_event_id: id.nullable(),
  output_hash: z.string(),
  created_at: timestamp,
})

export const issueSchema = z.object({
  id,
  code: z.string(),
  severity: z.enum(['info', 'warning', 'error']),
  message: z.string(),
  status: z.enum(['open', 'resolved', 'waived']),
  row_number: z.number().int().nullable(),
  field_name: z.string().nullable(),
  created_at: timestamp,
})

export const approvalSchema = z.object({
  id,
  company_id: id,
  target_type: z.string(),
  target_id: id,
  recommendation_id: id.nullable().optional(),
  requester_name: z.string(),
  status: z.enum(['pending', 'approved', 'rejected', 'invalidated', 'expired']),
  preview_hash: z.string(),
  analysis_signature: z.string(),
  expires_at: timestamp,
  recommended_product_name: z.string().nullable().optional(),
  supplier_name: z.string().nullable().optional(),
  projected_footprint_kgco2e: decimal.nullable().optional(),
  avoided_kgco2e: decimal.nullable().optional(),
  reduction_pct: decimal.nullable().optional(),
  cost_delta_pct: decimal.nullable().optional(),
  lead_time_delta_days: z.number().int().nullable().optional(),
  expired: z.boolean(),
  preview_current: z.boolean(),
  created_at: timestamp,
})

export const ledgerEventSchema = z.object({
  id,
  event_type: z.string(),
  entity_type: z.string(),
  entity_id: id,
  payload_hash: z.string(),
  created_at: timestamp,
})
export const catalogItemSchema = z.object({ id, name: z.string() })

export type Measurement = z.infer<typeof measurementSchema>
export type Approval = z.infer<typeof approvalSchema>
