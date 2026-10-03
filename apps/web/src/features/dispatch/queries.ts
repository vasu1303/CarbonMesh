import { queryOptions } from '@tanstack/react-query'
import type { WorkspaceScope } from '@/lib/workspace'
import { apiRequest, retryApiQuery } from '@/services/api'
import {
  loadsSchema,
  recommendationResultSchema,
  type Scenario,
} from './schemas'

export const scenarioKey = (company: string, id: string) =>
  ['dispatch', company, 'scenario', id] as const
export function scenarioInScope(scenario: Scenario, scope: WorkspaceScope) {
  return (
    scenario.company_id === scope.company_id &&
    scenario.site_id === scope.site_id &&
    scenario.flexible_load.company_id === scope.company_id &&
    scenario.flexible_load.site_id === scope.site_id
  )
}
export const dispatchQueries = {
  loads: (scope: WorkspaceScope, offset: number) =>
    queryOptions({
      queryKey: ['dispatch', scope.company_id, scope.site_id, 'loads', offset],
      queryFn: ({ signal }) =>
        apiRequest(
          '/dispatch/loads',
          loadsSchema.refine((page) =>
            page.items.every(
              (load) =>
                load.company_id === scope.company_id &&
                load.site_id === scope.site_id,
            ),
          ),
          {
            signal,
            params: {
              company_id: scope.company_id,
              site_id: scope.site_id,
              active_only: true,
              limit: 25,
              offset,
            },
          },
        ),
      retry: retryApiQuery,
    }),
  recommendation: (scope: WorkspaceScope, id: string) =>
    queryOptions({
      queryKey: ['dispatch', scope.company_id, 'recommendation', id],
      queryFn: ({ signal }) =>
        apiRequest(
          `/dispatch/scenarios/${id}/recommendation`,
          recommendationResultSchema.refine(
            (result) =>
              result.scenario_id === id &&
              (!result.recommendation ||
                (result.recommendation.company_id === scope.company_id &&
                  result.recommendation.scenario_id === id)),
          ),
          { signal, params: { company_id: scope.company_id } },
        ),
      retry: retryApiQuery,
    }),
}
