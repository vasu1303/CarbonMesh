import { queryOptions } from '@tanstack/react-query'

import type { WorkspaceScope } from '@/lib/workspace'
import { contextSchema } from '@/schemas/context'
import { apiRequest, retryApiQuery } from '@/services/api'
import {
  measurementDetailSchema,
  measurementBreakdownSchema,
  measurementLineageSchema,
  measurementListSchema,
  metricsSchema,
  type MeasurementFilters,
} from './schemas'

export const measurementKey = ['measurements'] as const
type ListFilters = Pick<
  MeasurementFilters,
  'status' | 'category' | 'limit' | 'offset'
>

function inScope(
  item: { company_id: string; site_id: string; reporting_period_id: string },
  scope: WorkspaceScope,
) {
  return (
    item.company_id === scope.company_id &&
    item.site_id === scope.site_id &&
    item.reporting_period_id === scope.reporting_period_id
  )
}

export const measurementQueries = {
  breakdown: (scope: WorkspaceScope, id: string) =>
    queryOptions({
      queryKey: [...measurementKey, scope, 'breakdown', id],
      queryFn: ({ signal }) =>
        apiRequest(
          `/measurements/${id}/breakdown`,
          measurementBreakdownSchema.refine(
            (data) => data.measurement_id === id,
          ),
          {
            signal,
            params: { company_id: scope.company_id },
          },
        ),
      staleTime: 30_000,
      retry: retryApiQuery,
    }),
  list: (scope: WorkspaceScope, filters: ListFilters) => {
    const schema = measurementListSchema.refine((page) =>
      page.items.every(
        (item) =>
          inScope(item, scope) &&
          (filters.status === 'all' || item.status === filters.status) &&
          (!filters.category || item.category === filters.category),
      ),
    )
    return queryOptions({
      queryKey: [...measurementKey, scope, 'list', filters],
      queryFn: ({ signal }) =>
        apiRequest('/measurements', schema, {
          signal,
          params: {
            company_id: scope.company_id,
            site_id: scope.site_id,
            reporting_period_id: scope.reporting_period_id,
            status: filters.status === 'all' ? undefined : filters.status,
            category: filters.category || undefined,
            limit: filters.limit,
            offset: filters.offset,
          },
        }),
      staleTime: 30_000,
      retry: retryApiQuery,
    })
  },
  detail: (scope: WorkspaceScope, id: string) => {
    const schema = measurementDetailSchema.refine(
      (item) => item.id === id && inScope(item, scope),
    )
    return queryOptions({
      queryKey: [...measurementKey, scope, 'detail', id],
      queryFn: ({ signal }) =>
        apiRequest(`/measurements/${id}`, schema, {
          signal,
          params: { company_id: scope.company_id },
        }),
      staleTime: 30_000,
      retry: retryApiQuery,
    })
  },
  lineage: (scope: WorkspaceScope, id: string) => {
    const schema = measurementLineageSchema.refine(
      (item) => item.measurement_id === id,
    )
    return queryOptions({
      queryKey: [...measurementKey, scope, 'lineage', id],
      queryFn: ({ signal }) =>
        apiRequest(`/measurements/${id}/lineage`, schema, {
          signal,
          params: { company_id: scope.company_id },
        }),
      staleTime: 30_000,
      retry: retryApiQuery,
    })
  },
  metrics: (scope: WorkspaceScope) => {
    const schema = metricsSchema.refine(
      (item) => item.company_id === scope.company_id,
    )
    return queryOptions({
      queryKey: [...measurementKey, scope.company_id, 'metrics'],
      queryFn: ({ signal }) =>
        apiRequest('/semantic/metrics', schema, {
          signal,
          params: { company_id: scope.company_id, active_only: true },
        }),
      staleTime: 60_000,
      retry: retryApiQuery,
    })
  },
  context: (scope: WorkspaceScope) => {
    const schema = contextSchema.refine(
      (item) =>
        item.company.id === scope.company_id &&
        item.site.id === scope.site_id &&
        item.reporting_period.id === scope.reporting_period_id,
    )
    return queryOptions({
      queryKey: [...measurementKey, scope, 'context'],
      queryFn: ({ signal }) =>
        apiRequest('/context/resolve', schema, {
          signal,
          body: {
            company_id: scope.company_id,
            site_id: scope.site_id,
            reporting_period_id: scope.reporting_period_id,
            metric_definition_ids: [scope.metric_id],
            workflow: 'measurement',
          },
        }),
      staleTime: 60_000,
      retry: retryApiQuery,
    })
  },
}
