import { Link } from 'react-router-dom'
import { z } from 'zod'
import { displayText, humanize } from '@/lib/presentation'

function referenceHref(key: string, id: string) {
  if (key === 'ledger_event_id' || key.endsWith('_ledger_event_id'))
    return `/ledger?event=${id}`
  if (key === 'source_document_id') return `/data?document=${id}`
  if (key === 'measurement_id') return `/measurement/${id}`
  if (key === 'agent_run_id') return `/runs/${id}`
  if (key === 'evidence_item_id')
    return `/ledger?audit_type=evidence_item&audit_id=${id}`
  return null
}

function FactValue({
  value,
  name,
  depth,
}: {
  value: unknown
  name: string
  depth: number
}) {
  if (typeof value === 'string' && z.uuid().safeParse(value).success) {
    const href = referenceHref(name, value)
    return href ? (
      <Link className="text-primary underline underline-offset-4" to={href}>
        {humanize(name.replace(/_id$/, ''))}
      </Link>
    ) : (
      <span>Linked record</span>
    )
  }
  if (value && typeof value === 'object') {
    if (depth >= 4) return <span>{displayText(value)}</span>
    if (Array.isArray(value))
      return (
        <ul className="space-y-2">
          {value.map((item, index) => (
            <li key={index}>
              <FactValue
                value={item}
                name={name.replace(/_ids$/, '_id')}
                depth={depth + 1}
              />
            </li>
          ))}
        </ul>
      )
    return (
      <ReadableFacts
        value={value as Record<string, unknown>}
        depth={depth + 1}
      />
    )
  }
  return <span>{displayText(value)}</span>
}

export function ReadableFacts({
  value,
  depth = 0,
}: {
  value: Record<string, unknown>
  depth?: number
}) {
  const entries = Object.entries(value).filter(([key, item]) => {
    if (/(hash|checksum|signature|token|^id$|trace_id|code_version)/i.test(key))
      return false
    if (/(^|_)ids?$/.test(key))
      return (
        referenceHref(key.replace(/_ids$/, '_id'), '') !== null && item != null
      )
    return item != null
  })
  if (!entries.length)
    return (
      <p className="text-sm text-muted-foreground">
        No additional readable facts recorded.
      </p>
    )
  return (
    <dl className="min-w-0 divide-y text-sm">
      {entries.map(([key, item]) => (
        <div
          key={key}
          className="grid min-w-0 gap-1 py-2 sm:grid-cols-[minmax(8rem,1fr)_minmax(0,3fr)] sm:gap-4"
        >
          <dt className="text-xs text-muted-foreground wrap-anywhere">
            {humanize(key.replace(/_ids?$/, ''))}
          </dt>
          <dd className="min-w-0 wrap-anywhere">
            <FactValue value={item} name={key} depth={depth} />
          </dd>
        </div>
      ))}
    </dl>
  )
}
