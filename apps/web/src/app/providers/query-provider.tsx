import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from '@tanstack/react-query'
import type { PropsWithChildren } from 'react'
import { retryApiQuery } from '@/services/api'

const queryClient = new QueryClient({
  mutationCache: new MutationCache({
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['workspace-options'] })
    },
  }),
  defaultOptions: {
    queries: {
      retry: retryApiQuery,
      staleTime: 30_000,
    },
    mutations: { retry: false },
  },
})

export function QueryProvider({ children }: PropsWithChildren) {
  return (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
}
