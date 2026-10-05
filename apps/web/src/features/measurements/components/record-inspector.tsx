import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { QueryRefresh, QueryState } from '@/components/query-state'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
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
import { measurementQueries } from '../queries'
import type { MeasurementDetail } from '../schemas'
import { EvidenceRecords } from './evidence-records'
import { MeasurementLineageView } from './measurement-lineage'
import { StatusBadge } from './status-badge'

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 py-2">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-1 text-sm leading-relaxed wrap-anywhere">
        {displayText(value)}
      </dd>
    </div>
  )
}

export function RecordedFacts({ item }: { item: MeasurementDetail }) {
  const breakdown = item.confidence_breakdown
  const components =
    breakdown.version === '2.0.0'
      ? [
          [
            'Source quality',
            breakdown.source_quality,
            breakdown.weights.source_quality,
          ],
          ['Method fit', breakdown.method_fit, breakdown.weights.method_fit],
          [
            'Temporal match',
            breakdown.temporal_match,
            breakdown.weights.temporal_match,
          ],
          [
            'Completeness',
            breakdown.completeness,
            breakdown.weights.completeness,
          ],
        ]
      : [
          [
            'Source quality',
            breakdown.source_quality,
            breakdown.source_quality_weight,
          ],
          [
            'Factor specificity',
            breakdown.factor_specificity,
            breakdown.factor_specificity_weight,
          ],
          [
            'Factor recency',
            breakdown.factor_recency,
            breakdown.factor_recency_weight,
          ],
          [
            'Record completeness',
            breakdown.record_completeness,
            breakdown.record_completeness_weight,
          ],
        ]
  return (
    <div className="min-w-0 space-y-5">
      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge status={item.status} />
        <span className="text-xs text-muted-foreground">
          {displayText(item.site_name)} /{' '}
          {displayText(item.reporting_period_name)}
        </span>
      </div>
      {item.status !== 'verified' && (
        <p
          role="status"
          className="border-l-2 border-amber-500 pl-3 text-sm text-amber-700 dark:text-amber-400"
        >
          {item.status === 'superseded'
            ? 'Historical result. A newer record has superseded this measurement.'
            : item.status === 'unsupported'
              ? 'Unsupported result. No verified emissions or confidence value is available.'
              : 'Draft result. This measurement has not been verified.'}
        </p>
      )}
      <dl className="grid gap-x-8 gap-y-3 border-b pb-5 sm:grid-cols-2">
        <div className="min-w-0">
          <dt className="text-sm text-muted-foreground">Measured emissions</dt>
          <dd className="mt-2 text-2xl font-semibold tabular-nums wrap-anywhere">
            {item.status === 'unsupported'
              ? 'Not supported'
              : formatDecimal(item.value_kgco2e)}{' '}
            {item.status !== 'unsupported' && (
              <span className="text-sm font-normal">kgCO2e</span>
            )}
          </dd>
        </div>
        <div>
          <dt className="text-sm text-muted-foreground">Confidence (0-1)</dt>
          <dd className="mt-2 text-2xl font-semibold tabular-nums">
            {item.status === 'unsupported'
              ? 'Not assessed'
              : formatDecimal(item.confidence)}
          </dd>
        </div>
        <Fact label="Metric" value={humanize(item.metric_key)} />
        <Fact label="Recorded" value={formatDate(item.created_at)} />
      </dl>
      {item.baseline && item.status !== 'unsupported' && (
        <section>
          <h3 className="text-sm font-semibold">
            {displayText(item.baseline.name)}
          </h3>
          <dl className="grid gap-x-5 sm:grid-cols-3">
            <Fact
              label="Baseline / kgCO2e"
              value={formatDecimal(item.baseline.value_kgco2e)}
            />
            <Fact
              label="Change / kgCO2e"
              value={formatDecimal(item.baseline.variance_kgco2e)}
            />
            <Fact
              label="Change / percent"
              value={
                item.baseline.variance_pct === null
                  ? 'Not available'
                  : formatDecimal(item.baseline.variance_pct)
              }
            />
          </dl>
        </section>
      )}
      {item.coverage && (
        <section>
          <h3 className="text-sm font-semibold">
            {item.coverage.full_reporting_period
              ? 'Full reporting period'
              : 'Partial reporting period'}
          </h3>
          <dl className="grid gap-x-5 sm:grid-cols-2">
            <Fact
              label="Measured interval"
              value={
                formatDate(item.coverage.interval_start) +
                ' to ' +
                formatDate(item.coverage.interval_end)
              }
            />
            <Fact
              label="Observed / reporting period hours"
              value={
                item.coverage.observed_hours +
                ' / ' +
                item.coverage.reporting_period_hours
              }
            />
          </dl>
        </section>
      )}
      <details className="border-t pt-4">
        <summary className="cursor-pointer text-sm font-semibold">
          Method and confidence
        </summary>
        <dl className="mt-3 grid gap-x-5 sm:grid-cols-2">
          <Fact
            label="Method"
            value={
              humanize(item.calculation_run.method_key) +
              ' / ' +
              displayText(item.calculation_run.method_version)
            }
          />
          <Fact
            label="Calculation"
            value={humanize(item.calculation_run.status)}
          />
          <Fact label="Formula" value={item.formula} />
          <Fact
            label="Rounding"
            value={humanize(item.calculation_run.rounding_policy)}
          />
          <Fact
            label="Verified"
            value={
              item.verified_at ? formatDate(item.verified_at) : 'Not verified'
            }
          />
        </dl>
        {item.status !== 'unsupported' && (
          <>
            <h3 className="mt-4 mb-2 text-sm font-semibold">
              Confidence breakdown
            </h3>
            <Table className="text-xs">
              <TableHeader>
                <TableRow>
                  <TableHead>Component</TableHead>
                  <TableHead className="text-right">Score / 0-1</TableHead>
                  <TableHead className="text-right">Weight / 0-1</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {components.map(([name, value, weight]) => (
                  <TableRow key={name}>
                    <TableCell>{name}</TableCell>
                    <TableCell className="text-right tabular-nums">
                      {formatDecimal(value)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {formatDecimal(weight)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </>
        )}
      </details>
      {item.facts.ledger_event_id && (
        <Button asChild variant="outline" size="sm">
          <Link to={'/ledger?event=' + item.facts.ledger_event_id}>
            Ledger event
          </Link>
        </Button>
      )}
    </div>
  )
}

export default function RecordInspector({
  scope,
  id,
  initialTab,
  onClose,
}: {
  scope: WorkspaceScope
  id: string
  initialTab: string
  onClose: () => void
}) {
  const [returnFocusTarget] = useState(() =>
    document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null,
  )
  const [tab, setTab] = useState(initialTab)
  const detail = useQuery(measurementQueries.detail(scope, id))
  const lineage = useQuery(measurementQueries.lineage(scope, id))
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
    >
      <DialogContent
        className="max-h-[85svh] min-w-0 grid-cols-1 overflow-y-auto rounded-lg p-5 sm:max-w-3xl motion-reduce:animate-none"
        onCloseAutoFocus={(event) => {
          event.preventDefault()
          returnFocusTarget?.focus()
        }}
      >
        <DialogHeader className="min-w-0 pr-8">
          <DialogTitle>Measurement inspection</DialogTitle>
          <DialogDescription>
            {detail.data
              ? humanize(detail.data.metric_key) +
                ' / ' +
                formatDate(detail.data.created_at)
              : 'Measurement and source evidence'}
          </DialogDescription>
        </DialogHeader>
        <Tabs value={tab} onValueChange={setTab} className="min-w-0">
          <TabsList className="w-full">
            <TabsTrigger value="facts" className="flex-1">
              Facts
            </TabsTrigger>
            <TabsTrigger value="evidence" className="flex-1">
              Evidence
            </TabsTrigger>
            <TabsTrigger value="lineage" className="flex-1">
              Lineage
            </TabsTrigger>
          </TabsList>
          <TabsContent value="facts" className="mt-3 min-w-0">
            <div className="flex justify-end">
              <QueryRefresh query={detail} label="record facts" />
            </div>
            <QueryState query={detail}>
              {(item) => <RecordedFacts item={item} />}
            </QueryState>
          </TabsContent>
          <TabsContent value="evidence" className="mt-3 min-w-0">
            <div className="flex justify-end">
              <QueryRefresh query={detail} label="record evidence" />
            </div>
            <QueryState query={detail}>
              {(item) => <EvidenceRecords item={item} />}
            </QueryState>
          </TabsContent>
          <TabsContent value="lineage" className="mt-3 min-w-0 space-y-3">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold">
                Source-to-result lineage
              </h3>
              <QueryRefresh query={lineage} label="record lineage" />
            </div>
            <QueryState query={lineage}>
              {(graph) => <MeasurementLineageView graph={graph} />}
            </QueryState>
          </TabsContent>
        </Tabs>
        <Button asChild variant="link" className="justify-start px-0">
          <Link to={'/measurement/' + id}>Open measurement details</Link>
        </Button>
      </DialogContent>
    </Dialog>
  )
}
