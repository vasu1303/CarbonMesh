import {
  Bar,
  BarChart,
  CartesianGrid,
  ReferenceArea,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { displayText, humanize } from '@/lib/presentation'
import { EmptyState } from '@/components/query-state'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import {
  HashValue,
  LedgerLink,
  StateBadge,
} from '@/features/assurance/workflow-ui'
import type {
  Constraints,
  ForecastPoint,
  Load,
  Recommendation,
} from './schemas'

export function OperatingConstraints({
  items,
}: {
  items: Load['constraints']
}) {
  return (
    <div className="space-y-3">
      {!items.length && (
        <p className="text-sm text-muted-foreground">
          No operating constraints recorded.
        </p>
      )}
      {items.map((item) => (
        <div key={item.id} className="space-y-2 border-b pb-3 text-sm">
          <div className="flex flex-wrap gap-2">
            <span className="font-medium">{displayText(item.name)}</span>
            <StateBadge
              state={item.is_hard ? 'hard_constraint' : 'advisory_constraint'}
            />
            <span>{humanize(item.constraint_type)}</span>
          </div>
          {(item.valid_from || item.valid_to) && (
            <p className="break-words text-xs text-muted-foreground">
              {item.valid_from ?? 'No start bound'} to{' '}
              {item.valid_to ?? 'No end bound'}
            </p>
          )}
          <dl className="flex flex-wrap gap-x-5 gap-y-2">
            {Object.entries(item.configuration)
              .filter(
                ([key, value]) =>
                  !/(^id$|_ids?$|hash|signature)/i.test(key) &&
                  ['string', 'number', 'boolean'].includes(typeof value),
              )
              .map(([key, value]) => (
                <div key={key}>
                  <dt className="text-xs text-muted-foreground">
                    {humanize(key)}
                  </dt>
                  <dd className="break-all text-xs">{displayText(value)}</dd>
                </div>
              ))}
          </dl>
        </div>
      ))}
    </div>
  )
}

export function FrozenConstraints({ value }: { value: Constraints }) {
  return (
    <section className="space-y-4" aria-label="Frozen operating constraints">
      <h2 className="text-base font-semibold">Frozen operating constraints</h2>
      <dl className="grid gap-4 text-sm sm:grid-cols-2 lg:grid-cols-3">
        <div>
          <dt className="text-xs text-muted-foreground">Earliest start</dt>
          <dd>{value.earliest_start}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Latest finish</dt>
          <dd>{value.latest_finish}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Maximum delay</dt>
          <dd>{value.maximum_delay_minutes} minutes</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Baseline start</dt>
          <dd>{value.baseline_start}</dd>
        </div>
        {value.load_snapshot && (
          <>
            <div>
              <dt className="text-xs text-muted-foreground">Load</dt>
              <dd>{displayText(value.load_snapshot.name)}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">
                Power / duration / energy
              </dt>
              <dd>
                {value.load_snapshot.power_kw} kW /{' '}
                {value.load_snapshot.duration_minutes} min /{' '}
                {value.load_snapshot.energy_kwh} kWh
              </dd>
            </div>
          </>
        )}
      </dl>
      {value.blackouts.length > 0 && (
        <div>
          <h3 className="mb-2 text-sm font-medium">Blackouts</h3>
          <ul className="space-y-1 text-sm">
            {value.blackouts.map((window, i) => (
              <li key={i}>
                {window.start} to {window.end}
              </li>
            ))}
          </ul>
        </div>
      )}
      {value.capacity_windows.length > 0 && (
        <div>
          <h3 className="mb-2 text-sm font-medium">Available capacity</h3>
          <ul className="space-y-1 text-sm">
            {value.capacity_windows.map((window, i) => (
              <li key={i}>
                {window.available_capacity_kw} kW /{' '}
                {window.start ?? 'Whole interval'}
                {window.end ? ` to ${window.end}` : ''}
              </li>
            ))}
          </ul>
        </div>
      )}
      <details>
        <summary className="cursor-pointer text-sm">
          Source constraints and method
        </summary>
        <div className="mt-3 space-y-4">
          <OperatingConstraints items={value.source_constraints} />
          {value.method_snapshot && (
            <dl className="grid gap-3 sm:grid-cols-2">
              <div>
                <dt className="text-xs text-muted-foreground">Method</dt>
                <dd className="text-sm">
                  {humanize(value.method_snapshot.key)} /{' '}
                  {displayText(value.method_snapshot.version)}
                </dd>
              </div>
              <HashValue
                label="Method hash"
                value={value.method_snapshot.snapshot_hash}
              />
            </dl>
          )}
        </div>
      </details>
    </section>
  )
}

export function ForecastView({
  points,
  recommendation,
}: {
  points: ForecastPoint[]
  recommendation?: Recommendation | null
}) {
  if (!points.length)
    return (
      <EmptyState
        title="No forecast points"
        detail="No recorded intervals are available."
      />
    )
  // Floating-point conversion is limited to chart coordinates; fact labels retain API Decimal strings.
  const chart = points.map((point) => ({
    ...point,
    timestamp: Date.parse(point.forecast_for),
    intensity: Number(point.intensity_gco2e_per_kwh),
  }))
  return (
    <section aria-label="Forecast intervals" className="min-w-0 space-y-4">
      <h2 className="text-base font-semibold">Forecast intensity</h2>
      <div
        className="h-64 w-full min-w-0"
        role="img"
        aria-label="Recorded forecast intensity; exact interval values are listed below"
      >
        <ResponsiveContainer width="100%" height="100%">
          <BarChart
            data={chart}
            margin={{ top: 12, right: 10, left: 0, bottom: 4 }}
          >
            <CartesianGrid vertical={false} strokeDasharray="3 3" />
            <XAxis
              type="number"
              dataKey="timestamp"
              domain={['dataMin', 'dataMax']}
              tickFormatter={(value: number) =>
                new Date(value).toISOString().slice(11, 16)
              }
              scale="time"
              fontSize={11}
            />
            <YAxis width={48} fontSize={11} />
            <Tooltip
              content={({ active, payload }) =>
                active && payload?.[0] ? (
                  <div className="rounded border bg-background p-3 text-xs">
                    <p>{payload[0].payload.forecast_for}</p>
                    <p>
                      {payload[0].payload.intensity_gco2e_per_kwh} gCO2e/kWh
                    </p>
                  </div>
                ) : null
              }
            />
            {recommendation && (
              <ReferenceArea
                x1={Date.parse(recommendation.recommended_start)}
                x2={Date.parse(recommendation.recommended_end)}
                fill="#047857"
                fillOpacity={0.12}
              />
            )}
            <Bar
              dataKey="intensity"
              fill="#64748b"
              isAnimationActive={false}
              maxBarSize={24}
            />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <details>
        <summary className="cursor-pointer text-sm font-medium">
          Exact forecast values / UTC
        </summary>
        <Table className="mt-3">
          <TableHeader>
            <TableRow>
              <TableHead>Interval</TableHead>
              <TableHead>gCO2e/kWh</TableHead>
              <TableHead>Quality</TableHead>
              <TableHead>Window</TableHead>
              <TableHead>Provenance</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {points.map((point) => {
              const time = Date.parse(point.forecast_for)
              const selected =
                recommendation &&
                time >= Date.parse(recommendation.recommended_start) &&
                time < Date.parse(recommendation.recommended_end)
              const baseline =
                recommendation &&
                time >= Date.parse(recommendation.baseline_start) &&
                time < Date.parse(recommendation.baseline_end)
              return (
                <TableRow key={point.id}>
                  <TableCell>{point.forecast_for}</TableCell>
                  <TableCell className="font-mono">
                    {point.intensity_gco2e_per_kwh}
                  </TableCell>
                  <TableCell>
                    {point.is_estimated === undefined
                      ? 'Not supplied'
                      : point.is_estimated
                        ? 'Estimated'
                        : 'Provider'}
                  </TableCell>
                  <TableCell>
                    {selected ? 'Recommended' : ''}
                    {selected && baseline ? ' / ' : ''}
                    {baseline ? 'Baseline' : ''}
                  </TableCell>
                  <TableCell>
                    <details>
                      <summary className="cursor-pointer text-xs">
                        Point evidence
                      </summary>
                      <dl className="max-w-72 space-y-2 py-2">
                        <HashValue
                          label="Point hash"
                          value={point.point_hash}
                        />
                        <div>
                          <dt className="text-xs text-muted-foreground">
                            Evidence
                          </dt>
                          <dd className="text-xs">
                            {point.evidence_item_id ? 'Linked' : 'Missing'}
                          </dd>
                        </div>
                        {point.forecast_ledger_event_id && (
                          <div>
                            <dt className="text-xs">Forecast ledger</dt>
                            <dd>
                              <LedgerLink id={point.forecast_ledger_event_id} />
                            </dd>
                          </div>
                        )}
                      </dl>
                    </details>
                  </TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      </details>
    </section>
  )
}
