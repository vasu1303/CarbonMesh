import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { EmptyState, QueryRefresh, QueryState } from '@/components/query-state'
import { Badge } from '@/components/ui/badge'
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
import { formatDate, formatDecimal, humanize } from '@/lib/format'
import type { WorkspaceScope } from '@/lib/workspace'
import { measurementQueries } from '../queries'
import type { MeasurementDetail } from '../schemas'
import { StatusBadge } from './status-badge'

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="py-2">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-1 break-all font-mono text-xs leading-relaxed">
        {value}
      </dd>
    </div>
  )
}

function RecordedFacts({ item }: { item: MeasurementDetail }) {
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
    <>
      <div className="my-4 flex flex-wrap items-center gap-2">
        <StatusBadge status={item.status} />
        <span className="text-xs text-muted-foreground">
          {item.site_name} / {item.reporting_period_name}
        </span>
      </div>
      {item.status !== 'verified' && (
        <p
          role="status"
          className="mb-4 border-l-2 border-amber-500 pl-3 text-sm text-amber-700 dark:text-amber-400"
        >
          {item.status === 'superseded'
            ? 'Historical result. A newer record has superseded this measurement.'
            : item.status === 'unsupported'
              ? 'Unsupported result. No verified emissions or confidence value is available.'
              : 'Draft result. This measurement has not been verified.'}
        </p>
      )}
      <dl className="grid grid-cols-1 gap-x-5 divide-y sm:grid-cols-2">
        <Fact label="Measurement ID" value={item.id} />
        <Fact
          label="Metric / version"
          value={`${item.metric_key} / ${item.metric_version}`}
        />
        <Fact
          label="Emissions / kgCO2e"
          value={
            item.status === 'unsupported'
              ? 'Not supported'
              : formatDecimal(item.value_kgco2e)
          }
        />
        <Fact
          label="Confidence / 0-1"
          value={
            item.status === 'unsupported'
              ? 'Not assessed'
              : formatDecimal(item.confidence)
          }
        />
        <Fact label="Created" value={formatDate(item.created_at)} />
        <Fact
          label="Verified"
          value={
            item.verified_at ? formatDate(item.verified_at) : 'Not verified'
          }
        />
      </dl>
      <h3 className="mt-6 mb-2 text-sm font-semibold">Recorded method</h3>
      <dl className="divide-y">
        <Fact
          label="Method / version"
          value={`${item.calculation_run.method_key} / ${item.calculation_run.method_version}`}
        />
        <Fact label="Formula" value={item.formula} />
        <Fact label="Code version" value={item.calculation_run.code_version} />
        <Fact
          label="Rounding policy"
          value={item.calculation_run.rounding_policy}
        />
        <Fact label="Input hash" value={item.calculation_run.input_hash} />
        {item.calculation_run.method_hash && (
          <Fact label="Method hash" value={item.calculation_run.method_hash} />
        )}
        {item.calculation_run.code_hash && (
          <Fact label="Code hash" value={item.calculation_run.code_hash} />
        )}
        <Fact label="Output hash" value={item.output_hash} />
        <Fact
          label="Ledger event ID"
          value={item.facts.ledger_event_id ?? 'Not available'}
        />
      </dl>
      {item.status !== 'unsupported' && (
        <>
          <h3 className="mt-6 mb-2 text-sm font-semibold">
            Confidence breakdown
          </h3>
          <Table className="text-xs">
            <TableHeader>
              <TableRow>
                <TableHead>Recorded component</TableHead>
                <TableHead className="text-right">Score / 0-1</TableHead>
                <TableHead className="text-right">Weight / 0-1</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {components.map(([name, value, weight]) => (
                <TableRow key={name}>
                  <TableCell>{name}</TableCell>
                  <TableCell className="text-right font-mono">
                    {formatDecimal(value)}
                  </TableCell>
                  <TableCell className="text-right font-mono">
                    {formatDecimal(weight)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </>
      )}
      {item.coverage && (
        <>
          <h3 className="mt-6 mb-2 text-sm font-semibold">
            Recorded time coverage
          </h3>
          <dl>
            <Fact
              label="Coverage"
              value={
                item.coverage.full_reporting_period
                  ? 'Full reporting period'
                  : 'Partial reporting period'
              }
            />
            <Fact
              label="Interval"
              value={`${formatDate(item.coverage.interval_start)} to ${formatDate(item.coverage.interval_end)}`}
            />
            <Fact
              label="Observed / reporting period hours"
              value={`${item.coverage.observed_hours} / ${item.coverage.reporting_period_hours}`}
            />
            <Fact
              label="Reporting period fraction / 0-1"
              value={formatDecimal(item.coverage.reporting_period_fraction)}
            />
          </dl>
        </>
      )}
      {item.baseline && item.status !== 'unsupported' && (
        <>
          <h3 className="mt-6 mb-2 text-sm font-semibold">
            Recorded baseline comparison
          </h3>
          <dl>
            <Fact
              label="Baseline"
              value={`${item.baseline.name} / ${item.baseline.baseline_id}`}
            />
            <Fact
              label="Baseline / kgCO2e"
              value={formatDecimal(item.baseline.value_kgco2e)}
            />
            <Fact
              label="Variance / kgCO2e"
              value={formatDecimal(item.baseline.variance_kgco2e)}
            />
            <Fact
              label="Variance / percent"
              value={
                item.baseline.variance_pct === null
                  ? 'Not available'
                  : formatDecimal(item.baseline.variance_pct)
              }
            />
          </dl>
        </>
      )}
    </>
  )
}

function Evidence({ item }: { item: MeasurementDetail }) {
  return (
    <>
      <h3 className="my-3 text-sm font-semibold">Source activity rows</h3>
      {item.inputs.length === 0 && (
        <EmptyState
          title="No activity rows returned"
          detail="The API did not provide source inputs for this record."
        />
      )}
      {item.inputs.map((input) => (
        <dl key={input.activity_record_id} className="mb-4 border-b pb-4">
          <Fact label="Raw row ID" value={input.raw_activity_record_id} />
          <Fact
            label="Source row"
            value={`${input.source_row_key}${input.source_row_number === null ? '' : ` / row ${input.source_row_number}`}`}
          />
          <Fact label="Material" value={input.material_code} />
          <Fact
            label="Source quantity"
            value={`${formatDecimal(input.source_quantity)} ${input.source_unit}`}
          />
          <Fact label="Source document ID" value={input.source_document_id} />
          <Fact label="Source checksum" value={input.raw_checksum} />
          {input.interval_start && (
            <Fact
              label="Interval start"
              value={formatDate(input.interval_start)}
            />
          )}
          {input.normalized_quantity_kwh != null && (
            <Fact
              label="Normalized electricity / kWh"
              value={formatDecimal(input.normalized_quantity_kwh)}
            />
          )}
        </dl>
      ))}
      <h3 className="my-3 text-sm font-semibold">Factor evidence</h3>
      {item.factors.length === 0 && item.grid_points.length === 0 && (
        <EmptyState
          title="No supported factor evidence"
          detail="No factor evidence was returned for this measurement."
        />
      )}
      {item.factors.map((factor) => (
        <dl key={factor.id} className="mb-4 border-b pb-4">
          <Fact
            label="Factor / version"
            value={`${factor.name} / ${factor.version}`}
          />
          <Fact
            label="Value"
            value={`${formatDecimal(factor.factor_value)} ${factor.numerator_unit}/${factor.denominator_unit}`}
          />
          <Fact
            label="Geography / effective dates"
            value={`${factor.geography} / ${factor.effective_from} to ${factor.effective_to ?? 'open-ended'}`}
          />
          <Fact
            label="Source document"
            value={factor.evidence.source_document_filename}
          />
          <Fact
            label="Evidence ID / locator"
            value={`${factor.evidence.id} / ${factor.evidence.locator}`}
          />
          <Fact label="Evidence checksum" value={factor.evidence.checksum} />
        </dl>
      ))}
      {item.grid_points.length > 0 && (
        <h3 className="my-3 text-sm font-semibold">Hourly grid evidence</h3>
      )}
      {item.grid_points.map((point) => (
        <dl key={point.id} className="mb-4 border-b pb-4">
          <Fact label="Grid point ID" value={point.id} />
          <Fact
            label="Provider / zone"
            value={`${point.provider} / ${point.zone}`}
          />
          <Fact label="Observed at" value={formatDate(point.observed_at)} />
          <Fact
            label="Intensity / gCO2e per kWh"
            value={formatDecimal(point.intensity_gco2e_per_kwh)}
          />
          <Fact
            label="Grid method / granularity"
            value={`${point.method_version} / ${point.temporal_granularity}`}
          />
          <Fact label="Estimated" value={point.is_estimated ? 'Yes' : 'No'} />
          <Fact label="Point hash" value={point.point_hash} />
          <Fact
            label="Source document"
            value={point.evidence.source_document_filename}
          />
          <Fact
            label="Evidence ID / locator"
            value={`${point.evidence.id} / ${point.evidence.locator}`}
          />
          <Fact label="Evidence checksum" value={point.evidence.checksum} />
        </dl>
      ))}
      <h3 className="my-3 text-sm font-semibold">Calculation records</h3>
      {item.calculations.map((calculation) => (
        <dl key={calculation.id} className="border-b py-2">
          <Fact label="Calculation ID" value={calculation.id} />
          <Fact
            label="Source activity ID"
            value={calculation.activity_record_id}
          />
          <Fact
            label="Emissions / kgCO2e"
            value={formatDecimal(calculation.emissions_kgco2e)}
          />
          <Fact label="Output hash" value={calculation.output_hash} />
          <Fact label="Formula" value={calculation.formula} />
          {calculation.grid_intensity_point_id && (
            <Fact
              label="Grid point ID"
              value={calculation.grid_intensity_point_id}
            />
          )}
          {calculation.normalized_quantity_kwh != null && (
            <Fact
              label="Electricity / kWh"
              value={formatDecimal(calculation.normalized_quantity_kwh)}
            />
          )}
          {calculation.intensity_gco2e_per_kwh != null && (
            <Fact
              label="Intensity / gCO2e per kWh"
              value={formatDecimal(calculation.intensity_gco2e_per_kwh)}
            />
          )}
        </dl>
      ))}
    </>
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
        className="max-h-[85svh] overflow-y-auto rounded-lg p-5 sm:max-w-3xl motion-reduce:animate-none"
        onCloseAutoFocus={(event) => {
          event.preventDefault()
          returnFocusTarget?.focus()
        }}
      >
        <DialogHeader className="pr-8">
          <DialogTitle>Measurement inspection</DialogTitle>
          <DialogDescription>
            API records for the selected company, site and period.
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
          <TabsContent value="facts" className="mt-3">
            <div className="flex justify-end">
              <QueryRefresh query={detail} label="record facts" />
            </div>
            <QueryState query={detail}>
              {(item) => <RecordedFacts item={item} />}
            </QueryState>
          </TabsContent>
          <TabsContent value="evidence" className="mt-3">
            <div className="flex justify-end">
              <QueryRefresh query={detail} label="record evidence" />
            </div>
            <QueryState query={detail}>
              {(item) => <Evidence item={item} />}
            </QueryState>
          </TabsContent>
          <TabsContent value="lineage" className="mt-3">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold">
                Source-to-result lineage
              </h3>
              <QueryRefresh query={lineage} label="record lineage" />
            </div>
            <QueryState query={lineage}>
              {(graph) => (
                <>
                  {graph.truncated && (
                    <p
                      role="status"
                      className="my-3 text-sm text-amber-700 dark:text-amber-400"
                    >
                      Partial lineage: the API traversal limit was reached.
                    </p>
                  )}
                  {graph.nodes.length === 0 ? (
                    <EmptyState
                      title="No lineage returned"
                      detail="No traceable source relationships are available for this record."
                    />
                  ) : (
                    <ol className="mt-4 divide-y">
                      {graph.nodes.map((node) => (
                        <li key={node.id} className="py-3">
                          <div className="flex flex-wrap items-center gap-2">
                            <p className="text-sm font-medium">{node.label}</p>
                            <Badge variant="outline">
                              {humanize(node.node_type)}
                            </Badge>
                          </div>
                          <p className="mt-2 break-all font-mono text-xs text-muted-foreground">
                            {node.id}
                          </p>
                          {node.payload_hash && (
                            <p className="mt-2 break-all font-mono text-xs text-muted-foreground">
                              Hash: {node.payload_hash}
                            </p>
                          )}
                        </li>
                      ))}
                    </ol>
                  )}
                  <ul className="mt-4 divide-y">
                    {graph.edges.map((edge) => (
                      <li key={edge.id} className="py-3 text-xs">
                        <p className="font-medium capitalize">
                          {humanize(edge.relationship_type)}
                        </p>
                        <p className="mt-1 break-words text-muted-foreground">
                          {graph.nodes.find((node) => node.id === edge.source)
                            ?.label ?? edge.source}{' '}
                          to{' '}
                          {graph.nodes.find((node) => node.id === edge.target)
                            ?.label ?? edge.target}
                        </p>
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </QueryState>
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  )
}
