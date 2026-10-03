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
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'
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
import { formatDate, formatDecimal, humanize } from '@/lib/format'
import type { WorkspaceScope } from '@/lib/workspace'
import { RecordedFacts } from './components/record-inspector'
import { measurementQueries } from './queries'
import type { MeasurementDetail, MeasurementLineage } from './schemas'

const BreakdownChart = lazy(() => import('./components/breakdown-chart'))
const pageSize = 20

function EvidenceRecords({ item }: { item: MeasurementDetail }) {
  const [kind, setKind] = useState('activities')
  const [offset, setOffset] = useState(0)
  const total =
    kind === 'activities'
      ? item.inputs.length
      : kind === 'factors'
        ? item.factors.length
        : item.grid_points.length
  const pageInputs = item.inputs.slice(offset, offset + pageSize)
  const sources =
    kind === 'factors'
      ? item.factors
          .slice(offset, offset + pageSize)
          .map((factor) => ({
            id: factor.id,
            label: factor.name,
            value: `${formatDecimal(factor.factor_value)} ${factor.numerator_unit}/${factor.denominator_unit}`,
            version: factor.version,
            evidence: factor.evidence,
          }))
      : item.grid_points
          .slice(offset, offset + pageSize)
          .map((point) => ({
            id: point.id,
            label: `${point.provider} / ${point.zone}`,
            value: `${formatDecimal(point.intensity_gco2e_per_kwh)} gCO2e/kWh`,
            version: `${point.method_version} / ${formatDate(point.observed_at)}${point.is_estimated ? ' / estimated' : ''}`,
            evidence: point.evidence,
          }))
  return (
    <>
      <div className="mb-5 max-w-full space-y-2">
        <label htmlFor="evidence-kind" className="text-xs font-medium">
          Source records
        </label>
        <NativeSelect
          id="evidence-kind"
          value={kind}
          onChange={(event) => {
            setKind(event.target.value)
            setOffset(0)
          }}
        >
          <NativeSelectOption value="activities">
            Activity rows
          </NativeSelectOption>
          <NativeSelectOption value="factors">
            Emission factors
          </NativeSelectOption>
          <NativeSelectOption value="grid">
            Hourly grid points
          </NativeSelectOption>
        </NativeSelect>
      </div>
      {total === 0 ? (
        <EmptyState
          title="No source records"
          detail="The measurement contains no records of this source type."
        />
      ) : kind === 'activities' ? (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Source / material</TableHead>
              <TableHead>Quantity</TableHead>
              <TableHead>Activity ID</TableHead>
              <TableHead>Document</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {pageInputs.map((input) => (
              <TableRow key={input.activity_record_id}>
                <TableCell className="max-w-72 whitespace-normal wrap-anywhere">
                  <p>{input.source_row_key}</p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {input.material_code}
                    {input.interval_start
                      ? ` / ${formatDate(input.interval_start)}`
                      : ''}
                  </p>
                </TableCell>
                <TableCell className="font-mono">
                  {formatDecimal(input.source_quantity)} {input.source_unit}
                </TableCell>
                <TableCell className="max-w-56 whitespace-normal break-all font-mono text-xs">
                  {input.activity_record_id}
                </TableCell>
                <TableCell>
                  <Button asChild variant="link" size="sm">
                    <Link to={`/data?document=${input.source_document_id}`}>
                      Source
                      <ArrowUpRight className="size-4" />
                    </Link>
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      ) : (
        <ul className="divide-y">
          {sources.map((source) => (
            <li key={source.id} className="space-y-2 py-4 wrap-anywhere">
              <p className="font-medium">{source.label}</p>
              <p className="font-mono text-sm">{source.value}</p>
              <p className="text-xs text-muted-foreground">{source.version}</p>
              <p className="text-xs">
                {source.evidence.source_document_filename} /{' '}
                {source.evidence.locator}
              </p>
              <p className="break-all font-mono text-xs text-muted-foreground">
                Evidence {source.evidence.id}
              </p>
              <p className="break-all font-mono text-xs text-muted-foreground">
                SHA-256 {source.evidence.checksum}
              </p>
              <Button asChild variant="link" className="h-auto p-0 text-xs">
                <Link
                  to={`/data?document=${source.evidence.source_document_id}`}
                >
                  Source document
                  <ArrowUpRight className="size-4" />
                </Link>
              </Button>
            </li>
          ))}
        </ul>
      )}
      <PageControls
        total={total}
        limit={pageSize}
        offset={offset}
        count={Math.max(0, Math.min(pageSize, total - offset))}
        onChange={setOffset}
      />
    </>
  )
}

function LineageRecords({ graph }: { graph: MeasurementLineage }) {
  const [offset, setOffset] = useState(0)
  const nodes = graph.nodes.slice(offset, offset + pageSize)
  const labels = new Map(graph.nodes.map((node) => [node.id, node.label]))
  return (
    <>
      {graph.truncated && (
        <p
          role="status"
          className="mb-4 text-sm text-amber-700 dark:text-amber-400"
        >
          Partial lineage: the backend traversal limit was reached.
        </p>
      )}
      {graph.nodes.length === 0 ? (
        <EmptyState
          title="No lineage returned"
          detail="There are no recorded source relationships for this measurement."
        />
      ) : (
        <ol className="divide-y">
          {nodes.map((node) => (
            <li key={node.id} className="py-4 wrap-anywhere">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-medium">
                  {humanize(node.label)}
                </span>
                <Badge variant="outline">{humanize(node.node_type)}</Badge>
              </div>
              <p className="mt-2 break-all font-mono text-xs text-muted-foreground">
                {node.id}
              </p>
              {node.node_type === 'ledger_event' &&
                z.uuid().safeParse(node.id).success && (
                  <Button
                    asChild
                    variant="link"
                    className="mt-2 h-auto p-0 text-xs"
                  >
                    <Link to={`/ledger?event=${node.id}`}>
                      Inspect ledger event
                      <ArrowUpRight className="size-4" />
                    </Link>
                  </Button>
                )}
              <ul className="mt-3 space-y-1 border-l pl-3 text-xs text-muted-foreground">
                {graph.edges
                  .filter((edge) => edge.source === node.id)
                  .map((edge) => (
                    <li key={edge.id}>
                      {humanize(edge.relationship_type)}:{' '}
                      {labels.get(edge.target) ?? edge.target}
                    </li>
                  ))}
              </ul>
            </li>
          ))}
        </ol>
      )}
      <PageControls
        total={graph.nodes.length}
        limit={pageSize}
        offset={offset}
        count={nodes.length}
        onChange={setOffset}
      />
    </>
  )
}

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
            Measurements
          </Link>
        </Button>
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="min-w-0">
            <h1 className="text-2xl font-semibold">Measurement record</h1>
            <p className="mt-2 break-all font-mono text-xs text-muted-foreground">
              {id}
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
              <span className="font-medium">{data.company.name}</span>
              <span className="text-muted-foreground">
                {data.site.name} / {data.reporting_period.name}
              </span>
              <Badge variant="outline" className="sm:ml-auto">
                {data.company.is_synthetic
                  ? 'Synthetic data / API'
                  : 'Non-synthetic data / API'}
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
              {(item) => (
                <>
                  <RecordedFacts item={item} />
                  {item.facts.ledger_event_id && (
                    <Button
                      asChild
                      variant="outline"
                      size="sm"
                      className="mt-5"
                    >
                      <Link to={`/ledger?event=${item.facts.ledger_event_id}`}>
                        Ledger event
                        <ArrowUpRight />
                      </Link>
                    </Button>
                  )}
                </>
              )}
            </QueryState>
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
                        <BreakdownChart
                          items={data.items.slice(offset, offset + pageSize)}
                        />
                      </Suspense>
                      <p className="my-4 text-xs text-muted-foreground">
                        Individual calculations on this page. The recorded total
                        comes from the measurement.
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
                                    {item.material_code}
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
              {(graph) => <LineageRecords key={id} graph={graph} />}
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
          <Link to="/measurement">Back to measurements</Link>
        </Button>
      </section>
    )
  return <MeasurementRecord key={parsed.data} id={parsed.data} scope={scope} />
}
