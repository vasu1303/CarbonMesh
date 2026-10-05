import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowRight } from 'lucide-react'
import { useId, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { z } from 'zod'
import { QueryRefresh, QueryState } from '@/components/query-state'
import { RecordSelect } from '@/components/record-select'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { humanize } from '@/lib/presentation'
import { formatDate } from '@/lib/format'
import { AgentContextForm, CommandError } from '@/features/agents/context-form'
import {
  agentContextSchema,
  resumeRequestSchema,
} from '@/features/agents/schemas'
import type { WorkspaceScope } from '@/lib/workspace'
import { apiRequest } from '@/services/api'
import { approvalObserverOptions, runOptions } from './queries'
import { resumeResultSchema, type AgentRun } from './schemas'

type Resume = z.infer<typeof resumeRequestSchema>
function useResume(run: AgentRun, onResumed: () => void) {
  const client = useQueryClient()
  const retry = useRef<{ fingerprint: string; key: string } | null>(null)
  const command = useMutation({
    mutationFn: (body: Resume) =>
      apiRequest(
        `/runs/${run.run_id}/resume`,
        resumeResultSchema.refine((v) => v.run_id === run.run_id),
        {
          signal: new AbortController().signal,
          method: 'POST',
          body,
          timeoutMs: 60000,
        },
      ),
    retry: false,
    onSuccess: async () => {
      await client.invalidateQueries({
        queryKey: runOptions(run.context.company_id, run.run_id).queryKey,
      })
      await client.invalidateQueries({
        queryKey: ['agent-sustainability', run.context.company_id],
      })
      onResumed()
    },
  })
  function submit(
    actor: string,
    payload: Pick<Resume, 'clarification' | 'approval'>,
  ) {
    const interrupt = run.pending_interrupt!
    const body = {
      company_id: run.context.company_id,
      actor_id: actor,
      interrupt_id: interrupt.interrupt_id,
      interrupt_sequence: interrupt.sequence,
      analysis_signature: interrupt.analysis_signature,
      ...payload,
    }
    const fingerprint = JSON.stringify(body)
    if (retry.current?.fingerprint !== fingerprint)
      retry.current = { fingerprint, key: crypto.randomUUID() }
    command.mutate(
      resumeRequestSchema.parse({
        ...body,
        idempotency_key: retry.current.key,
      }),
    )
  }
  return { command, submit }
}

function ApprovalObserver({
  run,
  onResumed,
}: {
  run: AgentRun
  onResumed: () => void
}) {
  const interrupt = run.pending_interrupt!
  const requesterFieldId = useId()
  const [actorId, setActorId] = useState(run.context.actor_id)
  const [invalid, setInvalid] = useState(false)
  const { command, submit } = useResume(run, onResumed)
  const query = useQuery({
    ...approvalObserverOptions(
      run.context.company_id,
      interrupt.approval_id ?? '',
    ),
    enabled: !!interrupt.approval_id,
  })
  if (!interrupt.approval_id || !interrupt.preview_hash || !interrupt.target_id)
    return (
      <p role="alert" className="text-sm">
        The approval interrupt has no complete preview binding. Resume is
        unavailable.
      </p>
    )
  return (
    <section
      aria-label="Approval observation"
      className="space-y-4 border-y py-5"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-base font-semibold">Human decision required</h2>
        <QueryRefresh query={query} label="approval decision" />
      </div>
      <Link
        className="text-sm text-emerald-700 underline underline-offset-4 dark:text-emerald-400"
        to={`/approvals?approval=${interrupt.approval_id}`}
      >
        Review exact approval
      </Link>
      <p className="text-sm text-muted-foreground">
        Continue this run after the review decision has been recorded.
      </p>
      <dl className="grid gap-2 text-xs sm:grid-cols-2">
        <div>
          <dt className="text-muted-foreground">Target</dt>
          <dd className="break-all">
            {humanize(interrupt.target_type ?? 'Review')}
          </dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Review integrity</dt>
          <dd>Exact preview recorded</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Expires</dt>
          <dd>
            {interrupt.expires_at
              ? formatDate(interrupt.expires_at)
              : 'Not recorded'}
          </dd>
        </div>
      </dl>
      <QueryState query={query}>
        {(approval) => {
          const bound =
            approval.preview_hash === interrupt.preview_hash &&
            approval.target_id === interrupt.target_id &&
            approval.target_type === interrupt.target_type
          const decided =
            !!approval.decided_by &&
            !!approval.decided_at &&
            !!approval.ledger_event_id &&
            (approval.status === 'approved' || approval.status === 'rejected')
          const usable =
            bound &&
            decided &&
            (approval.status === 'rejected' ||
              (!approval.expired && approval.preview_current)) &&
            !query.isError
          return (
            <form
              aria-label="Observe human decision"
              className="space-y-3"
              onSubmit={(event) => {
                event.preventDefault()
                if (!usable) return
                const parsed = z.uuid().safeParse(actorId)
                if (!parsed.success || parsed.data !== run.context.actor_id) {
                  setInvalid(true)
                  return
                }
                setInvalid(false)
                submit(parsed.data, {
                  approval: {
                    approval_id: interrupt.approval_id!,
                    preview_hash: interrupt.preview_hash!,
                  },
                })
              }}
            >
              <p className="text-sm">
                Recorded approval: <strong>{humanize(approval.status)}</strong>
              </p>
              {!bound && (
                <p role="alert" className="text-sm text-amber-700">
                  The current approval does not match this interrupt.
                </p>
              )}
              {approval.expired && (
                <p className="text-sm text-amber-700">
                  Approval preview expired.
                </p>
              )}
              {!approval.preview_current && (
                <p className="text-sm text-amber-700">
                  Approval preview is stale.
                </p>
              )}
              <div className="space-y-1.5">
                <Label htmlFor={requesterFieldId}>Requester</Label>
                <RecordSelect
                  id={requesterFieldId}
                  kind="actors"
                  value={actorId}
                  placeholder="Choose the original requester"
                  required
                  disabled={command.isPending}
                  onChange={(event) => {
                    setActorId(event.target.value)
                    setInvalid(false)
                  }}
                />
              </div>
              {invalid && (
                <p role="alert" className="text-sm text-amber-700">
                  Choose the original requester before continuing this activity.
                </p>
              )}
              <CommandError error={command.error} />
              {command.data && (
                <p role="status" className="text-sm">
                  Recorded resume result:{' '}
                  {humanize(command.data.terminal_state)}
                </p>
              )}
              <Button
                type="submit"
                disabled={!usable || command.isPending || !actorId}
                variant="outline"
              >
                <ArrowRight />
                {command.isPending
                  ? 'Observing decision...'
                  : 'Resume after recorded decision'}
              </Button>
            </form>
          )
        }}
      </QueryState>
    </section>
  )
}

function Clarification({
  run,
  scope,
  onResumed,
}: {
  run: AgentRun
  scope: WorkspaceScope
  onResumed: () => void
}) {
  const { command, submit } = useResume(run, onResumed)
  const [error, setError] = useState<Error | null>(null)
  return (
    <section
      aria-label="Clarification required"
      className="space-y-4 border-y py-5"
    >
      <h2 className="text-base font-semibold">Clarification required</h2>
      <p className="text-sm text-muted-foreground">
        The original requester must provide the missing information.
      </p>
      <AgentContextForm
        scope={scope}
        initialContext={agentContextSchema.parse(run.context)}
        workflow={run.workflow === 'planning' ? 'measurement' : run.workflow}
        promptRequired={run.plan === null}
        missingFields={run.pending_interrupt!.missing_fields}
        pending={command.isPending}
        error={error ?? command.error}
        submitLabel="Resume with clarification"
        onSubmit={(context, query) => {
          if (context.actor_id !== run.context.actor_id) {
            setError(
              new Error('Only the original requester can clarify this run.'),
            )
            return
          }
          setError(null)
          submit(context.actor_id, {
            clarification: {
              context,
              ...(query ? { clarified_query: query } : {}),
            },
          })
        }}
      />
    </section>
  )
}

export function ResumePanel({
  run,
  scope,
  onResumed,
}: {
  run: AgentRun
  scope: WorkspaceScope
  onResumed: () => void
}) {
  const interrupt = run.pending_interrupt
  if (
    !interrupt ||
    interrupt.status !== 'pending' ||
    ['stale', 'approval_invalidated'].includes(run.terminal_state)
  )
    return null
  return interrupt.kind === 'approval' ? (
    <ApprovalObserver
      key={interrupt.interrupt_id}
      run={run}
      onResumed={onResumed}
    />
  ) : (
    <Clarification
      key={interrupt.interrupt_id}
      run={run}
      scope={scope}
      onResumed={onResumed}
    />
  )
}
