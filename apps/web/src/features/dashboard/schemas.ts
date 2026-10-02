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

export const measurementDetailSchema = measurementSchema
  .omit({ ledger_event_id: true })
  .extend({
    formula: z.string(),
    calculation_run: z.object({
      id,
      method_key: z.string(),
      method_version: z.string(),
      code_version: z.string(),
      input_hash: z.string(),
      output_hash: z.string(),
      rounding_policy: z.string(),
    }),
    inputs: z.array(
      z.object({
        activity_record_id: id,
        raw_activity_record_id: id,
        source_document_id: id,
        source_row_key: z.string(),
        raw_checksum: z.string(),
        source_quantity: decimal,
        source_unit: z.string(),
      }),
    ),
    factors: z.array(
      z.object({
        id,
        name: z.string(),
        version: z.string(),
        factor_value: decimal,
        numerator_unit: z.string(),
        denominator_unit: z.string(),
        evidence: z.object({
          id,
          source_document_id: id,
          source_document_filename: z.string(),
          locator: z.string(),
          checksum: z.string(),
        }),
      }),
    ),
    facts: z.object({
      fact_id: id,
      ledger_event_id: id.nullable(),
      output_hash: z.string(),
    }),
  })

export const lineageSchema = z.object({
  measurement_id: id,
  root_event_id: id.nullable(),
  truncated: z.boolean(),
  nodes: z.array(
    z.object({
      id: z.string(),
      node_type: z.enum(['ledger_event', 'evidence']),
      label: z.string(),
    }),
  ),
  edges: z.array(
    z.object({
      id: z.string(),
      source: z.string(),
      target: z.string(),
      relationship_type: z.string(),
    }),
  ),
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
  recommendation_id: id,
  status: z.enum(['pending', 'approved', 'rejected']),
  preview_hash: z.string(),
  analysis_signature: z.string(),
  expires_at: timestamp,
  recommended_product_name: z.string(),
  supplier_name: z.string(),
  projected_footprint_kgco2e: decimal,
  avoided_kgco2e: decimal,
  reduction_pct: decimal,
  cost_delta_pct: decimal,
  lead_time_delta_days: z.number().int(),
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
export const ledgerDetailSchema = ledgerEventSchema.extend({
  payload: z.record(z.string(), z.unknown()),
  evidence: z.array(
    z.object({
      id,
      source_document_id: id,
      source_filename: z.string(),
      locator: z.string(),
      checksum: z.string(),
      is_synthetic: z.boolean(),
    }),
  ),
  parents: z.array(
    z.object({
      edge_id: id,
      relationship_type: z.string(),
      event: ledgerEventSchema,
    }),
  ),
  children: z.array(
    z.object({
      edge_id: id,
      relationship_type: z.string(),
      event: ledgerEventSchema,
    }),
  ),
  evidence_truncated: z.boolean(),
  parents_truncated: z.boolean(),
  children_truncated: z.boolean(),
})

export const gridSchema = z.object({
  site_id: id,
  grid_intensity_point_id: id,
  zone: z.string(),
  value: decimal,
  unit: z.string(),
  provider_timestamp: timestamp,
  is_estimated: z.boolean(),
  temporal_granularity: z.string(),
  provenance: z.object({
    provider: z.string(),
    provider_mode: z.enum(['fixture', 'live']),
    synthetic: z.boolean(),
    source_document_id: id,
    evidence_item_id: id,
    response_checksum: z.string(),
    retrieved_at: timestamp,
  }),
})

export const catalogItemSchema = z.object({ id, name: z.string() })

export type Measurement = z.infer<typeof measurementSchema>
export type Approval = z.infer<typeof approvalSchema>
export type GridIntensity = z.infer<typeof gridSchema>
