import { z } from 'zod'
import { count, decimal, timestamp, uuid } from '@/features/data/schemas'

export const healthSchema = z.object({
  status: z.literal('ok'),
  service: z.string(),
})
export const databaseSchema = z.object({
  connected: z.boolean(),
  database_time: timestamp,
  message: z.string(),
})
export const providerSchema = z.object({
  provider: z.string(),
  api_version: z.string(),
  authenticated: z.boolean(),
  accessible_zone_count: count,
  zones_truncated: z.boolean(),
  zones: z.array(
    z.object({
      zone: z.string(),
      zone_name: z.string(),
      country_code: z.string().nullable(),
      accessible_endpoints: z.array(z.string()),
    }),
  ),
})
export const providerFormSchema = z.object({
  max_zones: z
    .string()
    .regex(/^\d+$/)
    .refine(
      (value) => Number(value) >= 1 && Number(value) <= 100,
      'Choose from 1 to 100 zones.',
    ),
})
export const gridFormSchema = z
  .object({
    zone: z
      .string()
      .trim()
      .max(100)
      .refine(
        (value) => !value || value.length >= 2,
        'Zone must contain at least two characters.',
      ),
    start: z.union([timestamp, z.literal('')]),
    end: z.union([timestamp, z.literal('')]),
    lookback_hours: z
      .string()
      .regex(/^\d+$/)
      .refine(
        (value) => Number(value) >= 1 && Number(value) <= 240,
        'Choose from 1 to 240 hours.',
      ),
    disable_estimations: z.boolean(),
  })
  .superRefine((data, ctx) => {
    if (!!data.start !== !!data.end)
      ctx.addIssue({
        code: 'custom',
        path: ['end'],
        message: 'Both range boundaries are required.',
      })
    if (data.start && data.end) {
      const duration = Date.parse(data.end) - Date.parse(data.start)
      if (duration <= 0 || duration > 240 * 3_600_000)
        ctx.addIssue({
          code: 'custom',
          path: ['end'],
          message: 'End must follow start within 240 hours.',
        })
    }
  })
export const gridSyncSchema = z.object({
  site_id: uuid,
  zone: z.string(),
  zone_resolution: z.enum([
    'request',
    'cached',
    'configured',
    'country_exact',
    'country_unique',
  ]),
  requested_start: timestamp,
  requested_end: timestamp,
  received_points: count,
  inserted_points: count,
  existing_points: count,
  estimated_points: count,
  data_source_id: uuid,
  source_document_id: uuid,
  response_checksum: z.string(),
})
export const latestGridSchema = z.object({
  site_id: uuid,
  grid_intensity_point_id: uuid,
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
    source_document_id: uuid,
    evidence_item_id: uuid,
    response_checksum: z.string(),
    retrieved_at: timestamp,
  }),
})

export function demoResetFormSchema(target: string) {
  return z.object({
    target_confirmation: z.string().refine(
      value => target.length > 0 && value === target,
      'Type the configured target label exactly.',
    ),
    reset_token: z.string().min(16, 'Enter the complete reset token.').max(256),
    disposable: z.boolean().refine(value => value, 'Confirm that the entire database is disposable.'),
  })
}

export const demoResetResponseSchema = z.object({
  status: z.literal('reset'),
  synthetic: z.literal(true),
  company_id: uuid,
  site_id: uuid,
  reporting_period_id: uuid,
  seeded: z.object({
    metrics: count,
    activity_records: count,
    supplier_products: count,
    emission_factors: count,
  }),
})
