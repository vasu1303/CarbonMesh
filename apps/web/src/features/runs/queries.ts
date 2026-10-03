import { queryOptions } from '@tanstack/react-query'
import { apiRequest, retryApiQuery } from '@/services/api'
import {
  approvalObserverSchema,
  runSchema,
  sustainabilitySchema,
} from './schemas'

export const runOptions = (companyId: string, runId: string) =>
  queryOptions({
    queryKey: ['agent-runs', companyId, runId],
    queryFn: ({ signal }) =>
      apiRequest(
        `/runs/${runId}`,
        runSchema.refine(
          (v) => v.run_id === runId && v.context.company_id === companyId,
        ),
        { signal, params: { company_id: companyId } },
      ),
    staleTime: 0,
    retry: false,
    refetchOnWindowFocus: false,
  })
export const sustainabilityOptions = (companyId: string) =>
  queryOptions({
    queryKey: ['agent-sustainability', companyId],
    queryFn: ({ signal }) =>
      apiRequest(
        '/metrics/agent-sustainability',
        sustainabilitySchema.refine((v) => v.company_id === companyId),
        { signal, params: { company_id: companyId } },
      ),
    staleTime: 30000,
    retry: retryApiQuery,
  })
export const approvalObserverOptions = (
  companyId: string,
  approvalId: string,
) =>
  queryOptions({
    queryKey: ['agent-approval-observer', companyId, approvalId],
    queryFn: ({ signal }) =>
      apiRequest(
        `/approvals/${approvalId}`,
        approvalObserverSchema.refine(
          (v) => v.id === approvalId && v.company_id === companyId,
        ),
        { signal, params: { company_id: companyId } },
      ),
    staleTime: 0,
    retry: false,
  })
