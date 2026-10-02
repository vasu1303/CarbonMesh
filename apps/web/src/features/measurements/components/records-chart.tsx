import { Bar, BarChart, CartesianGrid, XAxis, YAxis } from 'recharts'
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
} from '@/components/ui/chart'
import { EmptyState } from '@/components/query-state'
import { Button } from '@/components/ui/button'
import { formatDecimal } from '@/lib/format'
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
  const data = records.map((item, index) => ({
    label: `R${index + 1}`,
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
            onClick={(data) => {
              if (typeof data.id === 'string') onInspect(data.id)
            }}
            cursor="pointer"
          />
        </BarChart>
      </ChartContainer>
      <ul className="mt-4 grid gap-x-6 divide-y sm:grid-cols-2">
        {records.map((item, index) => (
          <li
            key={item.id}
            className="flex items-center justify-between gap-3 py-2 text-xs"
          >
            <Button
              variant="link"
              className="h-auto p-0 text-xs"
              onClick={() => onInspect(item.id)}
            >
              R{index + 1} / {item.id.slice(-12)}
            </Button>
            <span className="capitalize text-muted-foreground">
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
