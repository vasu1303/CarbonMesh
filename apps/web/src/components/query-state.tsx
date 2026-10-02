import type { UseQueryResult } from '@tanstack/react-query'
import { AlertCircle, ChevronLeft, ChevronRight, RefreshCw } from 'lucide-react'
import type { ReactNode } from 'react'

import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import { ApiError } from '@/services/api'
import { formatDate } from '@/lib/format'

export function QueryState<T>({
  query,
  children,
  emptyCode,
}: {
  query: UseQueryResult<T, Error>
  children: (data: T) => ReactNode
  emptyCode?: string
}) {
  if (query.data === undefined) {
    if (query.isError) {
      if (query.error instanceof ApiError && query.error.code === emptyCode) {
        return (
          <EmptyState
            title="No cached grid data"
            detail="No historical grid point is available for this site."
          />
        )
      }
      return (
        <div role="alert" className="space-y-3 py-4 text-sm">
          <p className="flex items-center gap-2 font-medium">
            <AlertCircle className="size-4 shrink-0 text-amber-600" />
            Data unavailable
          </p>
          <p className="text-muted-foreground">{query.error.message}</p>
          {query.error instanceof ApiError && query.error.traceId && (
            <p className="break-all font-mono text-xs text-muted-foreground">
              Trace: {query.error.traceId}
            </p>
          )}
          <Button
            variant="outline"
            size="sm"
            onClick={() => void query.refetch()}
          >
            <RefreshCw />
            Retry
          </Button>
        </div>
      )
    }
    return (
      <div
        role="status"
        className="space-y-3 py-4"
        aria-label={query.isPaused ? 'Waiting for connection' : 'Loading data'}
      >
        <span className="sr-only">
          {query.isPaused ? 'Waiting for connection' : 'Loading data'}
        </span>
        <Skeleton className="h-8 w-1/2 motion-reduce:animate-none" />
        <Skeleton className="h-4 w-full motion-reduce:animate-none" />
        <Skeleton className="h-4 w-3/4 motion-reduce:animate-none" />
      </div>
    )
  }
  return (
    <>
      {query.isError && (
        <div
          role="alert"
          className="mb-3 flex flex-wrap items-center gap-2 text-xs text-amber-700 dark:text-amber-400"
        >
          <AlertCircle className="size-4" />
          Refresh failed. Showing previously fetched data.
          <Button
            size="sm"
            variant="ghost"
            onClick={() => void query.refetch()}
          >
            Retry
          </Button>
        </div>
      )}
      {children(query.data)}
    </>
  )
}

export function QueryRefresh({
  query,
  label,
}: {
  query: Pick<UseQueryResult, 'isFetching' | 'refetch' | 'dataUpdatedAt'>
  label: string
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className="size-8 shrink-0"
          aria-label={`Refresh ${label}`}
          disabled={query.isFetching}
          onClick={() => void query.refetch()}
        >
          <RefreshCw
            className={
              query.isFetching
                ? 'size-3.5 motion-safe:animate-spin'
                : 'size-3.5'
            }
          />
        </Button>
      </TooltipTrigger>
      <TooltipContent>
        {query.dataUpdatedAt
          ? `Fetched ${formatDate(new Date(query.dataUpdatedAt).toISOString())}`
          : `Refresh ${label}`}
      </TooltipContent>
    </Tooltip>
  )
}

export function EmptyState({
  title,
  detail,
}: {
  title: string
  detail: string
}) {
  return (
    <div className="flex min-h-28 flex-col justify-center gap-1 py-5 text-sm">
      <p className="font-medium">{title}</p>
      <p className="text-muted-foreground">{detail}</p>
    </div>
  )
}

export function PageControls({
  total,
  offset,
  limit,
  count,
  onChange,
}: {
  total: number
  offset: number
  limit: number
  count: number
  onChange: (offset: number) => void
}) {
  return (
    <div className="mt-4 flex items-center justify-between gap-3 text-xs text-muted-foreground">
      <span>
        {count
          ? `${offset + 1}-${offset + count} of ${total}`
          : `0 of ${total}`}
      </span>
      <div className="flex gap-1">
        <Button
          size="icon"
          variant="outline"
          className="size-8"
          aria-label="Previous page"
          title="Previous page"
          disabled={offset === 0}
          onClick={() => onChange(Math.max(0, offset - limit))}
        >
          <ChevronLeft />
        </Button>
        <Button
          size="icon"
          variant="outline"
          className="size-8"
          aria-label="Next page"
          title="Next page"
          disabled={offset + limit >= total}
          onClick={() => onChange(offset + limit)}
        >
          <ChevronRight />
        </Button>
      </div>
    </div>
  )
}
