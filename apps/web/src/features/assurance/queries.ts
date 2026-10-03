import { queryOptions } from '@tanstack/react-query'
import type { WorkspaceScope } from '@/lib/workspace'
import { apiRequest, retryApiQuery } from '@/services/api'
import { draftSchema, standardsSchema, type Draft } from './schemas'

export function draftInScope(draft: Draft, scope: WorkspaceScope) {
  return (
    draft.company_id === scope.company_id &&
    draft.standard.company_id === scope.company_id &&
    draft.site_id === scope.site_id &&
    draft.reporting_period_id === scope.reporting_period_id
  )
}
export const assuranceQueries = {
  standards: (scope: WorkspaceScope, offset: number) =>
    queryOptions({
      queryKey: ['assurance', scope.company_id, 'standards', offset],
      queryFn: ({ signal }) =>
        apiRequest(
          '/assurance/standards',
          standardsSchema.refine((page) =>
            page.items.every(
              (item) =>
                item.company_id === scope.company_id &&
                item.requirements.every((req) => req.standard_id === item.id),
            ),
          ),
          {
            signal,
            params: {
              company_id: scope.company_id,
              active_only: true,
              limit: 25,
              offset,
            },
          },
        ),
      retry: retryApiQuery,
    }),
  draft: (scope: WorkspaceScope, id: string) =>
    queryOptions({
      queryKey: ['assurance', scope, 'draft', id],
      queryFn: ({ signal }) =>
        apiRequest(
          `/assurance/drafts/${id}`,
          draftSchema.refine(
            (draft) => draft.id === id && draftInScope(draft, scope),
          ),
          {
            signal,
            params: { company_id: scope.company_id },
          },
        ),
      retry: retryApiQuery,
    }),
}
