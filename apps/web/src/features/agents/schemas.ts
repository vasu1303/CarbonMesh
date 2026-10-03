import { z } from 'zod'

export const uuid = z.uuid()
export const timestamp = z.iso.datetime({ offset: true })
export const hash = z.string().regex(/^[0-9a-f]{64}$/)
export const decimal = z
  .string()
  .regex(/^-?\d+(\.\d+)?([eE][+-]?\d+)?$/, 'Enter a decimal value.')
const positive = decimal.refine(
  (v) => Number.isFinite(Number(v)) && Number(v) > 0,
  'Must be positive.',
)
const percentage = decimal.refine(
  (v) => Number(v) >= 0 && Number(v) <= 100,
  'Must be between 0 and 100.',
)
const ids = (max: number) =>
  z
    .array(uuid)
    .max(max)
    .refine(
      (v) => new Set(v).size === v.length,
      'Duplicate IDs are not allowed.',
    )
const strings = (max: number) =>
  z
    .array(z.string().trim().min(1).max(200))
    .max(max)
    .refine(
      (v) => new Set(v).size === v.length,
      'Duplicate values are not allowed.',
    )
const optionalId = uuid.nullish()

export const constraintsSchema = z.object({
  max_cost_increase_pct: percentage.nullish(),
  max_lead_time_days: z.number().int().min(0).max(3650).nullish(),
  minimum_circularity_score: percentage.nullish(),
})
export const dispatchConstraintsSchema = z
  .object({
    window_start: timestamp.nullish(),
    window_end: timestamp.nullish(),
    duration_minutes: z.number().int().min(1).max(1440).nullish(),
    max_delay_minutes: z.number().int().min(0).max(10080).nullish(),
    maximum_power_kw: positive.nullish(),
    blackout_constraint_ids: ids(100).optional(),
  })
  .refine(
    (v) =>
      !v.window_start ||
      !v.window_end ||
      Date.parse(v.window_end) > Date.parse(v.window_start),
    'Window end must follow its start.',
  )
export const freshInputsSchema = z
  .object({
    measurements: z
      .array(
        z.object({
          material_code: z.string().trim().min(1).max(100),
          output_metric_key: z.enum([
            'emissions.scope2.location_based',
            'emissions.scope3.category1',
          ]),
          activity_record_ids: ids(3000).optional(),
          geography: z.string().min(2).max(100).nullish(),
          grid_zone: z.string().min(1).max(100).nullish(),
          grid_method_version: z.string().min(1).max(100).nullish(),
        }),
      )
      .max(2),
    history_start: timestamp.nullish(),
    history_end: timestamp.nullish(),
    history_fixture_variant: z
      .enum(['quality_cases_v1', 'complete_q3_v1'])
      .optional(),
    procurement_quantity: positive.nullish(),
    procurement_quantity_unit: z.literal('kg').optional(),
    procurement_method_id: optionalId,
    dispatch_method_id: optionalId,
    dispatch_baseline_start: timestamp.nullish(),
  })
  .superRefine((v, ctx) => {
    if (!!v.history_start !== !!v.history_end)
      ctx.addIssue({
        code: 'custom',
        message: 'Supply both history boundaries.',
        path: ['history_start'],
      })
    if (v.history_start && v.history_end) {
      const start = Date.parse(v.history_start),
        end = Date.parse(v.history_end)
      if (
        end <= start ||
        end - start > 93 * 86400000 ||
        start % 3600000 ||
        end % 3600000
      )
        ctx.addIssue({
          code: 'custom',
          message:
            'History must align to exact UTC hours and span at most 93 days.',
          path: ['history_end'],
        })
    }
  })
export const agentContextSchema = z.object({
  company_id: uuid,
  actor_id: uuid,
  site_id: optionalId,
  reporting_period_id: optionalId,
  grid_source_mode: z.enum(['live', 'fixture']),
  fresh_inputs: freshInputsSchema.nullish(),
  carbon_measurement_id: optionalId,
  current_product_id: optionalId,
  method_definition_id: optionalId,
  activity_record_ids: ids(500).optional(),
  standard_id: optionalId,
  disclosure_draft_id: optionalId,
  requirement_ids: ids(100).optional(),
  evidence_item_ids: ids(100).optional(),
  procurement_scenario_id: optionalId,
  flexible_load_id: optionalId,
  dispatch_scenario_id: optionalId,
  forecast_id: optionalId,
  policy_definition_id: optionalId,
  metric_keys: z
    .array(z.string().regex(/^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+$/))
    .max(20)
    .optional(),
  material_scope: strings(20).optional(),
  supplier_product_ids: ids(100).optional(),
  constraints: constraintsSchema,
  dispatch_constraints: dispatchConstraintsSchema,
})
export const promptSchema = z
  .string()
  .transform((v) => v.trim().replace(/\s+/g, ' '))
  .pipe(z.string().min(3).max(4000))
export const agentRequestSchema = z.object({
  query: promptSchema,
  context: agentContextSchema,
  previous_run_id: optionalId,
})
export const acceptedSchema = z.object({
  run_id: uuid,
  trace_id: z.string(),
  terminal_state: z.literal('running'),
})
export type AgentContext = z.infer<typeof agentContextSchema>
export type AgentRequest = z.infer<typeof agentRequestSchema>

export const resumeRequestSchema = z
  .object({
    company_id: uuid,
    actor_id: uuid,
    interrupt_id: uuid,
    interrupt_sequence: z.number().int().positive(),
    analysis_signature: hash,
    idempotency_key: z.string().min(8).max(255),
    clarification: z
      .object({
        context: agentContextSchema,
        clarified_query: promptSchema.optional(),
      })
      .optional(),
    approval: z.object({ approval_id: uuid, preview_hash: hash }).optional(),
  })
  .refine(
    (v) => !!v.clarification !== !!v.approval,
    'Supply exactly one resume payload.',
  )

export const pageFields = {
  total: z.number().int().nonnegative(),
  limit: z.number().int().positive(),
  offset: z.number().int().nonnegative(),
}
export const selectorSchemas = {
  measurements: z.object({
    ...pageFields,
    items: z.array(
      z.object({
        id: uuid,
        company_id: uuid,
        site_id: uuid,
        reporting_period_id: uuid,
        metric_key: z.string(),
        value_kgco2e: decimal,
        status: z.string(),
      }),
    ),
  }),
  products: z.object({
    ...pageFields,
    items: z.array(
      z.object({
        id: uuid,
        name: z.string(),
        material_code: z.string(),
        supplier_name: z.string(),
      }),
    ),
  }),
  standards: z.object({
    ...pageFields,
    items: z.array(
      z.object({
        id: uuid,
        company_id: uuid,
        name: z.string(),
        version: z.string(),
        requirements: z.array(z.object({ id: uuid })),
      }),
    ),
  }),
  loads: z.object({
    ...pageFields,
    items: z.array(
      z.object({ id: uuid, company_id: uuid, site_id: uuid, name: z.string() }),
    ),
  }),
}
