import { queryOptions } from '@tanstack/react-query'
import { z } from 'zod'

import { apiRequest, retryApiQuery } from '@/services/api'
import * as schemas from './schemas'

const scopeSchema = z.object({
  company_id: z.uuid(),
  site_id: z.uuid(),
  reporting_period_id: z.uuid(),
  metric_id: z.uuid(),
})
// Stable seeded identifiers only. Names, values and provenance always come from the API.
export const dashboardScope = scopeSchema.safeParse({
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
export type DashboardScope = z.infer<typeof scopeSchema>
export const dashboardKey = ['dashboard'] as const

function matchesScope(
  item: { company_id: string; site_id: string; reporting_period_id: string },
  scope: DashboardScope,
) {
  return (
    item.company_id === scope.company_id &&
    item.site_id === scope.site_id &&
    item.reporting_period_id === scope.reporting_period_id
  )
}

function read<T>(
  path: string,
  schema: z.ZodType<T>,
  params: Record<string, string | number | boolean | undefined>,
) {
  return queryOptions({
    queryKey: [...dashboardKey, path, params],
    queryFn: ({ signal }) => apiRequest(path, schema, { params, signal }),
    retry: retryApiQuery,
    staleTime: 30_000,
  })
}

export const dashboardQueries = {
  context: (scope: DashboardScope) =>
    queryOptions({
      queryKey: [...dashboardKey, 'context', scope],
      queryFn: ({ signal }) =>
        apiRequest(
          '/context/resolve',
          schemas.contextSchema.refine(
            (data) =>
              data.company.id === scope.company_id &&
              data.site.id === scope.site_id &&
              data.reporting_period.id === scope.reporting_period_id,
          ),
          {
            signal,
            // This endpoint resolves context only; it does not execute a workflow.
            body: {
              company_id: scope.company_id,
              site_id: scope.site_id,
              reporting_period_id: scope.reporting_period_id,
              metric_definition_ids: [scope.metric_id],
              workflow: 'measurement',
            },
          },
        ),
      staleTime: 60_000,
      retry: retryApiQuery,
    }),
  measurements: (scope: DashboardScope, offset = 0) =>
    read(
      '/measurements',
      schemas.pageSchema(
        schemas.measurementSchema.refine(
          (item) => matchesScope(item, scope) && item.status === 'verified',
        ),
      ),
      {
        company_id: scope.company_id,
        site_id: scope.site_id,
        reporting_period_id: scope.reporting_period_id,
        status: 'verified',
        limit: 6,
        offset,
      },
    ),
  measurement: (scope: DashboardScope, id: string) =>
    read(
      `/measurements/${id}`,
      schemas.measurementDetailSchema.refine(
        (item) => item.id === id && matchesScope(item, scope),
      ),
      {
        company_id: scope.company_id,
      },
    ),
  lineage: (scope: DashboardScope, id: string) =>
    read(
      `/measurements/${id}/lineage`,
      schemas.lineageSchema.refine((item) => item.measurement_id === id),
      {
        company_id: scope.company_id,
      },
    ),
  issues: (scope: DashboardScope, severity?: string) =>
    read('/quality/issues', schemas.pageSchema(schemas.issueSchema), {
      company_id: scope.company_id,
      status: 'open',
      severity,
      limit: 4,
      offset: 0,
    }),
  approvals: (scope: DashboardScope, offset = 0) =>
    read('/approvals', schemas.pageSchema(schemas.approvalSchema), {
      company_id: scope.company_id,
      status: 'pending',
      limit: 2,
      offset,
    }),
  ledger: (scope: DashboardScope) =>
    read('/ledger/events', schemas.pageSchema(schemas.ledgerEventSchema), {
      company_id: scope.company_id,
      limit: 5,
      offset: 0,
    }),
  event: (scope: DashboardScope, id: string) =>
    read(
      `/ledger/events/${id}`,
      schemas.ledgerDetailSchema.refine((item) => item.id === id),
      {
        company_id: scope.company_id,
      },
    ),
  grid: (scope: DashboardScope) =>
    read(
      '/measurement/grid/latest',
      schemas.gridSchema.refine((item) => item.site_id === scope.site_id),
      {
        company_id: scope.company_id,
        site_id: scope.site_id,
      },
    ),
  suppliers: (scope: DashboardScope) =>
    read(
      '/procurement/suppliers',
      schemas.pageSchema(schemas.catalogItemSchema),
      { company_id: scope.company_id, active_only: true, limit: 1, offset: 0 },
    ),
  products: (scope: DashboardScope) =>
    read(
      '/procurement/products',
      schemas.pageSchema(schemas.catalogItemSchema),
      { company_id: scope.company_id, active_only: true, limit: 1, offset: 0 },
    ),
  standards: (scope: DashboardScope) =>
    read(
      '/assurance/standards',
      schemas.pageSchema(schemas.catalogItemSchema),
      { company_id: scope.company_id, active_only: true, limit: 1, offset: 0 },
    ),
  loads: (scope: DashboardScope) =>
    read('/dispatch/loads', schemas.pageSchema(schemas.catalogItemSchema), {
      company_id: scope.company_id,
      site_id: scope.site_id,
      active_only: true,
      limit: 1,
      offset: 0,
    }),
}
