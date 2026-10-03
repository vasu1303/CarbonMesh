import { queryOptions } from '@tanstack/react-query'

import type { WorkspaceScope } from '@/lib/workspace'
import { contextSchema } from '@/schemas/context'
import { apiRequest, retryApiQuery } from '@/services/api'
import {
  productListSchema,
  supplierListSchema,
  type CatalogFilters,
} from './schemas'

export const catalogKey = ['procurement-catalog'] as const

export const catalogQueries = {
  suppliers: (companyId: string, filters: CatalogFilters) => {
    const params = {
      company_id: companyId,
      search: filters.supplier_search || undefined,
      country_code: filters.country || undefined,
      active_only: filters.active === 'true',
      limit: filters.limit,
      offset: filters.supplier_offset,
    }
    const schema = supplierListSchema.refine(
      (page) =>
        page.limit === params.limit &&
        page.offset === params.offset &&
        page.items.length <= page.limit &&
        page.items.every(
          (item) =>
            (!params.country_code ||
              item.country_code === params.country_code) &&
            (!params.active_only || item.status === 'active'),
        ),
    )
    return queryOptions({
      queryKey: [...catalogKey, 'suppliers', params],
      queryFn: ({ signal }) =>
        apiRequest('/procurement/suppliers', schema, { signal, params }),
      staleTime: 30_000,
      retry: retryApiQuery,
    })
  },
  products: (companyId: string, filters: CatalogFilters) => {
    const params = {
      company_id: companyId,
      search: filters.product_search || undefined,
      supplier_id: filters.supplier || undefined,
      material_code: filters.material || undefined,
      category: filters.category || undefined,
      active_only: filters.active === 'true',
      limit: filters.limit,
      offset: filters.product_offset,
    }
    const schema = productListSchema.refine(
      (page) =>
        page.limit === params.limit &&
        page.offset === params.offset &&
        page.items.length <= page.limit &&
        page.items.every(
          (item) =>
            (!params.supplier_id || item.supplier_id === params.supplier_id) &&
            (!params.material_code ||
              item.material_code === params.material_code) &&
            (!params.category || item.category === params.category) &&
            (!params.active_only ||
              (item.is_active && item.supplier_status === 'active')),
        ),
    )
    return queryOptions({
      queryKey: [...catalogKey, 'products', params],
      queryFn: ({ signal }) =>
        apiRequest('/procurement/products', schema, { signal, params }),
      staleTime: 30_000,
      retry: retryApiQuery,
    })
  },
  context: (scope: WorkspaceScope) =>
    queryOptions({
      queryKey: [...catalogKey, 'context', scope],
      queryFn: ({ signal }) =>
        apiRequest(
          '/context/resolve',
          contextSchema.refine(
            (data) =>
              data.company.id === scope.company_id &&
              data.site.id === scope.site_id &&
              data.reporting_period.id === scope.reporting_period_id,
          ),
          {
            signal,
            body: {
              company_id: scope.company_id,
              site_id: scope.site_id,
              reporting_period_id: scope.reporting_period_id,
              metric_definition_ids: [scope.metric_id],
              workflow: 'procurement',
            },
          },
        ),
      staleTime: 60_000,
      retry: retryApiQuery,
    }),
}
