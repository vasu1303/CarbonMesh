import {
  QueryCache,
  QueryClient,
  QueryClientProvider,
} from '@tanstack/react-query'
import type { PropsWithChildren } from 'react'
import { clearWorkspaceQueries, sessionKey } from '@/features/auth/session'
import { ApiError } from '@/services/api'

const queryClient = new QueryClient({
  queryCache: new QueryCache({
    onSuccess: (data, query) => {
      if (query.queryKey[0] === 'session' && data === null)
        void clearWorkspaceQueries(queryClient)
    },
    onError: (error, query) => {
      if (error instanceof ApiError && error.status === 401) {
        void queryClient.cancelQueries(
          { queryKey: sessionKey },
          { revert: false },
        )
        queryClient.setQueryData(sessionKey, null)
        void clearWorkspaceQueries(queryClient)
      } else if (query.queryKey[0] === 'session') {
        void clearWorkspaceQueries(queryClient)
      }
    },
  }),
  defaultOptions: {
    queries: {
      retry: 1,
      staleTime: 30_000,
    },
  },
})

export function QueryProvider({ children }: PropsWithChildren) {
  return (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
}
