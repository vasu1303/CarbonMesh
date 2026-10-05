import { useState } from 'react'
import { Link } from 'react-router-dom'
import { z } from 'zod'
import { LineageDiagram } from '@/components/lineage-diagram'
import { EmptyState } from '@/components/query-state'
import { Button } from '@/components/ui/button'
import { formatDate } from '@/lib/format'
import { displayText, humanize, recordLabel } from '@/lib/presentation'
import type { MeasurementLineage } from '../schemas'

export function MeasurementLineageView({
  graph,
  onEvent,
}: {
  graph: MeasurementLineage
  onEvent?: (id: string) => void
}) {
  const [selection, setSelection] = useState<string>()
  const selected = graph.nodes.find(
    (node) => node.id === (selection ?? graph.root_event_id),
  )
  const label = (node: MeasurementLineage['nodes'][number]) => {
    const name =
      node.metadata?.source_filename ??
      node.payload?.name ??
      humanize(node.label)
    const title = recordLabel(
      node.entity_type ?? node.node_type,
      typeof name === 'string' ? name : humanize(node.label),
    )
    return node.created_at ? `${title} / ${formatDate(node.created_at)}` : title
  }
  const documentId = z.uuid().safeParse(selected?.metadata?.source_document_id)
  return (
    <div className="min-w-0 space-y-4">
      {graph.truncated && (
        <p role="status" className="text-sm text-amber-700 dark:text-amber-400">
          Partial lineage. More source relationships exist beyond this trace.
        </p>
      )}
      {graph.nodes.length === 0 ? (
        <EmptyState
          title="No lineage returned"
          detail="No source relationships are recorded for this measurement."
        />
      ) : (
        <LineageDiagram
          nodes={graph.nodes.map((node) => ({
            id: node.id,
            label: label(node),
            type: humanize(node.entity_type ?? node.node_type),
            status:
              typeof node.payload?.status === 'string'
                ? displayText(node.payload.status)
                : undefined,
          }))}
          edges={graph.edges.map((edge) => ({
            id: edge.id,
            source: edge.source,
            target: edge.target,
            label: humanize(edge.relationship_type),
          }))}
          selectedId={selected?.id}
          onSelect={setSelection}
        />
      )}
      {graph.nodes.length > 0 && graph.edges.length === 0 && (
        <p className="text-sm text-muted-foreground">
          No relationships were returned for these records.
        </p>
      )}
      {selected && (
        <section
          aria-label="Selected lineage record"
          className="space-y-2 border-t pt-4 text-sm"
        >
          <h3 className="font-semibold wrap-anywhere">{label(selected)}</h3>
          <p className="text-muted-foreground">
            {humanize(
              selected.event_type ?? selected.entity_type ?? selected.node_type,
            )}
            {selected.created_at ? ` / ${formatDate(selected.created_at)}` : ''}
          </p>
          {typeof selected.metadata?.locator === 'string' && (
            <p className="wrap-anywhere">
              {displayText(selected.metadata.locator)}
            </p>
          )}
          <div className="flex flex-wrap gap-3">
            {selected.node_type === 'ledger_event' &&
              z.uuid().safeParse(selected.id).success &&
              (onEvent ? (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => onEvent(selected.id)}
                >
                  Inspect ledger event
                </Button>
              ) : (
                <Button asChild size="sm" variant="outline">
                  <Link to={`/ledger?event=${selected.id}`}>
                    Inspect ledger event
                  </Link>
                </Button>
              ))}
            {documentId.success && (
              <Button asChild size="sm" variant="outline">
                <Link to={`/data?document=${documentId.data}`}>
                  Source document
                </Link>
              </Button>
            )}
            {selected.node_type === 'evidence' && selected.entity_id && (
              <Button asChild size="sm" variant="outline">
                <Link
                  to={`/ledger?audit_type=evidence_item&audit_id=${selected.entity_id}`}
                >
                  Evidence history
                </Link>
              </Button>
            )}
          </div>
        </section>
      )}
    </div>
  )
}
