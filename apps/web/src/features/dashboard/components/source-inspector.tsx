import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'
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
import { QueryRefresh, QueryState } from '@/components/query-state'
import { LedgerEventContents } from '@/features/ledger/ledger-detail'
import { ledgerQueries } from '@/features/ledger/queries'
import { EvidenceRecords } from '@/features/measurements/components/evidence-records'
import { MeasurementLineageView } from '@/features/measurements/components/measurement-lineage'
import { RecordedFacts } from '@/features/measurements/components/record-inspector'
import { measurementQueries } from '@/features/measurements/queries'
import { formatDate, formatDecimal } from '@/lib/format'
import { displayText, humanize } from '@/lib/presentation'
import { dashboardQueries, type DashboardScope } from '../queries'
import type { Approval } from '../schemas'

export type Inspection =
  | { kind: 'measurement' | 'event'; id: string }
  | { kind: 'approval'; item: Approval }

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

function MeasurementSource({
  scope,
  id,
  onEvent,
}: {
  scope: DashboardScope
  id: string
  onEvent: (id: string) => void
}) {
  const query = useQuery(measurementQueries.detail(scope, id))
  const lineage = useQuery(measurementQueries.lineage(scope, id))
  return (
    <Tabs defaultValue="facts" className="min-w-0">
      <TabsList>
        <TabsTrigger value="facts">Facts</TabsTrigger>
        <TabsTrigger value="evidence">Evidence</TabsTrigger>
        <TabsTrigger value="lineage">Lineage</TabsTrigger>
      </TabsList>
      <TabsContent value="facts" className="mt-4">
        <div className="flex justify-end">
          <QueryRefresh query={query} label="measurement source" />
        </div>
        <QueryState query={query}>
          {(item) => <RecordedFacts item={item} />}
        </QueryState>
      </TabsContent>
      <TabsContent value="evidence" className="mt-4">
        <QueryState query={query}>
          {(item) => <EvidenceRecords item={item} />}
        </QueryState>
      </TabsContent>
      <TabsContent value="lineage" className="mt-4 space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-medium">
            Source-to-result relationships
          </h3>
          <QueryRefresh query={lineage} label="lineage" />
        </div>
        <QueryState query={lineage}>
          {(graph) => (
            <MeasurementLineageView graph={graph} onEvent={onEvent} />
          )}
        </QueryState>
      </TabsContent>
      <Button asChild variant="link" className="justify-start px-0">
        <Link to={'/measurement/' + id}>Open measurement details</Link>
      </Button>
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
  const query = useQuery(ledgerQueries.detail(scope.company_id, id))
  return (
    <>
      <div className="flex justify-end">
        <QueryRefresh query={query} label="ledger event" />
      </div>
      <QueryState query={query}>
        {(item) => <LedgerEventContents item={item} onOpen={onEvent} />}
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
  const context = useQuery({
    ...dashboardQueries.context(scope),
    enabled: selection !== null,
  })
  const onEvent = (id: string) => onSelect({ kind: 'event', id })
  return (
    <Dialog
      open={selection !== null}
      onOpenChange={(open) => {
        if (!open) onSelect(null)
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
          <DialogTitle>
            {selection?.kind === 'approval'
              ? 'Approval queue preview'
              : 'Source inspector'}
          </DialogTitle>
          <DialogDescription>
            {context.data
              ? displayText(context.data.company.name) +
                ' / ' +
                displayText(context.data.site.name)
              : 'Recorded facts and source evidence'}
          </DialogDescription>
          {context.data && (
            <div>
              <Badge variant="outline">
                {context.data.company.is_synthetic
                  ? 'Synthetic data'
                  : 'Non-synthetic data'}
              </Badge>
            </div>
          )}
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
          <>
            <dl className="grid gap-x-5 sm:grid-cols-2">
              <Fact
                label="Pending review"
                value={humanize(selection.item.target_type)}
              />
              <Fact label="Requester" value={selection.item.requester_name} />
              {selection.item.recommended_product_name && (
                <Fact
                  label="Product / supplier"
                  value={
                    selection.item.recommended_product_name +
                    ' / ' +
                    (selection.item.supplier_name ?? 'Not provided')
                  }
                />
              )}
              {selection.item.projected_footprint_kgco2e != null && (
                <Fact
                  label="Projected footprint / kgCO2e"
                  value={formatDecimal(
                    selection.item.projected_footprint_kgco2e,
                  )}
                />
              )}
              {selection.item.avoided_kgco2e != null && (
                <Fact
                  label="Projected avoided / kgCO2e"
                  value={formatDecimal(selection.item.avoided_kgco2e)}
                />
              )}
              {selection.item.reduction_pct != null && (
                <Fact
                  label="Reduction"
                  value={formatDecimal(selection.item.reduction_pct) + '%'}
                />
              )}
              {selection.item.cost_delta_pct != null && (
                <Fact
                  label="Cost change"
                  value={formatDecimal(selection.item.cost_delta_pct) + '%'}
                />
              )}
              {selection.item.lead_time_delta_days != null && (
                <Fact
                  label="Lead time change / days"
                  value={String(selection.item.lead_time_delta_days)}
                />
              )}
              <Fact
                label="Expires"
                value={formatDate(selection.item.expires_at)}
              />
            </dl>
            <Button asChild variant="outline">
              <Link to={'/approvals?approval=' + selection.item.id}>
                Review approval
              </Link>
            </Button>
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}
