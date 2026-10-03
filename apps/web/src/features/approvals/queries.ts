import { queryOptions } from '@tanstack/react-query'
import { apiRequest, retryApiQuery } from '@/services/api'
import { approvalDetailSchema, approvalListSchema } from './schemas'

export const approvalKey = ['approvals'] as const
export const approvalQueries = {
  list: (company: string, status: string, limit: number, offset: number) =>
    queryOptions({
      queryKey: [...approvalKey, company, 'list', status, limit, offset],
      retry: retryApiQuery,
      queryFn: ({ signal }) =>
        apiRequest(
          '/approvals',
          approvalListSchema.refine(
            (v) =>
              v.limit === limit &&
              v.offset === offset &&
              v.items.every(
                (i) =>
                  i.company_id === company &&
                  (status === 'all' || i.status === status),
              ),
          ),
          {
            signal,
            params: {
              company_id: company,
              status: status === 'all' ? undefined : status,
              limit,
              offset,
            },
          },
        ),
    }),
  detail: (company: string, id: string) =>
    queryOptions({
      queryKey: [...approvalKey, company, 'detail', id],
      retry: retryApiQuery,
      queryFn: ({ signal }) =>
        apiRequest(
          `/approvals/${id}`,
          approvalDetailSchema.refine(
            (v) => v.id === id && v.company_id === company,
          ),
          { signal, params: { company_id: company } },
        ),
    }),
}
