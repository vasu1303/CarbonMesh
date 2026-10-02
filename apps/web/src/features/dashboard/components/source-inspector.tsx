import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { formatDate, formatDecimal, humanize } from '../format'
import { dashboardQueries, type DashboardScope } from '../queries'
import type { Approval, GridIntensity } from '../schemas'
import { QueryRefresh, QueryState } from './query-state'

export type Inspection =
  | { kind: 'measurement' | 'event'; id: string }
  | { kind: 'approval'; item: Approval }
  | { kind: 'grid'; item: GridIntensity }

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

function MeasurementSource({
  scope,
  id,
  onEvent,
}: {
  scope: DashboardScope
  id: string
  onEvent: (id: string) => void
}) {
  const query = useQuery(dashboardQueries.measurement(scope, id))
  const lineage = useQuery(dashboardQueries.lineage(scope, id))
  return (
    <Tabs defaultValue="facts">
      <TabsList>
        <TabsTrigger value="facts">Verified facts</TabsTrigger>
        <TabsTrigger value="lineage">Lineage</TabsTrigger>
      </TabsList>
      <TabsContent value="facts" className="mt-4">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-medium">Measurement source</h3>
          <QueryRefresh query={query} label="measurement source" />
        </div>
        <QueryState query={query}>
          {(item) => (
            <>
              <Badge
                variant={item.status === 'verified' ? 'secondary' : 'outline'}
              >
                {item.status}
              </Badge>
              <dl className="divide-y">
                <Fact label="Measurement ID" value={item.id} />
                <Fact
                  label="Emissions (kgCO2e)"
                  value={formatDecimal(item.value_kgco2e)}
                />
                <Fact
                  label="Confidence (0-1)"
                  value={formatDecimal(item.confidence)}
                />
                <Fact label="Formula" value={item.formula} />
                <Fact
                  label="Method / version"
                  value={`${item.calculation_run.method_key} / ${item.calculation_run.method_version}`}
                />
                <Fact
                  label="Code version"
                  value={item.calculation_run.code_version}
                />
                <Fact
                  label="Input hash"
                  value={item.calculation_run.input_hash}
                />
                <Fact label="Output hash" value={item.output_hash} />
                <Fact
                  label="Rounding policy"
                  value={item.calculation_run.rounding_policy}
                />
              </dl>
              {item.facts.ledger_event_id && (
                <Button
                  size="sm"
                  variant="outline"
                  className="my-3"
                  onClick={() => onEvent(item.facts.ledger_event_id!)}
                >
                  Open ledger fact
                </Button>
              )}
              <h3 className="mt-4 text-sm font-semibold">Source rows</h3>
              {item.inputs.map((input) => (
                <dl
                  key={input.activity_record_id}
                  className="mt-2 border-t py-2"
                >
                  <Fact
                    label="Raw row ID"
                    value={input.raw_activity_record_id}
                  />
                  <Fact label="Source row key" value={input.source_row_key} />
                  <Fact
                    label="Quantity"
                    value={`${formatDecimal(input.source_quantity)} ${input.source_unit}`}
                  />
                  <Fact
                    label="Source document ID"
                    value={input.source_document_id}
                  />
                  <Fact label="Raw checksum" value={input.raw_checksum} />
                </dl>
              ))}
              <h3 className="mt-4 text-sm font-semibold">Factor evidence</h3>
              {item.factors.map((factor) => (
                <dl key={factor.id} className="mt-2 border-t py-2">
                  <Fact
                    label="Factor / version"
                    value={`${factor.name} / ${factor.version}`}
                  />
                  <Fact
                    label="Value"
                    value={`${formatDecimal(factor.factor_value)} ${factor.numerator_unit}/${factor.denominator_unit}`}
                  />
                  <Fact
                    label="Document / locator"
                    value={`${factor.evidence.source_document_filename} / ${factor.evidence.locator}`}
                  />
                  <Fact label="Evidence ID" value={factor.evidence.id} />
                  <Fact
                    label="Evidence checksum"
                    value={factor.evidence.checksum}
                  />
                </dl>
              ))}
            </>
          )}
        </QueryState>
      </TabsContent>
      <TabsContent value="lineage" className="mt-4">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-medium">
            Source-to-result relationships
          </h3>
          <QueryRefresh query={lineage} label="lineage" />
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
              {graph.nodes.length === 0 && (
                <p className="py-4 text-sm text-muted-foreground">
                  No lineage is available for this measurement.
                </p>
              )}
              <ul className="divide-y">
                {graph.nodes.map((node) => (
                  <li key={node.id} className="py-3">
                    <p className="text-sm font-medium">{node.label}</p>
                    <p className="mt-1 break-all font-mono text-xs text-muted-foreground">
                      {node.id}
                    </p>
                    <Badge variant="outline" className="mt-2">
                      {humanize(node.node_type)}
                    </Badge>
                  </li>
                ))}
              </ul>
              <ul className="mt-4 divide-y">
                {graph.edges.map((edge) => (
                  <li key={edge.id} className="py-3 text-xs">
                    <p className="font-medium capitalize">
                      {humanize(edge.relationship_type)}
                    </p>
                    <p className="mt-1 break-all text-muted-foreground">
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
  )
}

function LedgerSource({
  scope,
  id,
  onEvent,
}: {
  scope: DashboardScope
  id: string
  onEvent: (id: string) => void
}) {
  const query = useQuery(dashboardQueries.event(scope, id))
  return (
    <>
      <div className="flex justify-end">
        <QueryRefresh query={query} label="ledger event" />
      </div>
      <QueryState query={query}>
        {(item) => (
          <>
            <dl>
              <Fact label="Event ID" value={item.id} />
              <Fact label="Event type" value={item.event_type} />
              <Fact label="Entity ID" value={item.entity_id} />
              <Fact label="Recorded" value={formatDate(item.created_at)} />
              <Fact label="Payload hash" value={item.payload_hash} />
            </dl>
            <h3 className="my-3 text-sm font-semibold">Persisted payload</h3>
            <pre className="max-h-64 overflow-auto rounded-md border bg-muted/30 p-3 text-xs whitespace-pre-wrap break-all">
              {JSON.stringify(item.payload, null, 2)}
            </pre>
            <h3 className="my-3 text-sm font-semibold">Evidence</h3>
            {item.evidence.length === 0 && (
              <p className="text-sm text-muted-foreground">
                No evidence attached to this event.
              </p>
            )}
            {item.evidence.map((evidence) => (
              <dl key={evidence.id} className="border-t py-2">
                <Fact
                  label="Source document"
                  value={evidence.source_filename}
                />
                <Fact label="Locator" value={evidence.locator} />
                <Fact label="Evidence ID" value={evidence.id} />
                <Fact label="Checksum" value={evidence.checksum} />
                <Badge variant="outline">
                  {evidence.is_synthetic
                    ? 'Synthetic evidence'
                    : 'Non-synthetic source'}
                </Badge>
              </dl>
            ))}
            {(item.evidence_truncated ||
              item.parents_truncated ||
              item.children_truncated) && (
              <p className="my-3 text-sm text-amber-700 dark:text-amber-400">
                Partial result: one or more API limits were reached.
              </p>
            )}
            {(['parents', 'children'] as const).map((direction) => (
              <div key={direction} className="mt-4">
                <h3 className="text-sm font-semibold">
                  {direction === 'parents'
                    ? 'Upstream events'
                    : 'Downstream events'}
                </h3>
                {item[direction].length === 0 && (
                  <p className="mt-2 text-xs text-muted-foreground">
                    None returned.
                  </p>
                )}
                {item[direction].map((neighbor) => (
                  <Button
                    key={neighbor.edge_id}
                    variant="link"
                    className="h-auto max-w-full justify-start px-0 py-2 text-left whitespace-normal"
                    onClick={() => onEvent(neighbor.event.id)}
                  >
                    {humanize(neighbor.event.event_type)} /{' '}
                    {humanize(neighbor.relationship_type)}
                  </Button>
                ))}
              </div>
            ))}
          </>
        )}
      </QueryState>
    </>
  )
}

export function SourceInspector({
  scope,
  selection,
  onSelect,
}: {
  scope: DashboardScope
  selection: Inspection | null
  onSelect: (selection: Inspection | null) => void
}) {
  const [returnFocusTarget] = useState(() =>
    document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null,
  )
  const onEvent = (id: string) => onSelect({ kind: 'event', id })
  return (
    <Dialog
      open={selection !== null}
      onOpenChange={(open) => {
        if (!open) onSelect(null)
      }}
    >
      <DialogContent
        className="max-h-[85svh] overflow-y-auto rounded-lg p-5 sm:max-w-2xl motion-reduce:animate-none"
        onCloseAutoFocus={(event) => {
          event.preventDefault()
          returnFocusTarget?.focus()
        }}
      >
        <DialogHeader className="pr-8">
          <DialogTitle>
            {selection?.kind === 'approval'
              ? 'Procurement queue preview'
              : 'Source inspector'}
          </DialogTitle>
          <DialogDescription>
            {selection?.kind === 'approval'
              ? 'Stored recommendation values. No decision is submitted from this dashboard.'
              : 'API records, provenance and source relationships.'}
          </DialogDescription>
        </DialogHeader>
        {selection?.kind === 'measurement' && (
          <MeasurementSource
            key={selection.id}
            scope={scope}
            id={selection.id}
            onEvent={onEvent}
          />
        )}
        {selection?.kind === 'event' && (
          <LedgerSource
            key={selection.id}
            scope={scope}
            id={selection.id}
            onEvent={onEvent}
          />
        )}
        {selection?.kind === 'approval' && (
          <dl className="divide-y">
            <Fact label="Approval ID" value={selection.item.id} />
            <Fact
              label="Recommendation ID"
              value={selection.item.recommendation_id}
            />
            <Fact
              label="Product / supplier"
              value={`${selection.item.recommended_product_name} / ${selection.item.supplier_name}`}
            />
            <Fact
              label="Projected footprint (kgCO2e)"
              value={formatDecimal(selection.item.projected_footprint_kgco2e)}
            />
            <Fact
              label="Projected avoided (kgCO2e)"
              value={formatDecimal(selection.item.avoided_kgco2e)}
            />
            <Fact
              label="Reduction"
              value={`${formatDecimal(selection.item.reduction_pct)}%`}
            />
            <Fact
              label="Cost change"
              value={`${formatDecimal(selection.item.cost_delta_pct)}%`}
            />
            <Fact
              label="Lead time change (days)"
              value={String(selection.item.lead_time_delta_days)}
            />
            <Fact
              label="Expires"
              value={formatDate(selection.item.expires_at)}
            />
            <Fact label="Preview hash" value={selection.item.preview_hash} />
            <Fact
              label="Analysis signature"
              value={selection.item.analysis_signature}
            />
            <p className="py-3 text-xs text-muted-foreground">
              This queue snapshot is not an authorization to commit. Facts and
              expiry must be revalidated during approval.
            </p>
          </dl>
        )}
        {selection?.kind === 'grid' && (
          <dl className="divide-y">
            <Fact
              label="Grid point ID"
              value={selection.item.grid_intensity_point_id}
            />
            <Fact
              label="Intensity"
              value={`${formatDecimal(selection.item.value)} ${selection.item.unit}`}
            />
            <Fact
              label="Provider / mode"
              value={`${selection.item.provenance.provider} / ${selection.item.provenance.provider_mode}`}
            />
            <Fact
              label="Synthetic source"
              value={String(selection.item.provenance.synthetic)}
            />
            <Fact
              label="Provider timestamp"
              value={formatDate(selection.item.provider_timestamp)}
            />
            <Fact
              label="Source document ID"
              value={selection.item.provenance.source_document_id}
            />
            <Fact
              label="Evidence ID"
              value={selection.item.provenance.evidence_item_id}
            />
            <Fact
              label="Response checksum"
              value={selection.item.provenance.response_checksum}
            />
          </dl>
        )}
      </DialogContent>
    </Dialog>
  )
}
