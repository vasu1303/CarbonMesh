import { queryOptions } from '@tanstack/react-query'
import { apiRequest, retryApiQuery } from '@/services/api'
import {
  auditSchema,
  eventDetailSchema,
  eventListSchema,
  type LedgerFilters,
} from './schemas'

export const ledgerKey = ['ledger-explorer'] as const
export const ledgerQueries = {
  list: (company: string, filters: LedgerFilters) => {
    const params = {
      company_id: company,
      event_type: filters.event_type || undefined,
      entity_type: filters.entity_type || undefined,
      entity_id: filters.entity_id || undefined,
      agent_run_id: filters.agent_run_id || undefined,
      created_from: filters.created_from || undefined,
      created_to: filters.created_to || undefined,
      limit: filters.limit,
      offset: filters.offset,
    }
    return queryOptions({
      queryKey: [...ledgerKey, 'list', params],
      retry: retryApiQuery,
      queryFn: ({ signal }) =>
        apiRequest(
          '/ledger/events',
          eventListSchema.refine(
            (v) =>
              v.limit === filters.limit &&
              v.offset === filters.offset &&
              v.items.every(
                (item) =>
                  item.company_id === company &&
                  (!filters.entity_id ||
                    item.entity_id === filters.entity_id) &&
                  (!filters.entity_type ||
                    item.entity_type === filters.entity_type) &&
                  (!filters.event_type ||
                    item.event_type === filters.event_type),
              ),
          ),
          { signal, params },
        ),
    })
  },
  detail: (company: string, id: string) =>
    queryOptions({
      queryKey: [...ledgerKey, company, 'detail', id],
      retry: retryApiQuery,
      queryFn: ({ signal }) =>
        apiRequest(
          `/ledger/events/${id}`,
          eventDetailSchema.refine(
            (v) =>
              v.id === id &&
              v.company_id === company &&
              [...v.parents, ...v.children].every(
                (n) => n.event.company_id === company,
              ),
          ),
          { signal, params: { company_id: company } },
        ),
    }),
  audit: (company: string, type: string, id: string) =>
    queryOptions({
      queryKey: [...ledgerKey, company, 'audit', type, id],
      retry: retryApiQuery,
      queryFn: ({ signal }) =>
        apiRequest(
          `/audit/${type}/${id}`,
          auditSchema.refine(
            (v) =>
              v.company_id === company &&
              v.entity_id === id &&
              v.entity_type === type,
          ),
          { signal, params: { company_id: company } },
        ),
    }),
}
