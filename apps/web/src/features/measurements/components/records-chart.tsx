import { Bar, BarChart, CartesianGrid, XAxis, YAxis } from 'recharts'
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
} from '@/components/ui/chart'
import { EmptyState } from '@/components/query-state'
import { Button } from '@/components/ui/button'
import { formatDate, formatDecimal } from '@/lib/format'
import { humanize } from '@/lib/presentation'
import type { MeasurementSummary } from '../schemas'

export default function RecordsChart({
  items,
  onInspect,
}: {
  items: MeasurementSummary[]
  onInspect: (id: string) => void
}) {
  const records = items.filter((item) => item.status !== 'unsupported')
  if (!records.length)
    return (
      <EmptyState
        title="No supported values on this page"
        detail="Unsupported records are excluded from the chart."
      />
    )
  const data = records.map((item) => ({
    label: formatDate(item.created_at),
    metric: humanize(item.metric_key),
    value: Number(item.value_kgco2e),
    exact: item.value_kgco2e,
    status: item.status,
    id: item.id,
  }))
  return (
    <>
      <ChartContainer
        config={{ value: { label: 'kgCO2e', color: '#059669' } }}
        className="aspect-auto h-64 w-full"
      >
        <BarChart data={data} accessibilityLayer margin={{ top: 12, right: 8 }}>
          <CartesianGrid vertical={false} />
          <XAxis dataKey="label" tickLine={false} axisLine={false} />
          <YAxis
            width={62}
            tickLine={false}
            axisLine={false}
            tickFormatter={(value: number) =>
              new Intl.NumberFormat('en', { notation: 'compact' }).format(value)
            }
          />
          <ChartTooltip
            content={
              <ChartTooltipContent
                formatter={(_value, _name, item) => (
                  <div className="space-y-1">
                    <p className="max-w-64 text-xs wrap-anywhere">
                      {item.payload.metric}
                    </p>
                    <p className="font-mono">
                      {formatDecimal(item.payload.exact)} kgCO2e
                    </p>
                    <p className="capitalize text-muted-foreground">
                      {item.payload.status}
                    </p>
                  </div>
                )}
              />
            }
          />
          <Bar
            dataKey="value"
            fill="var(--color-value)"
            radius={[3, 3, 0, 0]}
            maxBarSize={52}
            isAnimationActive={false}
            onClick={(_item, index) => {
              const selected = data[index]
              if (selected) onInspect(selected.id)
            }}
            cursor="pointer"
          />
        </BarChart>
      </ChartContainer>
      <ul className="mt-4 grid min-w-0 grid-cols-1 gap-x-6 divide-y sm:grid-cols-2">
        {records.map((item) => (
          <li
            key={item.id}
            className="flex min-w-0 items-center justify-between gap-3 py-2 text-xs"
          >
            <Button
              variant="link"
              className="h-auto min-w-0 flex-1 shrink justify-start p-0 text-left text-xs whitespace-normal"
              onClick={() => onInspect(item.id)}
            >
              <span className="min-w-0 wrap-anywhere">
                {humanize(item.metric_key)} / {formatDate(item.created_at)}
              </span>
            </Button>
            <span className="shrink-0 capitalize text-muted-foreground">
              {item.status}
            </span>
          </li>
        ))}
      </ul>
      <p className="mt-4 text-xs text-muted-foreground">
        Current page only. Individual results may overlap and are not an
        additive footprint or a time series.
      </p>
    </>
  )
}
