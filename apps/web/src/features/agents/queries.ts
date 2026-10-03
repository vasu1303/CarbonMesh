import { queryOptions } from '@tanstack/react-query'
import type { WorkspaceScope } from '@/lib/workspace'
import { contextSchema } from '@/schemas/context'
import { apiRequest, retryApiQuery } from '@/services/api'
import { selectorSchemas } from './schemas'

export const agentKeys = ['agent-workspace'] as const
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

export type SelectorKind = keyof typeof selectorSchemas
export type SelectorPage = {
  total: number
  limit: number
  offset: number
  items: { id: string; label: string }[]
}
export function selectorOptions(
  kind: SelectorKind,
  scope: WorkspaceScope,
  offset: number,
) {
  const params = { company_id: scope.company_id, limit: 25, offset }
  return queryOptions<SelectorPage>({
    queryKey: [...agentKeys, kind, scope, offset],
    retry: retryApiQuery,
    staleTime: 30000,
    queryFn: async ({ signal }) => {
      if (kind === 'measurements') {
        const page = await apiRequest(
          '/measurements',
          selectorSchemas.measurements.refine((v) =>
            v.items.every(
              (i) =>
                i.company_id === scope.company_id &&
                i.site_id === scope.site_id &&
                i.reporting_period_id === scope.reporting_period_id,
            ),
          ),
          {
            signal,
            params: {
              ...params,
              site_id: scope.site_id,
              reporting_period_id: scope.reporting_period_id,
              status: 'verified',
            },
          },
        )
        return {
          ...page,
          items: page.items.map((i) => ({
            id: i.id,
            label: `${i.metric_key} / ${i.value_kgco2e} kgCO2e / ${i.status}`,
          })),
        }
      }
      if (kind === 'products') {
        const page = await apiRequest(
          '/procurement/products',
          selectorSchemas.products,
          { signal, params: { ...params, active_only: true } },
        )
        return {
          ...page,
          items: page.items.map((i) => ({
            id: i.id,
            label: `${i.name} / ${i.supplier_name} / ${i.material_code}`,
          })),
        }
      }
      if (kind === 'standards') {
        const page = await apiRequest(
          '/assurance/standards',
          selectorSchemas.standards.refine((v) =>
            v.items.every((i) => i.company_id === scope.company_id),
          ),
          { signal, params },
        )
        return {
          ...page,
          items: page.items.map((i) => ({
            id: i.id,
            label: `${i.name} / ${i.version}`,
          })),
        }
      }
      const page = await apiRequest(
        '/dispatch/loads',
        selectorSchemas.loads.refine((v) =>
          v.items.every(
            (i) =>
              i.company_id === scope.company_id && i.site_id === scope.site_id,
          ),
        ),
        { signal, params: { ...params, site_id: scope.site_id } },
      )
      return {
        ...page,
        items: page.items.map((i) => ({ id: i.id, label: i.name })),
      }
    },
  })
}
