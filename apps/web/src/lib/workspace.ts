import { z } from 'zod'

const scopeSchema = z.object({
  company_id: z.uuid(),
  site_id: z.uuid(),
  reporting_period_id: z.uuid(),
  metric_id: z.uuid(),
})
// Stable seeded identifiers only. Names, values and provenance always come from the API.
export const workspaceScope = scopeSchema.safeParse({
  company_id:
    import.meta.env.VITE_COMPANY_ID || '00000000-0000-4000-8000-000000000001',
  site_id:
    import.meta.env.VITE_SITE_ID || '00000000-0000-4000-8000-000000000002',
  reporting_period_id:
    import.meta.env.VITE_REPORTING_PERIOD_ID ||
    '00000000-0000-4000-8000-000000000003',
  metric_id:
    import.meta.env.VITE_METRIC_DEFINITION_ID ||
    '00000000-0000-4000-8000-000000000102',
})
export type WorkspaceScope = z.infer<typeof scopeSchema>
