import { zodResolver } from '@hookform/resolvers/zod'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Download, ShieldCheck } from 'lucide-react'
import { useEffect } from 'react'
import { useForm } from 'react-hook-form'
import { Link, useOutletContext, useParams } from 'react-router-dom'
import { z } from 'zod'
import { EmptyState, QueryRefresh, QueryState } from '@/components/query-state'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useWorkspaceActor } from '@/features/auth/use-workspace-actor'
import type { WorkspaceScope } from '@/lib/workspace'
import { ApiError, apiRequest } from '@/services/api'
import { assuranceQueries, draftInScope } from './queries'
import {
  evidencePackSchema,
  validationSchema,
  type Claim,
  type Draft,
} from './schemas'
import {
  CommandError,
  FieldError,
  HashValue,
  LedgerLink,
  StateBadge,
  ValidationReasons,
  WorkflowContext,
} from './workflow-ui'
import { usePayloadKey } from './use-payload-key'

function AtomicClaim({ claim }: { claim: Claim }) {
  return (
    <article
      className="space-y-3 border-b py-5"
      aria-label={`Claim ${claim.sequence}`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold">
          {claim.requirement_code ?? claim.claim_type}
        </h3>
        <StateBadge state={claim.support_status} />
        <span className="text-xs text-muted-foreground">
          Confidence: {claim.confidence}
        </span>
      </div>
      <p className="whitespace-pre-wrap break-words text-sm">
        {claim.rendered_text ?? 'No supported claim text.'}
      </p>
      {!claim.rendered_text && (
        <details className="text-sm">
          <summary className="cursor-pointer text-muted-foreground">
            Unbound claim template
          </summary>
          <p className="mt-2 break-words">{claim.claim_template}</p>
        </details>
      )}
      <ValidationReasons details={claim.validation_details} />
      {claim.ledger_event_id && (
        <p className="text-xs">
          <LedgerLink id={claim.ledger_event_id}>Claim ledger fact</LedgerLink>
        </p>
      )}
      {claim.fact_binding_id && (
        <p className="break-all text-xs text-muted-foreground">
          Fact binding: {claim.fact_binding_id}
        </p>
      )}
      <h4 className="text-xs font-semibold">Citations</h4>
      {!claim.citations.length && (
        <p className="text-sm text-muted-foreground">No citations recorded.</p>
      )}
      {claim.citations.map((citation) => (
        <div key={citation.id} className="space-y-2 border-l-2 pl-3 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <StateBadge state={citation.validation_status} />
            <span className="break-words">
              {citation.evidence?.source_filename ?? 'Ledger citation'}
            </span>
          </div>
          <p className="break-words text-xs">
            {citation.locator ?? citation.evidence?.locator}
          </p>
          {citation.ledger_event_id && (
            <LedgerLink id={citation.ledger_event_id}>
              Source ledger event
            </LedgerLink>
          )}
          {citation.evidence_item_id && (
            <p className="break-all text-xs text-muted-foreground">
              Evidence: {citation.evidence_item_id}
            </p>
          )}
          <ValidationReasons details={citation.validation_details} />
          {citation.evidence && (
            <details>
              <summary className="cursor-pointer text-xs">
                Evidence provenance
              </summary>
              <Link
                className="mt-2 block text-xs text-emerald-700 underline"
                to={`/data?document=${citation.evidence.source_document_id}`}
              >
                Source document
              </Link>
              <dl className="mt-3 grid gap-3 sm:grid-cols-2">
                <HashValue
                  label="Source document"
                  value={citation.evidence.source_document_id}
                />
                <HashValue
                  label="Source checksum"
                  value={citation.evidence.source_document_checksum}
                />
                <HashValue
                  label="Evidence checksum"
                  value={citation.evidence.checksum}
                />
                <HashValue
                  label="Evidence type"
                  value={citation.evidence.evidence_type}
                />
              </dl>
            </details>
          )}
        </div>
      ))}
    </article>
  )
}

const actorSchema = z.object({ requested_by: z.uuid() })
function DraftContent({
  draft,
  scope,
  readFailed,
}: {
  draft: Draft
  scope: WorkspaceScope
  readFailed: boolean
}) {
  const client = useQueryClient()
  const actor = useWorkspaceActor()
  const keyFor = usePayloadKey()
  const form = useForm<z.infer<typeof actorSchema>>({
    resolver: zodResolver(actorSchema),
    defaultValues: { requested_by: actor.actorId },
  })
  useEffect(() => {
    if (actor.actorId) form.setValue('requested_by', actor.actorId)
  }, [actor.actorId, form])
  const stale =
    draft.status === 'invalidated' ||
    draft.invalidated_at !== null ||
    draft.validation_summary.terminal_state === 'stale'
  const validate = useMutation({
    mutationFn: ({ requested_by }: z.infer<typeof actorSchema>) => {
      const body = {
        company_id: scope.company_id,
        requested_by: actor.actorId || requested_by,
        expected_context_hash: draft.context_hash,
      }
      return apiRequest(
        `/assurance/drafts/${draft.id}/validate`,
        validationSchema.refine(
          (result) =>
            result.draft.id === draft.id && draftInScope(result.draft, scope),
        ),
        {
          signal: new AbortController().signal,
          method: 'POST',
          body: {
            ...body,
            idempotency_key: keyFor({ draft_id: draft.id, ...body }),
          },
        },
      )
    },
    retry: false,
    onSuccess: (result) =>
      client.setQueryData(
        assuranceQueries.draft(scope, draft.id).queryKey,
        result.draft,
      ),
  })
  const pack = useMutation({
    mutationFn: () =>
      apiRequest(
        `/assurance/drafts/${draft.id}/evidence-pack`,
        evidencePackSchema.refine(
          (result) =>
            result.company_id === scope.company_id &&
            result.draft_id === draft.id &&
            result.context_hash === draft.context_hash &&
            result.payload_hash === draft.payload_hash,
        ),
        {
          signal: new AbortController().signal,
          params: { company_id: scope.company_id },
        },
      ),
    retry: false,
    onSuccess: (result) => {
      const url = URL.createObjectURL(
        new Blob([JSON.stringify(result, null, 2)], {
          type: 'application/json',
        }),
      )
      const link = document.createElement('a')
      link.href = url
      link.download = `assurance-${draft.id}-evidence-pack.json`
      link.click()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    },
  })
  const staleExport =
    pack.error instanceof ApiError &&
    (pack.error.code.includes('stale') || pack.error.terminalState === 'stale')
  return (
    <>
      <header className="space-y-3">
        <h1 className="break-words text-2xl font-semibold">{draft.title}</h1>
        <div className="flex flex-wrap items-center gap-3">
          <StateBadge state={draft.status} />
          <span className="text-sm text-muted-foreground">
            {draft.standard.code} / {draft.standard.version}
          </span>
          <span className="text-sm">Version {draft.version}</span>
        </div>
        <p className="text-sm text-muted-foreground">
          POC draft; not an assurance opinion or filing.
        </p>
      </header>
      {(stale || staleExport) && (
        <p
          role="alert"
          className="border-l-2 border-amber-600 pl-3 text-sm text-amber-800 dark:text-amber-400"
        >
          Stale draft. Its recorded claims and preview are no longer current.
        </p>
      )}
      <div className="flex flex-wrap gap-4 text-sm">
        <Link
          className="text-emerald-700 underline"
          to={`/measurement/${draft.measurement_id}`}
        >
          Source measurement
        </Link>
        {draft.ledger_event_id && (
          <LedgerLink id={draft.ledger_event_id}>Draft lineage</LedgerLink>
        )}
        {draft.agent_run_id && (
          <Link
            className="text-emerald-700 underline"
            to={`/runs/${draft.agent_run_id}`}
          >
            Agent run
          </Link>
        )}
      </div>
      <section
        aria-label="Draft validation"
        className="space-y-4 border-y py-5"
      >
        <h2 className="text-base font-semibold">Validation and export</h2>
        <form
          aria-label="Validate draft"
          onSubmit={form.handleSubmit((values) => validate.mutate(values))}
          className="flex flex-wrap items-start gap-3"
        >
          <div className="min-w-0 basis-80">
            <label
              htmlFor="validate-actor"
              className="mb-2 block text-xs font-medium"
            >
              Validating actor UUID
            </label>
            <Input
              id="validate-actor"
              required
              readOnly={!!actor.actorId}
              {...form.register('requested_by')}
            />
            <FieldError message={form.formState.errors.requested_by?.message} />
          </div>
          <Button
            type="submit"
            className="mt-6"
            disabled={validate.isPending || readFailed || stale}
          >
            <ShieldCheck />
            {validate.isPending ? 'Validating...' : 'Validate draft'}
          </Button>
        </form>
        <CommandError error={validate.error} />
        {validate.data && (
          <div role="status" className="flex flex-wrap gap-3 text-sm">
            <StateBadge state={validate.data.terminal_state} />
            <span>Supported: {validate.data.supported_claims}</span>
            <span>
              Partially supported: {validate.data.partially_supported_claims}
            </span>
            <span>Unsupported: {validate.data.unsupported_claims}</span>
            <span>Open gaps: {validate.data.open_gaps}</span>
          </div>
        )}
        <form
          aria-label="Export evidence pack"
          onSubmit={(event) => {
            event.preventDefault()
            pack.mutate()
          }}
        >
          <Button
            type="submit"
            variant="outline"
            disabled={
              pack.isPending ||
              stale ||
              staleExport ||
              readFailed ||
              !draft.claims.length ||
              validate.isPending
            }
          >
            <Download />
            {pack.isPending ? 'Exporting...' : 'Export evidence pack'}
          </Button>
        </form>
        <CommandError error={pack.error} />
      </section>
      {draft.approval && (
        <section
          aria-label="Disclosure approval"
          className="space-y-3 border-b pb-5"
        >
          <div className="flex flex-wrap items-center gap-3">
            <h2 className="text-base font-semibold">Approval preview</h2>
            <StateBadge state={draft.approval.status} />
            <Link
              className="text-sm text-emerald-700 underline"
              to={`/approvals?approval=${draft.approval.id}`}
            >
              Review approval
            </Link>
          </div>
          <p className="text-sm">Expires: {draft.approval.expires_at}</p>
          <dl className="grid gap-3 sm:grid-cols-2">
            <HashValue
              label="Preview hash"
              value={draft.approval.preview_hash}
            />
            <HashValue
              label="Analysis signature"
              value={draft.approval.analysis_signature}
            />
          </dl>
        </section>
      )}
      <section aria-label="Atomic claims">
        <h2 className="text-base font-semibold">Atomic claims</h2>
        {draft.claims.length ? (
          draft.claims.map((claim) => (
            <AtomicClaim key={claim.id} claim={claim} />
          ))
        ) : (
          <EmptyState
            title="No validated claims"
            detail="This draft has not produced atomic claims."
          />
        )}
      </section>
      <section aria-label="Evidence gaps" className="space-y-3">
        <h2 className="text-base font-semibold">Evidence gaps</h2>
        {!draft.gaps.length && (
          <p className="text-sm text-muted-foreground">
            No evidence gaps recorded.
          </p>
        )}
        {draft.gaps.map((gap) => (
          <article key={gap.id} className="space-y-2 border-b py-3">
            <div className="flex flex-wrap items-center gap-2">
              <StateBadge state={gap.status} />
              <span className="text-xs font-medium">
                {gap.severity} / {gap.code}
              </span>
            </div>
            <p className="text-sm">{gap.message}</p>
            <ValidationReasons details={gap.details} />
          </article>
        ))}
      </section>
      <details className="border-t pt-4">
        <summary className="cursor-pointer text-sm font-medium">
          Record identity and hashes
        </summary>
        <dl className="mt-4 grid gap-4 sm:grid-cols-2">
          <HashValue label="Draft" value={draft.id} />
          <HashValue label="Context hash" value={draft.context_hash} />
          <HashValue label="Payload hash" value={draft.payload_hash} />
          <HashValue label="Updated" value={draft.updated_at} />
        </dl>
      </details>
    </>
  )
}
function DraftWorkspace({ scope, id }: { scope: WorkspaceScope; id: string }) {
  const query = useQuery(assuranceQueries.draft(scope, id))
  return (
    <main className="min-w-0 space-y-6 px-5 py-6 sm:px-8">
      <div className="flex items-center justify-between">
        <Link to="/assurance" className="flex items-center gap-2 text-sm">
          <ArrowLeft className="size-4" />
          Assurance
        </Link>
        <QueryRefresh query={query} label="draft" />
      </div>
      <WorkflowContext scope={scope} />
      <QueryState query={query}>
        {(draft) => (
          <DraftContent
            draft={draft}
            scope={scope}
            readFailed={query.isError}
          />
        )}
      </QueryState>
    </main>
  )
}
export default function AssuranceDraftPage() {
  const scope = useOutletContext<WorkspaceScope>()
  const { draftId } = useParams()
  if (!z.uuid().safeParse(draftId).success)
    return (
      <main className="px-5 py-6 sm:px-8">
        <h1 className="text-xl font-semibold">Invalid draft identifier</h1>
        <Link to="/assurance" className="text-sm underline">
          Return to Assurance
        </Link>
      </main>
    )
  return (
    <DraftWorkspace
      key={`${scope.company_id}:${scope.site_id}:${scope.reporting_period_id}:${draftId}`}
      scope={scope}
      id={draftId!}
    />
  )
}
