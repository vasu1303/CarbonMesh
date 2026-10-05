import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight, X } from 'lucide-react'
import { LineageDiagram } from '@/components/lineage-diagram'
import { QueryRefresh, QueryState } from '@/components/query-state'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  AuditLink,
  DefinitionList,
} from '@/features/approvals/review-components'
import { formatDate } from '@/lib/format'
import { displayText, humanize, recordLabel } from '@/lib/presentation'
import { useWorkspaceOptions } from '@/services/workspace'
import { ledgerQueries } from './queries'
import { ReadableFacts } from './readable-facts'
import type { LedgerEvent } from './schemas'

function ActorName({ id }: { id?: string | null }) {
  const query = useWorkspaceOptions('actors', {
    id: id ?? undefined,
    enabled: !!id,
  })
  const actor = query.data?.items.find((item) => item.id === id)
  return (
    <span>
      {!id
        ? 'Not recorded'
        : query.isPending
          ? 'Loading user...'
          : actor
            ? displayText(actor.label)
            : 'User name unavailable'}
    </span>
  )
}

function EventLineage({
  item,
  onOpen,
}: {
  item: LedgerEvent
  onOpen: (id: string) => void
}) {
  const [selection, setSelection] = useState(item.id)
  const nodes = [
    ...new Map(
      [
        item,
        ...item.parents.map((parent) => parent.event),
        ...item.children.map((child) => child.event),
      ].map((event) => [event.id, event]),
    ).values(),
  ]
  const selected = nodes.find((node) => node.id === selection) ?? item
  const edges = [
    ...item.parents.map((parent) => ({
      id: parent.edge_id,
      source: parent.event.id,
      target: item.id,
      label: humanize(parent.relationship_type),
    })),
    ...item.children.map((child) => ({
      id: child.edge_id,
      source: item.id,
      target: child.event.id,
      label: humanize(child.relationship_type),
    })),
  ]
  return (
    <section
      aria-label="Event lineage"
      className="min-w-0 space-y-3 border-t pt-5"
    >
      <h3 className="text-sm font-semibold">Source and decision lineage</h3>
      {(item.parents_truncated || item.children_truncated) && (
        <p role="status" className="text-sm text-amber-700 dark:text-amber-400">
          Partial lineage. More linked events exist.
        </p>
      )}
      <LineageDiagram
        nodes={nodes.map((event) => ({
          id: event.id,
          label:
            humanize(event.event_type) + ' / ' + formatDate(event.created_at),
          type: humanize(event.entity_type),
        }))}
        edges={[...new Map(edges.map((edge) => [edge.id, edge])).values()]}
        selectedId={selected.id}
        onSelect={setSelection}
      />
      {!edges.length && (
        <p className="text-sm text-muted-foreground">
          No linked events recorded.
        </p>
      )}
      <div className="flex flex-wrap items-center justify-between gap-3 border-t pt-3">
        <div>
          <p className="text-sm font-medium">{humanize(selected.event_type)}</p>
          <p className="mt-1 text-xs text-muted-foreground">
            {humanize(selected.entity_type)} / {formatDate(selected.created_at)}
          </p>
        </div>
        {selected.id !== item.id && (
          <Button
            variant="outline"
            size="sm"
            onClick={() => onOpen(selected.id)}
          >
            Open linked event
            <ArrowRight />
          </Button>
        )}
      </div>
    </section>
  )
}

export function LedgerEventContents({
  item,
  onOpen,
  onAudit,
}: {
  item: LedgerEvent
  onOpen: (id: string) => void
  onAudit?: (type: string, id: string) => void
}) {
  const auditType =
    item.entity_type === 'procurement_recommendation'
      ? 'recommendation'
      : item.entity_type
  return (
    <div className="min-w-0 space-y-5">
      <div className="flex flex-wrap items-center gap-3">
        <Badge variant="outline">{humanize(item.event_type)}</Badge>
        <span className="text-xs text-muted-foreground">
          {formatDate(item.created_at)}
        </span>
      </div>
      <DefinitionList
        items={[
          ['Record', humanize(item.entity_type)],
          ['Recorded by', <ActorName key="actor" id={item.created_by} />],
          [
            'Previous version',
            item.supersedes_event_id ? (
              <Button
                key="previous"
                variant="link"
                className="h-auto p-0"
                onClick={() => onOpen(item.supersedes_event_id!)}
              >
                Open previous event
              </Button>
            ) : (
              'No previous version recorded'
            ),
          ],
        ]}
      />
      {onAudit ? (
        <Button
          size="sm"
          variant="outline"
          onClick={() => onAudit(auditType, item.entity_id)}
        >
          Trace entity audit
          <ArrowRight />
        </Button>
      ) : (
        <Button asChild size="sm" variant="outline">
          <Link
            to={
              '/ledger?audit_type=' + auditType + '&audit_id=' + item.entity_id
            }
          >
            Trace entity audit
            <ArrowRight />
          </Link>
        </Button>
      )}
      <section className="space-y-3 border-t pt-4">
        <h3 className="text-sm font-semibold">Recorded facts</h3>
        <ReadableFacts value={item.payload} />
      </section>
      <EventLineage key={item.id} item={item} onOpen={onOpen} />
      <section
        aria-label="Event evidence"
        className="min-w-0 space-y-4 border-t pt-4"
      >
        <h3 className="text-sm font-semibold">Evidence and provenance</h3>
        {item.evidence_truncated && (
          <p
            role="status"
            className="text-sm text-amber-700 dark:text-amber-400"
          >
            Evidence list is partial.
          </p>
        )}
        {item.evidence.map((evidence) => (
          <article
            key={evidence.id}
            className="min-w-0 space-y-3 border-b pb-4"
          >
            <div className="flex flex-wrap items-center gap-3">
              <h4 className="text-sm font-medium wrap-anywhere">
                <Link
                  className="text-primary underline underline-offset-4"
                  to={'/data?document=' + evidence.source_document_id}
                >
                  {recordLabel('Source document', evidence.source_filename)}
                </Link>
              </h4>
              <Badge variant="outline">
                {evidence.is_synthetic
                  ? 'Synthetic evidence'
                  : 'Non-synthetic evidence'}
              </Badge>
            </div>
            <DefinitionList
              items={[
                ['Source', displayText(evidence.data_source_name)],
                [
                  'Evidence',
                  <AuditLink
                    key="evidence"
                    type="evidence_item"
                    id={evidence.id}
                  >
                    {humanize(evidence.evidence_type)}
                  </AuditLink>,
                ],
                ['Location in source', displayText(evidence.locator)],
                ['Recorded', formatDate(evidence.created_at)],
                ['Relevance', displayText(evidence.relevance)],
              ]}
            />
            <details>
              <summary className="cursor-pointer text-xs font-medium">
                Source details
              </summary>
              <ReadableFacts value={evidence.metadata} />
            </details>
          </article>
        ))}
        {!item.evidence.length && (
          <p className="text-sm text-muted-foreground">
            No evidence links recorded for this event.
          </p>
        )}
      </section>
    </div>
  )
}

export function LedgerDetail({
  company,
  id,
  onOpen,
  onAudit,
  onClose,
}: {
  company: string
  id: string
  onOpen: (id: string) => void
  onAudit: (type: string, id: string) => void
  onClose: () => void
}) {
  const query = useQuery(ledgerQueries.detail(company, id))
  return (
    <section
      aria-label="Ledger event detail"
      className="min-w-0 space-y-4 border-t pt-5"
    >
      <div className="flex items-center justify-between gap-4">
        <h2 className="text-lg font-semibold">Event detail</h2>
        <div className="flex gap-1">
          <QueryRefresh query={query} label="ledger event" />
          <Button
            variant="ghost"
            size="icon"
            aria-label="Close event"
            title="Close event"
            onClick={onClose}
          >
            <X />
          </Button>
        </div>
      </div>
      <QueryState query={query}>
        {(item) => (
          <LedgerEventContents item={item} onOpen={onOpen} onAudit={onAudit} />
        )}
      </QueryState>
    </section>
  )
}

export function EntityAudit({
  company,
  type,
  id,
  onOpen,
  onClose,
}: {
  company: string
  type: string
  id: string
  onOpen: (id: string) => void
  onClose: () => void
}) {
  const query = useQuery(ledgerQueries.audit(company, type, id))
  const [selectedId, setSelectedId] = useState<string>()
  const selected = query.data?.lineage.events.find(
    (event) => event.id === selectedId,
  )
  return (
    <section
      aria-label="Entity audit trace"
      className="min-w-0 space-y-4 border-t pt-5"
    >
      <div className="flex items-center justify-between gap-4">
        <h2 className="text-lg font-semibold">Entity audit trace</h2>
        <div className="flex gap-1">
          <QueryRefresh query={query} label="audit trace" />
          <Button
            variant="ghost"
            size="icon"
            title="Close audit"
            aria-label="Close audit"
            onClick={onClose}
          >
            <X />
          </Button>
        </div>
      </div>
      <p className="text-sm text-muted-foreground">{humanize(type)}</p>
      <QueryState query={query}>
        {(data) => (
          <>
            <section aria-label="Recursive lineage" className="space-y-3">
              <h3 className="text-sm font-semibold">
                Source and decision lineage
              </h3>
              {data.lineage.truncated && (
                <p
                  role="status"
                  className="text-sm text-amber-700 dark:text-amber-400"
                >
                  Partial trace. More source relationships exist beyond this
                  result.
                </p>
              )}
              {data.lineage.events.length > 0 && (
                <LineageDiagram
                  nodes={data.lineage.events.map((event) => ({
                    id: event.id,
                    label:
                      humanize(event.event_type) +
                      ' / ' +
                      formatDate(event.created_at),
                    type: humanize(event.entity_type),
                  }))}
                  edges={data.lineage.edges.map((edge) => ({
                    id: edge.id,
                    source: edge.parent_event_id,
                    target: edge.child_event_id,
                    label: humanize(edge.relationship_type),
                  }))}
                  selectedId={selected?.id}
                  onSelect={setSelectedId}
                />
              )}
              {!data.lineage.edges.length && (
                <p className="text-sm text-muted-foreground">
                  No lineage relationships recorded.
                </p>
              )}
              {selected && (
                <div className="flex flex-wrap items-center justify-between gap-3 border-t pt-3">
                  <div>
                    <p className="text-sm font-medium">
                      {humanize(selected.event_type)}
                    </p>
                    <p className="text-xs text-muted-foreground">
                      {formatDate(selected.created_at)}
                    </p>
                  </div>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => onOpen(selected.id)}
                  >
                    Open event
                    <ArrowRight />
                  </Button>
                </div>
              )}
            </section>
            <section aria-label="Audit timeline">
              <h3 className="mb-3 text-sm font-semibold">
                Decision and source history
              </h3>
              {data.timeline.map((item) => (
                <article
                  key={item.source + ':' + item.id}
                  className="min-w-0 space-y-3 border-b py-4"
                >
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div className="min-w-0">
                      <p className="text-sm font-medium wrap-anywhere">
                        {humanize(item.action)}
                      </p>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {formatDate(item.timestamp)}
                      </p>
                    </div>
                    <Badge variant="outline">
                      {item.direct ? 'Direct' : 'Related'} /{' '}
                      {humanize(item.source)}
                    </Badge>
                  </div>
                  <p className="text-sm text-muted-foreground">
                    {humanize(item.entity_type)} /{' '}
                    <ActorName id={item.actor_id} />
                  </p>
                  {item.source === 'ledger' && (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => onOpen(item.id)}
                    >
                      Open event
                      <ArrowRight />
                    </Button>
                  )}
                  <details>
                    <summary className="cursor-pointer text-xs font-medium">
                      Recorded details
                    </summary>
                    <ReadableFacts value={item.details} />
                  </details>
                </article>
              ))}
              {!data.timeline.length && (
                <p className="py-3 text-sm text-muted-foreground">
                  No audit history recorded.
                </p>
              )}
            </section>
          </>
        )}
      </QueryState>
    </section>
  )
}
