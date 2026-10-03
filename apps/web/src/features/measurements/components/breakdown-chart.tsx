import { CartesianGrid, Line, LineChart, XAxis, YAxis } from 'recharts'

import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
} from '@/components/ui/chart'
import { formatDecimal } from '@/lib/format'
import type { MeasurementBreakdown } from '../schemas'

export default function BreakdownChart({
  items,
}: {
  items: MeasurementBreakdown['items']
}) {
  const data = items.map((item, index) => ({
    label: item.interval_start ?? item.activity_date ?? item.material_code,
    row: index + 1,
    value: Number(item.emissions_kgco2e),
    exact: item.emissions_kgco2e,
  }))
  return (
    <ChartContainer
      config={{ value: { label: 'kgCO2e', color: '#059669' } }}
      className="aspect-auto h-56 w-full"
    >
      <LineChart data={data} accessibilityLayer margin={{ top: 12, right: 12 }}>
        <CartesianGrid vertical={false} />
        <XAxis dataKey="row" tickLine={false} axisLine={false} />
        <YAxis
          width={64}
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
                <div className="max-w-64 space-y-1 wrap-anywhere">
                  <p className="text-xs text-muted-foreground">
                    {item.payload.label}
                  </p>
                  <p className="font-mono">
                    {formatDecimal(item.payload.exact)} kgCO2e
                  </p>
                </div>
              )}
            />
          }
        />
        <Line
          dataKey="value"
          type="linear"
          stroke="var(--color-value)"
          strokeWidth={2}
          dot={data.length <= 25}
          isAnimationActive={false}
        />
      </LineChart>
    </ChartContainer>
  )
}
