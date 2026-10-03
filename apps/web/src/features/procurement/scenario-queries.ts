import { queryOptions } from '@tanstack/react-query'
import type { WorkspaceScope } from '@/lib/workspace'
import { apiRequest, retryApiQuery } from '@/services/api'
import { recommendationSchema, scenarioSchema } from './scenario-schemas'

export const scenarioKey = ['procurement-scenarios'] as const
export function scenarioInScope(
  item: { company_id: string; site_id: string; reporting_period_id: string },
  scope: WorkspaceScope,
) {
  return (
    item.company_id === scope.company_id &&
    item.site_id === scope.site_id &&
    item.reporting_period_id === scope.reporting_period_id
  )
}
export const scenarioQueries = {
  detail: (scope: WorkspaceScope, id: string) =>
    queryOptions({
      queryKey: [...scenarioKey, scope, id],
      retry: retryApiQuery,
      queryFn: ({ signal }) =>
        apiRequest(
          `/procurement/scenarios/${id}`,
          scenarioSchema.refine(
            (v) => v.id === id && scenarioInScope(v, scope),
          ),
          { signal, params: { company_id: scope.company_id } },
        ),
    }),
  recommendation: (
    scope: WorkspaceScope,
    id: string,
    recommendationId?: string,
  ) =>
    queryOptions({
      queryKey: [...scenarioKey, scope, id, 'recommendation', recommendationId],
      retry: retryApiQuery,
      queryFn: ({ signal }) =>
        apiRequest(
          `/procurement/scenarios/${id}/recommendation`,
          recommendationSchema.refine(
            (v) =>
              v.company_id === scope.company_id &&
              v.scenario_id === id &&
              (!recommendationId || v.id === recommendationId),
          ),
          { signal, params: { company_id: scope.company_id } },
        ),
    }),
}
