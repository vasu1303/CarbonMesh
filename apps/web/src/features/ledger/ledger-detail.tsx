import { useQuery } from '@tanstack/react-query'
import { ArrowRight, X } from 'lucide-react'
import { QueryRefresh, QueryState } from '@/components/query-state'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  AuditLink,
  DefinitionList,
  RawPayload,
  RecordFields,
} from '@/features/approvals/review-components'
import { ledgerQueries } from './queries'
import type { LedgerEvent } from './schemas'

function Neighbors({
  title,
  items,
  truncated,
  onOpen,
}: {
  title: string
  items: LedgerEvent['parents']
  truncated: boolean
  onOpen: (id: string) => void
}) {
  return (
    <section aria-label={title} className="min-w-0 space-y-3">
      <h3 className="text-sm font-semibold">{title}</h3>
      {truncated && (
        <p role="status" className="text-sm text-amber-700">
          Partial result. More linked events exist.
        </p>
      )}
      {items.map((item) => (
        <div
          key={item.edge_id}
          className="flex min-w-0 items-start justify-between gap-3 border-b py-3"
        >
          <div className="min-w-0 space-y-1 text-sm">
            <p className="wrap-anywhere">{item.relationship_type}</p>
            <p className="wrap-anywhere">{item.event.event_type}</p>
            <p className="break-all font-mono text-xs text-muted-foreground">
              {item.event.id}
            </p>
            <p className="text-xs text-muted-foreground">
              {item.event.created_at}
            </p>
          </div>
          <Button
            variant="outline"
            size="icon"
            className="shrink-0"
            aria-label={`Open linked event ${item.event.id}`}
            title="Open linked event"
            onClick={() => onOpen(item.event.id)}
          >
            <ArrowRight />
          </Button>
        </div>
      ))}
      {!items.length && (
        <p className="text-sm text-muted-foreground">
          No linked events recorded.
        </p>
      )}
    </section>
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
        {(data) => (
          <>
            <div className="flex flex-wrap items-center gap-3">
              <Badge variant="outline">{data.event_type}</Badge>
              <Badge variant="outline">Append-only record</Badge>
            </div>
            <DefinitionList
              items={[
                ['Event UUID', data.id],
                ['Entity type', data.entity_type],
                ['Entity UUID', data.entity_id],
                ['Created', data.created_at],
                ['Actor UUID', data.created_by],
                ['Payload hash', data.payload_hash],
                ['Analysis signature', data.analysis_signature],
                [
                  'Supersedes',
                  data.supersedes_event_id ? (
                    <button
                      key="supersedes"
                      className="break-all text-left text-emerald-700 underline"
                      onClick={() => onOpen(data.supersedes_event_id!)}
                    >
                      {data.supersedes_event_id}
                    </button>
                  ) : null,
                ],
              ]}
            />
            <Button
              size="sm"
              variant="outline"
              onClick={() =>
                onAudit(
                  data.entity_type === 'procurement_recommendation'
                    ? 'recommendation'
                    : data.entity_type,
                  data.entity_id,
                )
              }
            >
              Trace entity audit
              <ArrowRight />
            </Button>
            <section className="space-y-3 border-t pt-4">
              <h3 className="text-sm font-semibold">
                Recorded facts and source references
              </h3>
              <RecordFields value={data.payload} />
            </section>
            <section
              aria-label="Event evidence"
              className="min-w-0 space-y-4 border-t pt-4"
            >
              <h3 className="text-sm font-semibold">Evidence and provenance</h3>
              {data.evidence_truncated && (
                <p role="status" className="text-sm text-amber-700">
                  Evidence list is partial.
                </p>
              )}
              {data.evidence.map((item) => (
                <article
                  key={item.id}
                  className="min-w-0 space-y-3 border-b pb-4"
                >
                  <div className="flex flex-wrap items-center gap-3">
                    <h4 className="text-sm font-medium wrap-anywhere">
                      {item.source_filename}
                    </h4>
                    <Badge variant="outline">
                      {item.is_synthetic
                        ? 'Synthetic evidence'
                        : 'Non-synthetic evidence'}
                    </Badge>
                  </div>
                  <DefinitionList
                    items={[
                      [
                        'Evidence',
                        <AuditLink
                          key="evidence"
                          type="evidence_item"
                          id={item.id}
                        >
                          {item.evidence_type}
                        </AuditLink>,
                      ],
                      ['Locator', item.locator],
                      ['Evidence checksum', item.checksum],
                      [
                        'Source document',
                        <AuditLink
                          key="source-document"
                          type="source_document"
                          id={item.source_document_id}
                        />,
                      ],
                      ['Source checksum', item.source_document_checksum],
                      ['Data source', item.data_source_name],
                      ['Relevance', item.relevance],
                      ['Created', item.created_at],
                    ]}
                  />
                  <details>
                    <summary className="cursor-pointer text-xs font-medium">
                      Evidence metadata
                    </summary>
                    <RecordFields value={item.metadata} />
                  </details>
                </article>
              ))}
              {!data.evidence.length && (
                <p className="text-sm text-muted-foreground">
                  No evidence links recorded for this event.
                </p>
              )}
            </section>
            <div className="grid min-w-0 gap-6 border-t pt-5 lg:grid-cols-2">
              <Neighbors
                title="Upstream events"
                items={data.parents}
                truncated={data.parents_truncated}
                onOpen={onOpen}
              />
              <Neighbors
                title="Downstream events"
                items={data.children}
                truncated={data.children_truncated}
                onOpen={onOpen}
              />
            </div>
            <RawPayload value={data.payload} />
          </>
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
      <p className="break-all text-xs text-muted-foreground">
        {type} / {id}
      </p>
      <QueryState query={query}>
        {(data) => (
          <>
            {data.lineage.truncated && (
              <p role="status" className="text-sm text-amber-700">
                Partial trace: the server traversal limit was reached.
              </p>
            )}
            <section aria-label="Audit timeline">
              <h3 className="mb-3 text-sm font-semibold">
                Decision and source history
              </h3>
              {data.timeline.map((item) => (
                <article
                  key={`${item.source}:${item.id}`}
                  className="min-w-0 space-y-3 border-b py-4"
                >
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div className="min-w-0">
                      <p className="text-sm font-medium wrap-anywhere">
                        {item.action}
                      </p>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {item.timestamp}
                      </p>
                    </div>
                    <Badge variant="outline">
                      {item.direct ? 'Direct' : 'Related'} / {item.source}
                    </Badge>
                  </div>
                  <DefinitionList
                    items={[
                      ['Entity', `${item.entity_type} / ${item.entity_id}`],
                      ['Actor', item.actor_id],
                      ['Trace', item.trace_id],
                      ['Payload hash', item.payload_hash],
                    ]}
                  />
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
                    <RecordFields value={item.details} />
                  </details>
                </article>
              ))}
              {!data.timeline.length && (
                <p className="py-3 text-sm text-muted-foreground">
                  No audit history recorded.
                </p>
              )}
            </section>
            <section aria-label="Recursive lineage" className="space-y-3">
              <h3 className="text-sm font-semibold">Recursive lineage</h3>
              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <thead className="border-b text-xs text-muted-foreground">
                    <tr>
                      <th className="p-2">Source event</th>
                      <th className="p-2">Relationship</th>
                      <th className="p-2">Output event</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.lineage.edges.map((edge) => {
                      const parent = data.lineage.events.find(
                        (e) => e.id === edge.parent_event_id,
                      )
                      const child = data.lineage.events.find(
                        (e) => e.id === edge.child_event_id,
                      )
                      return (
                        <tr className="border-b align-top" key={edge.id}>
                          <td className="min-w-48 p-2">
                            <button
                              className="break-all text-left text-emerald-700 underline"
                              onClick={() => onOpen(edge.parent_event_id)}
                            >
                              {parent?.event_type ?? edge.parent_event_id}
                              <span className="block text-xs">
                                {edge.parent_event_id}
                              </span>
                            </button>
                          </td>
                          <td className="p-2">{edge.relationship_type}</td>
                          <td className="min-w-48 p-2">
                            <button
                              className="break-all text-left text-emerald-700 underline"
                              onClick={() => onOpen(edge.child_event_id)}
                            >
                              {child?.event_type ?? edge.child_event_id}
                              <span className="block text-xs">
                                {edge.child_event_id}
                              </span>
                            </button>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
              {!data.lineage.edges.length && (
                <p className="text-sm text-muted-foreground">
                  No lineage relationships recorded.
                </p>
              )}
            </section>
          </>
        )}
      </QueryState>
    </section>
  )
}
