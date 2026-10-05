import { useIsFetching, useQuery, useQueryClient } from '@tanstack/react-query'
import { BarChart3, Calculator, List, RefreshCw, RotateCcw } from 'lucide-react'
import { lazy, Suspense, useState } from 'react'
import { useOutletContext, useSearchParams } from 'react-router-dom'
import { z } from 'zod'

import { EmptyState, PageControls, QueryState } from '@/components/query-state'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import type { WorkspaceScope } from '@/lib/workspace'
import { displayText, humanize } from '@/lib/presentation'
import { RecordsTable } from './components/records-table'
import { measurementKey, measurementQueries } from './queries'
import { measurementFiltersSchema, type MeasurementStatus } from './schemas'

const RecordsChart = lazy(() => import('./components/records-chart'))
const RecordInspector = lazy(() => import('./components/record-inspector'))
const CalculateMeasurement = lazy(
  () => import('./components/calculate-measurement'),
)
const statuses: MeasurementStatus[] = [
  'verified',
  'draft',
  'superseded',
  'unsupported',
]

export default function MeasurementsPage() {
  const [calculating, setCalculating] = useState(false)
  const scope = useOutletContext<WorkspaceScope>()
  const [params, setParams] = useSearchParams()
  const filters = measurementFiltersSchema.parse({
    status: params.get('status'),
    category: params.get('category') ?? '',
    limit: params.get('limit') ?? 10,
    offset: params.get('offset') ?? 0,
    view: params.get('view'),
  })
  const { view, ...listFilters } = filters
  const context = useQuery(measurementQueries.context(scope))
  const records = useQuery(measurementQueries.list(scope, listFilters))
  const metrics = useQuery(measurementQueries.metrics(scope))
  const client = useQueryClient()
  const fetching = useIsFetching({ queryKey: measurementKey }) > 0
  const selected = z.uuid().safeParse(params.get('record'))
  const categoryOptions =
    metrics.data?.items.filter((item) => item.canonical_unit === 'kgCO2e') ?? []

  function update(
    values: Record<string, string | number | null>,
    resetPage = true,
  ) {
    const next = new URLSearchParams(params)
    if (resetPage) next.delete('offset')
    next.delete('record')
    next.delete('tab')
    Object.entries(values).forEach(([key, value]) => {
      if (value === null || value === '') next.delete(key)
      else next.set(key, String(value))
    })
    setParams(next, { flushSync: true })
  }
  function inspect(id: string, tab = 'facts') {
    const next = new URLSearchParams(params)
    next.set('record', id)
    next.set('tab', tab)
    setParams(next, { flushSync: true })
  }

  return (
    <>
      <div className="flex flex-wrap items-start justify-between gap-4 px-5 py-6 sm:px-8">
        <div>
          <p className="mb-2 text-xs font-medium text-muted-foreground">
            Carbon ledger
          </p>
          <h1 className="text-2xl font-semibold">Emissions</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            Recorded emissions, confidence and source evidence
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" onClick={() => setCalculating(true)}>
            <Calculator />
            Calculate
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={fetching}
            onClick={() =>
              void client.invalidateQueries({
                queryKey: measurementKey,
              })
            }
          >
            <RefreshCw className={fetching ? 'motion-safe:animate-spin' : ''} />
            Refresh measurements
          </Button>
        </div>
      </div>
      <section
        aria-label="Measurement context"
        className="mx-5 mb-5 border-y py-3 sm:mx-8"
      >
        <QueryState query={context}>
          {(data) => (
            <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
              <span className="font-medium">
                {displayText(data.company.name)}
              </span>
              <span className="text-muted-foreground">
                {displayText(data.site.name)}
              </span>
              <span className="text-muted-foreground">
                {displayText(data.reporting_period.name)}
              </span>
              <Badge variant="outline" className="sm:ml-auto">
                {data.company.is_synthetic
                  ? 'Synthetic data'
                  : 'Non-synthetic data'}
              </Badge>
            </div>
          )}
        </QueryState>
      </section>
      <section
        aria-label="Measurement records"
        className="min-w-0 border-y bg-card px-5 py-5 sm:px-8"
      >
        <div className="mb-5 flex flex-wrap items-end gap-4">
          <div className="min-w-0 space-y-2">
            <label
              htmlFor="measurement-status"
              className="block text-xs font-medium"
            >
              Status
            </label>
            <NativeSelect
              id="measurement-status"
              value={filters.status}
              onChange={(event) =>
                update({
                  status:
                    event.target.value === 'all' ? null : event.target.value,
                })
              }
            >
              <NativeSelectOption value="all">All statuses</NativeSelectOption>
              {statuses.map((status) => (
                <NativeSelectOption key={status} value={status}>
                  {status[0].toUpperCase() + status.slice(1)}
                </NativeSelectOption>
              ))}
            </NativeSelect>
          </div>
          <div className="min-w-0 max-w-full space-y-2">
            <label
              htmlFor="measurement-category"
              className="block text-xs font-medium"
            >
              Category
            </label>
            <NativeSelect
              id="measurement-category"
              className="max-w-full"
              value={filters.category}
              disabled={metrics.isPending}
              onChange={(event) => update({ category: event.target.value })}
            >
              <NativeSelectOption value="">All categories</NativeSelectOption>
              {categoryOptions.map((item) => (
                <NativeSelectOption key={item.id} value={item.key}>
                  {displayText(item.name)}
                </NativeSelectOption>
              ))}
              {filters.category &&
                !categoryOptions.some(
                  (item) => item.key === filters.category,
                ) && (
                  <NativeSelectOption value={filters.category}>
                    {humanize(filters.category)}
                  </NativeSelectOption>
                )}
            </NativeSelect>
          </div>
          <Button
            size="sm"
            variant="ghost"
            disabled={filters.status === 'all' && !filters.category}
            onClick={() => update({ status: null, category: null })}
          >
            <RotateCcw />
            Clear filters
          </Button>
          <Tabs
            value={view}
            onValueChange={(value) => update({ view: value }, false)}
            className="sm:ml-auto"
          >
            <TabsList aria-label="Measurement view">
              <TabsTrigger value="records">
                <List className="size-4" />
                Records
              </TabsTrigger>
              <TabsTrigger value="chart">
                <BarChart3 className="size-4" />
                Chart
              </TabsTrigger>
            </TabsList>
          </Tabs>
        </div>
        {metrics.isError && (
          <div
            role="status"
            className="mb-4 flex flex-wrap items-center gap-2 text-xs text-amber-700 dark:text-amber-400"
          >
            Category definitions unavailable. Records can still load.
            <Button
              size="sm"
              variant="ghost"
              onClick={() => void metrics.refetch()}
            >
              Retry categories
            </Button>
          </div>
        )}
        <QueryState query={records}>
          {(page) => (
            <>
              {page.items.length === 0 ? (
                <EmptyState
                  title={
                    filters.offset
                      ? 'No records on this page'
                      : 'No measurements found'
                  }
                  detail="No records match the selected site, period and filters."
                />
              ) : view === 'records' ? (
                <RecordsTable items={page.items} onInspect={inspect} />
              ) : (
                <Suspense
                  fallback={
                    <Skeleton className="h-64 motion-reduce:animate-none" />
                  }
                >
                  <RecordsChart items={page.items} onInspect={inspect} />
                </Suspense>
              )}
              {page.items.length === 0 && filters.offset > 0 && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => update({ offset: null })}
                >
                  Return to first page
                </Button>
              )}
              <div className="mt-4 flex flex-wrap items-center justify-between gap-4 border-t pt-4">
                <div className="flex items-center gap-2">
                  <label
                    htmlFor="measurement-page-size"
                    className="text-xs text-muted-foreground"
                  >
                    Rows per page
                  </label>
                  <NativeSelect
                    id="measurement-page-size"
                    size="sm"
                    value={filters.limit}
                    onChange={(event) => update({ limit: event.target.value })}
                  >
                    {[5, 10, 25, 50].map((limit) => (
                      <NativeSelectOption key={limit} value={limit}>
                        {limit}
                      </NativeSelectOption>
                    ))}
                  </NativeSelect>
                </div>
                <div className="min-w-48">
                  <PageControls
                    {...page}
                    count={page.items.length}
                    onChange={(offset) => update({ offset }, false)}
                  />
                </div>
              </div>
            </>
          )}
        </QueryState>
      </section>
      <p className="px-5 py-4 text-xs text-muted-foreground sm:px-8">
        Newest records first. Superseded and unsupported results must not be
        used as current verified facts.
      </p>
      {params.has('record') && !selected.success && (
        <div role="alert" className="px-5 py-4 text-sm">
          The measurement identifier is invalid.
          <Button
            variant="link"
            onClick={() => update({ record: null }, false)}
          >
            Dismiss
          </Button>
        </div>
      )}
      {selected.success && (
        <Suspense
          fallback={
            <p
              role="status"
              className="fixed right-5 bottom-5 rounded-md border bg-background px-4 py-3 text-sm"
            >
              Loading measurement...
            </p>
          }
        >
          <RecordInspector
            key={selected.data}
            scope={scope}
            id={selected.data}
            initialTab={params.get('tab') === 'lineage' ? 'lineage' : 'facts'}
            onClose={() => update({ record: null }, false)}
          />
        </Suspense>
      )}
      {calculating && (
        <Suspense
          fallback={
            <p role="status" className="p-5 text-sm">
              Loading calculation form...
            </p>
          }
        >
          <CalculateMeasurement
            scope={scope}
            onClose={() => setCalculating(false)}
          />
        </Suspense>
      )}
    </>
  )
}
