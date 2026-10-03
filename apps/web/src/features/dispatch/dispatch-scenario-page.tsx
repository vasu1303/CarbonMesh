import { zodResolver } from '@hookform/resolvers/zod'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, ChartNoAxesCombined } from 'lucide-react'
import { useEffect } from 'react'
import { Controller, useForm } from 'react-hook-form'
import { Link, useOutletContext, useParams } from 'react-router-dom'
import { z } from 'zod'
import { EmptyState, QueryRefresh, QueryState } from '@/components/query-state'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { useWorkspaceActor } from '@/features/auth/use-workspace-actor'
import {
  CommandError,
  FieldError,
  HashValue,
  LedgerLink,
  StateBadge,
  WorkflowContext,
} from '@/features/assurance/workflow-ui'
import type { WorkspaceScope } from '@/lib/workspace'
import { ApiError, apiRequest } from '@/services/api'
import { ForecastView, FrozenConstraints } from './dispatch-views'
import { dispatchQueries, scenarioInScope, scenarioKey } from './queries'
import {
  optimizationSchema,
  type Recommendation,
  type Scenario,
} from './schemas'

function RecommendationFacts({ value }: { value: Recommendation }) {
  const facts = [
    ['Baseline emissions', value.baseline_emissions_kgco2e, 'kgCO2e'],
    ['Recommended emissions', value.expected_emissions_kgco2e, 'kgCO2e'],
    ['Avoided emissions', value.avoided_kgco2e, 'kgCO2e'],
    ['Reduction', value.reduction_pct, '%'],
  ]
  return (
    <section
      aria-label="Recommendation facts"
      className="space-y-5 border-y py-5"
    >
      <h2 className="text-base font-semibold">Recommendation facts</h2>
      <dl className="grid gap-5 sm:grid-cols-2 xl:grid-cols-4">
        {facts.map(([name, amount, unit]) => (
          <div key={name}>
            <dt className="mb-2 text-xs text-muted-foreground">{name}</dt>
            <dd className="break-words font-mono text-lg">
              <LedgerLink id={value.ledger_event_id}>
                {amount} {unit}
              </LedgerLink>
            </dd>
          </div>
        ))}
      </dl>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Window</TableHead>
            <TableHead>Start</TableHead>
            <TableHead>Finish</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          <TableRow>
            <TableCell>Baseline</TableCell>
            <TableCell>{value.baseline_start}</TableCell>
            <TableCell>{value.baseline_end}</TableCell>
          </TableRow>
          <TableRow>
            <TableCell>Recommended</TableCell>
            <TableCell>{value.recommended_start}</TableCell>
            <TableCell>{value.recommended_end}</TableCell>
          </TableRow>
        </TableBody>
      </Table>
      <div className="space-y-2">
        <h3 className="text-sm font-medium">Recorded rationale template</h3>
        <p className="whitespace-pre-wrap break-words text-sm text-muted-foreground">
          {value.rationale}
        </p>
      </div>
      <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-3">
          <h3 className="text-sm font-semibold">Approval preview</h3>
          <StateBadge state={value.approval.status} />
          <Link
            className="text-sm text-emerald-700 underline"
            to={`/approvals?approval=${value.approval.id}`}
          >
            Review approval
          </Link>
        </div>
        <p className="text-sm">Expires: {value.approval.expires_at}</p>
        <dl className="grid gap-4 sm:grid-cols-2">
          <HashValue label="Preview hash" value={value.approval.preview_hash} />
          <HashValue
            label="Analysis signature"
            value={value.analysis_signature}
          />
          <HashValue label="Payload hash" value={value.payload_hash} />
          <HashValue label="Context hash" value={value.approval.context_hash} />
        </dl>
      </div>
      <details>
        <summary className="cursor-pointer text-sm font-medium">
          Calculation and evidence identity
        </summary>
        <Link
          className="mt-2 block text-sm text-emerald-700 underline"
          to={`/data?document=${value.approval.preview_payload.forecast_source_document_id}`}
        >
          Forecast source document
        </Link>
        <dl className="mt-3 grid gap-3 sm:grid-cols-2">
          <HashValue
            label="Input hash"
            value={value.impact_snapshot.input_hash}
          />
          <HashValue
            label="Output hash"
            value={value.impact_snapshot.output_hash}
          />
          <HashValue
            label="Forecast source document"
            value={value.approval.preview_payload.forecast_source_document_id}
          />
          <HashValue
            label="Forecast checksum"
            value={
              value.approval.preview_payload.forecast_source_document_checksum
            }
          />
          {value.evidence_item_ids.map((id) => (
            <HashValue key={id} label="Evidence item" value={id} />
          ))}
        </dl>
      </details>
    </section>
  )
}

const optimizeFormSchema = z.object({
  actor_id: z.uuid(),
  acknowledged: z.boolean().refine(Boolean, 'Acknowledge the advisory scope.'),
})
function ScenarioWorkspace({
  scope,
  id,
}: {
  scope: WorkspaceScope
  id: string
}) {
  const client = useQueryClient()
  const actor = useWorkspaceActor()
  const query = useQuery(dispatchQueries.recommendation(scope, id))
  // Creation returns a scenario; the mounted API only exposes recommendations for subsequent reads.
  const cachedScenario = client.getQueryData<Scenario>(
    scenarioKey(scope.company_id, id),
  )
  const scenario =
    cachedScenario && scenarioInScope(cachedScenario, scope)
      ? cachedScenario
      : undefined
  const form = useForm<z.infer<typeof optimizeFormSchema>>({
    resolver: zodResolver(optimizeFormSchema),
    defaultValues: { actor_id: actor.actorId, acknowledged: false },
  })
  useEffect(() => {
    if (actor.actorId) form.setValue('actor_id', actor.actorId)
  }, [actor.actorId, form])
  const optimize = useMutation({
    mutationFn: () =>
      apiRequest(
        `/dispatch/scenarios/${id}/optimize`,
        optimizationSchema.refine(
          (result) =>
            result.scenario_id === id &&
            (!result.recommendation ||
              (result.recommendation.scenario_id === id &&
                result.recommendation.company_id === scope.company_id)),
        ),
        {
          signal: new AbortController().signal,
          method: 'POST',
          body: { company_id: scope.company_id },
        },
      ),
    retry: false,
    onSuccess: (result) =>
      client.setQueryData(
        dispatchQueries.recommendation(scope, id).queryKey,
        result,
      ),
  })
  const recommendation = query.data?.recommendation
  const constraints =
    recommendation?.approval.preview_payload.frozen_constraints ??
    scenario?.constraints
  const points =
    recommendation?.approval.preview_payload.forecast_points ??
    scenario?.forecast_snapshot
  const counts = optimize.data ?? recommendation?.impact_snapshot
  const stale =
    query.data?.terminal_state === 'stale' ||
    (optimize.error instanceof ApiError &&
      (optimize.error.terminalState === 'stale' ||
        optimize.error.code.includes('stale')))
  return (
    <main className="min-w-0 space-y-6 px-5 py-6 sm:px-8">
      <div className="flex items-center justify-between">
        <Link to="/dispatch" className="flex items-center gap-2 text-sm">
          <ArrowLeft className="size-4" />
          Dispatch
        </Link>
        <QueryRefresh query={query} label="recommendation" />
      </div>
      <header className="space-y-2">
        <h1 className="text-2xl font-semibold">Advisory scenario</h1>
        <p className="break-all font-mono text-xs text-muted-foreground">
          {id}
        </p>
        <p className="text-sm text-muted-foreground">
          Advisory only. Approval cannot actuate equipment.
        </p>
      </header>
      <WorkflowContext scope={scope} />
      <section
        aria-label="Dispatch recommendation"
        className="min-w-0 space-y-4"
      >
        <QueryState query={query}>
          {(result) => (
            <>
              <StateBadge state={result.terminal_state} />
              {result.terminal_state === 'stale' && (
                <p
                  role="alert"
                  className="border-l-2 border-amber-600 pl-3 text-sm"
                >
                  Stale recommendation. Recorded windows and facts are
                  historical; the preview is no longer current.
                </p>
              )}
              {result.terminal_state === 'no_feasible_option' && (
                <EmptyState
                  title="No feasible operating window"
                  detail="No complete forecast window satisfies the frozen hard constraints."
                />
              )}
              {result.recommendation && (
                <RecommendationFacts value={result.recommendation} />
              )}
            </>
          )}
        </QueryState>
      </section>
      {scenario && !recommendation && (
        <p className="text-sm">
          Scenario status: <StateBadge state={scenario.status} />
        </p>
      )}
      <section
        aria-label="Optimize advisory scenario"
        className="space-y-4 border-y py-5"
      >
        <h2 className="text-base font-semibold">Optimize advisory window</h2>
        <form
          aria-label="Optimize advisory scenario"
          onSubmit={form.handleSubmit(() => optimize.mutate())}
          className="space-y-4"
        >
          <div className="max-w-md">
            <label
              htmlFor="optimize-actor"
              className="mb-2 block text-xs font-medium"
            >
              Workspace actor UUID
            </label>
            <Input
              id="optimize-actor"
              required
              readOnly={!!actor.actorId}
              {...form.register('actor_id')}
            />
            <FieldError message={form.formState.errors.actor_id?.message} />
          </div>
          <Label
            htmlFor="dispatch-advisory-acknowledged"
            className="flex items-start gap-2 text-sm leading-relaxed font-normal"
          >
            <Controller
              control={form.control}
              name="acknowledged"
              render={({ field }) => (
                <Checkbox
                  id="dispatch-advisory-acknowledged"
                  name={field.name}
                  ref={field.ref}
                  checked={field.value}
                  onBlur={field.onBlur}
                  onCheckedChange={(checked) =>
                    field.onChange(checked === true)
                  }
                  disabled={optimize.isPending || stale}
                  aria-invalid={!!form.formState.errors.acknowledged}
                  className="mt-0.5"
                />
              )}
            />
            Advisory recommendation and approval preview only; retain the frozen
            constraints.
          </Label>
          <FieldError message={form.formState.errors.acknowledged?.message} />
          <CommandError error={optimize.error} />
          <Button
            type="submit"
            disabled={optimize.isPending || stale || query.isFetching}
          >
            <ChartNoAxesCombined />
            {optimize.isPending ? 'Optimizing...' : 'Optimize scenario'}
          </Button>
        </form>
      </section>
      {counts && (
        <section aria-label="Window feasibility" className="min-w-0 space-y-3">
          <h2 className="text-base font-semibold">Window feasibility</h2>
          <p className="text-sm">
            Evaluated: {counts.evaluated_windows} / Feasible:{' '}
            {counts.feasible_windows}
          </p>
          {counts.rejected_windows.length > 0 && (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Start</TableHead>
                  <TableHead>Finish</TableHead>
                  <TableHead>Rejection reasons</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {counts.rejected_windows.map((window, i) => (
                  <TableRow key={`${window.start}-${i}`}>
                    <TableCell>{window.start}</TableCell>
                    <TableCell>{window.end}</TableCell>
                    <TableCell className="whitespace-normal">
                      <ul>
                        {window.reasons.map((reason) => (
                          <li key={reason}>{reason.replaceAll('_', ' ')}</li>
                        ))}
                      </ul>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </section>
      )}
      {constraints && <FrozenConstraints value={constraints} />}
      {points && (
        <ForecastView points={points} recommendation={recommendation} />
      )}
      {!constraints && (
        <p className="text-sm text-muted-foreground">
          No frozen scenario details are available from this recommendation
          read.
        </p>
      )}
    </main>
  )
}
export default function DispatchScenarioPage() {
  const scope = useOutletContext<WorkspaceScope>()
  const { scenarioId } = useParams()
  if (!z.uuid().safeParse(scenarioId).success)
    return (
      <main className="px-5 py-6 sm:px-8">
        <h1 className="text-xl font-semibold">Invalid scenario identifier</h1>
        <Link to="/dispatch" className="text-sm underline">
          Return to Dispatch
        </Link>
      </main>
    )
  return (
    <ScenarioWorkspace
      key={`${scope.company_id}:${scope.site_id}:${scenarioId}`}
      scope={scope}
      id={scenarioId!}
    />
  )
}
