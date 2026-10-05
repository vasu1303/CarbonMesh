import { useQuery } from '@tanstack/react-query'
import { useOutletContext } from 'react-router-dom'
import { z } from 'zod'
import { workspaceScope, type WorkspaceScope } from '@/lib/workspace'
import { apiRequest, retryApiQuery } from '@/services/api'

export const recordKinds = [
  'actors',
  'metrics',
  'methods',
  'policies',
  'imports',
  'documents',
  'activity',
  'evidence',
  'measurements',
  'suppliers',
  'products',
  'standards',
  'loads',
  'forecasts',
  'assurance',
  'procurement',
  'dispatch',
  'runs',
  'ledger',
] as const
export type RecordKind = (typeof recordKinds)[number]

const optionSchema = z.object({
  id: z.uuid(),
  label: z.string(),
  description: z.string().nullable().optional(),
  status: z.string().nullable().optional(),
  role: z.string().nullable().optional(),
})
const optionsSchema = z.object({
  items: z.array(optionSchema),
  total: z.number().int().nonnegative(),
  limit: z.number().int().positive(),
  offset: z.number().int().nonnegative(),
})

export function useWorkspaceOptions(
  kind: RecordKind,
  filters: {
    id?: string
    search?: string
    role?: string
    offset?: number
    limit?: number
    enabled?: boolean
  } = {},
) {
  const outlet = useOutletContext<WorkspaceScope | undefined>()
  const scope =
    outlet ?? (workspaceScope.success ? workspaceScope.data : undefined)
  const { enabled = true, ...params } = filters
  return useQuery({
    queryKey: ['workspace-options', scope, kind, params],
    enabled: enabled && !!scope,
    queryFn: ({ signal }) =>
      apiRequest('/workspace/options', optionsSchema, {
        signal,
        params: {
          company_id: scope?.company_id,
          site_id: scope?.site_id,
          reporting_period_id: scope?.reporting_period_id,
          kind,
          ...params,
        },
      }),
    retry: retryApiQuery,
    staleTime: 30_000,
  })
}
