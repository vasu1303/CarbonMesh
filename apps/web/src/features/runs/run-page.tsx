import { useQuery } from '@tanstack/react-query'
import { RefreshCw } from 'lucide-react'
import { Link, useOutletContext, useParams } from 'react-router-dom'
import { z } from 'zod'
import { EmptyState, QueryRefresh, QueryState } from '@/components/query-state'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
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
  SustainabilitySection,
} from './run-sections'
import type { AgentRun } from './schemas'
import { useRunEvents } from './use-run-events'

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
  return (
    <div className="min-w-0 space-y-6">
      <section aria-label="Run context" className="border-y py-3">
        {run.context.site_id && run.context.reporting_period_id ? (
          <QueryState query={context}>
            {(data) => (
              <div className="flex flex-wrap items-center gap-3 text-sm">
                <strong>{data.company.name}</strong>
                <span>{data.site.name}</span>
                <span>{data.reporting_period.name}</span>
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
            Incomplete run context; data provenance is not resolved.
          </p>
        )}
        <p className="mt-2 text-xs text-muted-foreground">
          Grid source:{' '}
          {run.context.grid_source_mode === 'fixture'
            ? 'synthetic fixture'
            : 'live provider'}
        </p>
      </section>
      <section aria-label="Run status" className="space-y-4">
        <div className="flex flex-wrap items-center gap-3">
          <Badge
            variant="outline"
            className={
              run.terminal_state === 'success' ||
              run.terminal_state === 'completed'
                ? 'border-emerald-600 text-emerald-700 dark:text-emerald-400'
                : ''
            }
          >
            {run.terminal_state.replaceAll('_', ' ')}
          </Badge>
          <span className="text-sm text-muted-foreground">
            {run.workflow.replaceAll('_', ' ')} / {run.stage}
          </span>
        </div>
        {run.message && (
          <p className="max-w-4xl break-words text-sm">{run.message}</p>
        )}
        {['stale', 'approval_invalidated'].includes(run.terminal_state) && (
          <p role="alert" className="text-sm text-amber-700">
            The recorded result is stale and cannot support a new decision.
          </p>
        )}
        {run.error_code && (
          <p className="text-xs text-amber-700">{run.error_code}</p>
        )}
        {run.missing_fields.length > 0 && !run.pending_interrupt && (
          <p className="break-words text-sm">
            Missing context: {run.missing_fields.join(', ')}
          </p>
        )}
        <RecordedValues
          values={[
            ['Run ID', run.run_id],
            ['Trace ID', run.trace_id],
            ['Started', run.started_at],
            ['Completed', run.completed_at],
            ['Actor', run.context.actor_id],
            ['Actor role', run.context.actor_role.replaceAll('_', ' ')],
          ]}
        />
      </section>
      {!readFailed && (
        <ResumePanel run={run} scope={scope} onResumed={stream.reconnect} />
      )}
      <RunFacts run={run} />
      <RunJudgments run={run} />
      <RunPlan run={run} />
      <section aria-label="Source records" className="space-y-3 border-t pt-5">
        <h2 className="text-base font-semibold">Source records</h2>
        <div className="flex flex-wrap gap-4 text-sm">
          {run.context.carbon_measurement_id && (
            <Link
              className="text-emerald-700 underline"
              to={`/measurement/${run.context.carbon_measurement_id}`}
            >
              Measurement
            </Link>
          )}
          {run.context.disclosure_draft_id && (
            <Link
              className="text-emerald-700 underline"
              to={`/assurance/${run.context.disclosure_draft_id}`}
            >
              Disclosure draft
            </Link>
          )}
          {run.context.procurement_scenario_id && (
            <Link
              className="text-emerald-700 underline"
              to={`/procurement/scenarios/${run.context.procurement_scenario_id}`}
            >
              Procurement scenario
            </Link>
          )}
          {run.context.dispatch_scenario_id && (
            <Link
              className="text-emerald-700 underline"
              to={`/dispatch/${run.context.dispatch_scenario_id}`}
            >
              Dispatch scenario
            </Link>
          )}
          {run.approval_requirement.approval_id && (
            <Link
              className="text-emerald-700 underline"
              to={`/approvals?approval=${run.approval_requirement.approval_id}`}
            >
              Approval
            </Link>
          )}
        </div>
        <details>
          <summary className="cursor-pointer text-sm text-muted-foreground">
            Frozen scope and hashes
          </summary>
          <div className="mt-4">
            <RecordedValues
              values={[
                ['Company UUID', run.context.company_id],
                ['Site UUID', run.context.site_id],
                ['Period UUID', run.context.reporting_period_id],
                ['Measurement method UUID', run.context.method_definition_id],
                ['Policy UUID', run.context.policy_definition_id],
                [
                  'Activity UUIDs',
                  run.context.activity_record_ids?.join(', ') || null,
                ],
                [
                  'Evidence UUIDs',
                  run.context.evidence_item_ids?.join(', ') || null,
                ],
                ['Standard UUID', run.context.standard_id],
                ['Flexible load UUID', run.context.flexible_load_id],
                ['Forecast UUID', run.context.forecast_id],
                ['Request hash', run.context.request_hash],
                ['Analysis signature', run.context.analysis_signature],
              ]}
            />
          </div>
        </details>
      </section>
      <section
        aria-label="Frozen constraints"
        className="space-y-4 border-t pt-5"
      >
        <h2 className="text-base font-semibold">Frozen constraints</h2>
        <RecordedValues
          values={[
            ['Material scope', run.context.material_scope?.join(', ') || null],
            ['Metric scope', run.context.metric_keys?.join(', ') || null],
            [
              'Maximum cost increase (%)',
              run.context.constraints.max_cost_increase_pct,
            ],
            [
              'Maximum lead time (days)',
              run.context.constraints.max_lead_time_days,
            ],
            [
              'Minimum circularity score',
              run.context.constraints.minimum_circularity_score,
            ],
            [
              'Dispatch window start',
              run.context.dispatch_constraints.window_start,
            ],
            [
              'Dispatch window end',
              run.context.dispatch_constraints.window_end,
            ],
            [
              'Duration (minutes)',
              run.context.dispatch_constraints.duration_minutes,
            ],
            [
              'Maximum delay (minutes)',
              run.context.dispatch_constraints.max_delay_minutes,
            ],
            [
              'Maximum power (kW)',
              run.context.dispatch_constraints.maximum_power_kw,
            ],
            [
              'Blackout constraint UUIDs',
              run.context.dispatch_constraints.blackout_constraint_ids?.join(
                ', ',
              ) || null,
            ],
          ]}
        />
        {run.context.fresh_inputs && (
          <details>
            <summary className="cursor-pointer text-sm text-muted-foreground">
              Fresh source inputs
            </summary>
            <div className="mt-4 space-y-4">
              <RecordedValues
                values={[
                  [
                    'Procurement quantity (kg)',
                    run.context.fresh_inputs.procurement_quantity,
                  ],
                  [
                    'Procurement method UUID',
                    run.context.fresh_inputs.procurement_method_id,
                  ],
                  [
                    'Dispatch method UUID',
                    run.context.fresh_inputs.dispatch_method_id,
                  ],
                  [
                    'Dispatch baseline start',
                    run.context.fresh_inputs.dispatch_baseline_start,
                  ],
                  ['History start', run.context.fresh_inputs.history_start],
                  ['History end', run.context.fresh_inputs.history_end],
                ]}
              />
              {run.context.fresh_inputs.measurements.map((source) => (
                <div className="border-t pt-3" key={source.output_metric_key}>
                  <RecordedValues
                    values={[
                      ['Output metric', source.output_metric_key],
                      ['Material code', source.material_code],
                      [
                        'Source activity UUIDs',
                        source.activity_record_ids?.join(', ') || null,
                      ],
                      ['Geography', source.geography],
                      ['Grid zone', source.grid_zone],
                      ['Grid method version', source.grid_method_version],
                    ]}
                  />
                </div>
              ))}
            </div>
          </details>
        )}
      </section>
      <RunTelemetry
        run={run}
        events={stream.events}
        connection={stream.connection}
      />
      <Button size="sm" variant="outline" onClick={stream.reconnect}>
        <RefreshCw />
        Reload event trace
      </Button>
      <SustainabilitySection companyId={scope.company_id} />
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
          <h1 className="text-2xl font-semibold">Run trace</h1>
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
        <h1 className="text-2xl font-semibold">Open run</h1>
      </header>
      <section aria-label="Workspace context" className="border-y py-3">
        <QueryState query={context}>
          {(data) => (
            <div className="flex flex-wrap items-center gap-3 text-sm">
              <strong>{data.company.name}</strong>
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
          title="Invalid run UUID"
          detail="Enter the identifier of an existing run."
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
