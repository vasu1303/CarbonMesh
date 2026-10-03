import { z } from 'zod'

const uuid = z.uuid()
const timestamp = z.iso.datetime({ offset: true })
const hash = z.string().regex(/^[0-9a-f]{64}$/)
export const decimal = z.string().regex(/^-?\d+(\.\d+)?([eE][+-]?\d+)?$/)
const nonnegative = decimal.refine(
  (value) => Number.isFinite(Number(value)) && Number(value) >= 0,
)
const metadata = z.record(z.string(), z.unknown())
const blackout = z.object({ start: timestamp, end: timestamp })
const constraintSchema = z.object({
  id: uuid,
  code: z.string(),
  name: z.string(),
  constraint_type: z.string(),
  is_hard: z.boolean(),
  valid_from: timestamp.nullable(),
  valid_to: timestamp.nullable(),
  configuration: metadata,
  is_active: z.boolean(),
})
export const loadSchema = z.object({
  id: uuid,
  company_id: uuid,
  site_id: uuid,
  semantic_entity_id: uuid.nullable(),
  code: z.string(),
  name: z.string(),
  description: z.string().nullable(),
  power_kw: nonnegative,
  duration_minutes: z.number().int().positive(),
  energy_kwh: nonnegative,
  minimum_power_kw: nonnegative.nullable(),
  maximum_power_kw: nonnegative.nullable(),
  is_interruptible: z.boolean(),
  metadata,
  is_active: z.boolean(),
  constraints: z.array(constraintSchema),
  created_at: timestamp,
  updated_at: timestamp,
})
export const loadsSchema = z.object({
  items: z.array(loadSchema),
  total: z.number().int().nonnegative(),
  limit: z.number().int().positive(),
  offset: z.number().int().nonnegative(),
})
export const forecastPointSchema = z.object({
  id: uuid,
  forecast_for: timestamp,
  intensity_gco2e_per_kwh: nonnegative,
  is_estimated: z.boolean().optional(),
  point_hash: hash,
  evidence_item_id: uuid.nullable(),
  evidence_checksum: hash.optional(),
  source_document_id: uuid.optional(),
  source_document_checksum: hash.optional(),
  forecast_ledger_event_id: uuid.optional(),
  forecast_ledger_event_hash: hash.optional(),
  zone: z.string().optional(),
  issued_at: timestamp.optional(),
})
export const forecastSchema = z.object({
  site_id: uuid,
  zone: z.string(),
  provider: z.string(),
  source_mode: z.enum(['fixture', 'live']),
  synthetic: z.boolean(),
  issued_at: timestamp,
  temporal_granularity: z.literal('hourly'),
  source_document_id: uuid,
  snapshot_hash: hash,
  received_points: z.number().int(),
  inserted_points: z.number().int(),
  existing_points: z.number().int(),
  estimated_points: z.number().int(),
  forecast_start: timestamp,
  forecast_end: timestamp,
  points: z.array(forecastPointSchema),
})
const loadSnapshotSchema = z.object({
  id: uuid,
  code: z.string(),
  name: z.string(),
  power_kw: nonnegative,
  duration_minutes: z.number().int().positive(),
  energy_kwh: nonnegative,
  minimum_power_kw: nonnegative.nullable(),
  maximum_power_kw: nonnegative.nullable(),
  is_interruptible: z.boolean(),
  is_active: z.boolean(),
  snapshot_hash: hash,
})
const methodSchema = z.object({
  id: uuid,
  key: z.string(),
  version: z.string(),
  code_version: z.string(),
})
const methodSnapshotSchema = methodSchema.extend({
  configuration: metadata,
  is_active: z.boolean(),
  snapshot_hash: hash,
})
const policySnapshotSchema = z.object({
  id: uuid,
  key: z.string(),
  version: z.string(),
  configuration: metadata,
  is_active: z.boolean(),
  snapshot_hash: hash,
})
export const constraintsSchema = z.object({
  earliest_start: timestamp,
  latest_finish: timestamp,
  baseline_start: timestamp,
  maximum_delay_minutes: z.number().int().nonnegative(),
  blackouts: z.array(blackout),
  capacity_windows: z.array(
    z.object({
      available_capacity_kw: nonnegative,
      start: timestamp.nullable(),
      end: timestamp.nullable(),
    }),
  ),
  source_constraints: z.array(constraintSchema),
  load_snapshot: loadSnapshotSchema.nullable(),
  method_snapshot: methodSnapshotSchema.nullable(),
  policy_snapshot: policySnapshotSchema.nullable(),
  requested_by: uuid,
  approval_expires_at: timestamp,
  approval_expiry_is_default: z.boolean(),
  approval_idempotency_key: z.string(),
})
export const scenarioSchema = z.object({
  id: uuid,
  company_id: uuid,
  site_id: uuid,
  flexible_load: loadSchema,
  agent_run_id: uuid.nullable(),
  method: methodSchema,
  policy_definition_id: uuid.nullable(),
  forecast_source_document_id: uuid,
  window_start: timestamp,
  window_end: timestamp,
  objective: z.string(),
  constraints: constraintsSchema,
  forecast_snapshot: z.array(forecastPointSchema),
  analysis_signature: hash,
  context_hash: hash,
  status: z.string(),
  created_at: timestamp,
  updated_at: timestamp,
})
export const rejectedWindowSchema = z.object({
  start: timestamp,
  end: timestamp,
  reasons: z.array(z.string()),
})
const previewSchema = z
  .object({
    target_type: z.literal('dispatch_recommendation'),
    scenario_id: uuid,
    recommended_start: timestamp,
    recommended_end: timestamp,
    baseline_start: timestamp,
    baseline_end: timestamp,
    expected_emissions_kgco2e: nonnegative,
    baseline_emissions_kgco2e: nonnegative,
    avoided_kgco2e: decimal,
    reduction_pct: decimal,
    load_snapshot: loadSnapshotSchema,
    method_snapshot: methodSnapshotSchema,
    policy_snapshot: policySnapshotSchema.nullable(),
    frozen_constraints: constraintsSchema,
    forecast_points: z.array(forecastPointSchema),
    forecast_source_document_id: uuid,
    forecast_source_document_checksum: hash,
    input_hash: hash,
    output_hash: hash,
    advisory_only: z.literal(true),
    actuation_authorized: z.literal(false),
  })
  .catchall(z.unknown())
export const recommendationSchema = z
  .object({
    id: uuid,
    company_id: uuid,
    scenario_id: uuid,
    status: z.string(),
    recommended_start: timestamp,
    recommended_end: timestamp,
    baseline_start: timestamp,
    baseline_end: timestamp,
    expected_emissions_kgco2e: nonnegative,
    baseline_emissions_kgco2e: nonnegative,
    avoided_kgco2e: decimal,
    reduction_pct: decimal,
    rationale: z.string(),
    impact_snapshot: z
      .object({
        input_hash: hash,
        output_hash: hash,
        method_id: uuid,
        method_key: z.string(),
        method_version: z.string(),
        method_code_version: z.string(),
        evaluated_windows: z.number().int().nonnegative(),
        feasible_windows: z.number().int().nonnegative(),
        rejected_windows: z.array(rejectedWindowSchema),
      })
      .catchall(z.unknown()),
    analysis_signature: hash,
    payload_hash: hash,
    ledger_event_id: uuid,
    evidence_item_ids: z.array(uuid),
    approval: z.object({
      id: uuid,
      target_type: z.literal('dispatch_recommendation'),
      target_id: uuid,
      status: z.string(),
      preview_payload: previewSchema,
      preview_hash: hash,
      analysis_signature: hash,
      context_hash: hash,
      idempotency_key: z.string(),
      expires_at: timestamp,
    }),
    actuation_authorized: z.literal(false),
    invalidated_at: timestamp.nullable(),
    created_at: timestamp,
  })
  .refine(
    (item) =>
      item.approval.target_id === item.id &&
      item.approval.preview_payload.scenario_id === item.scenario_id &&
      item.approval.analysis_signature === item.analysis_signature,
  )
const terminal = z.enum([
  'approval_required',
  'approved',
  'rejected',
  'stale',
  'no_feasible_option',
])
export const recommendationResultSchema = z.object({
  scenario_id: uuid,
  terminal_state: terminal,
  recommendation: recommendationSchema.nullable(),
})
export const optimizationSchema = recommendationResultSchema.extend({
  evaluated_windows: z.number().int().nonnegative(),
  feasible_windows: z.number().int().nonnegative(),
  rejected_windows: z.array(rejectedWindowSchema),
})
export const forecastFormSchema = z.object({
  zone: z
    .string()
    .trim()
    .max(100)
    .refine(
      (value) => value === '' || value.length >= 2,
      'Enter a zone or leave it empty.',
    ),
  fixture: z.boolean(),
  force_refresh: z.boolean(),
})
const localUtc = z
  .string()
  .regex(
    /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?$/,
    'Enter a UTC date and time.',
  )
  .refine(
    (value) => Number.isFinite(Date.parse(`${value}Z`)),
    'Enter a valid UTC time.',
  )
export const scenarioFormSchema = z
  .object({
    flexible_load_id: uuid,
    method_definition_id: uuid,
    forecast_source_document_id: uuid,
    requested_by: uuid,
    policy_definition_id: z.union([uuid, z.literal('')]),
    window_start: localUtc,
    window_end: localUtc,
    baseline_start: localUtc,
    maximum_delay_minutes: z
      .string()
      .regex(/^\d+$/, 'Enter whole minutes.')
      .refine(
        (value) => Number(value) <= 1440,
        'Maximum delay exceeds the forecast horizon.',
      ),
    available_capacity_kw: z.union([nonnegative, z.literal('')]),
    blackout_windows: z
      .array(
        z
          .object({ start: localUtc, end: localUtc })
          .refine((window) => window.end > window.start, {
            path: ['end'],
            message: 'End must follow start.',
          }),
      )
      .max(100),
    approval_expires_at: z.union([localUtc, z.literal('')]),
  })
  .superRefine((values, ctx) => {
    const start = Date.parse(`${values.window_start}Z`)
    const end = Date.parse(`${values.window_end}Z`)
    const baseline = Date.parse(`${values.baseline_start}Z`)
    if (end <= start || end - start > 86_400_000)
      ctx.addIssue({
        code: 'custom',
        path: ['window_end'],
        message:
          'Allowed interval must be positive and within the forecast horizon.',
      })
    if (baseline < start || baseline >= end)
      ctx.addIssue({
        code: 'custom',
        path: ['baseline_start'],
        message: 'Baseline must be inside the allowed interval.',
      })
    if (
      values.approval_expires_at &&
      Date.parse(`${values.approval_expires_at}Z`) <= Date.now()
    )
      ctx.addIssue({
        code: 'custom',
        path: ['approval_expires_at'],
        message: 'Approval expiry must be in the future.',
      })
  })
export type Load = z.infer<typeof loadSchema>
export type Forecast = z.infer<typeof forecastSchema>
export type ForecastPoint = z.infer<typeof forecastPointSchema>
export type Scenario = z.infer<typeof scenarioSchema>
export type Constraints = z.infer<typeof constraintsSchema>
export type Recommendation = z.infer<typeof recommendationSchema>
export type ScenarioForm = z.infer<typeof scenarioFormSchema>
