import { useQuery } from '@tanstack/react-query'
import { ArrowUpRight } from 'lucide-react'
import { lazy, Suspense, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { formatDate, formatDecimal, humanize } from '../format'
import { dashboardQueries, type DashboardScope } from '../queries'
import {
  EmptyState,
  PageControls,
  QueryRefresh,
  QueryState,
} from './query-state'

const MeasurementChart = lazy(() => import('./measurement-chart'))

export function MeasurementsPanel({
  scope,
  onInspect,
}: {
  scope: DashboardScope
  onInspect: (id: string) => void
}) {
  const [offset, setOffset] = useState(0)
  const query = useQuery(dashboardQueries.measurements(scope, offset))

  return (
    <section
      id="measurements"
      aria-labelledby="measurements-title"
      className="min-w-0 p-5 sm:p-6"
    >
      <div className="mb-4 flex items-start justify-between gap-3">
        <div>
          <h2 id="measurements-title" className="text-sm font-semibold">
            Verified measurements
          </h2>
          <p className="mt-1 text-xs text-muted-foreground">
            Selected site and period. Individual results, not an additive total.
          </p>
        </div>
        <QueryRefresh query={query} label="measurements" />
      </div>
      <QueryState query={query}>
        {(page) => (
          <>
            {page.items.length === 0 ? (
              <EmptyState
                title="No verified measurements"
                detail="There are no verified results in this context."
              />
            ) : (
              <>
                <Suspense
                  fallback={
                    <Skeleton className="h-48 w-full motion-reduce:animate-none" />
                  }
                >
                  <MeasurementChart measurements={page.items} />
                </Suspense>
                <Table className="mt-4 text-xs">
                  <TableHeader>
                    <TableRow>
                      <TableHead>Result</TableHead>
                      <TableHead className="text-right">kgCO2e</TableHead>
                      <TableHead className="text-right">Source</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {page.items.map((item, index) => (
                      <TableRow key={item.id}>
                        <TableCell>
                          <div className="font-medium">
                            M{index + 1}{' '}
                            <span className="ml-2 capitalize">
                              {humanize(item.category)}
                            </span>
                          </div>
                          <div className="mt-1 text-muted-foreground">
                            {formatDate(item.created_at)}
                          </div>
                        </TableCell>
                        <TableCell className="text-right font-mono tabular-nums">
                          {formatDecimal(item.value_kgco2e)}
                        </TableCell>
                        <TableCell className="text-right">
                          <Button
                            size="icon"
                            variant="ghost"
                            className="size-8"
                            aria-label={`Inspect measurement M${index + 1}`}
                            title="Inspect measurement and lineage"
                            onClick={() => onInspect(item.id)}
                          >
                            <ArrowUpRight />
                          </Button>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </>
            )}
            {page.total > 0 && (
              <PageControls
                {...page}
                count={page.items.length}
                onChange={setOffset}
              />
            )}
          </>
        )}
      </QueryState>
    </section>
  )
}
