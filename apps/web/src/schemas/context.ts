import { z } from 'zod'

export const contextSchema = z.object({
  company: z.object({
    id: z.uuid(),
    name: z.string(),
    is_synthetic: z.boolean(),
  }),
  site: z.object({ id: z.uuid(), name: z.string(), timezone: z.string() }),
  reporting_period: z.object({
    id: z.uuid(),
    name: z.string(),
    start_date: z.iso.date(),
    end_date: z.iso.date(),
  }),
  analysis_signature: z.string(),
})
