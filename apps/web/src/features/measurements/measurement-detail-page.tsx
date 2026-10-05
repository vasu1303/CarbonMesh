import { useQuery } from '@tanstack/react-query'
import { ArrowLeft, ArrowUpRight } from 'lucide-react'
import { lazy, Suspense, useState } from 'react'
import {
  Link,
  useOutletContext,
  useParams,
  useSearchParams,
} from 'react-router-dom'
import { z } from 'zod'

import {
  EmptyState,
  PageControls,
  QueryRefresh,
  QueryState,
} from '@/components/query-state'
import { Badge } from '@/components/ui/badge'
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { formatDate, formatDecimal } from '@/lib/format'
import { displayText, humanize } from '@/lib/presentation'
import type { WorkspaceScope } from '@/lib/workspace'
import { RecordedFacts } from './components/record-inspector'
import { EvidenceRecords } from './components/evidence-records'
import { MeasurementLineageView } from './components/measurement-lineage'
import { measurementQueries } from './queries'

const BreakdownChart = lazy(() => import('./components/breakdown-chart'))
const pageSize = 20

function MeasurementRecord({
  id,
  scope,
}: {
  id: string
  scope: WorkspaceScope
}) {
  const [params, setParams] = useSearchParams()
  const tab = ['facts', 'calculations', 'evidence', 'lineage'].includes(
    params.get('tab') ?? '',
  )
    ? params.get('tab')!
    : 'facts'
  const [offset, setOffset] = useState(0)
  const detail = useQuery(measurementQueries.detail(scope, id))
  const lineage = useQuery(measurementQueries.lineage(scope, id))
  const breakdown = useQuery(measurementQueries.breakdown(scope, id))
  const context = useQuery(measurementQueries.context(scope))
  return (
    <>
      <header className="space-y-4 px-5 py-6 sm:px-8">
        <Button asChild variant="ghost" size="sm" className="-ml-2">
          <Link to="/measurement">
            <ArrowLeft />
            Emissions
          </Link>
        </Button>
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="min-w-0">
            <h1 className="text-2xl font-semibold">Emission record</h1>
            <p className="mt-2 text-sm text-muted-foreground wrap-anywhere">
              {detail.data
                ? humanize(detail.data.metric_key)
                : 'Measurement details'}
            </p>
          </div>
          <QueryRefresh query={detail} label="measurement record" />
        </div>
      </header>
      <section
        aria-label="Measurement context"
        className="mx-5 mb-5 border-y py-3 sm:mx-8"
      >
        <QueryState query={context}>
          {(data) => (
            <div className="flex flex-wrap items-center gap-3 text-sm">
              <span className="font-medium">
                {displayText(data.company.name)}
              </span>
              <span className="text-muted-foreground">
                {displayText(data.site.name)} /{' '}
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
      <Tabs
        value={tab}
        onValueChange={(value) => setParams({ tab: value })}
        className="gap-0"
      >
        <div className="overflow-x-auto px-5 pb-5 sm:px-8">
          <TabsList aria-label="Measurement detail">
            <TabsTrigger value="facts">Facts</TabsTrigger>
            <TabsTrigger value="calculations">Calculations</TabsTrigger>
            <TabsTrigger value="evidence">Evidence</TabsTrigger>
            <TabsTrigger value="lineage">Lineage</TabsTrigger>
          </TabsList>
        </div>
        <div className="min-w-0 border-y bg-card px-5 py-5 sm:px-8">
          <TabsContent value="facts">
            <QueryState query={detail}>
              {(item) => <RecordedFacts item={item} />}
            </QueryState>
            <section
              aria-label="Emissions breakdown"
              className="mt-6 space-y-4 border-t pt-5"
            >
              <div className="flex items-center justify-between">
                <h2 className="text-sm font-semibold">Emissions by activity</h2>
                <QueryRefresh query={breakdown} label="emissions breakdown" />
              </div>
              <QueryState query={breakdown}>
                {(data) =>
                  data.status === 'unsupported' ? (
                    <EmptyState
                      title="Unsupported measurement"
                      detail="No verified emissions breakdown is available."
                    />
                  ) : data.items.length === 0 ? (
                    <EmptyState
                      title="No calculations returned"
                      detail="No emissions breakdown is available for this measurement."
                    />
                  ) : (
                    <Suspense
                      fallback={
                        <Skeleton className="h-56 motion-reduce:animate-none" />
                      }
                    >
                      <BreakdownChart items={data.items} />
                    </Suspense>
                  )
                }
              </QueryState>
            </section>
          </TabsContent>
          <TabsContent value="calculations">
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-sm font-semibold">Recorded calculations</h2>
              <QueryRefresh query={breakdown} label="calculation breakdown" />
            </div>
            <QueryState query={breakdown}>
              {(data) => (
                <>
                  {data.status === 'unsupported' ? (
                    <EmptyState
                      title="Unsupported measurement"
                      detail="These calculations are not verified facts."
                    />
                  ) : data.items.length === 0 ? (
                    <EmptyState
                      title="No calculations returned"
                      detail="No calculation breakdown is available for this record."
                    />
                  ) : (
                    <>
                      <p className="mb-4 text-sm">
                        Recorded total{' '}
                        <span className="ml-2 font-mono font-semibold">
                          {formatDecimal(data.total_kgco2e)} {data.unit}
                        </span>
                      </p>
                      <Suspense
                        fallback={
                          <Skeleton className="h-56 motion-reduce:animate-none" />
                        }
                      >
                        <BreakdownChart items={data.items} />
                      </Suspense>
                      <p className="my-4 text-xs text-muted-foreground">
                        Recorded activity emissions. Source records below are
                        paginated.
                      </p>
                      <Table>
                        <TableHeader>
                          <TableRow>
                            <TableHead>Interval / material</TableHead>
                            <TableHead>Quantity</TableHead>
                            <TableHead>Emissions / kgCO2e</TableHead>
                            <TableHead>Source</TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {data.items
                            .slice(offset, offset + pageSize)
                            .map((item) => (
                              <TableRow key={item.calculation_id}>
                                <TableCell className="whitespace-normal">
                                  <p>
                                    {item.interval_start
                                      ? formatDate(item.interval_start)
                                      : (item.activity_date ??
                                        'No date recorded')}
                                  </p>
                                  <p className="mt-1 text-xs text-muted-foreground">
                                    {humanize(item.material_code)}
                                  </p>
                                </TableCell>
                                <TableCell className="font-mono">
                                  {formatDecimal(item.quantity)}{' '}
                                  {item.quantity_unit}
                                </TableCell>
                                <TableCell className="font-mono">
                                  {formatDecimal(item.emissions_kgco2e)}
                                </TableCell>
                                <TableCell>
                                  <Button asChild variant="link" size="sm">
                                    <Link
                                      to={`/data?document=${item.source_document_id}`}
                                    >
                                      Document
                                      <ArrowUpRight />
                                    </Link>
                                  </Button>
                                </TableCell>
                              </TableRow>
                            ))}
                        </TableBody>
                      </Table>
                      <PageControls
                        total={data.items.length}
                        offset={offset}
                        limit={pageSize}
                        count={
                          data.items.slice(offset, offset + pageSize).length
                        }
                        onChange={setOffset}
                      />
                    </>
                  )}
                </>
              )}
            </QueryState>
          </TabsContent>
          <TabsContent value="evidence">
            <QueryState query={detail}>
              {(item) => <EvidenceRecords key={id} item={item} />}
            </QueryState>
          </TabsContent>
          <TabsContent value="lineage">
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-sm font-semibold">
                Source-to-result lineage
              </h2>
              <QueryRefresh query={lineage} label="measurement lineage" />
            </div>
            <QueryState query={lineage}>
              {(graph) => <MeasurementLineageView key={id} graph={graph} />}
            </QueryState>
          </TabsContent>
        </div>
      </Tabs>
    </>
  )
}

export default function MeasurementDetailPage() {
  const { id } = useParams()
  const scope = useOutletContext<WorkspaceScope>()
  const parsed = z.uuid().safeParse(id)
  if (!parsed.success)
    return (
      <section role="alert" className="space-y-4 p-8">
        <h1 className="text-xl font-semibold">
          Invalid measurement identifier
        </h1>
        <Button asChild variant="outline">
          <Link to="/measurement">Back to emissions</Link>
        </Button>
      </section>
    )
  return <MeasurementRecord key={parsed.data} id={parsed.data} scope={scope} />
}
