import { useQuery } from '@tanstack/react-query'
import { Link, useOutletContext, useParams } from 'react-router-dom'
import { z } from 'zod'
import { EmptyState, QueryRefresh, QueryState } from '@/components/query-state'
import { Badge } from '@/components/ui/badge'
import { displayText, humanize } from '@/lib/presentation'
import { useWorkspaceOptions, type RecordKind } from '@/services/workspace'
import { RecordFields } from '@/features/approvals/review-components'
import { contextOptions } from '@/features/agents/queries'
import type { WorkspaceScope } from '@/lib/workspace'
import { OpenRunForm } from './open-run-form'
import { runOptions } from './queries'
import { ResumePanel } from './resume-panel'
import {
  RecordedValues,
  RunFacts,
  RunJudgments,
  RunPlan,
  RunTelemetry,
  RunTimeline,
  SustainabilitySection,
} from './run-sections'
import type { AgentRun } from './schemas'
import { useRunEvents } from './use-run-events'

function RecordName({
  kind,
  id,
  fallback,
}: {
  kind: RecordKind
  id: string
  fallback: string
}) {
  const query = useWorkspaceOptions(kind, { id })
  const item = query.data?.items.find((record) => record.id === id)
  return <>{item ? displayText(item.label) : fallback}</>
}

function RunDetail({
  run,
  scope,
  readFailed,
}: {
  run: AgentRun
  scope: WorkspaceScope
  readFailed: boolean
}) {
  const stream = useRunEvents(run)
  const context = useQuery({
    ...contextOptions({
      ...scope,
      site_id: run.context.site_id ?? scope.site_id,
      reporting_period_id:
        run.context.reporting_period_id ?? scope.reporting_period_id,
    }),
    enabled: !!run.context.site_id && !!run.context.reporting_period_id,
  })
  const artifacts = [
    {
      id: run.context.carbon_measurement_id,
      kind: 'measurements',
      title: 'Measurement',
      path: '/measurement/',
    },
    {
      id: run.context.disclosure_draft_id,
      kind: 'assurance',
      title: 'Disclosure draft',
      path: '/assurance/',
    },
    {
      id:
        run.recommendation?.scenario_id ?? run.context.procurement_scenario_id,
      kind: 'procurement',
      title: 'Supplier comparison',
      path: '/procurement/scenarios/',
    },
    {
      id: run.context.dispatch_scenario_id,
      kind: 'dispatch',
      title: 'Operating plan',
      path: '/dispatch/',
    },
  ] as const
  return (
    <div className="min-w-0 space-y-6">
      <section aria-label="Run context" className="border-y py-3">
        {run.context.site_id && run.context.reporting_period_id ? (
          <QueryState query={context}>
            {(data) => (
              <div className="flex flex-wrap items-center gap-3 text-sm">
                <strong>{displayText(data.company.name)}</strong>
                <span>{displayText(data.site.name)}</span>
                <span>{displayText(data.reporting_period.name)}</span>
                <Badge variant="outline">
                  {data.company.is_synthetic
                    ? 'Synthetic data / API'
                    : 'Non-synthetic data / API'}
                </Badge>
              </div>
            )}
          </QueryState>
        ) : (
          <p className="text-sm text-muted-foreground">
            The reporting context is incomplete.
          </p>
        )}
      </section>
      <section aria-label="Run outcome" className="space-y-4">
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="text-lg font-semibold">
            {humanize(run.workflow)} outcome
          </h2>
          <Badge variant="outline">{humanize(run.terminal_state)}</Badge>
        </div>
        {run.message && (
          <p className="max-w-4xl break-words text-sm">
            {displayText(run.message)}
          </p>
        )}
        {['stale', 'approval_invalidated'].includes(run.terminal_state) && (
          <p role="alert" className="text-sm text-amber-700">
            This result is out of date and cannot support a new decision.
          </p>
        )}
        {run.error_code && (
          <p className="text-sm text-amber-700">{humanize(run.error_code)}</p>
        )}
        {run.missing_fields.length > 0 && !run.pending_interrupt && (
          <p className="text-sm">
            More information needed:{' '}
            {run.missing_fields
              .map((field) => humanize(field.replace(/_ids?\b/g, '')))
              .join(', ')}
          </p>
        )}
        <RecordedValues
          values={[
            ['Started', run.started_at],
            ['Completed', run.completed_at],
            [
              'Requested by',
              <RecordName
                key="actor"
                kind="actors"
                id={run.context.actor_id}
                fallback="Requester name unavailable"
              />,
            ],
          ]}
        />
      </section>
      {!readFailed && (
        <ResumePanel run={run} scope={scope} onResumed={stream.reconnect} />
      )}
      <section aria-label="Run artifacts" className="space-y-3 border-t pt-5">
        <h2 className="text-base font-semibold">Records and next steps</h2>
        <ul className="divide-y">
          {artifacts.map((artifact) =>
            artifact.id ? (
              <li
                key={artifact.kind}
                className="flex flex-wrap items-center justify-between gap-3 py-3 text-sm"
              >
                <span className="text-muted-foreground">{artifact.title}</span>
                <Link
                  className="min-w-0 break-words text-emerald-700 underline"
                  to={artifact.path + artifact.id}
                >
                  <RecordName
                    kind={artifact.kind}
                    id={artifact.id}
                    fallback={`Open ${artifact.title.toLowerCase()}`}
                  />
                </Link>
              </li>
            ) : null,
          )}
          {run.approval_requirement.approval_id && (
            <li className="py-3 text-sm">
              <Link
                className="text-emerald-700 underline"
                to={`/approvals?approval=${run.approval_requirement.approval_id}`}
              >
                Review approval
              </Link>
            </li>
          )}
        </ul>
        {!artifacts.some((artifact) => artifact.id) &&
          !run.approval_requirement.approval_id && (
            <p className="text-sm text-muted-foreground">
              No linked records have been returned for this run.
            </p>
          )}
      </section>
      <RunFacts run={run} />
      <RunTimeline
        events={stream.events}
        connection={stream.connection}
        onRefresh={stream.reconnect}
      />
      <RunJudgments run={run} />
      <details className="border-t pt-4">
        <summary className="cursor-pointer text-sm font-medium">
          Analysis scope and constraints
        </summary>
        <div className="mt-4 space-y-5">
          <RecordedValues
            values={[
              [
                'Materials',
                run.context.material_scope?.map(humanize).join(', ') || null,
              ],
              [
                'Metrics',
                run.context.metric_keys?.map(humanize).join(', ') || null,
              ],
              [
                'Request integrity',
                run.context.request_hash ? 'Recorded' : 'Not recorded',
              ],
              [
                'Analysis integrity',
                run.context.analysis_signature ? 'Recorded' : 'Not recorded',
              ],
            ]}
          />
          <RecordFields value={run.context.constraints} />
          <RecordFields value={run.context.dispatch_constraints} />
          {run.context.fresh_inputs && (
            <RecordFields value={run.context.fresh_inputs} />
          )}
        </div>
      </details>
      <details className="border-t pt-4">
        <summary className="cursor-pointer text-sm font-medium">
          Analysis plan
        </summary>
        <RunPlan run={run} />
      </details>
      <details className="border-t pt-4">
        <summary className="cursor-pointer text-sm font-medium">
          Technical telemetry and footprint estimates
        </summary>
        <RunTelemetry run={run} />
        <SustainabilitySection companyId={scope.company_id} />
      </details>
    </div>
  )
}

function KnownRun({ runId, scope }: { runId: string; scope: WorkspaceScope }) {
  const query = useQuery(runOptions(scope.company_id, runId))
  return (
    <main className="min-w-0 space-y-5 px-5 py-6 sm:px-8">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="mb-2 text-xs font-medium text-muted-foreground">
            Agent workspace
          </p>
          <h1 className="text-2xl font-semibold">Activity</h1>
        </div>
        <div className="flex items-center gap-4">
          <Link className="text-sm text-emerald-700 underline" to="/runs">
            Open another run
          </Link>
          <QueryRefresh query={query} label="run" />
        </div>
      </header>
      <QueryState query={query}>
        {(run) => (
          <RunDetail
            key={run.run_id}
            run={run}
            scope={scope}
            readFailed={query.isError}
          />
        )}
      </QueryState>
    </main>
  )
}

export default function RunPage() {
  const scope = useOutletContext<WorkspaceScope>()
  const { runId } = useParams()
  if (!runId || !z.uuid().safeParse(runId).success)
    return <RunLanding scope={scope} invalid={!!runId} />
  return (
    <KnownRun
      key={`${scope.company_id}-${runId}`}
      scope={scope}
      runId={runId}
    />
  )
}

function RunLanding({
  scope,
  invalid,
}: {
  scope: WorkspaceScope
  invalid: boolean
}) {
  const context = useQuery(contextOptions(scope))
  return (
    <main className="space-y-6 px-5 py-6 sm:px-8">
      <header className="space-y-2">
        <p className="text-xs font-medium text-muted-foreground">
          Agent workspace
        </p>
        <h1 className="text-2xl font-semibold">Activity</h1>
      </header>
      <section aria-label="Workspace context" className="border-y py-3">
        <QueryState query={context}>
          {(data) => (
            <div className="flex flex-wrap items-center gap-3 text-sm">
              <strong>{displayText(data.company.name)}</strong>
              <Badge variant="outline">
                {data.company.is_synthetic
                  ? 'Synthetic data / API'
                  : 'Non-synthetic data / API'}
              </Badge>
            </div>
          )}
        </QueryState>
      </section>
      {invalid && (
        <EmptyState
          title="This run link is unavailable"
          detail="Choose a saved run below."
        />
      )}
      <div className="max-w-lg">
        <OpenRunForm />
      </div>
      <Link
        to="/ask"
        className="inline-block text-sm text-emerald-700 underline underline-offset-4 dark:text-emerald-400"
      >
        Create a request
      </Link>
    </main>
  )
}
