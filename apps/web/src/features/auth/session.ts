import { queryOptions, type QueryClient } from '@tanstack/react-query'
import { z } from 'zod'

import { ApiError, apiRequest } from '@/services/api'

export const sessionKey = ['session'] as const
export const principalSchema = z.object({
  company_id: z.uuid(),
  actor_id: z.uuid(),
  role: z.enum([
    'sustainability_analyst',
    'procurement_manager',
    'approver',
    'auditor',
    'system',
  ]),
})

export async function clearWorkspaceQueries(client: QueryClient) {
  const filters = {
    predicate: (query: { queryKey: readonly unknown[] }) =>
      !['session', 'health'].includes(String(query.queryKey[0])),
  }
  await client.cancelQueries(filters)
  client.removeQueries(filters)
}

export const sessionOptions = queryOptions({
  queryKey: sessionKey,
  queryFn: async ({ signal, client }) => {
    try {
      const principal = await apiRequest('/auth/session', principalSchema, {
        signal,
      })
      const previous = client.getQueryData(sessionKey) as
        z.infer<typeof principalSchema> | null | undefined
      if (
        previous &&
        (previous.company_id !== principal.company_id ||
          previous.actor_id !== principal.actor_id ||
          previous.role !== principal.role)
      )
        await clearWorkspaceQueries(client)
      return principal
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) return null
      throw error
    }
  },
  retry: false,
  staleTime: 0,
  refetchInterval: 60_000,
})
