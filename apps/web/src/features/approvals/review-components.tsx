import { useQuery } from '@tanstack/react-query'
import {
  Children,
  cloneElement,
  isValidElement,
  useId,
  type AriaAttributes,
  type ReactNode,
} from 'react'
import { Link, useLocation, useOutletContext } from 'react-router-dom'
import { z } from 'zod'
import { QueryState } from '@/components/query-state'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import type { WorkspaceScope } from '@/lib/workspace'
import { ApiError } from '@/services/api'
import { catalogQueries } from '@/features/procurement/queries'
import type { Fact } from '@/features/procurement/scenario-schemas'

export function WorkspaceProvenance() {
  const scope = useOutletContext<WorkspaceScope>()
  const context = useQuery(catalogQueries.context(scope))
  return (
    <section
      aria-label="Workspace provenance"
      className="border-y py-3 text-sm"
    >
      <QueryState query={context}>
        {(data) => (
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <span className="font-medium">{data.company.name}</span>
            <span>
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
      {context.isError && (
        <p className="text-amber-700">Provenance unavailable.</p>
      )}
    </section>
  )
}
export function Field({
  label,
  error,
  children,
}: {
  label: string
  error?: string
  children: ReactNode
}) {
  const generatedId = useId()
  const content = Children.toArray(children)
  const control = content.find((child) =>
    isValidElement<AriaAttributes & { id?: string }>(child),
  )
  const controlId = control?.props.id ?? generatedId
  const errorId = `${controlId}-error`
  return (
    <div className="min-w-0 space-y-1.5 text-xs font-medium">
      <Label htmlFor={controlId} className="text-xs">
        {label}
      </Label>
      {content.map((child) =>
        child === control
          ? cloneElement(control, {
              id: controlId,
              'aria-invalid': error ? true : control.props['aria-invalid'],
              'aria-describedby':
                [control.props['aria-describedby'], error ? errorId : null]
                  .filter(Boolean)
                  .join(' ') || undefined,
            })
          : child,
      )}
      {error && (
        <span id={errorId} role="alert" className="block text-destructive">
          {error}
        </span>
      )}
    </div>
  )
}
export function ActorField({
  actorId,
  authenticated,
  value,
  onChange,
}: {
  actorId: string
  authenticated: boolean
  value: string
  onChange: (value: string) => void
}) {
  return (
    <Field label="Actor UUID">
      <Input
        value={actorId || value}
        onChange={(e) => onChange(e.target.value)}
        readOnly={authenticated || !!actorId}
        required
        aria-label="Actor UUID"
        placeholder="Required actor UUID"
        autoComplete="off"
      />
      {authenticated && (
        <span className="block text-muted-foreground">Signed-in principal</span>
      )}
    </Field>
  )
}
export function CommandError({ error }: { error: Error | null }) {
  if (!error) return null
  return (
    <div role="alert" className="space-y-2 py-3 text-sm text-destructive">
      <p>{error.message}</p>
      {error instanceof ApiError && (
        <>
          <p>{error.terminalState || error.code}</p>
          {error.traceId && (
            <p className="break-all font-mono text-xs">
              Trace: {error.traceId}
            </p>
          )}
          {error.fieldDetails && <RecordFields value={error.fieldDetails} />}
        </>
      )}
    </div>
  )
}
export function DefinitionList({ items }: { items: [string, ReactNode][] }) {
  return (
    <dl className="grid min-w-0 gap-x-6 gap-y-3 text-sm sm:grid-cols-2 xl:grid-cols-3">
      {items.map(([label, value]) => (
        <div key={label} className="min-w-0">
          <dt className="mb-1 text-xs text-muted-foreground">{label}</dt>
          <dd className="whitespace-pre-wrap wrap-anywhere">
            {value ?? 'Not recorded'}
          </dd>
        </div>
      ))}
    </dl>
  )
}
export function EventLink({
  id,
  children,
}: {
  id: string
  children?: ReactNode
}) {
  const location = useLocation()
  const params = new URLSearchParams(
    location.pathname === '/ledger' ? location.search : '',
  )
  params.set('event', id)
  return (
    <Link
      className="break-all text-emerald-700 underline underline-offset-4 dark:text-emerald-400"
      to={`/ledger?${params}`}
    >
      {children ?? id}
    </Link>
  )
}
export function AuditLink({
  type,
  id,
  children,
}: {
  type: string
  id: string
  children?: ReactNode
}) {
  const location = useLocation()
  const params = new URLSearchParams(
    location.pathname === '/ledger' ? location.search : '',
  )
  params.set('audit_type', type)
  params.set('audit_id', id)
  return (
    <Link
      className="break-all text-emerald-700 underline underline-offset-4 dark:text-emerald-400"
      to={`/ledger?${params}`}
    >
      {children ?? id}
    </Link>
  )
}
export function FactTable({ facts }: { facts: Fact[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead className="border-b text-xs text-muted-foreground">
          <tr>
            <th className="p-2">Fact</th>
            <th className="p-2">Recorded value</th>
            <th className="p-2">Source</th>
          </tr>
        </thead>
        <tbody>
          {facts.map((f, index) => (
            <tr
              className="border-b align-top"
              key={f.id ?? `${f.placeholder}-${index}`}
            >
              <td className="p-2 wrap-anywhere">{f.placeholder}</td>
              <td className="p-2 font-mono" title={f.unit ?? undefined}>
                {f.display_value}
              </td>
              <td className="space-y-1 p-2 text-xs">
                {f.ledger_event_id && (
                  <div>
                    <EventLink id={f.ledger_event_id}>Ledger event</EventLink>
                  </div>
                )}
                {f.evidence_item_id && (
                  <div>
                    <AuditLink type="evidence_item" id={f.evidence_item_id}>
                      Evidence
                    </AuditLink>
                  </div>
                )}
                {!f.ledger_event_id &&
                  !f.evidence_item_id &&
                  'No linked source'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {!facts.length && (
        <p className="py-3 text-sm text-muted-foreground">
          No fact bindings recorded.
        </p>
      )}
    </div>
  )
}

// Records remain server-owned; these views format exact values without deriving facts.
export function RecordFields({
  value,
  depth = 0,
}: {
  value: unknown
  depth?: number
}) {
  if (value === null || value === undefined)
    return <span className="text-muted-foreground">Not recorded</span>
  if (typeof value !== 'object')
    return (
      <span className="whitespace-pre-wrap wrap-anywhere">
        {typeof value === 'boolean' ? (value ? 'Yes' : 'No') : String(value)}
      </span>
    )
  if (depth > 8)
    return (
      <p className="text-muted-foreground">
        Nested record available in the exact payload.
      </p>
    )
  if (Array.isArray(value))
    return (
      <ul className="space-y-2">
        {value.map((item, i) => (
          <li className="border-l pl-3" key={i}>
            <RecordFields value={item} depth={depth + 1} />
          </li>
        ))}
      </ul>
    )
  return (
    <dl className="min-w-0 divide-y text-sm">
      {Object.entries(value).map(([key, item]) => (
        <div
          className="grid min-w-0 gap-1 py-2 sm:grid-cols-[minmax(8rem,1fr)_minmax(0,3fr)] sm:gap-4"
          key={key}
        >
          <dt className="wrap-anywhere text-xs text-muted-foreground">
            {key.replaceAll('_', ' ')}
          </dt>
          <dd className="min-w-0 wrap-anywhere">
            {typeof item === 'string' &&
            z.uuid().safeParse(item).success &&
            (key === 'ledger_event_id' || key.endsWith('_ledger_event_id')) ? (
              <EventLink id={item} />
            ) : typeof item === 'string' &&
              z.uuid().safeParse(item).success &&
              key === 'evidence_item_id' ? (
              <AuditLink type="evidence_item" id={item} />
            ) : (
              <RecordFields value={item} depth={depth + 1} />
            )}
          </dd>
        </div>
      ))}
    </dl>
  )
}
export function RawPayload({ value }: { value: unknown }) {
  return (
    <details className="min-w-0 border-t py-3 text-xs">
      <summary className="cursor-pointer font-medium">
        Exact technical payload
      </summary>
      <pre className="mt-3 max-h-96 overflow-auto whitespace-pre-wrap break-all rounded border bg-muted/30 p-3">
        {JSON.stringify(value, null, 2)}
      </pre>
    </details>
  )
}
