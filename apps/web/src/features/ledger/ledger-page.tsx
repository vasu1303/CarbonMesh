import { useQuery } from '@tanstack/react-query'
import { FolderOpen, Search } from 'lucide-react'
import { useState } from 'react'
import { useOutletContext, useSearchParams } from 'react-router-dom'
import { z } from 'zod'
import {
  EmptyState,
  PageControls,
  QueryRefresh,
  QueryState,
} from '@/components/query-state'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'
import {
  Field,
  WorkspaceProvenance,
} from '@/features/approvals/review-components'
import type { WorkspaceScope } from '@/lib/workspace'
import { EntityAudit, LedgerDetail } from './ledger-detail'
import { ledgerQueries } from './queries'
import {
  auditEntityTypes,
  ledgerFiltersSchema,
  type LedgerFilters,
} from './schemas'

function localTime(value: string) {
  if (!value) return ''
  const date = new Date(value)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}
function LedgerExplorer({ filters }: { filters: LedgerFilters }) {
  const scope = useOutletContext<WorkspaceScope>()
  const [params, setParams] = useSearchParams()
  const [error, setError] = useState('')
  const [openError, setOpenError] = useState('')
  const [auditError, setAuditError] = useState('')
  const events = useQuery(ledgerQueries.list(scope.company_id, filters))
  function update(values: Record<string, string | number | null>) {
    const next = new URLSearchParams(params)
    Object.entries(values).forEach(([key, value]) =>
      value === null || value === ''
        ? next.delete(key)
        : next.set(key, String(value)),
    )
    setParams(next)
  }
  return (
    <div className="min-w-0 space-y-6 px-5 py-6 sm:px-8">
      <header>
        <p className="mb-2 text-xs text-muted-foreground">Traceability</p>
        <h1 className="text-2xl font-semibold">Ledger and audit</h1>
      </header>
      <WorkspaceProvenance />
      <form
        key={[
          filters.event_type,
          filters.entity_type,
          filters.entity_id,
          filters.agent_run_id,
          filters.created_from,
          filters.created_to,
        ].join(':')}
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault()
          const values = Object.fromEntries(new FormData(e.currentTarget))
          const from = String(values.created_from || '')
          const to = String(values.created_to || '')
          if (
            (from && !Number.isFinite(Date.parse(from))) ||
            (to && !Number.isFinite(Date.parse(to)))
          ) {
            setError('Enter valid dates.')
            return
          }
          const parsed = ledgerFiltersSchema.safeParse({
            ...filters,
            ...values,
            created_from: from ? new Date(from).toISOString() : '',
            created_to: to ? new Date(to).toISOString() : '',
            offset: 0,
          })
          if (!parsed.success) {
            setError(
              parsed.error.issues
                .map((i) => `${i.path.join(' ')}: ${i.message}`)
                .join(' '),
            )
            return
          }
          setError('')
          const v = parsed.data
          update({
            event_type: v.event_type,
            entity_type: v.entity_type,
            entity_id: v.entity_id,
            agent_run_id: v.agent_run_id,
            created_from: v.created_from,
            created_to: v.created_to,
            offset: 0,
          })
        }}
      >
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          <Field label="Event type">
            <Input
              name="event_type"
              defaultValue={filters.event_type}
              maxLength={100}
            />
          </Field>
          <Field label="Entity type">
            <Input
              name="entity_type"
              defaultValue={filters.entity_type}
              maxLength={100}
            />
          </Field>
          <Field label="Entity UUID">
            <Input name="entity_id" defaultValue={filters.entity_id} />
          </Field>
          <Field label="Agent run UUID">
            <Input name="agent_run_id" defaultValue={filters.agent_run_id} />
          </Field>
          <Field label="Created from (local time)">
            <Input
              name="created_from"
              type="datetime-local"
              step="1"
              defaultValue={localTime(filters.created_from)}
            />
          </Field>
          <Field label="Created to (local time)">
            <Input
              name="created_to"
              type="datetime-local"
              step="1"
              defaultValue={localTime(filters.created_to)}
            />
          </Field>
        </div>
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
        <div className="flex flex-wrap gap-3">
          <Button variant="outline" size="sm">
            <Search />
            Apply filters
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => {
              setError('')
              update({
                event_type: null,
                entity_type: null,
                entity_id: null,
                agent_run_id: null,
                created_from: null,
                created_to: null,
                offset: 0,
              })
            }}
          >
            Clear filters
          </Button>
        </div>
      </form>
      <section aria-label="Ledger events" className="min-w-0 border-t pt-5">
        <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
          <h2 className="text-sm font-semibold">Company event history</h2>
          <div className="flex items-end gap-3">
            <Field label="Rows per page">
              <NativeSelect
                value={filters.limit}
                onChange={(e) => update({ limit: e.target.value, offset: 0 })}
              >
                {[10, 25, 50, 100].map((v) => (
                  <NativeSelectOption key={v} value={v}>
                    {v}
                  </NativeSelectOption>
                ))}
              </NativeSelect>
            </Field>
            <QueryRefresh query={events} label="ledger events" />
          </div>
        </div>
        <QueryState query={events}>
          {(data) => (
            <>
              {data.items.length ? (
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-sm">
                    <thead className="border-b text-xs text-muted-foreground">
                      <tr>
                        {[
                          'Created',
                          'Event',
                          'Entity',
                          'Payload hash',
                          'Inspect',
                        ].map((v) => (
                          <th className="p-2 font-medium" key={v}>
                            {v}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {data.items.map((item) => (
                        <tr key={item.id} className="border-b align-top">
                          <td className="whitespace-nowrap p-2 text-xs">
                            {item.created_at}
                          </td>
                          <td className="min-w-40 p-2 wrap-anywhere">
                            {item.event_type}
                          </td>
                          <td className="min-w-48 p-2">
                            <p>{item.entity_type}</p>
                            <p className="mt-1 break-all font-mono text-xs text-muted-foreground">
                              {item.entity_id}
                            </p>
                          </td>
                          <td className="max-w-52 break-all p-2 font-mono text-xs">
                            {item.payload_hash}
                          </td>
                          <td className="p-2">
                            <Button
                              variant="outline"
                              size="sm"
                              aria-label={`Inspect event ${item.id}`}
                              onClick={() => update({ event: item.id })}
                            >
                              Inspect
                            </Button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <EmptyState
                  title="No ledger events found"
                  detail="No events match the current filters and page."
                />
              )}
              {filters.offset + filters.limit <= 10000 ? (
                <PageControls
                  {...data}
                  count={data.items.length}
                  onChange={(offset) => update({ offset })}
                />
              ) : (
                <div className="mt-4 flex flex-wrap items-center justify-between gap-3 text-sm">
                  <p>
                    Search paging limit reached. Narrow the filters for more
                    events.
                  </p>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() =>
                      update({
                        offset: Math.max(0, filters.offset - filters.limit),
                      })
                    }
                  >
                    Previous page
                  </Button>
                </div>
              )}
            </>
          )}
        </QueryState>
      </section>
      <div className="grid gap-6 border-t pt-5 xl:grid-cols-2">
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault()
            const id = String(
              new FormData(e.currentTarget).get('event') || '',
            ).trim()
            if (!z.uuid().safeParse(id).success) {
              setOpenError('Enter a valid event UUID.')
              return
            }
            setOpenError('')
            update({ event: id })
          }}
        >
          <Field label="Event UUID">
            <Input name="event" required />
          </Field>
          <Button variant="outline" size="sm">
            <FolderOpen />
            Open event
          </Button>
          {openError && (
            <p role="alert" className="text-sm text-destructive">
              {openError}
            </p>
          )}
        </form>
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault()
            const data = new FormData(e.currentTarget)
            const id = String(data.get('audit_id') || '').trim()
            if (!z.uuid().safeParse(id).success) {
              setAuditError('Enter a valid audit entity UUID.')
              return
            }
            setAuditError('')
            update({ audit_type: String(data.get('audit_type')), audit_id: id })
          }}
        >
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Audit entity type">
              <NativeSelect name="audit_type" defaultValue="measurement">
                {auditEntityTypes.map((v) => (
                  <NativeSelectOption value={v} key={v}>
                    {v.replaceAll('_', ' ')}
                  </NativeSelectOption>
                ))}
              </NativeSelect>
            </Field>
            <Field label="Audit entity UUID">
              <Input name="audit_id" required />
            </Field>
          </div>
          <Button variant="outline" size="sm">
            <Search />
            Trace audit
          </Button>
          {auditError && (
            <p role="alert" className="text-sm text-destructive">
              {auditError}
            </p>
          )}
        </form>
      </div>
      {filters.event && (
        <LedgerDetail
          key={`${scope.company_id}:${filters.event}`}
          company={scope.company_id}
          id={filters.event}
          onClose={() => update({ event: null })}
          onOpen={(id) => update({ event: id })}
          onAudit={(type, id) => update({ audit_type: type, audit_id: id })}
        />
      )}
      {filters.audit_type && filters.audit_id && (
        <EntityAudit
          key={`${scope.company_id}:${filters.audit_type}:${filters.audit_id}`}
          company={scope.company_id}
          type={filters.audit_type}
          id={filters.audit_id}
          onOpen={(id) => update({ event: id })}
          onClose={() => update({ audit_type: null, audit_id: null })}
        />
      )}
    </div>
  )
}
export default function LedgerPage() {
  const [params, setParams] = useSearchParams()
  const parsed = ledgerFiltersSchema.safeParse(Object.fromEntries(params))
  if (!parsed.success)
    return (
      <section role="alert" className="space-y-4 px-5 py-6 sm:px-8">
        <h1 className="text-xl font-semibold">Invalid ledger filters</h1>
        <p className="text-sm">
          {parsed.error.issues.map((i) => i.message).join(' ')}
        </p>
        <Button variant="outline" onClick={() => setParams({})}>
          Reset filters
        </Button>
      </section>
    )
  return <LedgerExplorer filters={parsed.data} />
}
