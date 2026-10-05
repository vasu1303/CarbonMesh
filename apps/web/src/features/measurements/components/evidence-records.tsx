import { useState } from 'react'
import { ArrowUpRight } from 'lucide-react'
import { Link } from 'react-router-dom'
import { EmptyState, PageControls } from '@/components/query-state'
import { Button } from '@/components/ui/button'
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { formatDate, formatDecimal } from '@/lib/format'
import { displayText, humanize, recordLabel } from '@/lib/presentation'
import type { MeasurementDetail } from '../schemas'

const pageSize = 20

export function EvidenceRecords({ item }: { item: MeasurementDetail }) {
  const [kind, setKind] = useState('activities')
  const [offset, setOffset] = useState(0)
  const total =
    kind === 'activities'
      ? item.inputs.length
      : kind === 'factors'
        ? item.factors.length
        : item.grid_points.length
  const sources =
    kind === 'factors'
      ? item.factors.slice(offset, offset + pageSize).map((factor) => ({
          id: factor.id,
          label: recordLabel('emission factor', factor.name),
          value: `${formatDecimal(factor.factor_value)} ${factor.numerator_unit}/${factor.denominator_unit}`,
          detail: `${displayText(factor.geography)} / ${factor.effective_from} to ${factor.effective_to ?? 'open-ended'} / Version ${displayText(factor.version)}`,
          evidence: factor.evidence,
        }))
      : item.grid_points.slice(offset, offset + pageSize).map((point) => ({
          id: point.id,
          label: `${displayText(point.provider)} / ${displayText(point.zone)}`,
          value: `${formatDecimal(point.intensity_gco2e_per_kwh)} gCO2e/kWh`,
          detail: `${formatDate(point.observed_at)}${point.is_estimated ? ' / Estimated' : ''}`,
          evidence: point.evidence,
        }))
  return (
    <div className="min-w-0 space-y-4">
      <div className="space-y-2">
        <label htmlFor="evidence-kind" className="text-xs font-medium">
          Source records
        </label>
        <NativeSelect
          id="evidence-kind"
          value={kind}
          onChange={(event) => {
            setKind(event.target.value)
            setOffset(0)
          }}
        >
          <NativeSelectOption value="activities">
            Activity rows
          </NativeSelectOption>
          <NativeSelectOption value="factors">
            Emission factors
          </NativeSelectOption>
          <NativeSelectOption value="grid">
            Hourly grid points
          </NativeSelectOption>
        </NativeSelect>
      </div>
      {total === 0 ? (
        <EmptyState
          title="No source records"
          detail="No records of this source type were returned for this measurement."
        />
      ) : kind === 'activities' ? (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Activity</TableHead>
              <TableHead>Quantity</TableHead>
              <TableHead>Emissions / kgCO2e</TableHead>
              <TableHead>Source</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {item.inputs.slice(offset, offset + pageSize).map((input) => {
              const calculations = item.calculations.filter(
                (calculation) =>
                  calculation.activity_record_id === input.activity_record_id,
              )
              return (
                <TableRow key={input.activity_record_id}>
                  <TableCell className="max-w-72 whitespace-normal wrap-anywhere">
                    <p>{humanize(input.material_code)}</p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {input.interval_start
                        ? formatDate(input.interval_start)
                        : (input.activity_date ?? 'No date recorded')}
                    </p>
                    {input.source_row_number !== null && (
                      <p className="mt-1 text-xs text-muted-foreground">
                        Source row {input.source_row_number}
                      </p>
                    )}
                  </TableCell>
                  <TableCell className="tabular-nums">
                    {formatDecimal(input.source_quantity)}{' '}
                    {displayText(input.source_unit)}
                  </TableCell>
                  <TableCell className="tabular-nums">
                    {calculations.length
                      ? calculations.map((calculation) => (
                          <p key={calculation.id}>
                            {formatDecimal(calculation.emissions_kgco2e)}
                          </p>
                        ))
                      : 'Not calculated'}
                  </TableCell>
                  <TableCell>
                    <Button asChild variant="link" size="sm">
                      <Link to={`/data?document=${input.source_document_id}`}>
                        Source <ArrowUpRight />
                      </Link>
                    </Button>
                  </TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      ) : (
        <ul className="divide-y">
          {sources.map((source) => (
            <li key={source.id} className="space-y-2 py-4 wrap-anywhere">
              <p className="font-medium">{source.label}</p>
              <p className="text-sm tabular-nums">
                {displayText(source.value)}
              </p>
              <p className="text-xs text-muted-foreground">{source.detail}</p>
              <p className="text-xs">{displayText(source.evidence.locator)}</p>
              <div className="flex flex-wrap gap-4">
                <Button
                  asChild
                  variant="link"
                  className="h-auto max-w-full p-0 text-left text-xs whitespace-normal"
                >
                  <Link
                    to={`/data?document=${source.evidence.source_document_id}`}
                  >
                    {recordLabel(
                      'Source document',
                      source.evidence.source_document_filename,
                    )}{' '}
                    <ArrowUpRight />
                  </Link>
                </Button>
                <Button asChild variant="link" className="h-auto p-0 text-xs">
                  <Link
                    to={`/ledger?audit_type=evidence_item&audit_id=${source.evidence.id}`}
                  >
                    Evidence history <ArrowUpRight />
                  </Link>
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}
      {total > 0 && (
        <PageControls
          total={total}
          limit={pageSize}
          offset={offset}
          count={Math.max(0, Math.min(pageSize, total - offset))}
          onChange={setOffset}
        />
      )}
    </div>
  )
}
