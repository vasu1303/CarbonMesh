import { useQuery } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { EmptyState, QueryRefresh, QueryState } from '@/components/query-state'
import { Badge } from '@/components/ui/badge'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import {
  executionPlanSchema,
  legacyPlanSchema,
  type AgentRun,
  type RunEvent,
} from './schemas'
import { sustainabilityOptions } from './queries'

export function LedgerLink({
  id,
  children,
}: {
  id: string
  children?: ReactNode
}) {
  return (
    <Link
      to={`/ledger?event=${id}`}
      className="break-all text-emerald-700 underline underline-offset-4 dark:text-emerald-400"
    >
      {children ?? id}
    </Link>
  )
}
export function RecordedValues({ values }: { values: [string, ReactNode][] }) {
  return (
    <dl className="grid min-w-0 gap-x-6 gap-y-4 text-sm sm:grid-cols-2 lg:grid-cols-3">
      {values.map(([label, value]) => (
        <div key={label} className="min-w-0">
          <dt className="text-xs text-muted-foreground">{label}</dt>
          <dd className="mt-1 break-words [overflow-wrap:anywhere]">
            {value ?? 'Not recorded'}
          </dd>
        </div>
      ))}
    </dl>
  )
}
export function RunFacts({ run }: { run: AgentRun }) {
  return (
    <section aria-label="Verified facts" className="space-y-4 border-t pt-5">
      <h2 className="text-base font-semibold">Verified facts</h2>
      {run.facts.length ? (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Metric</TableHead>
              <TableHead>Recorded value</TableHead>
              <TableHead>Fact / source</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {run.facts.map((fact) => (
              <TableRow key={fact.fact_id}>
                <TableCell className="max-w-80 whitespace-normal break-words">
                  {fact.metric_key}
                </TableCell>
                <TableCell className="font-medium tabular-nums">
                  {fact.display_value}
                </TableCell>
                <TableCell>
                  <p className="font-mono text-xs">{fact.fact_id}</p>
                  <LedgerLink id={fact.ledger_event_id}>
                    Ledger lineage
                  </LedgerLink>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      ) : (
        <EmptyState
          title="No verified facts recorded"
          detail="This run has not returned bound ledger facts."
        />
      )}
      {run.recommendation && (
        <div className="space-y-4 border-t pt-4">
          <h3 className="text-sm font-medium">
            Recorded procurement recommendation
          </h3>
          <RecordedValues
            values={[
              ['Status', run.recommendation.status],
              [
                'Projected footprint (kgCO2e)',
                run.recommendation.projected_footprint_kgco2e,
              ],
              [
                'Projected avoided emissions (kgCO2e)',
                run.recommendation.avoided_kgco2e,
              ],
              ['Reduction (%)', run.recommendation.reduction_pct],
              ['Cost change (%)', run.recommendation.cost_delta_pct],
              [
                'Lead time change (days)',
                run.recommendation.lead_time_delta_days,
              ],
            ]}
          />
          <div className="flex flex-wrap gap-4 text-sm">
            <Link
              className="text-emerald-700 underline"
              to={`/procurement/scenarios/${run.recommendation.scenario_id}`}
            >
              Open scenario
            </Link>
            <LedgerLink id={run.recommendation.ledger_event_id}>
              Recommendation lineage
            </LedgerLink>
          </div>
        </div>
      )}
    </section>
  )
}
export function RunJudgments({ run }: { run: AgentRun }) {
  return (
    <section aria-label="Judgments" className="space-y-4 border-t pt-5">
      <h2 className="text-base font-semibold">Judgments</h2>
      {run.judgments.length ? (
        <ul className="divide-y">
          {run.judgments.map((judgment, index) => (
            <li key={index} className="space-y-2 py-3 text-sm">
              <p>
                {judgment.kind.replaceAll('_', ' ')}:{' '}
                <strong>{judgment.value.replaceAll('_', ' ')}</strong>
              </p>
              <p className="text-muted-foreground">
                Basis: {judgment.basis.replaceAll('_', ' ')}
              </p>
              {judgment.matched_terms.length > 0 && (
                <p>Matched terms: {judgment.matched_terms.join(', ')}</p>
              )}
            </li>
          ))}
        </ul>
      ) : (
        <EmptyState
          title="No judgments recorded"
          detail="No workflow judgment was returned."
        />
      )}
      {(run.unsupported_reason || run.unsupported_items.length > 0) && (
        <div className="space-y-2 border-l-2 border-amber-500 pl-3 text-sm">
          <h3 className="font-medium">Unsupported items</h3>
          {run.unsupported_reason && <p>{run.unsupported_reason}</p>}
          <ul className="space-y-1">
            {run.unsupported_items.map((item, index) => (
              <li key={index}>{item}</li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}
export function RunPlan({ run }: { run: AgentRun }) {
  const plan = executionPlanSchema.safeParse(run.plan)
  const legacy = legacyPlanSchema.safeParse(run.plan)
  return (
    <section aria-label="Execution plan" className="space-y-3 border-t pt-5">
      <h2 className="text-base font-semibold">Execution plan</h2>
      {plan.success ? (
        <>
          <p className="text-xs text-muted-foreground">
            {plan.data.version} / {plan.data.profile}
          </p>
          <p className="text-sm break-words">
            Context tools: {plan.data.context_tools.join(', ')}
          </p>
          <ol className="divide-y">
            {plan.data.stages.map((stage) => (
              <li key={stage.stage_id} className="space-y-2 py-3 text-sm">
                <div className="flex flex-wrap items-center gap-3">
                  <strong>{stage.module}</strong>
                  {stage.requires_human_approval && (
                    <Badge variant="outline">Human review</Badge>
                  )}
                </div>
                <p className="break-words text-muted-foreground">
                  {stage.tool_names.join(', ')}
                </p>
                {stage.depends_on.length > 0 && (
                  <p>Depends on: {stage.depends_on.join(', ')}</p>
                )}
              </li>
            ))}
          </ol>
        </>
      ) : legacy.success ? (
        <ul className="divide-y">
          {legacy.data.steps.map((step) => (
            <li key={step.id} className="space-y-1 py-3 text-sm">
              <strong>{step.title}</strong>
              <p>{step.responsibility}</p>
              <p className="text-muted-foreground">
                {step.status} / {step.tool_ids.join(', ')}
              </p>
            </li>
          ))}
        </ul>
      ) : (
        <EmptyState
          title={run.plan ? 'Plan format unsupported' : 'No frozen plan'}
          detail={
            run.plan
              ? 'The recorded plan does not match a supported display format.'
              : 'Planning has not produced a frozen execution plan.'
          }
        />
      )}
    </section>
  )
}
export function RunTelemetry({
  run,
  events,
  connection,
}: {
  run: AgentRun
  events: RunEvent[]
  connection: string
}) {
  const t = run.telemetry
  return (
    <section aria-label="Run telemetry" className="space-y-5 border-t pt-5">
      <h2 className="text-base font-semibold">Run telemetry</h2>
      <RecordedValues
        values={[
          ['Provider', `${t.provider} / ${t.provider_status}`],
          ['Model', t.model_id],
          ['Orchestrator', t.orchestrator_version],
          ['Model calls', t.model_calls],
          ['Tool calls', t.tool_calls],
          ['External API calls', t.api_calls],
          ['Input tokens', t.input_tokens],
          ['Output tokens', t.output_tokens],
          ['Cached input tokens', t.cached_input_tokens],
          ['Context tokens', t.context_tokens],
          ['Cache hits', t.cache_hits],
          ['External cache hits', t.external_cache_hits],
          ['Retries', t.retry_count],
          ['Repairs', t.repairs],
          ['Latency (ms)', t.elapsed_ms],
          ['Execution attempts', t.execution_attempts],
          ['Rows processed', t.rows_processed],
          ['Evidence chunks', t.evidence_chunks_retrieved],
          ['Estimated energy (Wh)', t.estimated_energy_wh],
          ['Estimated footprint (gCO2e)', t.estimated_co2e_g],
          ['Proxy method', t.sustainability_method],
        ]}
      />
      {Object.keys(t.sustainability_assumptions).length > 0 && (
        <details className="border-y py-3">
          <summary className="cursor-pointer text-sm font-medium">
            Recorded run proxy assumptions
          </summary>
          <div className="mt-4">
            <RecordedValues
              values={Object.entries(t.sustainability_assumptions).map(
                ([key, value]) => [key.replaceAll('_', ' '), value],
              )}
            />
          </div>
        </details>
      )}
      {t.stage_timings.length > 0 && (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Stage</TableHead>
              <TableHead>Elapsed (ms)</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {t.stage_timings.map((stage, index) => (
              <TableRow key={index}>
                <TableCell>{stage.stage}</TableCell>
                <TableCell>{stage.elapsed_ms}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      <div className="space-y-3">
        <h3 className="text-sm font-medium">Persisted event trace</h3>
        <p role="status" className="text-xs text-muted-foreground">
          {connection}
        </p>
        {events.length ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Sequence / time</TableHead>
                <TableHead>Event</TableHead>
                <TableHead>Graph / node / tool</TableHead>
                <TableHead>Provider / cache</TableHead>
                <TableHead>Tokens in / out</TableHead>
                <TableHead>Retry / latency</TableHead>
                <TableHead>Outcome</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {events.map((event) => (
                <TableRow key={event.sequence}>
                  <TableCell>
                    <p>{event.sequence}</p>
                    <p className="text-xs text-muted-foreground">
                      {event.occurred_at}
                    </p>
                  </TableCell>
                  <TableCell>{event.name}</TableCell>
                  <TableCell>
                    <p>{event.data.graph_name ?? 'Not recorded'}</p>
                    <p className="text-xs">
                      {event.data.node_name ?? event.data.stage ?? ''}
                    </p>
                    <p className="text-xs">
                      {event.data.tool_name ?? event.data.tool ?? ''}
                    </p>
                  </TableCell>
                  <TableCell>
                    <p>{event.data.provider ?? 'Not recorded'}</p>
                    <p className="text-xs">{event.data.model_id}</p>
                    <p className="text-xs">
                      {event.data.cache_hit === undefined ||
                      event.data.cache_hit === null
                        ? event.name === 'provider.cache_hit'
                          ? 'Cache hit'
                          : ''
                        : event.data.cache_hit
                          ? 'Cache hit'
                          : 'Cache miss'}
                    </p>
                  </TableCell>
                  <TableCell>
                    {event.data.input_tokens ?? 'Not recorded'} /{' '}
                    {event.data.output_tokens ?? 'Not recorded'}
                  </TableCell>
                  <TableCell>
                    {event.data.retry_count ?? 'Not recorded'} /{' '}
                    {event.data.latency_ms ??
                      event.data.elapsed_ms ??
                      'Not recorded'}{' '}
                    ms
                  </TableCell>
                  <TableCell className="max-w-64 whitespace-normal">
                    <p>
                      {event.data.status ??
                        event.data.terminal_state ??
                        'Not recorded'}
                    </p>
                    {event.data.error_code && (
                      <p className="text-xs text-amber-700">
                        {event.data.error_code}
                      </p>
                    )}
                    {event.data.ledger_event_id && (
                      <LedgerLink id={event.data.ledger_event_id}>
                        Source
                      </LedgerLink>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <EmptyState
            title="No event trace returned"
            detail="Persisted events are unavailable or this run has not recorded a step."
          />
        )}
      </div>
    </section>
  )
}

export function SustainabilitySection({ companyId }: { companyId: string }) {
  const query = useQuery(sustainabilityOptions(companyId))
  return (
    <section
      aria-label="Company sustainability telemetry"
      className="space-y-4 border-t pt-5"
    >
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-base font-semibold">
          Company sustainability telemetry
        </h2>
        <QueryRefresh query={query} label="sustainability telemetry" />
      </div>
      <p className="text-sm text-muted-foreground">
        Company-wide executions across all dates. Token-based estimates are
        proxies, not measured provider energy or realized emissions savings.
      </p>
      <QueryState query={query}>
        {(data) => (
          <>
            {data.run_count === 0 && (
              <EmptyState
                title="No executions in this scope"
                detail="No company executions contribute to the returned metrics."
              />
            )}
            <RecordedValues
              values={[
                ['Runs', data.run_count],
                ['Completed runs', data.completed_run_count],
                ['Interrupted runs', data.interrupted_run_count],
                ['Proxy coverage (runs)', data.proxy_coverage_runs],
                ['Assumption coverage (runs)', data.assumption_coverage_runs],
                ['Provider calls', data.provider_call_count],
                ['Tool calls', data.tool_call_count],
                ['External API calls', data.external_api_calls],
                ['Input tokens', data.input_tokens],
                ['Output tokens', data.output_tokens],
                ['Cached input tokens', data.cached_input_tokens],
                ['Retries', data.retry_count],
                ['Cache hits', data.cache_hits],
                ['Latency (ms)', data.latency_ms],
                ['Estimated energy (Wh)', data.estimated_energy_wh],
                ['Estimated footprint (gCO2e)', data.estimated_co2e_g],
                [
                  'Approved projected benefit (kgCO2e)',
                  data.business_benefit_kgco2e,
                ],
                ['Benefit / footprint ratio', data.benefit_to_footprint_ratio],
              ]}
            />
            <details open className="border-t pt-4">
              <summary className="cursor-pointer text-sm font-medium">
                Proxy assumptions
              </summary>
              <div className="mt-4 space-y-5">
                {[
                  { title: 'Configured assumptions', item: data.assumptions },
                  ...data.assumption_sets.map((item) => ({
                    title: 'Recorded assumption set',
                    item,
                  })),
                ].map(({ title, item }, index) => (
                  <div key={index} className="space-y-3">
                    <h3 className="text-sm font-medium">{title}</h3>
                    <RecordedValues
                      values={[
                        [
                          'Method / version',
                          `${item.method} / ${item.version}`,
                        ],
                        [
                          'Energy (Wh per 1k tokens)',
                          item.energy_wh_per_1k_tokens,
                        ],
                        [
                          'Grid assumption (gCO2e/kWh)',
                          item.grid_intensity_gco2e_per_kwh,
                        ],
                        ['Functional unit', item.functional_unit],
                        ['Region basis', item.region_basis],
                        ['Omitted footprint', item.omitted_footprint],
                        ['Uncertainty', item.uncertainty],
                        ['Benefit basis', item.benefit_basis],
                        ['Caveat', item.caveat],
                      ]}
                    />
                  </div>
                ))}
              </div>
            </details>
            {data.benefit_facts.length > 0 && (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Approved target</TableHead>
                    <TableHead>Projected benefit (kgCO2e)</TableHead>
                    <TableHead>Source</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.benefit_facts.map((fact) => (
                    <TableRow key={fact.ledger_event_id}>
                      <TableCell>
                        {fact.target_type}
                        <p className="font-mono text-xs">{fact.target_id}</p>
                      </TableCell>
                      <TableCell>{fact.avoided_kgco2e}</TableCell>
                      <TableCell>
                        <LedgerLink id={fact.ledger_event_id}>
                          Ledger fact
                        </LedgerLink>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </>
        )}
      </QueryState>
    </section>
  )
}
