import { z } from 'zod'

const uuid = z.uuid()
const timestamp = z.iso.datetime({ offset: true })
const decimal = z.string().regex(/^-?\d+(\.\d+)?([eE][+-]?\d+)?$/)
const nonnegative = decimal.refine(
  (value) => Number(value) >= 0 && Number.isFinite(Number(value)),
)
const confidence = nonnegative.refine((value) => Number(value) <= 1)
const hash = z.string().regex(/^[0-9a-f]{64}$/)
const evidenceSchema = z.object({
  id: uuid,
  source_document_id: uuid,
  data_source_id: uuid,
  source_document_filename: z.string(),
  source_document_checksum: hash,
  evidence_type: z.string(),
  locator: z.string(),
  checksum: hash,
})

export const measurementStatusSchema = z.enum([
  'draft',
  'verified',
  'superseded',
  'unsupported',
])
export const measurementSummarySchema = z.object({
  id: uuid,
  company_id: uuid,
  site_id: uuid,
  site_name: z.string(),
  reporting_period_id: uuid,
  reporting_period_name: z.string(),
  metric_definition_id: uuid,
  metric_key: z.string(),
  metric_version: z.string(),
  category: z.string(),
  value_kgco2e: nonnegative,
  unit: z.literal('kgCO2e'),
  confidence,
  status: measurementStatusSchema,
  ledger_event_id: uuid.nullable(),
  output_hash: hash,
  verified_at: timestamp.nullable(),
  created_at: timestamp,
})
export const measurementListSchema = z.object({
  items: z.array(measurementSummarySchema),
  total: z.number().int().nonnegative(),
  limit: z.number().int().min(1).max(100),
  offset: z.number().int().nonnegative(),
})

export const measurementDetailSchema = measurementSummarySchema
  .omit({ ledger_event_id: true })
  .extend({
    formula: z.string(),
    confidence_breakdown: z.union([
      z.object({
        version: z.never().optional(),
        source_quality: confidence,
        factor_specificity: confidence,
        factor_recency: confidence,
        record_completeness: confidence,
        source_quality_weight: confidence,
        factor_specificity_weight: confidence,
        factor_recency_weight: confidence,
        record_completeness_weight: confidence,
        overall: confidence,
      }),
      z.object({
        version: z.literal('2.0.0'),
        source_quality: confidence,
        method_fit: confidence,
        temporal_match: confidence,
        completeness: confidence,
        weights: z.object({
          source_quality: confidence,
          method_fit: confidence,
          temporal_match: confidence,
          completeness: confidence,
        }),
        overall: confidence,
      }),
    ]),
    calculation_run: z.object({
      id: uuid,
      method_definition_id: uuid,
      method_key: z.string(),
      method_version: z.string(),
      code_version: z.string(),
      method_hash: hash.nullable().optional(),
      code_hash: hash.nullable().optional(),
      rounding_policy: z.string(),
      input_hash: hash,
      output_hash: hash,
      status: z.enum(['pending', 'running', 'completed', 'failed']),
      started_at: timestamp.nullable(),
      completed_at: timestamp.nullable(),
    }),
    inputs: z.array(
      z.object({
        activity_record_id: uuid,
        raw_activity_record_id: uuid,
        data_source_id: uuid,
        source_document_id: uuid,
        source_row_key: z.string(),
        source_row_number: z.number().int().nullable(),
        raw_checksum: hash,
        supplier_product_id: uuid.nullable(),
        product_code: z.string().nullable(),
        material_code: z.string(),
        activity_date: z.iso.date().nullable(),
        source_quantity: nonnegative,
        source_unit: z.string(),
        normalized_quantity_kg: nonnegative.nullable().optional(),
        normalized_quantity_kwh: nonnegative.nullable().optional(),
        interval_start: timestamp.nullable().optional(),
        record_completeness: confidence,
      }),
    ),
    factors: z.array(
      z.object({
        id: uuid,
        factor_code: z.string(),
        version: z.string(),
        name: z.string(),
        material_code: z.string().nullable(),
        product_code: z.string().nullable(),
        geography: z.string(),
        factor_value: nonnegative,
        numerator_unit: z.string(),
        denominator_unit: z.string(),
        normalized_factor_kgco2e_per_kg: nonnegative,
        effective_from: z.iso.date(),
        effective_to: z.iso.date().nullable(),
        evidence: evidenceSchema,
      }),
    ),
    calculations: z.array(
      z.object({
        id: uuid,
        activity_record_id: uuid,
        emission_factor_id: uuid.nullable(),
        grid_intensity_point_id: uuid.nullable().optional(),
        normalized_quantity_kg: nonnegative.nullable().optional(),
        factor_kgco2e_per_kg: nonnegative.nullable().optional(),
        normalized_quantity_kwh: nonnegative.nullable().optional(),
        intensity_gco2e_per_kwh: nonnegative.nullable().optional(),
        interval_start: timestamp.nullable().optional(),
        emissions_kgco2e: nonnegative,
        formula: z.string(),
        output_hash: hash,
      }),
    ),
    grid_points: z
      .array(
        z.object({
          id: uuid,
          provider: z.string(),
          zone: z.string(),
          observed_at: timestamp,
          temporal_granularity: z.string(),
          intensity_gco2e_per_kwh: nonnegative,
          is_estimated: z.boolean(),
          method_version: z.string(),
          point_hash: hash,
          evidence: evidenceSchema,
        }),
      )
      .default([]),
    coverage: z
      .object({
        interval_start: timestamp,
        interval_end: timestamp,
        observed_hours: z.number().int().positive(),
        reporting_period_hours: z.number().int().positive(),
        reporting_period_fraction: confidence,
        full_reporting_period: z.boolean(),
      })
      .nullable()
      .optional(),
    baseline: z
      .object({
        baseline_id: uuid,
        name: z.string(),
        value_kgco2e: nonnegative,
        unit: z.string(),
        variance_kgco2e: decimal,
        variance_pct: decimal.nullable(),
        variance_alert_id: uuid.nullable(),
      })
      .nullable(),
    facts: z.object({
      fact_id: uuid,
      ledger_event_id: uuid.nullable(),
      output_hash: hash,
      audit_log_id: uuid.nullable(),
    }),
  })

export const measurementLineageSchema = z.object({
  measurement_id: uuid,
  root_event_id: uuid.nullable(),
  truncated: z.boolean(),
  nodes: z.array(
    z.object({
      id: z.string(),
      node_type: z.enum(['ledger_event', 'evidence']),
      label: z.string(),
      entity_type: z.string().nullable().optional(),
      entity_id: uuid.nullable().optional(),
      event_type: z.string().nullable().optional(),
      payload: z.record(z.string(), z.unknown()).optional(),
      payload_hash: z.string().nullable().optional(),
      created_at: timestamp.nullable().optional(),
      metadata: z.record(z.string(), z.unknown()).optional(),
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

export const measurementResultSchema = measurementDetailSchema.extend({
  terminal_state: z.literal('completed'),
  trace_id: z.string(),
  idempotent: z.boolean(),
})

export const measurementBreakdownSchema = z.object({
  measurement_id: uuid,
  metric_key: z.string(),
  status: measurementStatusSchema,
  unit: z.literal('kgCO2e'),
  total_kgco2e: nonnegative,
  facts: measurementDetailSchema.shape.facts,
  items: z.array(
    z.object({
      calculation_id: uuid,
      activity_record_id: uuid,
      raw_activity_record_id: uuid,
      source_document_id: uuid,
      interval_start: timestamp.nullable(),
      activity_date: z.iso.date().nullable(),
      material_code: z.string(),
      quantity: nonnegative,
      quantity_unit: z.enum(['kg', 'kWh']),
      emissions_kgco2e: nonnegative,
      emission_factor_id: uuid.nullable(),
      grid_intensity_point_id: uuid.nullable(),
      output_hash: hash,
    }),
  ),
})

export const metricsSchema = z.object({
  company_id: uuid,
  count: z.number().int().nonnegative(),
  items: z.array(
    z.object({
      id: uuid,
      key: z.string(),
      version: z.string(),
      name: z.string(),
      canonical_unit: z.string(),
    }),
  ),
})

export const measurementFiltersSchema = z.object({
  status: z.union([measurementStatusSchema, z.literal('all')]).catch('all'),
  category: z.string().trim().max(150).catch(''),
  limit: z.coerce
    .number()
    .refine((value) => [5, 10, 25, 50].includes(value))
    .catch(10),
  offset: z.coerce.number().int().min(0).max(1_000_000).catch(0),
  view: z.enum(['records', 'chart']).catch('records'),
})

export type MeasurementSummary = z.infer<typeof measurementSummarySchema>
export type MeasurementDetail = z.infer<typeof measurementDetailSchema>
export type MeasurementLineage = z.infer<typeof measurementLineageSchema>
export type MeasurementStatus = z.infer<typeof measurementStatusSchema>
export type MeasurementFilters = z.infer<typeof measurementFiltersSchema>
export type MeasurementBreakdown = z.infer<typeof measurementBreakdownSchema>
