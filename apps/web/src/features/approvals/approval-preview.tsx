import { Link } from 'react-router-dom'
import { Badge } from '@/components/ui/badge'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { displayText, humanize } from '@/lib/presentation'
import { formatDate } from '@/lib/format'
import {
  AuditLink,
  DefinitionList,
  EventLink,
  FactTable,
  RawPayload,
  RecordFields,
} from './review-components'
import {
  disclosurePreviewSchema,
  dispatchPreviewSchema,
  procurementPreviewSchema,
  type ApprovalDetail,
} from './schemas'

export function ApprovalPreview({ approval }: { approval: ApprovalDetail }) {
  const payload = approval.preview_payload
  if (approval.target_type === 'procurement_recommendation') {
    const parsed = procurementPreviewSchema.safeParse(payload)
    if (!parsed.success) return <Unsupported payload={payload} />
    const data = parsed.data
    const review = data.impact.review
    return (
      <div className="space-y-5">
        <div>
          <h3 className="text-base font-medium">
            {displayText(review.recommended_product.name)}
          </h3>
          <p className="text-sm text-muted-foreground">
            {displayText(review.recommended_product.supplier_name)}
          </p>
        </div>
        <DefinitionList
          items={[
            ['Baseline product', review.baseline_product.name],
            [
              'Quantity',
              `${data.impact.quantity} ${data.impact.quantity_unit}`,
            ],
            [
              'Baseline emissions',
              `${data.impact.baseline_footprint_kgco2e} kgCO2e`,
            ],
            [
              'Projected emissions',
              `${data.projected_footprint_kgco2e} kgCO2e`,
            ],
            ['Avoided emissions', `${data.avoided_kgco2e} kgCO2e`],
            ['Reduction', `${data.reduction_pct}%`],
            ['Cost change', `${data.cost_delta_pct}%`],
            ['Lead time change', `${data.lead_time_delta_days} days`],
            [
              'Method',
              `${data.impact.score.method_key} / ${data.impact.score.method_version}`,
            ],
            [
              'Source integrity',
              data.impact.source_state_hash ? 'Recorded' : 'Not recorded',
            ],
            [
              'Scenario',
              <Link
                key="scenario"
                className="text-emerald-700 underline"
                to={`/procurement/scenarios/${data.scenario_id}`}
              >
                Open procurement scenario
              </Link>,
            ],
          ]}
        />
        <section aria-label="Frozen supplier score">
          <h3 className="mb-3 text-sm font-semibold">Frozen supplier score</h3>
          <DefinitionList
            items={[
              [
                'Feasibility',
                review.supplier_score.feasible
                  ? 'Meets hard constraints'
                  : 'Does not meet hard constraints',
              ],
              ['Rank', review.supplier_score.rank ?? 'Unranked'],
              ['Carbon score', review.supplier_score.scores.carbon],
              ['Evidence score', review.supplier_score.scores.evidence],
              ['Circularity score', review.supplier_score.scores.circularity],
              [
                'Operational fit score',
                review.supplier_score.scores.operational_fit,
              ],
              ['Total score', review.supplier_score.scores.total],
            ]}
          />
          {review.supplier_score.infeasibility_reasons.map((reason, index) => (
            <p key={index} className="mt-2 text-sm text-amber-700">
              {displayText(reason.message)}
            </p>
          ))}
        </section>
        <section>
          <h3 className="mb-3 text-sm font-semibold">Bound facts</h3>
          <FactTable facts={review.fact_bindings} />
        </section>
        <section>
          <h3 className="mb-3 text-sm font-semibold">Frozen evidence</h3>
          {review.evidence.map((e) => (
            <div key={e.id} className="space-y-2 border-b py-3 text-sm">
              <AuditLink type="evidence_item" id={e.id}>
                {humanize(e.evidence_type)}
              </AuditLink>
              <p className="wrap-anywhere">{displayText(e.locator)}</p>
              <p className="text-xs text-muted-foreground">
                Source integrity {e.checksum ? 'recorded' : 'not recorded'}
              </p>
              <RecordFields value={e.metadata} />
            </div>
          ))}
          {!review.evidence.length && (
            <p className="text-sm text-muted-foreground">
              No supporting evidence recorded.
            </p>
          )}
        </section>
        <details className="border-t pt-3">
          <summary className="cursor-pointer text-sm font-medium">
            Frozen commercial terms and impact
          </summary>
          <RecordFields value={payload.impact} />
        </details>
        <RawPayload value={payload} />
      </div>
    )
  }
  if (approval.target_type === 'disclosure_draft') {
    const parsed = disclosurePreviewSchema.safeParse(payload)
    if (!parsed.success) return <Unsupported payload={payload} />
    const data = parsed.data
    return (
      <div className="space-y-5">
        <h3 className="text-base font-medium wrap-anywhere">
          {displayText(data.title)}
        </h3>
        <p className="text-sm text-amber-700">{displayText(data.disclaimer)}</p>
        <DefinitionList
          items={[
            ['Standard', `${data.standard.code} / ${data.standard.version}`],
            ['Draft version', data.draft_version],
            ['Validation method', data.validation_method],
            ['Retrieval method', data.retrieval_method],
            [
              'Measurement',
              <Link
                key="measurement"
                className="text-emerald-700 underline"
                to={`/measurement/${data.measurement_id}`}
              >
                Open verified measurement
              </Link>,
            ],
            [
              'Disclosure',
              <Link
                key="disclosure"
                className="text-emerald-700 underline"
                to={`/assurance/${data.target_id}`}
              >
                Open disclosure draft
              </Link>,
            ],
          ]}
        />
        <section aria-label="Disclosure claims">
          <h3 className="mb-3 text-sm font-semibold">Atomic claims</h3>
          {data.claims.map((claim) => (
            <article key={claim.id} className="space-y-3 border-b py-4">
              <div className="flex flex-wrap items-center gap-3">
                <span className="text-sm font-medium">
                  {humanize(claim.requirement_code || claim.claim_type)}
                </span>
                <Badge variant="outline">
                  {humanize(claim.support_status)}
                </Badge>
                <span className="text-xs">Confidence {claim.confidence}</span>
              </div>
              <p className="whitespace-pre-wrap text-sm">
                {claim.rendered_text
                  ? displayText(claim.rendered_text)
                  : 'No supported claim text recorded.'}
              </p>
              {claim.ledger_event_id && (
                <EventLink id={claim.ledger_event_id}>
                  Claim ledger source
                </EventLink>
              )}
              <ul className="space-y-3 text-sm">
                {claim.citations.map((c) => (
                  <li key={c.id} className="space-y-1">
                    <p>
                      {humanize(c.validation_status)} /{' '}
                      {displayText(c.locator || 'No source location recorded')}
                    </p>
                    {c.ledger_event_id && (
                      <EventLink id={c.ledger_event_id}>
                        Citation ledger event
                      </EventLink>
                    )}
                    {c.evidence_item_id && (
                      <div>
                        <AuditLink type="evidence_item" id={c.evidence_item_id}>
                          Citation evidence
                        </AuditLink>
                      </div>
                    )}
                    {c.evidence && (
                      <details>
                        <summary className="cursor-pointer text-xs">
                          Evidence provenance
                        </summary>
                        <RecordFields value={c.evidence} />
                      </details>
                    )}
                  </li>
                ))}
              </ul>
            </article>
          ))}
        </section>
        <section>
          <h3 className="mb-3 text-sm font-semibold">Evidence gaps</h3>
          {data.gaps.map((g) => (
            <div key={g.id} className="border-b py-3 text-sm">
              <p className="font-medium">
                {humanize(g.code)} / {humanize(g.severity)} /{' '}
                {humanize(g.status)}
              </p>
              <p>{displayText(g.message)}</p>
            </div>
          ))}
          {!data.gaps.length && (
            <p className="text-sm text-muted-foreground">No gaps recorded.</p>
          )}
        </section>
        <section>
          <h3 className="mb-3 text-sm font-semibold">Bound facts</h3>
          <FactTable facts={data.fact_bindings} />
        </section>
        <details className="border-t pt-3">
          <summary className="cursor-pointer text-sm font-medium">
            Frozen disclosure text
          </summary>
          <p className="mt-3 whitespace-pre-wrap text-sm">
            {displayText(data.rendered_text)}
          </p>
        </details>
        <RawPayload value={payload} />
      </div>
    )
  }
  if (approval.target_type === 'dispatch_recommendation') {
    const parsed = dispatchPreviewSchema.safeParse(payload)
    if (!parsed.success) return <Unsupported payload={payload} />
    const data = parsed.data
    return (
      <div className="space-y-5">
        <Badge variant="outline">Advisory only</Badge>
        <DefinitionList
          items={[
            ['Baseline start', data.baseline_start],
            ['Baseline end', data.baseline_end],
            ['Baseline emissions', `${data.baseline_emissions_kgco2e} kgCO2e`],
            ['Recommended start', data.recommended_start],
            ['Recommended end', data.recommended_end],
            ['Expected emissions', `${data.expected_emissions_kgco2e} kgCO2e`],
            ['Avoided emissions', `${data.avoided_kgco2e} kgCO2e`],
            ['Reduction', `${data.reduction_pct}%`],
            ['Method version', data.method_version],
            ['Code version', data.method_code_version],
            ['Input integrity', data.input_hash ? 'Recorded' : 'Not recorded'],
            [
              'Result integrity',
              data.output_hash ? 'Recorded' : 'Not recorded',
            ],
            [
              'Forecast integrity',
              data.forecast_source_document_checksum
                ? 'Recorded'
                : 'Not recorded',
            ],
            [
              'Scenario',
              <Link
                key="scenario"
                className="text-emerald-700 underline"
                to={`/dispatch/${data.scenario_id}`}
              >
                Open dispatch scenario
              </Link>,
            ],
          ]}
        />
        <section>
          <h3 className="text-sm font-semibold">Load and hard constraints</h3>
          <RecordFields value={data.load_snapshot} />
          <RecordFields value={data.constraint_snapshot} />
        </section>
        <section aria-label="Frozen forecast points">
          <h3 className="mb-3 text-sm font-semibold">Frozen forecast points</h3>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Forecast time</TableHead>
                <TableHead>gCO2e/kWh</TableHead>
                <TableHead>Evidence</TableHead>
                <TableHead>Ledger</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.forecast_points.map((p) => (
                <TableRow key={p.id}>
                  <TableCell>{formatDate(p.forecast_for)}</TableCell>
                  <TableCell className="font-mono">
                    {p.intensity_gco2e_per_kwh}
                  </TableCell>
                  <TableCell>
                    <AuditLink type="evidence_item" id={p.evidence_item_id}>
                      Evidence
                    </AuditLink>
                  </TableCell>
                  <TableCell>
                    <EventLink id={p.forecast_ledger_event_id}>
                      Source event
                    </EventLink>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </section>
        <details className="border-t pt-3">
          <summary className="cursor-pointer text-sm font-medium">
            Methods and source details
          </summary>
          <RecordFields
            value={{
              method: data.method_snapshot,
              policy: data.policy_snapshot,
              constraints: data.frozen_constraints,
              forecast_points: data.forecast_points,
            }}
          />
        </details>
        <RawPayload value={payload} />
      </div>
    )
  }
  return <Unsupported payload={payload} />
}
function Unsupported({ payload }: { payload: unknown }) {
  return (
    <div className="space-y-4">
      <p role="alert" className="text-sm text-amber-700">
        Unsupported or incomplete preview. Decisions are blocked.
      </p>
      <RawPayload value={payload} />
    </div>
  )
}
