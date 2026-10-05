import { queryOptions } from '@tanstack/react-query'
import type { WorkspaceScope } from '@/lib/workspace'
import { contextSchema } from '@/schemas/context'
import { apiRequest, retryApiQuery } from '@/services/api'

const agentKeys = ['agent-workspace'] as const
export const contextOptions = (
  scope: Pick<
    WorkspaceScope,
    'company_id' | 'site_id' | 'reporting_period_id' | 'metric_id'
  >,
) =>
  queryOptions({
    queryKey: [...agentKeys, 'context', scope],
    queryFn: ({ signal }) =>
      apiRequest(
        '/context/resolve',
        contextSchema.refine(
          (v) =>
            v.company.id === scope.company_id &&
            v.site.id === scope.site_id &&
            v.reporting_period.id === scope.reporting_period_id,
        ),
        {
          signal,
          body: {
            company_id: scope.company_id,
            site_id: scope.site_id,
            reporting_period_id: scope.reporting_period_id,
            metric_definition_ids: [scope.metric_id],
            workflow: 'measurement',
          },
        },
      ),
    retry: retryApiQuery,
    staleTime: 60000,
  })
