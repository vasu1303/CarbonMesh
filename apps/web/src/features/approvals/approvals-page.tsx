import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from '@tanstack/react-query'
import { Check, FolderOpen, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useOutletContext, useSearchParams } from 'react-router-dom'
import { z } from 'zod'
import {
  EmptyState,
  PageControls,
  QueryRefresh,
  QueryState,
} from '@/components/query-state'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'
import { Textarea } from '@/components/ui/textarea'
import { useWorkspaceActor } from '@/features/auth/use-workspace-actor'
import type { WorkspaceScope } from '@/lib/workspace'
import { ApiError, apiRequest } from '@/services/api'
import { ApprovalPreview } from './approval-preview'
import { approvalKey, approvalQueries } from './queries'
import {
  ActorField,
  AuditLink,
  CommandError,
  DefinitionList,
  EventLink,
  Field,
  WorkspaceProvenance,
} from './review-components'
import {
  approvalFiltersSchema,
  approvalStatus,
  decisionSchema,
  supportedPreview,
  type ApprovalDetail,
} from './schemas'

const targetLabel: Record<string, string> = {
  procurement_recommendation: 'Procurement',
  disclosure_draft: 'Disclosure',
  dispatch_recommendation: 'Dispatch',
}

function DecisionForm({
  approval,
  query,
}: {
  approval: ApprovalDetail
  query: UseQueryResult<ApprovalDetail, Error>
}) {
  const actor = useWorkspaceActor()
  const client = useQueryClient()
  const [manualActor, setManualActor] = useState('')
  const [decision, setDecision] = useState<'approve' | 'reject'>('approve')
  const [note, setNote] = useState('')
  const [confirmedHash, setConfirmedHash] = useState('')
  const [confirmed, setConfirmed] = useState(false)
  const [clock, setClock] = useState(Date.now)
  const expires = Date.parse(approval.expires_at)
  useEffect(() => {
    if (clock >= expires) return
    const timer = window.setTimeout(
      () => setClock(Date.now()),
      Math.min(Math.max(expires - Date.now() + 1, 1), 2_147_483_647),
    )
    return () => window.clearTimeout(timer)
  }, [clock, expires])
  const actorId = actor.actorId || manualActor.trim()
  const allowedRole = !actor.authenticated || actor.role === 'approver'
  const current =
    approval.status === 'pending' &&
    approval.preview_current &&
    !approval.expired &&
    clock < expires &&
    supportedPreview(approval)
  const validActor = z.uuid().safeParse(actorId).success
  const mutation = useMutation({
    mutationFn: async (body: {
      company_id: string
      decision: 'approve' | 'reject'
      preview_hash: string
      actor_id: string
      decision_note: string | null
      idempotency_key: string
    }) => {
      const fresh = await query.refetch()
      const item = fresh.data
      if (
        fresh.isError ||
        !item ||
        !item.preview_current ||
        item.status !== 'pending' ||
        item.expired ||
        Date.parse(item.expires_at) <= Date.now() ||
        item.preview_hash !== body.preview_hash ||
        item.idempotency_key !== body.idempotency_key ||
        !supportedPreview(item)
      ) {
        throw new ApiError(
          'The exact preview could not be revalidated. Review the refreshed record before deciding.',
          'stale',
        )
      }
      return apiRequest(
        `/approvals/${approval.id}/decision`,
        decisionSchema.refine(
          (v) =>
            v.approval_id === approval.id &&
            v.target_id === approval.target_id &&
            v.target_type === approval.target_type &&
            v.preview_hash === body.preview_hash &&
            v.decided_by === body.actor_id &&
            v.status ===
              (body.decision === 'approve' ? 'approved' : 'rejected'),
        ),
        { signal: new AbortController().signal, method: 'POST', body },
      )
    },
    onSettled: async () => {
      setConfirmed(false)
      await client.invalidateQueries({ queryKey: approvalKey })
      await client.invalidateQueries({ queryKey: ['procurement-scenarios'] })
      await client.invalidateQueries({ queryKey: ['ledger-explorer'] })
    },
  })
  const blocked =
    !current ||
    !allowedRole ||
    query.isError ||
    query.isFetching ||
    mutation.isPending
  return (
    <section aria-label="Approval decision" className="space-y-4 border-t pt-5">
      <h3 className="text-sm font-semibold">Human decision</h3>
      {!current && (
        <p role="alert" className="text-sm text-amber-700">
          Decision unavailable:{' '}
          {approval.status !== 'pending'
            ? approval.status
            : approval.expired || clock >= expires
              ? 'expired preview'
              : !approval.preview_current
                ? 'stale preview'
                : 'unsupported or incomplete preview'}
          .
        </p>
      )}
      {!allowedRole && (
        <p role="alert" className="text-sm text-amber-700">
          The signed-in actor must have the approver role.
        </p>
      )}
      {query.isError && (
        <p role="alert" className="text-sm text-amber-700">
          Freshness could not be verified. Refresh the approval before deciding.
        </p>
      )}
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault()
          if (
            blocked ||
            !validActor ||
            !confirmed ||
            confirmedHash !== approval.preview_hash ||
            Date.now() >= expires
          )
            return
          mutation.mutate({
            company_id: approval.company_id,
            actor_id: actorId,
            decision,
            preview_hash: approval.preview_hash,
            decision_note: note || null,
            idempotency_key: approval.idempotency_key,
          })
        }}
      >
        <fieldset
          disabled={blocked}
          className="grid min-w-0 gap-4 sm:grid-cols-2"
        >
          <ActorField
            {...actor}
            value={manualActor}
            onChange={(value) => {
              setManualActor(value)
              setConfirmed(false)
            }}
          />
          <Field label="Decision">
            <NativeSelect
              value={decision}
              onChange={(event) => {
                setDecision(event.target.value as 'approve' | 'reject')
                setConfirmed(false)
              }}
            >
              <NativeSelectOption value="approve">Approve</NativeSelectOption>
              <NativeSelectOption value="reject">Reject</NativeSelectOption>
            </NativeSelect>
          </Field>
          <div className="sm:col-span-2">
            <Field label="Decision note (optional)">
              <Textarea
                value={note}
                maxLength={2000}
                onChange={(event) => {
                  setNote(event.target.value)
                  setConfirmed(false)
                }}
              />
            </Field>
          </div>
          <div className="sm:col-span-2">
            <Field label="Confirm preview hash">
              <Input
                value={confirmedHash}
                onChange={(event) => {
                  setConfirmedHash(event.target.value.trim())
                  setConfirmed(false)
                }}
                autoComplete="off"
                placeholder="Exact preview hash"
                className="font-mono text-xs"
                required
              />
            </Field>
          </div>
          <Label
            htmlFor="confirm-decision"
            className="flex items-start gap-3 text-sm sm:col-span-2"
          >
            <Checkbox
              id="confirm-decision"
              className="mt-0.5 shrink-0"
              checked={confirmed}
              onCheckedChange={(checked) => setConfirmed(checked === true)}
            />
            <span>
              I confirm this {decision === 'approve' ? 'approval' : 'rejection'}{' '}
              of the exact preview, its facts and evidence.
            </span>
          </Label>
        </fieldset>
        <CommandError error={mutation.error} />
        {mutation.data && (
          <p role="status" className="text-sm text-emerald-700">
            Decision recorded: {mutation.data.status}.{' '}
            <EventLink id={mutation.data.ledger_event_id}>
              Decision ledger event
            </EventLink>
          </p>
        )}
        <Button
          type="submit"
          variant={decision === 'reject' ? 'outline' : 'default'}
          disabled={
            blocked ||
            !validActor ||
            !confirmed ||
            confirmedHash !== approval.preview_hash
          }
        >
          {decision === 'approve' ? <Check /> : <X />}
          {mutation.isPending
            ? 'Submitting decision...'
            : decision === 'approve'
              ? 'Confirm approval'
              : 'Confirm rejection'}
        </Button>
      </form>
    </section>
  )
}

function ApprovalInspector({
  id,
  onClose,
}: {
  id: string
  onClose: () => void
}) {
  const scope = useOutletContext<WorkspaceScope>()
  const query = useQuery(approvalQueries.detail(scope.company_id, id))
  return (
    <section
      aria-label="Approval detail"
      className="min-w-0 space-y-4 border-t pt-5"
    >
      <div className="flex items-center justify-between gap-4">
        <h2 className="text-lg font-semibold">Exact approval preview</h2>
        <div className="flex gap-1">
          <QueryRefresh query={query} label="approval" />
          <Button
            variant="ghost"
            size="icon"
            title="Close approval"
            aria-label="Close approval"
            onClick={onClose}
          >
            <X />
          </Button>
        </div>
      </div>
      <QueryState query={query}>
        {(data) => (
          <>
            <div className="flex flex-wrap gap-2">
              <Badge variant="outline">
                {targetLabel[data.target_type] || data.target_type}
              </Badge>
              <Badge variant="outline">{data.status}</Badge>
              <Badge variant="outline">
                {data.preview_current ? 'Preview current' : 'Preview stale'}
              </Badge>
              {data.expired && <Badge variant="outline">Expired</Badge>}
            </div>
            <DefinitionList
              items={[
                ['Approval UUID', data.id],
                ['Target UUID', data.target_id],
                [
                  'Requested by',
                  `${data.requester_name} / ${data.requested_by}`,
                ],
                ['Created', data.created_at],
                ['Expires', data.expires_at],
                ['Preview hash', data.preview_hash],
                ['Analysis signature', data.analysis_signature],
                ['Context hash', data.context_hash],
                ['Idempotency key', data.idempotency_key],
                ['Policy', data.policy_definition_id],
                [
                  'Audit',
                  <AuditLink key="audit" type="approval" id={data.id}>
                    Approval history
                  </AuditLink>,
                ],
              ]}
            />
            <div className="border-t pt-5">
              <ApprovalPreview approval={data} />
            </div>
            {data.decided_at && (
              <section className="space-y-3 border-t pt-4">
                <h3 className="text-sm font-semibold">Recorded decision</h3>
                <DefinitionList
                  items={[
                    ['Decider', data.decider_name || data.decided_by],
                    ['Decided at', data.decided_at],
                    ['Decision note', data.decision_note],
                    [
                      'Ledger event',
                      data.ledger_event_id ? (
                        <EventLink
                          key="ledger-event"
                          id={data.ledger_event_id}
                        />
                      ) : null,
                    ],
                  ]}
                />
              </section>
            )}
            <DecisionForm
              key={`${data.id}:${data.preview_hash}`}
              approval={data}
              query={query}
            />
          </>
        )}
      </QueryState>
    </section>
  )
}

function Approvals({
  filters,
}: {
  filters: z.infer<typeof approvalFiltersSchema>
}) {
  const scope = useOutletContext<WorkspaceScope>()
  const [params, setParams] = useSearchParams()
  const [openError, setOpenError] = useState('')
  const list = useQuery(
    approvalQueries.list(
      scope.company_id,
      filters.status,
      filters.limit,
      filters.offset,
    ),
  )
  function update(values: Record<string, string | number | null>) {
    const next = new URLSearchParams(params)
    Object.entries(values).forEach(([key, value]) =>
      value === null ? next.delete(key) : next.set(key, String(value)),
    )
    setParams(next)
  }
  return (
    <div className="min-w-0 space-y-6 px-5 py-6 sm:px-8">
      <header>
        <p className="mb-2 text-xs text-muted-foreground">Review queue</p>
        <h1 className="text-2xl font-semibold">Approvals</h1>
      </header>
      <WorkspaceProvenance />
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="flex flex-wrap gap-3">
          <Field label="Approval status">
            <NativeSelect
              value={filters.status}
              onChange={(e) => update({ status: e.target.value, offset: 0 })}
            >
              <NativeSelectOption value="all">All statuses</NativeSelectOption>
              {approvalStatus.options.map((v) => (
                <NativeSelectOption key={v} value={v}>
                  {v}
                </NativeSelectOption>
              ))}
            </NativeSelect>
          </Field>
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
        </div>
        <form
          className="flex flex-wrap items-end gap-3"
          onSubmit={(e) => {
            e.preventDefault()
            const id = String(
              new FormData(e.currentTarget).get('approval') || '',
            ).trim()
            if (!z.uuid().safeParse(id).success) {
              setOpenError('Enter a valid approval UUID.')
              return
            }
            setOpenError('')
            update({ approval: id })
          }}
        >
          <Field label="Approval UUID">
            <Input name="approval" required />
          </Field>
          <Button variant="outline" size="sm">
            <FolderOpen />
            Open approval
          </Button>
          {openError && (
            <p role="alert" className="text-sm text-destructive">
              {openError}
            </p>
          )}
        </form>
      </div>
      <section aria-label="Approval queue" className="min-w-0">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold">Company approvals</h2>
          <QueryRefresh query={list} label="approval queue" />
        </div>
        <QueryState query={list}>
          {(data) => (
            <>
              {data.items.length ? (
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-sm">
                    <thead className="border-b text-xs text-muted-foreground">
                      <tr>
                        {[
                          'Target',
                          'Requested by',
                          'Status',
                          'Freshness',
                          'Expiry',
                          'Review',
                        ].map((v) => (
                          <th key={v} className="p-2 font-medium">
                            {v}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {data.items.map((item) => (
                        <tr key={item.id} className="border-b align-top">
                          <td className="min-w-44 max-w-72 p-2 wrap-anywhere">
                            <p>
                              {targetLabel[item.target_type] ||
                                item.target_type}
                            </p>
                            <p className="mt-1 text-xs text-muted-foreground">
                              {item.recommended_product_name || item.target_id}
                            </p>
                          </td>
                          <td className="p-2">{item.requester_name}</td>
                          <td className="p-2">
                            <Badge variant="outline">{item.status}</Badge>
                          </td>
                          <td className="p-2">
                            {item.preview_current ? 'Current' : 'Stale'}
                          </td>
                          <td className="whitespace-nowrap p-2 text-xs">
                            {item.expired ? 'Expired / ' : ''}
                            {item.expires_at}
                          </td>
                          <td className="p-2">
                            <Button
                              size="sm"
                              variant="outline"
                              aria-label={`Review approval ${item.id}`}
                              onClick={() => update({ approval: item.id })}
                            >
                              Review
                            </Button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <EmptyState
                  title="No approvals found"
                  detail="No approvals match this status and page."
                />
              )}
              <PageControls
                {...data}
                count={data.items.length}
                onChange={(offset) => update({ offset })}
              />
            </>
          )}
        </QueryState>
      </section>
      {filters.approval && (
        <ApprovalInspector
          key={`${scope.company_id}:${filters.approval}`}
          id={filters.approval}
          onClose={() => update({ approval: null })}
        />
      )}
    </div>
  )
}
export default function ApprovalsPage() {
  const [params, setParams] = useSearchParams()
  const parsed = approvalFiltersSchema.safeParse(Object.fromEntries(params))
  if (!parsed.success)
    return (
      <section role="alert" className="space-y-4 px-5 py-6 sm:px-8">
        <h1 className="text-xl font-semibold">Invalid approval filters</h1>
        <Button variant="outline" onClick={() => setParams({})}>
          Reset filters
        </Button>
      </section>
    )
  return <Approvals filters={parsed.data} />
}
