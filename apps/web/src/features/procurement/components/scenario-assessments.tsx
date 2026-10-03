import { Badge } from '@/components/ui/badge'
import { AuditLink } from '@/features/approvals/review-components'
import type { Assessment } from '../scenario-schemas'

export function ScenarioAssessments({ items }: { items: Assessment[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead className="border-b text-xs text-muted-foreground">
          <tr>
            {[
              'Product / supplier',
              'Feasibility',
              'Rank',
              'Carbon',
              'Evidence',
              'Circularity',
              'Operational fit',
              'Total',
              'Projected kgCO2e',
              'Cost change',
              'Lead time',
            ].map((v) => (
              <th key={v} className="whitespace-nowrap p-2 font-medium">
                {v}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {items.map((v) => (
            <tr key={v.score_id} className="border-b align-top">
              <td className="min-w-44 max-w-64 p-2 wrap-anywhere">
                <AuditLink type="supplier_product" id={v.product.id}>
                  {v.product.name}
                </AuditLink>
                <div className="mt-1 text-xs text-muted-foreground">
                  {v.product.supplier_name}
                </div>
              </td>
              <td className="min-w-48 max-w-72 p-2">
                <Badge variant="outline">
                  {v.feasible ? 'Feasible' : 'Infeasible'}
                </Badge>
                <ul className="mt-2 space-y-2 text-xs">
                  {v.infeasibility_reasons.map((r, i) => (
                    <li key={`${r.code}-${i}`} className="wrap-anywhere">
                      {r.message}
                      {r.actual !== null && r.actual !== undefined && (
                        <div>Actual: {String(r.actual)}</div>
                      )}
                      {r.required !== null && r.required !== undefined && (
                        <div>Required: {String(r.required)}</div>
                      )}
                    </li>
                  ))}
                </ul>
              </td>
              <td className="p-2 font-mono">{v.rank ?? 'Unranked'}</td>
              {[
                v.scores.carbon,
                v.scores.evidence,
                v.scores.circularity,
                v.scores.operational_fit,
                v.scores.total,
                v.impact.projected_footprint_kgco2e,
              ].map((value, i) => (
                <td className="p-2 font-mono tabular-nums" key={i}>
                  {value}
                </td>
              ))}
              <td className="p-2 font-mono">{v.impact.cost_delta_pct}%</td>
              <td className="p-2 font-mono">{v.product.lead_time_days} days</td>
            </tr>
          ))}
        </tbody>
      </table>
      {!items.length && (
        <p className="py-5 text-sm text-muted-foreground">
          No candidate assessments recorded.
        </p>
      )}
    </div>
  )
}
