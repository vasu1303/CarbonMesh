import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowRight, Plus, Scale } from 'lucide-react'
import { useState } from 'react'
import { Link, useOutletContext, useParams } from 'react-router-dom'
import { z } from 'zod'
import { EmptyState, QueryRefresh, QueryState } from '@/components/query-state'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useWorkspaceActor } from '@/features/auth/use-workspace-actor'
import {
  ActorField,
  AuditLink,
  CommandError,
  DefinitionList,
  EventLink,
  FactTable,
  RawPayload,
  WorkspaceProvenance,
} from '@/features/approvals/review-components'
import type { WorkspaceScope } from '@/lib/workspace'
import { apiRequest } from '@/services/api'
import { ScenarioAssessments } from './components/scenario-assessments'
import { scenarioKey, scenarioQueries } from './scenario-queries'
import { scoreResultSchema } from './scenario-schemas'

function ScenarioDetail({ id }: { id: string }) {
  const scope = useOutletContext<WorkspaceScope>()
  const actor = useWorkspaceActor()
  const [manualActor, setManualActor] = useState('')
  const actorReady = z
    .uuid()
    .safeParse(actor.actorId || manualActor.trim()).success
  const client = useQueryClient()
  const scenario = useQuery(scenarioQueries.detail(scope, id))
  const selected = scenario.data?.selected_recommendation
  const recommendation = useQuery({
    ...scenarioQueries.recommendation(scope, id, selected?.id),
    enabled: !!selected,
  })
  const score = useMutation({
    mutationFn: () =>
      apiRequest(
        `/procurement/scenarios/${id}/score`,
        scoreResultSchema.refine((v) => v.scenario_id === id),
        {
          signal: new AbortController().signal,
          method: 'POST',
          body: { company_id: scope.company_id },
        },
      ),
    onSettled: async () => {
      await client.invalidateQueries({ queryKey: scenarioKey })
      await client.invalidateQueries({ queryKey: ['approvals'] })
    },
  })
  return (
    <div className="min-w-0 space-y-6 px-5 py-6 sm:px-8">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="mb-2 text-xs text-muted-foreground">Procurement</p>
          <h1 className="text-2xl font-semibold">Scenario review</h1>
          <p className="mt-2 break-all font-mono text-xs text-muted-foreground">
            {id}
          </p>
        </div>
        <Button variant="outline" size="sm" asChild>
          <Link to="/procurement/scenarios/new">
            <Plus />
            New scenario
          </Link>
        </Button>
      </header>
      <WorkspaceProvenance />
      <section aria-label="Scenario requirements" className="space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold">Frozen requirements</h2>
          <QueryRefresh query={scenario} label="scenario" />
        </div>
        <QueryState query={scenario}>
          {(data) => (
            <>
              <div className="flex flex-wrap gap-2">
                <Badge variant="outline">{data.status}</Badge>
                <Badge variant="outline">{data.terminal_state}</Badge>
              </div>
              {(data.status === 'stale' || data.status === 'invalidated') && (
                <p role="alert" className="text-sm text-amber-700">
                  This scenario is stale. Create a new scenario to review
                  changed inputs.
                </p>
              )}
              {data.terminal_state === 'no_feasible_option' && (
                <EmptyState
                  title="No feasible option"
                  detail="No candidate satisfies the frozen hard constraints."
                />
              )}
              <DefinitionList
                items={[
                  [
                    'Current product',
                    <AuditLink
                      key="current-product"
                      type="supplier_product"
                      id={data.current_product.id}
                    >
                      {data.current_product.name}
                    </AuditLink>,
                  ],
                  ['Quantity', `${data.quantity} ${data.quantity_unit}`],
                  [
                    'Current unit cost',
                    `${data.currency} ${data.current_unit_cost}`,
                  ],
                  [
                    'Maximum cost increase',
                    `${data.constraints.max_cost_increase_pct}%`,
                  ],
                  [
                    'Maximum lead time',
                    `${data.constraints.max_lead_time_days} days`,
                  ],
                  [
                    'Minimum circularity',
                    data.constraints.minimum_circularity_score,
                  ],
                  [
                    'Allowed materials',
                    data.constraints.material.allowed_material_codes.join(
                      ', ',
                    ) || 'Unrestricted',
                  ],
                  [
                    'Excluded risk levels',
                    data.constraints.material.excluded_risk_levels.join(', ') ||
                      'None',
                  ],
                  [
                    'Measurement',
                    <Link
                      key="measurement"
                      className="text-emerald-700 underline"
                      to={`/measurement/${data.carbon_measurement_id}`}
                    >
                      {data.carbon_measurement_id}
                    </Link>,
                  ],
                  [
                    'Scoring method',
                    `${data.method.key} / ${data.method.version}`,
                  ],
                  ['Code version', data.method.code_version],
                  ['Analysis signature', data.analysis_signature],
                ]}
              />
              <div className="border-t pt-4">
                <h3 className="mb-3 text-xs font-medium">
                  Recorded scoring weights
                </h3>
                <DefinitionList
                  items={Object.entries(data.weights).map(([key, value]) => [
                    key.replaceAll('_', ' '),
                    value,
                  ])}
                />
              </div>
              <form
                className="space-y-3 border-t pt-4"
                onSubmit={(e) => {
                  e.preventDefault()
                  if (actorReady && !score.isPending && !scenario.isError)
                    score.mutate()
                }}
              >
                <div className="max-w-md">
                  <ActorField
                    {...actor}
                    value={manualActor}
                    onChange={setManualActor}
                  />
                </div>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={
                    score.isPending ||
                    scenario.isFetching ||
                    scenario.isError ||
                    !actorReady ||
                    ['stale', 'invalidated', 'approved', 'rejected'].includes(
                      data.status,
                    )
                  }
                >
                  <Scale />
                  {score.isPending ? 'Scoring...' : 'Score frozen scenario'}
                </Button>
                <CommandError error={score.error} />
              </form>
              <RawPayload value={data.frozen_context} />
            </>
          )}
        </QueryState>
      </section>
      <section
        aria-label="Candidate assessments"
        className="space-y-4 border-t pt-5"
      >
        <h2 className="text-sm font-semibold">Candidate assessments</h2>
        <QueryState query={scenario}>
          {(data) => <ScenarioAssessments items={data.alternatives} />}
        </QueryState>
      </section>
      <section
        aria-label="Procurement recommendation"
        className="space-y-4 border-t pt-5"
      >
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold">Recommendation</h2>
          {selected && (
            <QueryRefresh query={recommendation} label="recommendation" />
          )}
        </div>
        {!selected ? (
          <QueryState query={scenario}>
            {() => (
              <EmptyState
                title="No recommendation"
                detail="No recommendation is linked to this scenario."
              />
            )}
          </QueryState>
        ) : (
          <QueryState query={recommendation}>
            {(data) => (
              <>
                <div className="flex flex-wrap items-center gap-3">
                  <h3 className="text-base font-medium wrap-anywhere">
                    {data.recommended_product.name}
                  </h3>
                  <Badge variant="outline">{data.status}</Badge>
                </div>
                {(data.invalidated_at ||
                  data.status === 'stale' ||
                  data.status === 'invalidated') && (
                  <p role="alert" className="text-sm text-amber-700">
                    Recommendation invalidated. The recorded facts are retained
                    for audit.
                  </p>
                )}
                <DefinitionList
                  items={[
                    ['Supplier', data.recommended_product.supplier_name],
                    [
                      'Projected emissions',
                      `${data.projected_footprint_kgco2e} kgCO2e`,
                    ],
                    ['Avoided emissions', `${data.avoided_kgco2e} kgCO2e`],
                    ['Reduction', `${data.reduction_pct}%`],
                    ['Cost change', `${data.cost_delta_pct}%`],
                    ['Lead time change', `${data.lead_time_delta_days} days`],
                    ['Payload hash', data.payload_hash],
                    ['Analysis signature', data.analysis_signature],
                    [
                      'Ledger source',
                      data.ledger_event_id ? (
                        <EventLink
                          key="ledger-source"
                          id={data.ledger_event_id}
                        />
                      ) : (
                        'Not linked'
                      ),
                    ],
                  ]}
                />
                <div className="space-y-3 border-t pt-4">
                  <h3 className="text-sm font-semibold">
                    Bound recommendation narrative
                  </h3>
                  <p className="whitespace-pre-wrap text-sm">
                    {data.narrative.resolved_text}
                  </p>
                  {data.narrative.unsupported_fragments.length > 0 && (
                    <div role="alert" className="text-sm text-amber-700">
                      <p>Unsupported fragments</p>
                      <ul>
                        {data.narrative.unsupported_fragments.map((v, i) => (
                          <li key={i}>{v}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                  <FactTable facts={data.narrative.fact_bindings} />
                </div>
                <div className="space-y-3 border-t pt-4">
                  <h3 className="text-sm font-semibold">Evidence</h3>
                  {data.evidence.map((item) => (
                    <div
                      key={item.id}
                      className="space-y-1 border-b pb-3 text-sm"
                    >
                      <AuditLink type="evidence_item" id={item.id}>
                        {item.evidence_type}
                      </AuditLink>
                      <p className="wrap-anywhere">{item.locator}</p>
                      <p className="break-all font-mono text-xs">
                        {item.checksum}
                      </p>
                    </div>
                  ))}
                  {!data.evidence.length && (
                    <p className="text-sm text-muted-foreground">
                      No evidence linked.
                    </p>
                  )}
                </div>
                {data.approval ? (
                  <div className="space-y-3 border-t pt-4">
                    <DefinitionList
                      items={[
                        ['Approval status', data.approval.status],
                        ['Expires', data.approval.expires_at],
                        ['Preview hash', data.approval.preview_hash],
                      ]}
                    />
                    <Button asChild variant="outline" size="sm">
                      <Link to={`/approvals?approval=${data.approval.id}`}>
                        Review exact approval
                        <ArrowRight />
                      </Link>
                    </Button>
                  </div>
                ) : (
                  <p className="text-sm text-muted-foreground">
                    No approval preview is available.
                  </p>
                )}
              </>
            )}
          </QueryState>
        )}
      </section>
    </div>
  )
}
export default function ScenarioPage() {
  const { id } = useParams()
  const scope = useOutletContext<WorkspaceScope>()
  if (!id || !z.uuid().safeParse(id).success)
    return (
      <section className="px-5 py-6 sm:px-8">
        <EmptyState
          title="Invalid scenario UUID"
          detail="Open a scenario using its recorded identifier."
        />
        <Button asChild variant="outline">
          <Link to="/procurement/scenarios/new">Open or create a scenario</Link>
        </Button>
      </section>
    )
  return (
    <ScenarioDetail
      key={`${scope.company_id}:${scope.site_id}:${scope.reporting_period_id}:${id}`}
      id={id}
    />
  )
}
