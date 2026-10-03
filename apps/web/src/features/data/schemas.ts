import { z } from 'zod'

export const uuid = z.uuid('Enter a valid UUID.')
export const timestamp = z.iso.datetime({ offset: true })
export const decimal = z
  .string()
  .regex(
    /^\d+(?:\.\d+)?(?:[eE][+-]?\d+)?$/,
    'Expected an exact non-negative decimal string.',
  )
export const count = z.number().int().nonnegative()
export const optionalUuid = z.union([uuid, z.literal('')])
export const actorSchema = z.object({ actor_id: uuid })
const hash = z.string().regex(/^[0-9a-f]{64}$/)

export const issueSchema = z.object({
  id: uuid,
  company_id: uuid,
  raw_activity_record_id: uuid.nullable(),
  activity_record_id: uuid.nullable(),
  import_id: uuid.nullable(),
  row_number: count.nullable(),
  issue_type: z.string(),
  code: z.string(),
  severity: z.enum(['info', 'warning', 'error']),
  field_name: z.string().nullable(),
  message: z.string(),
  status: z.enum(['open', 'resolved', 'waived']),
  details: z.object({
    review: z
      .object({
        status: z.enum(['resolved', 'waived']),
        actor_id: uuid,
        decision_note: z.string(),
      })
      .optional(),
    reviewed_at: timestamp.optional(),
    data_validity_unchanged: z.boolean().optional(),
  }),
  created_at: timestamp,
  updated_at: timestamp,
})
export type QualityIssue = z.infer<typeof issueSchema>
export const issueListSchema = z.object({
  items: z.array(issueSchema),
  total: count,
  limit: count.min(1).max(200),
  offset: count,
})
export const importResultSchema = z.object({
  import_id: uuid,
  data_source_id: uuid,
  source_document_id: uuid.nullable(),
  import_type: z.enum(['activity', 'suppliers']),
  status: z.enum([
    'processing',
    'completed',
    'completed_with_errors',
    'failed',
  ]),
  accepted_count: count,
  rejected_count: count,
  issue_count: count,
  returned_issue_count: count,
  issues_truncated: z.boolean(),
  is_synthetic: z.boolean(),
  issues: z.array(issueSchema),
  created_at: timestamp,
  updated_at: timestamp,
})
export const metricsSchema = z.object({
  company_id: uuid,
  count,
  items: z.array(
    z.object({
      id: uuid,
      key: z.string(),
      version: z.string(),
      name: z.string(),
      canonical_unit: z.string(),
      method_version: z.string(),
    }),
  ),
})
export const importFormSchema = z
  .object({
    kind: z.enum(['activity', 'suppliers']),
    actor_id: uuid,
    source_name: z.string().trim().min(1, 'Source name is required.').max(160),
    metric_definition_id: optionalUuid,
    interval_start: z.union([timestamp, z.literal('')]),
    interval_end: z.union([timestamp, z.literal('')]),
    external_reference: z.string().trim().max(255),
    is_synthetic: z.boolean(),
  })
  .superRefine((data, ctx) => {
    if (data.kind !== 'activity') return
    if (!data.metric_definition_id)
      ctx.addIssue({
        code: 'custom',
        path: ['metric_definition_id'],
        message: 'Select a metric or enter its UUID.',
      })
    if (!!data.interval_start !== !!data.interval_end)
      ctx.addIssue({
        code: 'custom',
        path: ['interval_end'],
        message: 'Both interval boundaries are required.',
      })
    for (const key of ['interval_start', 'interval_end'] as const) {
      if (data[key] && Date.parse(data[key]) % 3_600_000 !== 0)
        ctx.addIssue({
          code: 'custom',
          path: [key],
          message: 'Use an exact UTC hour with an explicit offset.',
        })
    }
    if (data.interval_start && data.interval_end) {
      const difference =
        Date.parse(data.interval_end) - Date.parse(data.interval_start)
      if (difference <= 0 || difference > 366 * 86_400_000)
        ctx.addIssue({
          code: 'custom',
          path: ['interval_end'],
          message: 'End must follow start within 366 days.',
        })
    }
  })
export type ImportForm = z.infer<typeof importFormSchema>

export const sourceFormSchema = z.object({
  actor_id: uuid,
  source_name: z.string().trim().min(1).max(200),
  evidence_type: z.string().trim().min(1, 'Evidence type is required.').max(40),
  external_reference: z.string().trim().max(255),
  is_synthetic: z.boolean(),
})
export const sourceUploadSchema = z.object({
  replayed: z.boolean(),
  ingestion_method_id: z.string(),
  extraction_method_id: z.string(),
  chunking_method_id: z.string(),
  embedding_model_id: z.string(),
  decoded_size_bytes: count,
  evidence_count: count,
  source: z.object({
    id: uuid,
    company_id: uuid,
    site_id: uuid.nullable(),
    name: z.string(),
    source_type: z.string(),
    status: z.string(),
    is_synthetic: z.boolean(),
  }),
  document: z.object({
    id: uuid,
    data_source_id: uuid,
    filename: z.string(),
    content_type: z.string(),
    checksum: hash,
    version: count,
    size_bytes: count,
  }),
  evidence: z.array(
    z.object({
      id: uuid,
      source_document_id: uuid,
      evidence_type: z.string(),
      locator: z.string(),
      checksum: hash,
      embedding_model: z.string().nullable(),
      embedded_at: timestamp.nullable(),
    }),
  ),
})
export const sourceIndexSchema = z.object({
  document_id: uuid,
  embedding_model_id: z.string(),
  indexed_count: count,
  replayed: z.boolean(),
})

const boundedDecimal = z
  .string()
  .regex(
    /^\d{1,12}(?:\.\d{1,12})?$/,
    'Use a non-negative decimal with up to 12 decimal places.',
  )
const qualityDecimal = z
  .string()
  .regex(
    /^(?:0(?:\.\d{1,5})?|1(?:\.0{1,5})?)$/,
    'Use a decimal from 0 to 1, with up to 5 decimal places.',
  )
export const factorFormSchema = z
  .object({
    actor_id: uuid,
    metric_definition_id: uuid,
    evidence_item_id: uuid,
    factor_code: z.string().trim().min(1).max(100),
    version: z.string().trim().min(1).max(50),
    name: z.string().trim().min(1).max(255),
    material_code: z.string().trim().min(1).max(100),
    product_code: z.string().trim().max(100),
    geography: z.string().trim().min(2).max(100),
    factor_value: boundedDecimal,
    effective_from: z.iso.date(),
    effective_to: z.union([z.iso.date(), z.literal('')]),
    source_quality: qualityDecimal,
    factor_specificity: qualityDecimal,
    factor_recency: qualityDecimal,
  })
  .refine(
    (data) => !data.effective_to || data.effective_to >= data.effective_from,
    { path: ['effective_to'], message: 'End date cannot precede start date.' },
  )
export type FactorForm = z.infer<typeof factorFormSchema>
export const factorSchema = z.object({
  id: uuid,
  company_id: uuid,
  metric_definition_id: uuid,
  evidence_item_id: uuid,
  factor_code: z.string(),
  version: z.string(),
  name: z.string(),
  material_code: z.string().nullable(),
  product_code: z.string().nullable(),
  geography: z.string(),
  factor_value: decimal,
  numerator_unit: z.string(),
  denominator_unit: z.string(),
  effective_from: z.iso.date(),
  effective_to: z.iso.date().nullable(),
  source_quality: decimal,
  factor_specificity: decimal,
  factor_recency: decimal,
  status: z.enum(['active', 'inactive', 'superseded']),
  created_at: timestamp,
})
export const factorListSchema = z.object({
  items: z.array(factorSchema),
  total: count,
  limit: count.min(1).max(100),
  offset: count,
})
export const factorRegistrationSchema = z.object({
  factor: factorSchema,
  ledger_event_id: uuid,
  replayed: z.boolean(),
})
