import { useQuery } from '@tanstack/react-query'
import { ArrowUpRight, Zap } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { formatDate, formatDecimal } from '../format'
import { dashboardQueries, type DashboardScope } from '../queries'
import type { GridIntensity } from '../schemas'
import { QueryRefresh, QueryState } from './query-state'

export function GridPanel({
  scope,
  onInspect,
}: {
  scope: DashboardScope
  onInspect: (grid: GridIntensity) => void
}) {
  const query = useQuery(dashboardQueries.grid(scope))
  return (
    <section aria-labelledby="grid-title" className="min-w-0 p-5 sm:p-6">
      <div className="mb-5 flex items-start justify-between gap-3">
        <div>
          <h2 id="grid-title" className="text-sm font-semibold">
            Cached grid intensity
          </h2>
          <p className="mt-1 text-xs text-muted-foreground">
            Latest stored historical point for this site
          </p>
        </div>
        <QueryRefresh query={query} label="grid intensity" />
      </div>
      <QueryState query={query} emptyCode="grid_intensity_not_found">
        {(grid) => (
          <>
            <div className="mb-5 flex size-10 items-center justify-center rounded-md bg-amber-50 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300">
              <Zap className="size-5" />
            </div>
            <p className="break-all font-mono text-3xl font-semibold tabular-nums">
              {formatDecimal(grid.value)}
            </p>
            <p className="mt-1 text-sm text-muted-foreground">{grid.unit}</p>
            <div className="mt-5 flex flex-wrap gap-2">
              <Badge variant="secondary">{grid.zone}</Badge>
              <Badge variant="outline">
                {grid.provenance.provider_mode === 'fixture'
                  ? 'Synthetic fixture'
                  : 'Live-source cache'}
              </Badge>
              {grid.is_estimated && <Badge variant="outline">Estimated</Badge>}
            </div>
            <dl className="mt-5 space-y-3 text-xs">
              <div>
                <dt className="text-muted-foreground">Provider timestamp</dt>
                <dd className="mt-1">{formatDate(grid.provider_timestamp)}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">Retrieved</dt>
                <dd className="mt-1">
                  {formatDate(grid.provenance.retrieved_at)}
                </dd>
              </div>
            </dl>
            <Button
              className="mt-5"
              size="sm"
              variant="outline"
              onClick={() => onInspect(grid)}
            >
              Source snapshot
              <ArrowUpRight />
            </Button>
          </>
        )}
      </QueryState>
    </section>
  )
}
